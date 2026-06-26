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
        ├── status(device_id)  ← reads from TTL cache first
        └── capabilities(device_id)

    All device actions flow through:
        AI → Policy Engine → Device Layer → Physical Device
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import unique, Enum
from typing import Any, Dict, List, Optional, Set

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


class DeviceDriver(ABC):
    """Abstract base class for physical device protocols (MQTT, HTTP, Zigbee, etc)."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish physical connection."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        ...

    @abstractmethod
    async def execute(self, action: DeviceAction) -> DeviceActionResult:
        """Execute action over the protocol."""
        ...

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Fetch raw state from hardware."""
        ...


class Device(ABC):
    """Abstract base class for logical FRIDAY devices.

    Separates the logical device representation from the physical protocol
    layer (DeviceDriver).
    """

    def __init__(self, driver: DeviceDriver):
        self.driver = driver

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
        """Communication protocol (derived from driver type usually)."""
        return self.driver.__class__.__name__.replace("Driver", "").lower()

    @property
    def location(self) -> Optional[str]:
        """Physical location (room, zone)."""
        return None

    @property
    def capabilities(self) -> Set[str]:
        """Declare what this device can do."""
        return set()

    async def execute(self, action: DeviceAction) -> DeviceActionResult:
        """Execute an action on the device via its driver."""
        return await self.driver.execute(action)

    async def get_status(self) -> Dict[str, Any]:
        """Get current device state via its driver."""
        return await self.driver.get_status()

    @property
    def status(self) -> DeviceStatus:
        """Connection status."""
        return DeviceStatus.OFFLINE


class DeviceStateCache:
    """TTL-based cache for device states.

    Prevents querying hardware on every status request.
    With 100+ devices, polling hardware each time is expensive.

    Usage::

        cache = DeviceStateCache(ttl_seconds=10)
        cache.set("light_01", {"power": "on", "brightness": 80})
        state = cache.get("light_01")  # → cached dict or None if expired
    """

    def __init__(self, ttl_seconds: float = 10.0):
        self._ttl = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}       # device_id → state
        self._timestamps: Dict[str, float] = {}            # device_id → time.monotonic()

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get cached state if TTL has not expired."""
        ts = self._timestamps.get(device_id)
        if ts is None:
            return None
        if (time.monotonic() - ts) > self._ttl:
            # Expired
            self._cache.pop(device_id, None)
            self._timestamps.pop(device_id, None)
            return None
        return self._cache.get(device_id)

    def set(self, device_id: str, state: Dict[str, Any]) -> None:
        """Cache a device state snapshot."""
        self._cache[device_id] = state
        self._timestamps[device_id] = time.monotonic()

    def invalidate(self, device_id: str) -> None:
        """Invalidate cache for a specific device."""
        self._cache.pop(device_id, None)
        self._timestamps.pop(device_id, None)

    def invalidate_all(self) -> None:
        """Clear entire cache."""
        self._cache.clear()
        self._timestamps.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class DeviceManager:
    """Manages all registered devices through a uniform interface.

    All device commands go through Policy Engine validation before execution.
    Device status queries use a TTL cache to avoid hardware polling overhead.

    Usage::

        dm = DeviceManager(bus, policy_engine)
        dm.register(my_light)
        result = await dm.execute("light_01", DeviceAction(command="turn_on"))
    """

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        policy_engine: Optional[PolicyEngine] = None,
        cache_ttl: float = 10.0,
    ):
        self._devices: Dict[str, Device] = {}
        self._bus = bus
        self._policy = policy_engine
        self._state_cache = DeviceStateCache(ttl_seconds=cache_ttl)

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

            # Invalidate cache after action (state likely changed)
            self._state_cache.invalidate(device_id)

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
        """Get current status (cache-first, then hardware query)."""
        # Try cache first
        cached = self._state_cache.get(device_id)
        if cached is not None:
            return cached

        # Cache miss — query hardware
        device = self._devices.get(device_id)
        if device is None:
            return None
        try:
            status = await device.get_status()
            self._state_cache.set(device_id, status)
            return status
        except Exception as e:
            logger.error(f"DeviceManager: status query failed for '{device_id}': {e}")
            return {"error": str(e)}

    def get_capabilities(self, device_id: str) -> Optional[Set[str]]:
        """Get the declared capabilities of a device."""
        device = self._devices.get(device_id)
        if device is None:
            return None
        return device.capabilities

    def list_devices(self) -> List[Dict[str, Any]]:
        """List all registered devices with capabilities."""
        return [
            {
                "device_id": d.device_id,
                "name": d.name,
                "type": d.device_type.value,
                "protocol": d.protocol,
                "location": d.location,
                "status": d.status.value,
                "capabilities": sorted(d.capabilities),
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
