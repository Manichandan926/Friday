"""
actor_runtime.pyi — Interface stub for the future Rust ActorRuntime.

Defines the stable PyO3 boundaries for the actor system and mailboxes.
"""

from typing import Any, Optional
from app.core.actors.actor import Actor, ActorMessage


class ActorRuntime:
    """Core runtime interface for Actor System implementations."""
    
    async def start_all(self) -> None: ...
    
    async def stop_all(self) -> None: ...
    
    def spawn(self, actor: Actor) -> None: ...
    
    def get_actor(self, name: str) -> Optional[Actor]: ...
    
    async def send(self, target: str, message: ActorMessage) -> bool: ...
