import pytest
import asyncio
import json
from datetime import datetime, timezone, timedelta
from app.core.actors.system import ActorSystem
from app.core.actors.db_writer import DBWriterActor
from app.core.state_store import StateStore
from app.core.snapshot_manager import SnapshotManager
from app.memory.database import get_db_session
from app.memory.models import StateSnapshot, EventStore
from app.core.events.event import Event, Priority

async def wait_for_condition(condition_fn, timeout=1.0, interval=0.01):
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if condition_fn():
            return True
        await asyncio.sleep(interval)
    return False

def utc_now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)

@pytest.mark.asyncio
async def test_snapshot_creates_record():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    store = StateStore()
    store.update("system", "status", value="active_for_test")
    
    manager = SnapshotManager(store)
    success = await manager.take_snapshot()
    assert success is True
    
    # Send FLUSH message to force db_writer to commit the batch
    from app.core.actors.actor import ActorMessage
    await sys.send("db_writer", ActorMessage("test", "FLUSH", None))
    
    # Wait for the record to show up in the DB
    def record_exists():
        with get_db_session() as session:
            latest = session.query(StateSnapshot).first()
            return latest is not None and "active_for_test" in latest.state_data
            
    found = await wait_for_condition(record_exists)
    assert found is True
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_snapshot_replay():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    store = StateStore()
    
    # 1. Register a reducer
    def system_reducer(state_slice, action):
        if action["type"] == "STATUS_CHANGED":
            return {**state_slice, "status": action["payload"]["status"]}
        return state_slice
    store.register_reducer("system", system_reducer)
    
    # 2. Mutate state and take snapshot
    store.dispatch({"type": "STATUS_CHANGED", "payload": {"status": "state_1"}})
    manager = SnapshotManager(store)
    await manager.take_snapshot()
    
    # 3. Dispatch more events (which would be persisted in event store in production)
    # We will manually persist them to the database to simulate event sourcing storage
    with get_db_session() as session:
        e1 = EventStore(
            event_id="evt-1",
            event_type="STATUS_CHANGED",
            source="test",
            payload=json.dumps({"status": "state_2"}),
            priority="MEDIUM",
            created_at=utc_now_naive() + timedelta(seconds=1)
        )
        e2 = EventStore(
            event_id="evt-2",
            event_type="STATUS_CHANGED",
            source="test",
            payload=json.dumps({"status": "state_3"}),
            priority="MEDIUM",
            created_at=utc_now_naive() + timedelta(seconds=2)
        )
        session.add(e1)
        session.add(e2)
        
    # Flush db_writer
    from app.core.actors.actor import ActorMessage
    await sys.send("db_writer", ActorMessage("test", "FLUSH", None))
    await asyncio.sleep(0.05)
    
    # 4. Reconstruct state by restoring snapshot first
    new_store = StateStore()
    new_store.register_reducer("system", system_reducer)
    
    restored = SnapshotManager.restore(new_store)
    assert restored is True
    assert new_store.state["system"]["status"] == "state_1"
    
    # 5. Replay subsequent events
    # Query events from database created after snapshot version (in this case all events)
    with get_db_session() as session:
        events = session.query(EventStore).order_by(EventStore.created_at.asc()).all()
        for record in events:
            action = {
                "type": record.event_type,
                "payload": json.loads(record.payload),
                "source": record.source
            }
            new_store.dispatch(action)
            
    # Verify final state matches
    assert new_store.state["system"]["status"] == "state_3"
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_ttl_pruning():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    # Insert old event and critical event
    with get_db_session() as session:
        # Non-critical event older than 7 days
        old_time = utc_now_naive() - timedelta(days=10)
        e_old = EventStore(
            event_id="evt-old",
            event_type="TELEMETRY",
            source="test",
            payload="{}",
            priority="LOW",
            created_at=old_time
        )
        # Critical event older than 7 days (should not be pruned)
        e_crit = EventStore(
            event_id="evt-crit",
            event_type="ALARM",
            source="test",
            payload="{}",
            priority="CRITICAL",
            created_at=old_time
        )
        # Fresh event (should not be pruned)
        e_fresh = EventStore(
            event_id="evt-fresh",
            event_type="TELEMETRY",
            source="test",
            payload="{}",
            priority="LOW",
            created_at=utc_now_naive()
        )
        session.add(e_old)
        session.add(e_crit)
        session.add(e_fresh)
        
    # Flush db_writer
    from app.core.actors.actor import ActorMessage
    await sys.send("db_writer", ActorMessage("test", "FLUSH", None))
    await asyncio.sleep(0.05)
    
    # Trigger TTL pruning via DBWriterActor message
    # DELETE_OLD_EVENTS executes flush_buffer synchronously in DBWriterActor receive()!
    await sys.send("db_writer", ActorMessage(
        sender="test",
        message_type="DELETE_OLD_EVENTS",
        payload={"retention_days": 7}
    ))
    
    # Wait for deletion to take effect
    await asyncio.sleep(0.05)
    
    # Verify records in db
    with get_db_session() as session:
        remaining_ids = {e.event_id for e in session.query(EventStore).all()}
        assert "evt-old" not in remaining_ids
        assert "evt-crit" in remaining_ids
        assert "evt-fresh" in remaining_ids
        
    await sys.stop_all()
