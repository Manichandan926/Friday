import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from app.core.actors.system import ActorSystem
from app.core.actors.db_writer import DBWriterActor
from app.core.actors.actor import ActorMessage
from app.memory.database import get_db_session
from app.memory.models import AuditRecord, EventStore, StateSnapshot

async def wait_for_condition(condition_fn, timeout=1.5, interval=0.01):
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if condition_fn():
            return True
        await asyncio.sleep(interval)
    return False

@pytest.mark.asyncio
async def test_single_write():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    msg = ActorMessage(
        sender="test",
        message_type="WRITE_AUDIT",
        payload={
            "actor": "user_test",
            "action": "test_action",
            "resource": "test_resource",
            "decision": "ALLOW",
            "source_ip": "127.0.0.1",
            "context_data": {"meta": "data"}
        }
    )
    await sys.send("db_writer", msg)
    
    # Trigger flush manually
    await sys.send("db_writer", ActorMessage("test", "FLUSH", None))
    
    def check_record():
        with get_db_session() as session:
            record = session.query(AuditRecord).filter_by(actor="user_test").first()
            return record is not None
            
    found = await wait_for_condition(check_record)
    assert found is True
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_batch_flush_on_count():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    # Send 100 messages to trigger auto-flush
    for i in range(100):
        msg = ActorMessage(
            sender="test",
            message_type="WRITE_AUDIT",
            payload={
                "actor": f"user_{i}",
                "action": "batch_test",
                "resource": "res",
                "decision": "ALLOW"
            }
        )
        await sys.send("db_writer", msg)
        
    def check_count():
        with get_db_session() as session:
            count = session.query(AuditRecord).filter_by(action="batch_test").count()
            return count == 100
            
    found = await wait_for_condition(check_count)
    assert found is True
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_batch_flush_on_timeout():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    # Send 5 messages (less than 100 threshold)
    for i in range(5):
        msg = ActorMessage(
            sender="test",
            message_type="WRITE_AUDIT",
            payload={
                "actor": f"user_timeout_{i}",
                "action": "timeout_test",
                "resource": "res",
                "decision": "ALLOW"
            }
        )
        await sys.send("db_writer", msg)
        
    # Wait for the flush loop to run (> 0.5s)
    def check_count():
        with get_db_session() as session:
            count = session.query(AuditRecord).filter_by(action="timeout_test").count()
            return count == 5
            
    found = await wait_for_condition(check_count, timeout=2.0)
    assert found is True
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_flush_on_stop():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    # Send 5 messages
    for i in range(5):
        msg = ActorMessage(
            sender="test",
            message_type="WRITE_AUDIT",
            payload={
                "actor": f"user_stop_{i}",
                "action": "stop_test",
                "resource": "res",
                "decision": "ALLOW"
            }
        )
        await sys.send("db_writer", msg)
        
    # Immediately stop all actors. This should flush the buffer synchronously during on_stop.
    await sys.stop_all()
    
    with get_db_session() as session:
        count = session.query(AuditRecord).filter_by(action="stop_test").count()
        assert count == 5

@pytest.mark.asyncio
async def test_event_store_write():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    msg = ActorMessage(
        sender="test",
        message_type="WRITE_EVENT",
        payload={
            "event_id": "evt-db-writer-1",
            "event_type": "TEST_EVENT",
            "source": "db_writer_test",
            "payload": {"key": "val"},
            "priority": "HIGH"
        }
    )
    await sys.send("db_writer", msg)
    await sys.send("db_writer", ActorMessage("test", "FLUSH", None))
    
    def check_event():
        with get_db_session() as session:
            record = session.query(EventStore).filter_by(event_id="evt-db-writer-1").first()
            return record is not None
            
    found = await wait_for_condition(check_event)
    assert found is True
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_snapshot_write():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    msg = ActorMessage(
        sender="test",
        message_type="WRITE_SNAPSHOT",
        payload={
            "version": 42,
            "state_data": '{"test": "snapshot"}'
        }
    )
    await sys.send("db_writer", msg)
    await sys.send("db_writer", ActorMessage("test", "FLUSH", None))
    
    def check_snapshot():
        with get_db_session() as session:
            record = session.query(StateSnapshot).filter_by(version=42).first()
            return record is not None
            
    found = await wait_for_condition(check_snapshot)
    assert found is True
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_delete_old_events():
    sys = ActorSystem.get_instance()
    db_writer = DBWriterActor()
    sys.spawn(db_writer)
    await sys.start_all()
    
    cutoff_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    
    # 1. Insert old and new events directly to DB
    with get_db_session() as session:
        e_old = EventStore(
            event_id="evt-old-prune",
            event_type="TELEMETRY",
            source="test",
            payload="{}",
            priority="LOW",
            created_at=cutoff_time
        )
        e_crit = EventStore(
            event_id="evt-crit-keep",
            event_type="ALARM",
            source="test",
            payload="{}",
            priority="CRITICAL",
            created_at=cutoff_time
        )
        session.add(e_old)
        session.add(e_crit)
        
    # 2. Trigger prune
    await sys.send("db_writer", ActorMessage(
        sender="test",
        message_type="DELETE_OLD_EVENTS",
        payload={"retention_days": 7}
    ))
    
    def check_pruned():
        with get_db_session() as session:
            remaining = {e.event_id for e in session.query(EventStore).all()}
            return "evt-old-prune" not in remaining and "evt-crit-keep" in remaining
            
    found = await wait_for_condition(check_pruned)
    assert found is True
    
    await sys.stop_all()
