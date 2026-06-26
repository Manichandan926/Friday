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
    """Actor responsible for all SQLite write operations."""
    
    def __init__(self):
        super().__init__(name="db_writer")

    async def receive(self, message: ActorMessage) -> None:
        """Process incoming database write requests."""
        
        msg_type = message.message_type
        payload = message.payload
        
        try:
            # We use an executor to prevent blocking the async loop
            # since SQLAlchemy is synchronous.
            import asyncio
            loop = asyncio.get_running_loop()
            
            if msg_type == "WRITE_AUDIT":
                await loop.run_in_executor(None, self._write_audit, payload)
            elif msg_type == "WRITE_SNAPSHOT":
                await loop.run_in_executor(None, self._write_snapshot, payload)
            elif msg_type == "WRITE_EVENT":
                await loop.run_in_executor(None, self._write_event, payload)
            else:
                logger.warning(f"DBWriterActor: Unknown message type {msg_type}")
                
            if message.reply_to:
                await message.reply_to.put(True)
                
        except Exception as e:
            logger.error(f"DBWriterActor: Error writing {msg_type}: {e}")
            if message.reply_to:
                await message.reply_to.put(False)

    def _write_audit(self, payload: dict) -> None:
        with get_db_session() as session:
            record = AuditRecord(
                actor=payload["actor"],
                action=payload["action"],
                resource=payload["resource"],
                decision=payload["decision"],
                source_ip=payload.get("source_ip"),
                context_data=json.dumps(payload.get("context_data")) if payload.get("context_data") else None
            )
            session.add(record)

    def _write_snapshot(self, payload: dict) -> None:
        with get_db_session() as session:
            record = StateSnapshot(
                version=payload["version"],
                state_data=payload["state_data"]
            )
            session.add(record)

    def _write_event(self, payload: dict) -> None:
        with get_db_session() as session:
            record = EventStore(
                event_id=payload["event_id"],
                event_type=payload["event_type"],
                source=payload["source"],
                payload=json.dumps(payload["payload"]),
                priority=payload["priority"]
            )
            session.add(record)
