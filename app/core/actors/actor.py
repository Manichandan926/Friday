"""
actor.py — Base Actor implementation for FRIDAY.

Introduces the Actor Model to FRIDAY, enforcing strict message-passing.
Future migration to Rust (Actix/Ractor) will be simplified since all
interactions are forced through mailboxes, preventing shared state mutation.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional

from app.core.logger import logger


class RestartStrategy(Enum):
    NEVER = auto()
    ALWAYS = auto()
    ON_FAILURE = auto()


@dataclass
class ActorMessage:
    """Standardized message format for actor mailboxes."""
    sender: str
    message_type: str
    payload: Any
    reply_to: Optional[asyncio.Queue] = None


class Actor(ABC):
    """Base class for all FRIDAY Actors.
    
    Actors process messages sequentially from their mailbox, eliminating
    the need for locks.
    """

    def __init__(self, name: str, restart_strategy: RestartStrategy = RestartStrategy.ON_FAILURE):
        self.name = name
        self.restart_strategy = restart_strategy
        self.mailbox: asyncio.Queue[ActorMessage] = asyncio.Queue()
        self.state: Dict[str, Any] = {}
        
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the actor's processing loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        await self.on_start()
        logger.info(f"Actor [{self.name}]: started.")

    async def stop(self) -> None:
        """Gracefully stop the actor."""
        self._running = False
        # Send a poison pill to wake up the queue
        await self.mailbox.put(ActorMessage("system", "STOP", None))
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.on_stop()
        logger.info(f"Actor [{self.name}]: stopped.")

    async def send(self, message: ActorMessage) -> None:
        """Send a message to this actor's mailbox."""
        await self.mailbox.put(message)
        
    async def ask(self, sender: str, message_type: str, payload: Any, timeout: float = 5.0) -> Any:
        """Send a message and wait for a reply."""
        reply_queue = asyncio.Queue()
        msg = ActorMessage(sender=sender, message_type=message_type, payload=payload, reply_to=reply_queue)
        await self.mailbox.put(msg)
        
        try:
            # Wait for reply
            return await asyncio.wait_for(reply_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Actor [{self.name}]: Timeout waiting for reply to {message_type}")
            raise

    async def _run_loop(self) -> None:
        """Internal processing loop. Consumes mailbox messages sequentially."""
        while self._running:
            try:
                msg = await self.mailbox.get()
                
                if not self._running or msg.message_type == "STOP":
                    self.mailbox.task_done()
                    break

                # Process the message
                await self.receive(msg)
                
                self.mailbox.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Actor [{self.name}]: Unhandled exception during receive: {e}")
                # Restart logic handled by Supervisor in a real system,
                # here we just catch to prevent the task from dying silently
                if self.restart_strategy == RestartStrategy.NEVER:
                    self._running = False
                    break

    @abstractmethod
    async def receive(self, message: ActorMessage) -> None:
        """Process an incoming message. Must be implemented by subclasses."""
        pass

    async def on_start(self) -> None:
        """Hook called when the actor starts."""
        pass

    async def on_stop(self) -> None:
        """Hook called when the actor stops."""
        pass
