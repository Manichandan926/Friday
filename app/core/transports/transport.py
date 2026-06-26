"""
transport.py — Protocol transport abstraction for FRIDAY devices.

Prevents protocol-specific logic (MQTT, Zigbee, BLE) from leaking into
the orchestration layer. Transport classes manage pure physical connections
and publish/subscribe streams.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, Optional


@dataclass
class TransportMessage:
    topic: str
    payload: bytes
    qos: int = 0
    retain: bool = False


class Transport(ABC):
    """Abstract base class for all physical protocol transports."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the broker/hardware."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        ...

    @abstractmethod
    async def publish(self, message: TransportMessage) -> bool:
        """Publish a message over the transport."""
        ...

    @abstractmethod
    async def subscribe(self, topic: str, callback: Callable[[TransportMessage], Coroutine[Any, Any, None]]) -> bool:
        """Subscribe to a topic and register an async callback."""
        ...
