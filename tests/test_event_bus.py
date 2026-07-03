import pytest
import asyncio
from app.core.events.event import Event, Priority
from app.core.events.event_bus import EventBus, MAX_QUEUE_SIZE
from app.core.events.middleware import Middleware, MetricsMiddleware

async def wait_for_condition(condition_fn, timeout=1.0, interval=0.005):
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        if condition_fn():
            return True
        await asyncio.sleep(interval)
    return False

@pytest.mark.asyncio
async def test_publish_and_subscribe():
    bus = EventBus.get_instance()
    await bus.start()
    
    received = []
    def handler(event):
        received.append(event)
        
    bus.subscribe("test.event", handler)
    
    event = Event(event_type="test.event", source="test_src", payload={"foo": "bar"})
    await bus.publish(event)
    
    fired = await wait_for_condition(lambda: len(received) > 0)
    assert fired is True
    assert received[0].payload["foo"] == "bar"
    
    await bus.stop()

@pytest.mark.asyncio
async def test_priority_ordering():
    bus = EventBus.get_instance()
    # We do NOT start the bus dispatcher loop, so we can load events in queues,
    # then pop them manually using _pop_event() to verify priority ordering.
    
    event_low = Event(event_type="low", source="test", priority=Priority.LOW)
    event_med = Event(event_type="med", source="test", priority=Priority.MEDIUM)
    event_high = Event(event_type="high", source="test", priority=Priority.HIGH)
    event_crit = Event(event_type="crit", source="test", priority=Priority.CRITICAL)
    
    await bus.publish(event_low)
    await bus.publish(event_med)
    await bus.publish(event_high)
    await bus.publish(event_crit)
    
    # Priority.CRITICAL should pop first
    e1 = bus._pop_event()
    assert e1.priority == Priority.CRITICAL
    
    # Priority.HIGH should pop second
    e2 = bus._pop_event()
    assert e2.priority == Priority.HIGH
    
    # Priority.MEDIUM should pop third
    e3 = bus._pop_event()
    assert e3.priority == Priority.MEDIUM
    
    # Priority.LOW should pop fourth
    e4 = bus._pop_event()
    assert e4.priority == Priority.LOW

@pytest.mark.asyncio
async def test_backpressure_low_queue_drops_oldest():
    bus = EventBus.get_instance()
    
    # Modify MAX_QUEUE_SIZE for testing
    orig_low = MAX_QUEUE_SIZE[Priority.LOW.value]
    MAX_QUEUE_SIZE[Priority.LOW.value] = 3
    
    try:
        # Publish 4 events (exceeds limit 3)
        e1 = Event(event_type="low", source="test", priority=Priority.LOW, payload={"id": 1})
        e2 = Event(event_type="low", source="test", priority=Priority.LOW, payload={"id": 2})
        e3 = Event(event_type="low", source="test", priority=Priority.LOW, payload={"id": 3})
        e4 = Event(event_type="low", source="test", priority=Priority.LOW, payload={"id": 4})
        
        await bus.publish(e1)
        await bus.publish(e2)
        await bus.publish(e3)
        await bus.publish(e4)
        
        # Verify oldest (e1) is dropped and queue has e2, e3, e4
        q = bus._queues[Priority.LOW.value]
        assert len(q) == 3
        assert q[0].payload["id"] == 2
        assert q[2].payload["id"] == 4
    finally:
        MAX_QUEUE_SIZE[Priority.LOW.value] = orig_low

@pytest.mark.asyncio
async def test_backpressure_medium_queue_drops_oldest():
    bus = EventBus.get_instance()
    
    orig_med = MAX_QUEUE_SIZE[Priority.MEDIUM.value]
    MAX_QUEUE_SIZE[Priority.MEDIUM.value] = 3
    
    try:
        e1 = Event(event_type="med", source="test", priority=Priority.MEDIUM, payload={"id": 1})
        e2 = Event(event_type="med", source="test", priority=Priority.MEDIUM, payload={"id": 2})
        e3 = Event(event_type="med", source="test", priority=Priority.MEDIUM, payload={"id": 3})
        e4 = Event(event_type="med", source="test", priority=Priority.MEDIUM, payload={"id": 4})
        
        await bus.publish(e1)
        await bus.publish(e2)
        await bus.publish(e3)
        await bus.publish(e4)
        
        q = bus._queues[Priority.MEDIUM.value]
        assert len(q) == 3
        assert q[0].payload["id"] == 2
    finally:
        MAX_QUEUE_SIZE[Priority.MEDIUM.value] = orig_med

@pytest.mark.asyncio
async def test_backpressure_high_queue_rejects():
    bus = EventBus.get_instance()
    
    orig_high = MAX_QUEUE_SIZE[Priority.HIGH.value]
    MAX_QUEUE_SIZE[Priority.HIGH.value] = 2
    
    try:
        e1 = Event(event_type="high", source="test", priority=Priority.HIGH)
        e2 = Event(event_type="high", source="test", priority=Priority.HIGH)
        e3 = Event(event_type="high", source="test", priority=Priority.HIGH)
        
        await bus.publish(e1)
        await bus.publish(e2)
        
        # 3rd publish should raise RuntimeError
        with pytest.raises(RuntimeError, match="HIGH priority queue is full"):
            await bus.publish(e3)
    finally:
        MAX_QUEUE_SIZE[Priority.HIGH.value] = orig_high

@pytest.mark.asyncio
async def test_ring_buffer_cache():
    bus = EventBus.get_instance()
    
    for i in range(10):
        await bus.publish(Event(event_type="test", source="test", payload={"i": i}))
        
    recent = bus.get_recent_events(limit=5)
    assert len(recent) == 5
    assert recent[0].payload["i"] == 5
    assert recent[4].payload["i"] == 9

@pytest.mark.asyncio
async def test_ring_buffer_capacity():
    bus = EventBus.get_instance()
    # Mock history maxlen to 5 for test
    orig_history = bus._history
    from collections import deque
    bus._history = deque(maxlen=5)
    
    try:
        for i in range(10):
            await bus.publish(Event(event_type="test", source="test", payload={"i": i}))
            
        assert len(bus._history) == 5
        assert list(bus._history)[0].payload["i"] == 5
    finally:
        bus._history = orig_history

@pytest.mark.asyncio
async def test_wildcard_subscription():
    bus = EventBus.get_instance()
    await bus.start()
    
    received = []
    def handler(event):
        received.append(event)
        
    bus.subscribe("*", handler)
    
    await bus.publish(Event(event_type="device.telemetry", source="sensor"))
    await bus.publish(Event(event_type="device.command", source="app"))
    
    fired = await wait_for_condition(lambda: len(received) == 2)
    assert fired is True
    assert {e.event_type for e in received} == {"device.telemetry", "device.command"}
    
    await bus.stop()

@pytest.mark.asyncio
async def test_middleware_chain():
    bus = EventBus.get_instance()
    
    processed_events = []
    class DummyMiddleware(Middleware):
        async def process(self, event):
            processed_events.append(event)
            return event
            
    mw = DummyMiddleware()
    bus.add_middleware(mw)
    bus.add_middleware(MetricsMiddleware())
    
    await bus.start()
    await bus.publish(Event(event_type="test", source="test"))
    
    fired = await wait_for_condition(lambda: len(processed_events) == 1)
    assert fired is True
    
    await bus.stop()

@pytest.mark.asyncio
async def test_event_bus_start_stop():
    bus = EventBus.get_instance()
    assert bus.is_running is False
    
    await bus.start()
    assert bus.is_running is True
    assert bus._dispatch_task is not None
    assert not bus._dispatch_task.done()
    
    await bus.stop()
    assert bus.is_running is False
    assert bus._dispatch_task.done() or bus._dispatch_task.cancelled()
