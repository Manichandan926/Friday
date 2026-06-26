"""
snapshot_manager.py — Snapshot Manager for FRIDAY StateStore.

Periodically serializes the in-memory StateStore to the SQLite database.
Ensures that runtime state (UI state, counters, device statuses) is not
lost during crashes or restarts.

Architecture:
    StateStore → (every N seconds) → SnapshotManager → SQLite (state_snapshots)
    Startup → SnapshotManager reads latest snapshot → StateStore initialized
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional, Any

from app.core.logger import logger
from app.core.state_store import StateStore
from app.core.actors.system import ActorSystem
from app.core.actors.actor import ActorMessage


class SnapshotManager:
    """Manages periodic persistence of the StateStore to the database.

    Usage::

        store = StateStore()
        # Restore latest on startup
        SnapshotManager.restore(store)
        
        # Start background periodic snapshots
        manager = SnapshotManager(store, interval_seconds=60)
        manager.start()
    """

    def __init__(self, store: StateStore, interval_seconds: float = 60.0, ttl_days: int = 7):
        self._store = store
        self._interval = interval_seconds
        self._ttl_days = ttl_days
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background snapshot loop (native asyncio)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._snapshot_loop())
        logger.info(f"SnapshotManager: Started periodic snapshots ({self._interval}s)")

    async def stop(self) -> None:
        """Stop the background snapshot loop and take one final snapshot."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final snapshot before exit
        await self.take_snapshot()
        logger.info("SnapshotManager: Stopped")

    async def _snapshot_loop(self) -> None:
        """Async loop for periodic snapshots and Event TTL cleanup."""
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                await self.take_snapshot()
                
                # Also do TTL cleanup
                sys = ActorSystem.get_instance()
                await sys.send("db_writer", ActorMessage(
                    sender="snapshot_manager",
                    message_type="DELETE_OLD_EVENTS",
                    payload={"retention_days": self._ttl_days}
                ))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SnapshotManager loop error: {e}")

    async def take_snapshot(self) -> bool:
        """Send a snapshot request to the DBWriterActor."""
        snapshot = self._store.snapshot()
        version = snapshot.get("version", 0)
        state_data = snapshot.get("state", {})

        sys = ActorSystem.get_instance()
        success = await sys.send("db_writer", ActorMessage(
            sender="snapshot_manager",
            message_type="WRITE_SNAPSHOT",
            payload={
                "version": version,
                "state_data": json.dumps(state_data)
            }
        ))
        
        if success:
            logger.debug(f"SnapshotManager: Dispatched state snapshot (v{version}) to db_writer")
        else:
            logger.error("SnapshotManager: Failed to dispatch snapshot to db_writer")
            
        return success

    @staticmethod
    def restore(store: StateStore) -> bool:
        """Restore the latest state from the database into a StateStore instance."""
        try:
            from app.memory.database import get_db_session
            from app.memory.models import StateSnapshot

            with get_db_session() as session:
                latest = session.query(StateSnapshot).order_by(
                    StateSnapshot.id.desc()
                ).first()

                if not latest:
                    logger.info("SnapshotManager: No previous snapshots found.")
                    return False

                state_data = json.loads(latest.state_data)
                
                # We need to dispatch a restore action to properly update the store
                # Since reducers handle domain state, we use an internal restore action
                # If a domain has a reducer, it should handle INIT_RESTORE
                # But for a direct overwrite we can just directly modify `_state` and `_version`
                # (Note: direct mutation of `_state` is typically avoided, but required here)
                with store._lock:
                    store._state = state_data
                    store._version = latest.version
                    
                logger.info(f"SnapshotManager: Restored state snapshot (v{latest.version})")
                return True
                
        except Exception as e:
            logger.error(f"SnapshotManager: Failed to restore snapshot: {e}")
            return False
