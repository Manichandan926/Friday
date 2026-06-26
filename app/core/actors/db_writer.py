"""
db_writer.py — Database Writer Actor.

Handles all asynchronous database writes. Instead of each subsystem
(AuditSystem, SnapshotManager, EventBus) opening database transactions,
they send messages to the DBWriterActor. 

This prevents SQLite 'database is locked' errors by funneling all writes
through a single, sequential actor mailbox.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.actors.actor import Actor, ActorMessage
from app.core.logger import logger
from app.memory.database import get_db_session
from app.memory.models import AuditRecord, EventStore, StateSnapshot


class DBWriterActor(Actor):
    """Actor responsible for all SQLite write operations.
    
    Batches writes (100 records or 500ms) to drastically reduce disk I/O.
    """
    
    def __init__(self):
        super().__init__(name="db_writer")
        self._buffer: list[ActorMessage] = []
        self._flush_task: getattr(asyncio, 'Task', Any) = None

    async def on_start(self) -> None:
        import asyncio
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def on_stop(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        if self._buffer:
            await self._flush_buffer()

    async def _flush_loop(self) -> None:
        import asyncio
        while self._running:
            await asyncio.sleep(0.5)
            if self._buffer:
                # Send FLUSH command to our own mailbox to ensure sequential processing
                await self.send(ActorMessage(sender="system", message_type="FLUSH", payload=None))

    async def receive(self, message: ActorMessage) -> None:
        """Process incoming database write requests by buffering them."""
        
        if message.message_type == "FLUSH":
            if self._buffer:
                await self._flush_buffer()
            return

        self._buffer.append(message)
        
        if len(self._buffer) >= 100 or message.message_type == "DELETE_OLD_EVENTS":
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Execute all buffered writes in a single transaction."""
        import asyncio
        loop = asyncio.get_running_loop()
        
        batch = self._buffer[:]
        self._buffer.clear()
        
        try:
            await loop.run_in_executor(None, self._execute_batch, batch)
            # Notify waiters if requested
            for msg in batch:
                if msg.reply_to:
                    msg.reply_to.put_nowait(True)
        except Exception as e:
            logger.error(f"DBWriterActor: Error flushing batch: {e}")
            for msg in batch:
                if msg.reply_to:
                    msg.reply_to.put_nowait(False)

    def _execute_batch(self, batch: list[ActorMessage]) -> None:
        with get_db_session() as session:
            for message in batch:
                msg_type = message.message_type
                payload = message.payload
                
                try:
                    if msg_type == "WRITE_AUDIT":
                        record = AuditRecord(
                            actor=payload["actor"],
                            action=payload["action"],
                            resource=payload["resource"],
                            decision=payload["decision"],
                            source_ip=payload.get("source_ip"),
                            context_data=json.dumps(payload.get("context_data")) if payload.get("context_data") else None
                        )
                        session.add(record)
                    elif msg_type == "WRITE_SNAPSHOT":
                        record = StateSnapshot(
                            version=payload["version"],
                            state_data=payload["state_data"]
                        )
                        session.add(record)
                    elif msg_type == "WRITE_EVENT":
                        record = EventStore(
                            event_id=payload["event_id"],
                            event_type=payload["event_type"],
                            source=payload["source"],
                            payload=json.dumps(payload["payload"]),
                            priority=payload["priority"]
                        )
                        session.add(record)
                    elif msg_type == "DELETE_OLD_EVENTS":
                        # TTL cleanup
                        retention_days = payload.get("retention_days", 7)
                        import datetime
                        cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=retention_days)
                        
                        # Delete older than cutoff, except CRITICAL events
                        session.query(EventStore).filter(
                            EventStore.created_at < cutoff,
                            EventStore.priority != "CRITICAL"
                        ).delete()
                    else:
                        logger.warning(f"DBWriterActor: Unknown message type {msg_type}")
                except Exception as e:
                    logger.error(f"DBWriterActor: Failed to process record in batch: {e}")
