"""
system.py — Actor System / Supervisor for FRIDAY.

Manages the lifecycle, registration, and restart strategies of all actors.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional, Type

from app.core.actors.actor import Actor, ActorMessage, RestartStrategy
from app.core.logger import logger


class ActorSystem:
    """Supervises and routes messages between actors."""
    
    _instance: Optional[ActorSystem] = None
    
    def __init__(self):
        self._actors: Dict[str, Actor] = {}
        self._running = False
        
    @classmethod
    def get_instance(cls) -> ActorSystem:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start_all(self) -> None:
        """Start all registered actors."""
        self._running = True
        for name, actor in self._actors.items():
            await actor.start()
        logger.info("ActorSystem: All actors started.")

    async def stop_all(self) -> None:
        """Stop all actors."""
        self._running = False
        tasks = [actor.stop() for actor in self._actors.values()]
        if tasks:
            await asyncio.gather(*tasks)
        logger.info("ActorSystem: All actors stopped.")

    def spawn(self, actor: Actor) -> None:
        """Register and spawn an actor."""
        if actor.name in self._actors:
            logger.warning(f"ActorSystem: Actor {actor.name} already exists. Overwriting.")
            
        self._actors[actor.name] = actor
        
        # Start immediately if system is running
        if self._running:
            asyncio.create_task(actor.start())
            
        logger.debug(f"ActorSystem: Spawned actor {actor.name}")

    def get_actor(self, name: str) -> Optional[Actor]:
        """Retrieve an actor by name."""
        return self._actors.get(name)

    async def send(self, target: str, message: ActorMessage) -> bool:
        """Route a message to a specific actor."""
        actor = self._actors.get(target)
        if not actor:
            logger.error(f"ActorSystem: Target actor '{target}' not found.")
            return False
            
        await actor.send(message)
        return True
