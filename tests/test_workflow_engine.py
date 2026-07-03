import pytest
import asyncio
from datetime import datetime, time
from app.core.workflow_engine import (
    WorkflowEngine, Workflow, EventTrigger, Condition, Action,
    TimeCondition, StateCondition, EventCondition
)
from app.core.events.event import Event
from app.core.events.event_bus import EventBus
from app.core.state_store import StateStore

class DummyAction(Action):
    def __init__(self):
        self.executed = False
        self.event_received = None

    async def execute(self, event: Event) -> None:
        self.executed = True
        self.event_received = event

def test_workflow_registration():
    engine = WorkflowEngine()
    
    wf = Workflow(
        name="test_wf",
        trigger=EventTrigger("test.event"),
        conditions=[],
        actions=[]
    )
    
    engine.register(wf)
    assert len(engine._workflows) == 1
    assert "test.event" in engine._trigger_map
    assert engine._trigger_map["test.event"][0].name == "test_wf"
    
    engine.unregister("test_wf")
    assert len(engine._workflows) == 0
    assert "test.event" not in engine._trigger_map or len(engine._trigger_map["test.event"]) == 0

def test_time_condition():
    # Save original datetime
    import app.core.workflow_engine
    orig_datetime = app.core.workflow_engine.datetime
    
    class MockDatetime:
        current_dt = None
        @classmethod
        def now(cls):
            return cls.current_dt
            
    app.core.workflow_engine.datetime = MockDatetime
    
    try:
        # Case 1: Normal window (no midnight crossing) - 10:00 to 18:00
        cond = TimeCondition("10:00:00", "18:00:00")
        
        # Test time inside window: 14:30
        MockDatetime.current_dt = datetime(2026, 6, 27, 14, 30, 0)
        assert cond.evaluate(None, {}) is True
        
        # Test time outside window: 08:00
        MockDatetime.current_dt = datetime(2026, 6, 27, 8, 0, 0)
        assert cond.evaluate(None, {}) is False
        
        # Case 2: Crossing midnight window - 22:00 to 06:00
        cond_cross = TimeCondition("22:00:00", "06:00:00")
        
        # Test time inside window (before midnight): 23:30
        MockDatetime.current_dt = datetime(2026, 6, 27, 23, 30, 0)
        assert cond_cross.evaluate(None, {}) is True
        
        # Test time inside window (after midnight): 02:00
        MockDatetime.current_dt = datetime(2026, 6, 27, 2, 0, 0)
        assert cond_cross.evaluate(None, {}) is True
        
        # Test time outside window: 12:00
        MockDatetime.current_dt = datetime(2026, 6, 27, 12, 0, 0)
        assert cond_cross.evaluate(None, {}) is False
    finally:
        app.core.workflow_engine.datetime = orig_datetime

def test_state_condition():
    # We will test nested path evaluation in StateCondition
    cond = StateCondition("system.security_mode", "armed")
    
    # Matching state
    state_ok = {"system": {"security_mode": "armed"}}
    assert cond.evaluate(None, state_ok) is True
    
    # Mismatching value
    state_nok = {"system": {"security_mode": "disarmed"}}
    assert cond.evaluate(None, state_nok) is False
    
    # Missing key path
    state_missing = {"system": {}}
    assert cond.evaluate(None, state_missing) is False

def test_event_condition():
    cond = EventCondition("status", "error")
    
    event_ok = Event("test", "test", payload={"status": "error"})
    assert cond.evaluate(event_ok, {}) is True
    
    event_nok = Event("test", "test", payload={"status": "ok"})
    assert cond.evaluate(event_nok, {}) is False
    
    event_missing = Event("test", "test", payload={})
    assert cond.evaluate(event_missing, {}) is False

@pytest.mark.asyncio
async def test_workflow_execution():
    bus = EventBus.get_instance()
    await bus.start()
    
    store = StateStore()
    store.update("system", "security_mode", value="armed")
    
    engine = WorkflowEngine(bus, store)
    
    action = DummyAction()
    wf = Workflow(
        name="test_execution",
        trigger=EventTrigger("intrusion.detected"),
        conditions=[
            StateCondition("system.security_mode", "armed"),
            EventCondition("zone", "back_door")
        ],
        actions=[action]
    )
    engine.register(wf)
    
    # Publish event that triggers the workflow
    event = Event("intrusion.detected", "sensor", payload={"zone": "back_door"})
    await bus.publish(event)
    
    # Wait for the action to be executed
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < 1.0:
        if action.executed:
            break
        await asyncio.sleep(0.01)
        
    assert action.executed is True
    assert action.event_received.payload["zone"] == "back_door"
    
    await bus.stop()
