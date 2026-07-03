import pytest
import asyncio
from app.core.actors.actor import Actor, ActorMessage, RestartStrategy
from app.core.actors.system import ActorSystem
from app.core.actors.supervisor import SupervisorActor
from app.core.metrics import MetricsRegistry

class FragileActor(Actor):
    def __init__(self, name="fragile", restart_strategy=RestartStrategy.ON_FAILURE):
        super().__init__(name=name, restart_strategy=restart_strategy)
        self.received = []

    async def receive(self, message: ActorMessage) -> None:
        self.received.append(message)
        if message.message_type == "CRASH":
            raise RuntimeError("Induced crash")

async def wait_for_condition(condition_fn, timeout=2.0, interval=0.005):
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        if condition_fn():
            return True
        await asyncio.sleep(interval)
    return False

@pytest.mark.asyncio
async def test_supervisor_catches_crash():
    sys = ActorSystem.get_instance()
    sup = SupervisorActor()
    sys.spawn(sup)
    
    actor = FragileActor("fragile")
    sys.spawn(actor)
    sup.watch(actor)
    
    await sys.start_all()
    
    await sys.send("fragile", ActorMessage("test", "CRASH", None))
    
    # Wait until it is watched and crashes
    await wait_for_condition(lambda: sys.get_actor("fragile")._running is False)
    
    assert "fragile" in sup._registry
    await sys.stop_all()

@pytest.mark.asyncio
async def test_supervisor_restarts_actor():
    sys = ActorSystem.get_instance()
    sup = SupervisorActor()
    sys.spawn(sup)
    
    actor = FragileActor("fragile")
    sys.spawn(actor)
    sup.watch(actor)
    
    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        await original_sleep(0.001)
        
    import app.core.actors.supervisor
    app.core.actors.supervisor.asyncio.sleep = mock_sleep
    
    try:
        await sys.start_all()
        await sys.send("fragile", ActorMessage("test", "CRASH", None))
        
        # Wait for crash
        crashed = await wait_for_condition(lambda: sys.get_actor("fragile")._running is False)
        assert crashed is True
        
        # Wait for restart
        restarted = await wait_for_condition(lambda: sys.get_actor("fragile")._running is True)
        assert restarted is True
        
        # Send message to restarted actor to confirm functionality
        await sys.send("fragile", ActorMessage("test", "PING", None))
        
        pinged = await wait_for_condition(lambda: any(m.message_type == "PING" for m in sys.get_actor("fragile").received))
        assert pinged is True
    finally:
        app.core.actors.supervisor.asyncio.sleep = original_sleep
        await sys.stop_all()

@pytest.mark.asyncio
async def test_supervisor_restart_counter():
    sys = ActorSystem.get_instance()
    sup = SupervisorActor()
    sys.spawn(sup)
    
    actor = FragileActor("fragile")
    sys.spawn(actor)
    sup.watch(actor)
    
    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        await original_sleep(0.001)
        
    import app.core.actors.supervisor
    app.core.actors.supervisor.asyncio.sleep = mock_sleep
    
    try:
        await sys.start_all()
        await sys.send("fragile", ActorMessage("test", "CRASH", None))
        
        crashed = await wait_for_condition(lambda: sys.get_actor("fragile")._running is False)
        assert crashed is True
        
        restarted = await wait_for_condition(lambda: sys.get_actor("fragile")._running is True)
        assert restarted is True
        
        metrics = MetricsRegistry.get_instance().export_json()
        assert "actor_restarts" in metrics
        assert sum(metrics["actor_restarts"]["counts"].values()) >= 1
    finally:
        app.core.actors.supervisor.asyncio.sleep = original_sleep
        await sys.stop_all()

@pytest.mark.asyncio
async def test_supervisor_max_restarts():
    sys = ActorSystem.get_instance()
    sup = SupervisorActor()
    sys.spawn(sup)
    
    actor = FragileActor("fragile")
    sys.spawn(actor)
    sup.watch(actor)
    
    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        await original_sleep(0.001)
        
    import app.core.actors.supervisor
    app.core.actors.supervisor.asyncio.sleep = mock_sleep
    
    try:
        await sys.start_all()
        
        # Induce 5 crashes, waiting for crash and restart between each
        for i in range(5):
            await sys.send("fragile", ActorMessage("test", "CRASH", None))
            # Wait for crash
            crashed = await wait_for_condition(lambda: sys.get_actor("fragile")._running is False)
            assert crashed is True
            # Wait for restart
            restarted = await wait_for_condition(lambda: sys.get_actor("fragile")._running is True)
            assert restarted is True
                
        # Send 6th crash, which should trigger max retries exceeded and stop restarting
        await sys.send("fragile", ActorMessage("test", "CRASH", None))
        
        # Wait for crash
        crashed = await wait_for_condition(lambda: sys.get_actor("fragile")._running is False)
        assert crashed is True
        
        # Wait to confirm it stays down
        await asyncio.sleep(0.1)
        
        assert sys.get_actor("fragile")._running is False
        assert sup._retry_counts["fragile"] >= 5
    finally:
        app.core.actors.supervisor.asyncio.sleep = original_sleep
        await sys.stop_all()

@pytest.mark.asyncio
async def test_supervisor_watches_multiple():
    sys = ActorSystem.get_instance()
    sup = SupervisorActor()
    sys.spawn(sup)
    
    actor1 = FragileActor("fragile1")
    actor2 = FragileActor("fragile2")
    sys.spawn(actor1)
    sys.spawn(actor2)
    sup.watch(actor1)
    sup.watch(actor2)
    
    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        await original_sleep(0.001)
        
    import app.core.actors.supervisor
    app.core.actors.supervisor.asyncio.sleep = mock_sleep
    
    try:
        await sys.start_all()
        
        # Crash both
        await sys.send("fragile1", ActorMessage("test", "CRASH", None))
        await sys.send("fragile2", ActorMessage("test", "CRASH", None))
        
        crashed1 = await wait_for_condition(lambda: sys.get_actor("fragile1")._running is False)
        crashed2 = await wait_for_condition(lambda: sys.get_actor("fragile2")._running is False)
        assert crashed1 is True
        assert crashed2 is True
        
        restarted1 = await wait_for_condition(lambda: sys.get_actor("fragile1")._running is True)
        restarted2 = await wait_for_condition(lambda: sys.get_actor("fragile2")._running is True)
        assert restarted1 is True
        assert restarted2 is True
    finally:
        app.core.actors.supervisor.asyncio.sleep = original_sleep
        await sys.stop_all()
