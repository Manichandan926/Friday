"""
discovery.py — Device Discovery Actor for FRIDAY.

Listens for auto-discovery payloads on MQTT or other transports and
automatically provisions them in the DeviceRegistry.

Matches the Home Assistant MQTT Discovery protocol approach.
"""

from app.core.actors.actor import Actor, ActorMessage
from app.core.logger import logger


class DeviceDiscoveryActor(Actor):
    """Automatically registers new physical devices when they broadcast presence."""

    def __init__(self):
        super().__init__(name="device_discovery")

    async def receive(self, message: ActorMessage) -> None:
        if message.message_type == "DISCOVERY_PAYLOAD":
            payload = message.payload
            device_id = payload.get("device_id")
            
            logger.info(f"DeviceDiscoveryActor: Discovered new device: {device_id}")
            # In a full implementation, this would instantiate the correct 
            # DeviceDriver and Device subclass, then call dm.register()
