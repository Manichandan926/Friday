"""
mqtt.py — MQTT Actor for FRIDAY.

Bridges the physical MQTT broker to the logical FRIDAY EventBus.
Receives MQTT messages, decodes them, and publishes standard FRIDAY Events.
Listens to FRIDAY Events and publishes MQTT messages (e.g. commands to devices).
"""

import json
from typing import Optional

from app.core.actors.actor import Actor, ActorMessage
from app.core.events.event import Event, Priority
from app.core.events.event_bus import EventBus
from app.core.logger import logger
from app.core.transports.transport import Transport, TransportMessage


class MQTTActor(Actor):
    """Bridges MQTT traffic to/from the FRIDAY EventBus."""

    def __init__(self, transport: Transport, bus: EventBus):
        super().__init__(name="mqtt_actor")
        self.transport = transport
        self.bus = bus

    async def on_start(self) -> None:
        """Connect transport and subscribe to inbound device topics."""
        success = await self.transport.connect()
        if not success:
            raise RuntimeError("MQTTActor: Failed to connect transport")
            
        # Subscribe to standard FRIDAY device telemetry topic
        await self.transport.subscribe("friday/devices/#", self._handle_mqtt_message)
        
        # Subscribe to internal EventBus to forward outgoing commands
        self.bus.subscribe("device.command", self._handle_bus_event)

    async def on_stop(self) -> None:
        await self.transport.disconnect()

    async def receive(self, message: ActorMessage) -> None:
        """Handle internal mailbox messages (mostly lifecycle or dynamic sub requests)."""
        if message.message_type == "PUBLISH":
            payload = message.payload
            tm = TransportMessage(
                topic=payload["topic"],
                payload=json.dumps(payload["data"]).encode(),
                qos=payload.get("qos", 0)
            )
            await self.transport.publish(tm)

    async def _handle_mqtt_message(self, msg: TransportMessage) -> None:
        """Callback from transport. Decodes MQTT and fires EventBus event."""
        try:
            data = json.loads(msg.payload.decode())
            topic_parts = msg.topic.split("/")
            
            # e.g. friday/devices/light_01/status
            if len(topic_parts) >= 4:
                device_id = topic_parts[2]
                event_type = topic_parts[3] # e.g. status, telemetry
                
                event = Event(
                    event_type=f"device.{event_type}",
                    source=f"mqtt:{device_id}",
                    payload=data,
                    priority=Priority.LOW  # Default telemetry is LOW
                )
                await self.bus.publish(event)
        except Exception as e:
            logger.error(f"MQTTActor: Failed to parse incoming MQTT message {msg.topic}: {e}")
            # Forward to Dead Letter Queue
            from app.core.actors.system import ActorSystem
            sys = ActorSystem.get_instance()
            await sys.send("dead_letter", ActorMessage(
                sender="mqtt_actor",
                message_type="PARSE_ERROR",
                payload={"topic": msg.topic, "payload": msg.payload, "error": str(e)}
            ))

    async def _handle_bus_event(self, event: Event) -> None:
        """Callback from EventBus. Forwards device commands to MQTT."""
        try:
            device_id = event.payload.get("device_id")
            command = event.payload.get("command")
            params = event.payload.get("params", {})
            
            if not device_id or not command:
                return
                
            topic = f"friday/devices/{device_id}/command"
            tm = TransportMessage(
                topic=topic,
                payload=json.dumps({"command": command, "params": params}).encode(),
                qos=1
            )
            await self.transport.publish(tm)
            logger.debug(f"MQTTActor: Forwarded command {command} to {device_id}")
        except Exception as e:
            logger.error(f"MQTTActor: Failed to route bus event to MQTT: {e}")
