"""
event_bus_bench.py — Benchmarks for EventBus throughput.

Measures how many events/sec the EventBus can handle, factoring in backpressure
and RingBuffer cache.
"""

import asyncio
import time

from app.core.events.event_bus import EventBus
from app.core.events.event import Event


async def main():
    bus = EventBus()
    await bus.start()

    total_events = 100_000
    start_time = time.monotonic()

    print(f"Publishing {total_events} events...")

    for i in range(total_events):
        await bus.publish(Event(event_type="benchmark.test", source="bench", payload={"id": i}))

    publish_time = time.monotonic() - start_time
    print(f"Publish Rate: {total_events / publish_time:.2f} events/sec")
    
    # Wait for dispatcher to clear queues
    while True:
        with bus._queue_lock:
            q_sum = sum(len(q) for q in bus._queues.values())
        if q_sum == 0:
            break
        await asyncio.sleep(0.01)

    dispatch_time = time.monotonic() - start_time
    print(f"Total Dispatch Rate: {total_events / dispatch_time:.2f} events/sec")
    
    await bus.stop()

if __name__ == "__main__":
    asyncio.run(main())
