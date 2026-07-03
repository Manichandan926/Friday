import pytest
import time
import asyncio
from unittest.mock import patch
from app.core.actors.actor import Actor, ActorMessage
from app.core.actors.system import ActorSystem
from app.core.events.event_bus import EventBus
from app.core.events.event import Event
from app.core.shell import is_command_safe

class BenchActor(Actor):
    def __init__(self):
        super().__init__(name="bench")
        self.latency = 0.0
        self.done_event = asyncio.Event()

    async def receive(self, msg: ActorMessage) -> None:
        if msg.message_type == "PING":
            send_time = msg.payload
            self.latency = time.perf_counter() - send_time
            self.done_event.set()

@pytest.mark.asyncio
async def test_actor_message_latency(cleanup_singletons):
    sys = ActorSystem.get_instance()
    actor = BenchActor()
    sys.spawn(actor)
    await sys.start_all()

    start = time.perf_counter()
    await sys.send("bench", ActorMessage("bench_test", "PING", start))
    
    await asyncio.wait_for(actor.done_event.wait(), timeout=1.0)
    assert actor.latency > 0.0
    print(f"\nActor message latency: {actor.latency * 1000.0:.4f} ms")
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_event_bus_throughput(cleanup_singletons):
    bus = EventBus.get_instance()
    await bus.start()

    processed_count = 0
    done_event = asyncio.Event()
    target_count = 1000

    async def mock_handler(event: Event):
        nonlocal processed_count
        processed_count += 1
        if processed_count == target_count:
            done_event.set()

    bus.subscribe("bench_event", mock_handler)

    start_time = time.perf_counter()
    
    # Publish 1000 events
    for i in range(target_count):
        bus.publish_sync(Event(event_type="bench_event", source="bench", payload={"index": i}))

    await asyncio.wait_for(done_event.wait(), timeout=5.0)
    end_time = time.perf_counter()
    
    elapsed = end_time - start_time
    throughput = target_count / elapsed
    print(f"\nEventBus throughput: {throughput:.2f} events/sec (elapsed: {elapsed:.4f}s)")
    assert throughput > 100.0  # Basic baseline sanity check

    await bus.stop()

def test_native_vs_python_validation_benchmark():
    cmd = "ps aux | grep python | head -5"
    iterations = 2000

    # 1. Pure Python validation (mocking native C validator to return None)
    with patch("app.core.fast_ops.is_command_safe_native", return_value=None):
        # Warmup
        is_command_safe(cmd)
        
        start_py = time.perf_counter()
        for _ in range(iterations):
            is_command_safe(cmd)
        end_py = time.perf_counter()
        py_time = end_py - start_py

    # 2. Native C validation (no mock)
    # Warmup
    is_command_safe(cmd)
    
    start_c = time.perf_counter()
    for _ in range(iterations):
        is_command_safe(cmd)
    end_c = time.perf_counter()
    c_time = end_c - start_c

    speedup = py_time / c_time if c_time > 0 else float('inf')
    print(f"\nBenchmark Command Validation (iterations={iterations}):")
    print(f"  Pure Python: {py_time * 1000.0:.2f} ms")
    print(f"  Native C:    {c_time * 1000.0:.2f} ms")
    print(f"  Speedup:     {speedup:.2f}x")

    # Assert C is significantly faster (at least 5x per spec)
    assert speedup >= 5.0, f"Native C validator is only {speedup:.2f}x faster, expected >= 5x"
