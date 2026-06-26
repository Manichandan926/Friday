"""
transport_runtime.pyi — Interface stub for the future Rust TransportRuntime.

Defines the stable PyO3 boundaries for MQTT, RTSP, and hardware I/O.
"""

from typing import Any, Callable, Coroutine
from app.core.transports.transport import TransportMessage


class TransportRuntime:
    """Core runtime interface for Hardware transports."""
    
    async def connect(self) -> bool: ...
    
    async def disconnect(self) -> None: ...
    
    async def publish(self, message: TransportMessage) -> bool: ...
    
    async def subscribe(self, topic: str, callback: Callable[[TransportMessage], Coroutine[Any, Any, None]]) -> bool: ...
