"""
devices.py — Device Abstraction Layer for FRIDAY.

Provides a uniform interface for all physical and virtual devices.
Every future device (light, sensor, camera, NAS, router) becomes
simply: device.execute(action)

Architecture:
    DeviceManager
        ├── register(device)
        ├── get(device_id)
        ├── execute(device_id, action)
        └── status(device_id)

    All device actions flow through:
        AI → Policy Engine → Device Layer → Physical Device
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import unique, Enum
from typing import Any, Dict, List, Optional

from app.core.events.event import Event, Priority
from app.core.events.event_bus import EventBus
from app.core.events.event_types import EventType
from app.core.logger import logger
from app.core.policy_engine import PolicyEngine, PolicyRequest


@unique
class DeviceType(str, Enum):
    """Standard device type categories."""
    LIGHT       = "light"
    SWITCH      = "switch"
    SENSOR      = "sensor"
    CAMERA      = "camera"
    LOCK        = "lock"
    THERMOSTAT  = "thermostat"
    FAN         = "fan"
    TV          = "tv"
    SPEAKER     = "speaker"
    NAS         = "nas"
    ROUTER      = "router"
    CUSTOM      = "custom"


@unique
class DeviceStatus(str, Enum):
    """Device connection states."""
    ONLINE      = "online"
    OFFLINE     = "offline"
    ERROR       = "error"
    CONNECTING  = "connecting"


@dataclass
class DeviceAction:
    """An action to be executed on a device."""
    command: str                            # e.g. "turn_on", "set_brightness", "lock"
    params: Dict[str, Any] = field(default_factory=dict)
    source: str = "user"                    # who requested this action


@dataclass
class DeviceActionResult:
    """Result of a device action execution."""
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class Device(ABC):
    """Abstract base class for all FRIDAY devices.

    Every device (physical or virtual) must implement this interface.
    The DeviceManager uses it to provide a uniform control layer.

    Subclass example::

        class MqttLight(Device):
            @property
            def device_id(self) -> str: return "light_living_room_01"

            @property
            def name(self) -> str: return "Living Room Light"

            @property
            def device_type(self) -> DeviceType: return DeviceType.LIGHT

            async def connect(self) -> bool:
                # connect to MQTT broker
                return True

            async def execute(self, action: DeviceAction) -> DeviceActionResult:
                if action.command == "turn_on":
                    # publish MQTT message
                    return DeviceActionResult(success=True, message="Light turned on")
                ...

            async def get_status(self) -> Dict[str, Any]:
                return {"power": "on", "brightness": 80}
    """

    @property
    @abstractmethod
    def device_id(self) -> str:
        """Unique device identifier."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable device name."""
        ...

    @property
    @abstractmethod
    def device_type(self) -> DeviceType:
        """Device category."""
        ...

    @property
    def protocol(self) -> str:
        """Communication protocol (mqtt, http, zigbee, etc)."""
        return "mqtt"

    @property
    def location(self) -> Optional[str]:
        """Physical location (room, zone)."""
        return None

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the physical device. Returns True on success."""
        ...

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        pass

    @abstractmethod
    async def execute(self, action: DeviceAction) -> DeviceActionResult:
        """Execute an action on the device."""
        ...

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Get current device state."""
        ...

    @property
    def status(self) -> DeviceStatus:
        """Connection status."""
        return DeviceStatus.OFFLINE


class DeviceManager:
    """Manages all registered devices through a uniform interface.

    All device commands go through Policy Engine validation before execution.

    Usage::

        dm = DeviceManager(bus, policy_engine)
        dm.register(my_light)
        result = await dm.execute("light_01", DeviceAction(command="turn_on"))
    """

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        policy_engine: Optional[PolicyEngine] = None,
    ):
        self._devices: Dict[str, Device] = {}
        self._bus = bus
        self._policy = policy_engine

    def register(self, device: Device) -> bool:
        """Register a device with the manager."""
        if device.device_id in self._devices:
            logger.warning(f"DeviceManager: device '{device.device_id}' already registered")
            return False

        self._devices[device.device_id] = device
        logger.info(
            f"DeviceManager: registered {device.device_type.value} "
            f"'{device.name}' ({device.device_id})"
        )

        # Persist to database
        self._persist_device(device)

        # Emit event
        if self._bus:
            self._bus.publish_sync(Event(
                event_type=EventType.DEVICE_CONNECTED,
                source="device_manager",
                payload={
                    "device_id": device.device_id,
                    "name": device.name,
                    "type": device.device_type.value,
                },
                priority=Priority.LOW,
            ))

        return True

    def unregister(self, device_id: str) -> bool:
        """Remove a device from the manager."""
        device = self._devices.pop(device_id, None)
        if device is None:
            return False

        if self._bus:
            self._bus.publish_sync(Event(
                event_type=EventType.DEVICE_DISCONNECTED,
                source="device_manager",
                payload={"device_id": device_id, "name": device.name},
                priority=Priority.LOW,
            ))

        logger.info(f"DeviceManager: unregistered '{device_id}'")
        return True

    def get(self, device_id: str) -> Optional[Device]:
        """Get a registered device by ID."""
        return self._devices.get(device_id)

    async def execute(
        self,
        device_id: str,
        action: DeviceAction,
        role: str = "owner",
    ) -> DeviceActionResult:
        """Execute an action on a device, with policy enforcement.

        Flow: PolicyEngine → Device.execute() → EventBus notification
        """
        device = self._devices.get(device_id)
        if device is None:
            return DeviceActionResult(
                success=False,
                message=f"Device '{device_id}' not found",
            )

        # Policy check
        if self._policy:
            policy_action = f"device.{device.device_type.value}.{action.command}"
            decision = self._policy.evaluate(PolicyRequest(
                action=policy_action,
                role=role,
                context={
                    "device_id": device_id,
                    "device_type": device.device_type.value,
                    "command": action.command,
                    "params": action.params,
                },
                source=action.source,
            ))

            if decision.decision.value != "allow":
                logger.warning(
                    f"DeviceManager: policy DENIED {policy_action} for {role}: {decision.reason}"
                )
                return DeviceActionResult(
                    success=False,
                    message=f"Policy denied: {decision.reason}",
                )

        # Execute
        try:
            result = await device.execute(action)

            # Emit event
            if self._bus:
                self._bus.publish_sync(Event(
                    event_type=EventType.DEVICE_EVENT,
                    source="device_manager",
                    payload={
                        "device_id": device_id,
                        "command": action.command,
                        "success": result.success,
                        "message": result.message,
                    },
                    priority=Priority.MEDIUM,
                ))

            return result

        except Exception as e:
            logger.error(f"DeviceManager: execution failed on '{device_id}': {e}")
            return DeviceActionResult(success=False, message=str(e))

    async def get_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a device."""
        device = self._devices.get(device_id)
        if device is None:
            return None
        try:
            return await device.get_status()
        except Exception as e:
            logger.error(f"DeviceManager: status query failed for '{device_id}': {e}")
            return {"error": str(e)}

    def list_devices(self) -> List[Dict[str, Any]]:
        """List all registered devices."""
        return [
            {
                "device_id": d.device_id,
                "name": d.name,
                "type": d.device_type.value,
                "protocol": d.protocol,
                "location": d.location,
                "status": d.status.value,
            }
            for d in self._devices.values()
        ]

    def _persist_device(self, device: Device) -> None:
        """Save device metadata to the database."""
        try:
            from app.memory.database import get_db_session
            from app.memory.models import DeviceRecord

            with get_db_session() as session:
                existing = session.query(DeviceRecord).filter_by(
                    device_id=device.device_id
                ).first()

                if existing:
                    existing.name = device.name
                    existing.device_type = device.device_type.value
                    existing.protocol = device.protocol
                    existing.status = device.status.value
                else:
                    record = DeviceRecord(
                        device_id=device.device_id,
                        name=device.name,
                        device_type=device.device_type.value,
                        protocol=device.protocol,
                        location=device.location,
                        status=device.status.value,
                    )
                    session.add(record)

        except Exception as e:
            logger.error(f"DeviceManager: failed to persist device '{device.device_id}': {e}")
