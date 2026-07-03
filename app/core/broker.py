"""
broker.py — Message Broker Abstraction for FRIDAY.

Decouples the EventBus and CommandBus from the underlying transport.
Currently uses an InMemoryBroker, but allows seamless migration to
MQTT, Redis, or NATS when FRIDAY expands to a multi-node cluster.

Architecture:
    EventBus/CommandBus → MessageBroker (ABC) → InMemory / MQTT / Redis
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Set, Union

from app.core.logger import logger


class MessageBroker(ABC):
    """Abstract interface for all message brokers."""
    
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the broker."""
        ...
        
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        ...
        
    @abstractmethod
    async def publish(self, topic: str, payload: Any, priority: int = 0) -> None:
        """Publish a message to a topic."""
        ...
        
    @abstractmethod
    def subscribe(self, topic: str, callback: Callable[[str, Any], None]) -> None:
        """Subscribe to a topic with a callback."""
        ...
        
    @abstractmethod
    def unsubscribe(self, topic: str, callback: Callable) -> None:
        """Remove a subscription."""
        ...


class InMemoryBroker(MessageBroker):
    """Local, memory-based broker for single-node deployments.
    
    Uses asyncio queues and background dispatching.
    """
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        
    async def connect(self) -> bool:
        if self._running:
            return True
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop())
        logger.info("InMemoryBroker: connected and dispatching.")
        return True
        
    async def disconnect(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("InMemoryBroker: disconnected.")
        
    async def publish(self, topic: str, payload: Any, priority: int = 0) -> None:
        """Publish message (priority ignored in basic memory broker for now)."""
        await self._queue.put((topic, payload))
        
    def subscribe(self, topic: str, callback: Callable[[str, Any], None]) -> None:
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        if callback not in self._subscribers[topic]:
            self._subscribers[topic].append(callback)
            
    def unsubscribe(self, topic: str, callback: Callable) -> None:
        if topic in self._subscribers:
            if callback in self._subscribers[topic]:
                self._subscribers[topic].remove(callback)
                
    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                topic, payload = await self._queue.get()
                handlers = self._subscribers.get(topic, [])
                wildcards = self._subscribers.get("*", [])
                
                for handler in handlers + wildcards:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            await handler(topic, payload)
                        else:
                            handler(topic, payload)
                    except Exception as e:
                        logger.error(f"InMemoryBroker: handler failed on '{topic}': {e}")
                        
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"InMemoryBroker: dispatch error: {e}")
