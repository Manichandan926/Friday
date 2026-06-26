"""
supervisor.py — Supervisor Actor for FRIDAY.

Manages the health and lifecycle of other actors. If an actor crashes,
the Supervisor intercepts the failure (via a fault message) and restarts
it according to its RestartStrategy.

This is a fundamental building block of the Erlang/OTP and Rust/Tokio
actor model (Let It Crash philosophy).
"""

from __future__ import annotations

import asyncio
from typing import Dict

from app.core.actors.actor import Actor, ActorMessage, RestartStrategy
from app.core.logger import logger


class SupervisorActor(Actor):
    """Monitors and restarts actors upon failure."""

    def __init__(self):
        super().__init__(name="supervisor", restart_strategy=RestartStrategy.ALWAYS)
        self._registry: Dict[str, Actor] = {}
        self._retry_counts: Dict[str, int] = {}
        self._max_retries = 5

    def watch(self, actor: Actor) -> None:
        """Register an actor under supervision."""
        self._registry[actor.name] = actor
        self._retry_counts[actor.name] = 0
        logger.debug(f"SupervisorActor: Now watching [{actor.name}]")

    async def receive(self, message: ActorMessage) -> None:
        """Handle incoming fault reports or lifecycle requests."""
        if message.message_type == "ACTOR_CRASHED":
            failed_actor_name = message.payload.get("actor_name")
            error = message.payload.get("error")
            await self._handle_crash(failed_actor_name, error)
            
        elif message.message_type == "RESTART_ACTOR":
            target = message.payload.get("actor_name")
            await self._restart_actor(target)

    async def _handle_crash(self, actor_name: str, error: str) -> None:
        actor = self._registry.get(actor_name)
        if not actor:
            logger.warning(f"SupervisorActor: Unknown actor [{actor_name}] crashed. Ignoring.")
            return

        logger.error(f"SupervisorActor: Actor [{actor_name}] crashed: {error}")

        if actor.restart_strategy == RestartStrategy.NEVER:
            logger.info(f"SupervisorActor: [{actor_name}] strategy is NEVER. Not restarting.")
            return

        retries = self._retry_counts.get(actor_name, 0)
        if retries >= self._max_retries:
            logger.error(f"SupervisorActor: [{actor_name}] exceeded max retries ({self._max_retries}). Giving up.")
            return

        self._retry_counts[actor_name] = retries + 1
        
        # Exponential backoff
        backoff = 2 ** retries
        logger.info(f"SupervisorActor: Restarting [{actor_name}] in {backoff} seconds (Attempt {retries + 1})...")
        
        import asyncio
        asyncio.create_task(self._delayed_restart(actor_name, backoff))

    async def _delayed_restart(self, actor_name: str, delay: int) -> None:
        await asyncio.sleep(delay)
        await self._restart_actor(actor_name)

    async def _restart_actor(self, actor_name: str) -> None:
        actor = self._registry.get(actor_name)
        if not actor:
            return

        logger.info(f"SupervisorActor: Booting [{actor_name}]...")
        
        # Force stop just in case
        actor._running = False
        if actor._task and not actor._task.done():
            actor._task.cancel()

        # Reset mailbox
        actor.mailbox = asyncio.Queue()
        
        # Start new task
        await actor.start()
