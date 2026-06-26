"""
event_runtime.pyi — Interface stub for the future Rust EventEngine.

Defines the stable PyO3 boundaries for the event bus runtime.
"""

from typing import Any, Callable, Coroutine, List, Optional
from app.core.events.event import Event


class EventRuntime:
    """Core runtime interface for EventBus implementations."""
    
    async def start(self) -> None: ...
    
    async def stop(self) -> None: ...
    
    async def publish(self, event: Event) -> None: ...
    
    def publish_sync(self, event: Event) -> None: ...
    
    def subscribe(self, event_type: str, handler: Any) -> None: ...
    
    def get_recent_events(self, limit: int = 100) -> List[Event]: ...
