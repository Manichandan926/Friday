import pytest
import asyncio
import random
from app.core.actors.actor import Actor, ActorMessage
from app.core.actors.system import ActorSystem
from app.core.actors.supervisor import SupervisorActor
from app.core.metrics import MetricsRegistry

class FragileActor(Actor):
    def __init__(self):
        super().__init__(name="fragile")
        self.processed = 0
        self.crashed = False
        
    async def receive(self, msg: ActorMessage) -> None:
        if msg.message_type == "CRASH":
            self.crashed = True
            raise ValueError("Simulated crash")
        elif msg.message_type == "WORK":
            self.processed += 1

class StressActor(Actor):
    def __init__(self):
        super().__init__(name="stress")
        self.processed = 0
        
    async def receive(self, msg: ActorMessage) -> None:
        if msg.message_type == "WORK":
            self.processed += 1

@pytest.mark.asyncio
async def test_actor_recovery_on_crash(cleanup_singletons):
    # Setup System & Supervisor
    sys = ActorSystem.get_instance()
    sup = SupervisorActor()
    sys.spawn(sup)
    
    actor = FragileActor()
    sys.spawn(actor)
    sup.watch(actor)
    
    await sys.start_all()
    
    # 1. Send normal work
    await sys.send("fragile", ActorMessage("test", "WORK", None))
    # Give it a millisecond to process
    await asyncio.sleep(0.05)
    assert actor.processed == 1
    
    # 2. Trigger a crash
    await sys.send("fragile", ActorMessage("test", "CRASH", None))
    # Wait for crash detection and 1 second delayed restart
    await asyncio.sleep(1.2)
    
    # 3. Check restarted actor is active and can process work again
    restarted_actor = sys.get_actor("fragile")
    assert restarted_actor is not None
    assert restarted_actor is actor # It is the same object, but its task is restarted
    
    await sys.send("fragile", ActorMessage("test", "WORK", None))
    await asyncio.sleep(0.05)
    
    # Processed count should be 2 now (1 before crash, 1 after restart)
    assert restarted_actor.processed == 2
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_stress_message_bombardment(cleanup_singletons):
    sys = ActorSystem.get_instance()
    actor = StressActor()
    sys.spawn(actor)
    
    await sys.start_all()
    
    # Concurrently send 500 messages
    tasks = []
    for i in range(500):
        tasks.append(sys.send("stress", ActorMessage("test", "WORK", i)))
        
    # Gather sender futures
    results = await asyncio.gather(*tasks)
    assert all(results)
    
    # Wait for queue to process
    for _ in range(50):
        if actor.processed == 500:
            break
        await asyncio.sleep(0.05)
        
    assert actor.processed == 500
    await sys.stop_all()
