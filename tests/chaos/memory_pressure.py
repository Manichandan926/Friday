import pytest
import asyncio
from app.core.events.event_bus import EventBus
from app.core.events.event import Event, Priority
from app.core.metrics import MetricsRegistry

@pytest.mark.asyncio
async def test_ring_buffer_bounded_under_flood(cleanup_singletons):
    bus = EventBus.get_instance()
    await bus.start()
    
    # Flood EventBus with 105,000 events
    for i in range(105000):
        bus.publish_sync(Event(
            event_type="ring_test.event",
            source="flood",
            payload={"index": i},
            priority=Priority.LOW
        ))
        
    recent = bus.get_recent_events(limit=20000)
    # The recent events ring buffer/cache should be capped at 10,000
    assert len(recent) <= 10000
    
    await bus.stop()

def test_metrics_history_bounded(cleanup_singletons):
    registry = MetricsRegistry.get_instance()
    counter = registry.get_counter("pressure_test", "Pressure counter")
    
    # Increment counter 5,000 times
    for _ in range(5000):
        counter.inc()
        
    # The value/history of increments in counter should be bounded (capped at 1000)
    # Let's check the length of history in export_json()
    metrics = registry.export_json()
    pressure_history = metrics.get("pressure_test", {}).get("history", [])
    
    # Assert it never exceeds 1000 items
    assert len(pressure_history) <= 1000
