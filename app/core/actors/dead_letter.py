"""
dead_letter.py — Dead Letter Queue Actor for FRIDAY.

Captures events, commands, or network messages that failed to process
(due to exceptions, bad formatting, or missing handlers). 
This allows for manual inspection and automated retry logic later.
"""

from typing import Dict, Any, List
from datetime import datetime, timezone

from app.core.actors.actor import Actor, ActorMessage
from app.core.logger import logger


class DeadLetterActor(Actor):
    """Stores failed messages for debugging and replay."""

    def __init__(self, max_memory_items: int = 1000):
        super().__init__(name="dead_letter")
        self._dlq: List[Dict[str, Any]] = []
        self._max_items = max_memory_items

    async def receive(self, message: ActorMessage) -> None:
        """Handle incoming failed messages."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sender": message.sender,
            "error_type": message.message_type,
            "payload": message.payload
        }
        
        self._dlq.append(record)
        if len(self._dlq) > self._max_items:
            self._dlq.pop(0)
            
        logger.warning(f"DeadLetterActor: Captured failed message from [{message.sender}]: {message.message_type}")

    def get_failures(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent failures for debugging/dashboard."""
        return self._dlq[-limit:]
