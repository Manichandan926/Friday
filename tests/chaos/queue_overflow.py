import pytest
import asyncio
from app.core.events.event_bus import EventBus
from app.core.events.event import Event, Priority

@pytest.mark.asyncio
async def test_event_bus_survives_100k_flood(cleanup_singletons):
    bus = EventBus.get_instance()
    await bus.start()
    
    # Register a dummy listener
    events_received = 0
    async def listener(event):
        nonlocal events_received
        events_received += 1
        
    bus.subscribe("test_chaos.*", listener)
    
    # Publish 100,000 events of low priority
    # (they will trigger backpressure and drop oldest, keeping memory bounded)
    for i in range(100000):
        # We can publish in batches or just call publish_sync
        bus.publish_sync(Event(
            event_type="test_chaos.item",
            source="chaos_test",
            payload={"index": i},
            priority=Priority.LOW
        ))
        
    # Give dispatcher a tiny bit to process what is left in the queue
    await asyncio.sleep(0.5)
    
    # Assert dispatcher is still alive and event bus did not deadlock or OOM
    assert bus._running is True
    await bus.stop()

@pytest.mark.asyncio
async def test_backpressure_drops_not_crashes(cleanup_singletons):
    bus = EventBus.get_instance()
    await bus.start()
    
    # Publish 15,000 events (more than 10,000 capacity)
    for i in range(15000):
        # This should not raise any exceptions
        bus.publish_sync(Event(
            event_type="test_chaos.drop",
            source="chaos_test",
            payload={"index": i},
            priority=Priority.LOW
        ))
        
    # Verify that the internal queue does not grow unbounded (should be <= 10000)
    assert len(bus._queues[Priority.LOW.value]) <= 10000
    
    await bus.stop()
