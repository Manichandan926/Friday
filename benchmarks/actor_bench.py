"""
actor_bench.py — Benchmarks for Actor message passing throughput.
"""

import asyncio
import time

from app.core.actors.actor import Actor, ActorMessage
from app.core.actors.system import ActorSystem


class SinkActor(Actor):
    def __init__(self):
        super().__init__(name="sink")
        self.count = 0
        self.start_time = None
        self.ready_event = asyncio.Event()
        
    async def receive(self, message: ActorMessage) -> None:
        if self.count == 0:
            self.start_time = time.monotonic()
            
        self.count += 1
        if self.count >= 100_000:
            self.ready_event.set()


async def main():
    sys = ActorSystem.get_instance()
    sink = SinkActor()
    sys.spawn(sink)
    
    await sys.start_all()
    
    total_messages = 100_000
    print(f"Sending {total_messages} messages to Actor mailbox...")
    
    send_start = time.monotonic()
    
    # We use put_nowait directly for benching raw queue speed
    for i in range(total_messages):
        msg = ActorMessage(sender="bench", message_type="TEST", payload=None)
        sink.mailbox.put_nowait(msg)
        
    send_time = time.monotonic() - send_start
    print(f"Enqueue Rate: {total_messages / send_time:.2f} msg/sec")
    
    await sink.ready_event.wait()
    
    process_time = time.monotonic() - sink.start_time
    print(f"Receive Rate: {total_messages / process_time:.2f} msg/sec")
    
    await sys.stop_all()

if __name__ == "__main__":
    asyncio.run(main())
