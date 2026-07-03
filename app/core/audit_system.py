"""
audit_system.py — Security Audit Logging for FRIDAY.

Records an immutable log of critical decisions, especially those made
by the Policy Engine (e.g. unlocking doors, disabling security).
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.logger import logger


@dataclass
class AuditEntry:
    actor: str
    action: str
    resource: str
    decision: str
    source_ip: Optional[str] = None
    context_data: Optional[Dict[str, Any]] = None


class AuditSystem:
    """Central audit logger.
    
    Usage::
    
        audit = AuditSystem()
        audit.log(AuditEntry(
            actor="plugin:weather",
            action="read",
            resource="device:temp_sensor",
            decision="ALLOW"
        ))
    """

    _instance: Optional[AuditSystem] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queue = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> AuditSystem:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def start(self) -> None:
        """Start the background persistence task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._process_queue())
        logger.info("AuditSystem: started background persistence.")

    async def stop(self) -> None:
        """Stop the background persistence task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AuditSystem: stopped.")

    def log(self, entry: AuditEntry) -> None:
        """Log an audit event asynchronously."""
        # We use put_nowait so synchronous callers don't block
        try:
            self._queue.put_nowait(entry)
        except Exception as e:
            logger.error(f"AuditSystem: failed to enqueue log: {e}")

    async def _process_queue(self) -> None:
        """Background loop to write audit logs to the database."""
        while self._running:
            try:
                entry: AuditEntry = await self._queue.get()
                self._persist(entry)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"AuditSystem: persistence error: {e}")

    def _persist(self, entry: AuditEntry) -> None:
        """Write the audit entry to SQLite."""
        try:
            from app.memory.database import get_db_session
            from app.memory.models import AuditRecord

            with get_db_session() as session:
                record = AuditRecord(
                    actor=entry.actor,
                    action=entry.action,
                    resource=entry.resource,
                    decision=entry.decision,
                    source_ip=entry.source_ip,
                    context_data=json.dumps(entry.context_data) if entry.context_data else None
                )
                session.add(record)
        except Exception as e:
            logger.error(f"AuditSystem: failed to write to DB: {e}")
