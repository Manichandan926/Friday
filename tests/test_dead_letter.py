import pytest
import asyncio
from app.core.actors.system import ActorSystem
from app.core.actors.dead_letter import DeadLetterActor
from app.core.actors.actor import ActorMessage

async def wait_for_condition(condition_fn, timeout=1.0, interval=0.01):
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if condition_fn():
            return True
        await asyncio.sleep(interval)
    return False

@pytest.mark.asyncio
async def test_capture_failed_message_and_metadata():
    sys = ActorSystem.get_instance()
    dlq_actor = DeadLetterActor()
    sys.spawn(dlq_actor)
    await sys.start_all()
    
    msg = ActorMessage(
        sender="faulty_actor",
        message_type="BAD_JSON",
        payload={"raw_string": "{invalid_json}"}
    )
    
    await sys.send("dead_letter", msg)
    
    # Wait for the DLQ actor to process the message
    def check_processed():
        return len(dlq_actor.get_failures()) > 0
        
    found = await wait_for_condition(check_processed)
    assert found is True
    
    failures = dlq_actor.get_failures()
    assert len(failures) == 1
    
    failure = failures[0]
    assert failure["sender"] == "faulty_actor"
    assert failure["error_type"] == "BAD_JSON"
    assert "timestamp" in failure
    assert failure["payload"] == {"raw_string": "{invalid_json}"}
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_get_failures_returns_list():
    sys = ActorSystem.get_instance()
    dlq_actor = DeadLetterActor()
    sys.spawn(dlq_actor)
    await sys.start_all()
    
    # Send 3 failures
    for i in range(3):
        msg = ActorMessage(
            sender="actor_test",
            message_type=f"ERROR_{i}",
            payload={"id": i}
        )
        await sys.send("dead_letter", msg)
        
    def check_failures_count():
        return len(dlq_actor.get_failures()) == 3
        
    found = await wait_for_condition(check_failures_count)
    assert found is True
    
    failures = dlq_actor.get_failures()
    assert len(failures) == 3
    assert failures[0]["error_type"] == "ERROR_0"
    assert failures[1]["error_type"] == "ERROR_1"
    assert failures[2]["error_type"] == "ERROR_2"
    
    # Check limit parameter in get_failures
    limited = dlq_actor.get_failures(limit=2)
    assert len(limited) == 2
    assert limited[0]["error_type"] == "ERROR_1"
    assert limited[1]["error_type"] == "ERROR_2"
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_max_retention():
    sys = ActorSystem.get_instance()
    # Max size = 3
    dlq_actor = DeadLetterActor(max_memory_items=3)
    sys.spawn(dlq_actor)
    await sys.start_all()
    
    # Send 4 failures
    for i in range(4):
        msg = ActorMessage(
            sender="actor_test",
            message_type=f"ERROR_{i}",
            payload={"id": i}
        )
        await sys.send("dead_letter", msg)
        
    def check_failures_count():
        return len(dlq_actor.get_failures()) == 3
        
    found = await wait_for_condition(check_failures_count)
    assert found is True
    
    # The first failure (ERROR_0) should be dropped
    failures = dlq_actor.get_failures()
    assert len(failures) == 3
    error_types = {f["error_type"] for f in failures}
    assert "ERROR_0" not in error_types
    assert "ERROR_1" in error_types
    assert "ERROR_2" in error_types
    assert "ERROR_3" in error_types
    
    await sys.stop_all()
