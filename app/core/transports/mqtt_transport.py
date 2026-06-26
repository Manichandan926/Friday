"""
mqtt.py — MQTT Transport implementation via aiomqtt.

Isolated here to ensure aiomqtt dependencies do not bleed into the
rest of the application architecture.
"""

import asyncio
from typing import Callable, Coroutine, Any, Dict, List

from app.core.logger import logger
from app.core.transports.transport import Transport, TransportMessage

try:
    import aiomqtt
except ImportError:
    aiomqtt = None  # type: ignore


class MQTTTransport(Transport):
    """Async MQTT Transport wrapper using aiomqtt."""

    def __init__(self, host: str = "localhost", port: int = 1883, username: str = None, password: str = None):
        if aiomqtt is None:
            logger.error("MQTTTransport: aiomqtt is not installed. Run `pip install aiomqtt`")
            raise ImportError("aiomqtt not installed")
            
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        
        self._client: aiomqtt.Client = None
        self._running = False
        self._listen_task: asyncio.Task = None
        self._callbacks: Dict[str, List[Callable[[TransportMessage], Coroutine[Any, Any, None]]]] = {}

    async def connect(self) -> bool:
        if self._running:
            return True
            
        try:
            self._client = aiomqtt.Client(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password
            )
            await self._client.__aenter__()
            self._running = True
            self._listen_task = asyncio.create_task(self._listen_loop())
            logger.info(f"MQTTTransport: Connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"MQTTTransport: Connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        if not self._running:
            return
            
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            
        if self._client:
            await self._client.__aexit__(None, None, None)
            
        logger.info("MQTTTransport: Disconnected")

    async def publish(self, message: TransportMessage) -> bool:
        if not self._running or not self._client:
            return False
            
        try:
            await self._client.publish(
                topic=message.topic,
                payload=message.payload,
                qos=message.qos,
                retain=message.retain
            )
            return True
        except Exception as e:
            logger.error(f"MQTTTransport: Publish failed on {message.topic}: {e}")
            return False

    async def subscribe(self, topic: str, callback: Callable[[TransportMessage], Coroutine[Any, Any, None]]) -> bool:
        if topic not in self._callbacks:
            self._callbacks[topic] = []
            if self._running and self._client:
                try:
                    await self._client.subscribe(topic)
                except Exception as e:
                    logger.error(f"MQTTTransport: Subscribe failed for {topic}: {e}")
                    return False
                    
        self._callbacks[topic].append(callback)
        logger.debug(f"MQTTTransport: Subscribed to {topic}")
        return True

    async def _listen_loop(self) -> None:
        """Internal loop to consume aiomqtt messages and route them to callbacks."""
        try:
            async for message in self._client.messages:
                topic_str = str(message.topic)
                tm = TransportMessage(
                    topic=topic_str,
                    payload=message.payload,
                    qos=message.qos,
                    retain=message.retain
                )
                
                # Naive routing (doesn't handle +/# wildcards fully yet, just exact matches)
                # In production, use aiomqtt topic matching
                if topic_str in self._callbacks:
                    for cb in self._callbacks[topic_str]:
                        asyncio.create_task(cb(tm))
                        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"MQTTTransport: Listen loop crashed: {e}")
            self._running = False
