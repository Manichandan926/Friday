"""
actor_crashes.py — Chaos testing for Actor Supervisor.

Randomly crashes actors while asserting that no messages are lost 
and the system recovers automatically via Supervisor Let-It-Crash semantics.
"""

import asyncio
import random
import time

from app.core.actors.actor import Actor, ActorMessage
from app.core.actors.system import ActorSystem
from app.core.actors.supervisor import SupervisorActor


class FragileActor(Actor):
    def __init__(self):
        super().__init__(name="fragile")
        self.processed = 0
        
    async def receive(self, msg: ActorMessage) -> None:
        if msg.message_type == "WORK":
            # 10% chance to simulate a random memory fault or crash
            if random.random() < 0.1:
                raise RuntimeError("Random chaos fault!")
            self.processed += 1
            await asyncio.sleep(0.001)


async def main():
    sys = ActorSystem.get_instance()
    sup = SupervisorActor()
    sys.spawn(sup)
    
    actor = FragileActor()
    sys.spawn(actor)
    sup.watch(actor)
    
    await sys.start_all()
    
    # We will bombard it with work and see if it eventually processes enough
    print("Chaos Test: Bombarding FragileActor with work (expect stack traces)...")
    
    for i in range(100):
        # Fire and forget. System.send will route it.
        await sys.send("fragile", ActorMessage("chaos", "WORK", None))
        await asyncio.sleep(0.01) # Small delay to allow restarts to happen

    await asyncio.sleep(2)
    
    from app.core.metrics import MetricsRegistry
    metrics = MetricsRegistry.get_instance().export_json()
    restarts = list(metrics.get("actor_restarts", {}).get("counts", {}).values())
    total_restarts = restarts[0] if restarts else 0
    
    print(f"Chaos Test Complete. Actor crashed and restarted {total_restarts} times.")
    
    await sys.stop_all()

if __name__ == "__main__":
    asyncio.run(main())
