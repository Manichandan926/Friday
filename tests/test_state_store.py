import pytest
import asyncio
import copy
from app.core.state_store import StateStore
from app.core.events.event import Event
from app.core.events.event_bus import EventBus

def test_initial_state():
    store = StateStore()
    state = store.state
    assert "system" in state
    assert "counters" in state
    assert "devices" in state
    assert state["system"]["status"] == "running"

def test_reducer_updates_state():
    store = StateStore()
    
    def dummy_reducer(state_slice, action):
        if action["type"] == "DUMMY_ACTION":
            return {**state_slice, "value": action["payload"]["val"]}
        return state_slice
        
    store.register_reducer("system", dummy_reducer)
    store.dispatch({"type": "DUMMY_ACTION", "payload": {"val": 42}})
    
    assert store.state["system"]["value"] == 42
    assert store.state["counters"]["events_processed"] == 1

def test_state_immutability():
    store = StateStore()
    state1 = store.state
    
    # Try modifying the returned state directly
    state1["system"]["status"] = "corrupted"
    
    # Verify state store remains uncorrupted
    state2 = store.state
    assert state2["system"]["status"] == "running"

def test_selector():
    store = StateStore()
    cpu_pct = store.select(lambda s: s["system"]["health"]["cpu_pct"])
    assert cpu_pct == 0

def test_listener_fires_on_change():
    store = StateStore()
    
    called_with = []
    def listener(old, new):
        called_with.append((old, new))
        
    store.subscribe(listener)
    
    store.dispatch({"type": "ANY_ACTION", "payload": {}})
    assert len(called_with) == 1
    assert called_with[0][0]["counters"]["events_processed"] == 0
    assert called_with[0][1]["counters"]["events_processed"] == 1

@pytest.mark.asyncio
async def test_event_bus_binding():
    bus = EventBus.get_instance()
    await bus.start()
    
    store = StateStore()
    store.bind_to_bus(bus)
    
    def dummy_reducer(state_slice, action):
        if action["type"] == "BUS_EVENT":
            return {**state_slice, "received": True}
        return state_slice
        
    store.register_reducer("system", dummy_reducer)
    
    # Publish event to bus
    event = Event(event_type="BUS_EVENT", source="test_src", payload={})
    await bus.publish(event)
    
    # Wait for dispatch to propagate
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 1.0:
        if store.state["system"].get("received") is True:
            break
        await asyncio.sleep(0.01)
        
    assert store.state["system"].get("received") is True
    await bus.stop()
