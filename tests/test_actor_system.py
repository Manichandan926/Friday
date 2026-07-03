import pytest
import asyncio
from app.core.actors.actor import Actor, ActorMessage, RestartStrategy
from app.core.actors.system import ActorSystem
from app.core.metrics import MetricsRegistry

class DummyActor(Actor):
    def __init__(self, name="dummy"):
        super().__init__(name=name)
        self.messages_received = []

    async def receive(self, message: ActorMessage) -> None:
        self.messages_received.append(message)
        if message.message_type == "PING":
            if message.reply_to:
                await message.reply_to.put("PONG")

@pytest.mark.asyncio
async def test_actor_spawn_and_start():
    sys = ActorSystem.get_instance()
    actor = DummyActor("test_actor")
    sys.spawn(actor)
    await sys.start_all()
    
    assert actor._running is True
    
    msg = ActorMessage(sender="test", message_type="PING", payload=None)
    await sys.send("test_actor", msg)
    await asyncio.sleep(0.1)
    
    assert len(actor.messages_received) == 1
    assert actor.messages_received[0].message_type == "PING"
    await sys.stop_all()

@pytest.mark.asyncio
async def test_actor_stop():
    sys = ActorSystem.get_instance()
    actor = DummyActor("test_actor")
    sys.spawn(actor)
    await sys.start_all()
    assert actor._running is True
    
    await sys.stop_all()
    assert actor._running is False

@pytest.mark.asyncio
async def test_actor_message_ordering():
    sys = ActorSystem.get_instance()
    actor = DummyActor("ordered_actor")
    sys.spawn(actor)
    await sys.start_all()
    
    for i in range(10):
        await sys.send("ordered_actor", ActorMessage("test", "NUM", i))
        
    await asyncio.sleep(0.1)
    assert len(actor.messages_received) == 10
    for i in range(10):
        assert actor.messages_received[i].payload == i
        
    await sys.stop_all()

@pytest.mark.asyncio
async def test_actor_mailbox_depth_metric():
    sys = ActorSystem.get_instance()
    actor = DummyActor("metric_actor")
    sys.spawn(actor)
    
    # Send messages without starting actor to build depth
    for _ in range(5):
        await actor.send(ActorMessage("test", "PING", None))
        
    # Start it, which triggers the run loop and sets the gauge
    await sys.start_all()
    await asyncio.sleep(0.1)
    
    metrics = MetricsRegistry.get_instance().export_json()
    assert "actor_mailbox_depth" in metrics
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_actor_processing_time_metric():
    sys = ActorSystem.get_instance()
    actor = DummyActor("time_actor")
    sys.spawn(actor)
    await sys.start_all()
    
    await sys.send("time_actor", ActorMessage("test", "PING", None))
    await asyncio.sleep(0.1)
    
    metrics = MetricsRegistry.get_instance().export_json()
    assert "actor_processing_time" in metrics
    assert metrics["actor_processing_time"]["count"] > 0
    
    await sys.stop_all()

@pytest.mark.asyncio
async def test_actor_system_send_to_missing():
    sys = ActorSystem.get_instance()
    res = await sys.send("missing_actor", ActorMessage("test", "PING", None))
    assert res is False
    
    metrics = MetricsRegistry.get_instance().export_json()
    assert "actor_dropped_messages" in metrics
    assert metrics["actor_dropped_messages"]["counts"] is not None

@pytest.mark.asyncio
async def test_actor_system_start_all_stop_all():
    sys = ActorSystem.get_instance()
    actor1 = DummyActor("actor1")
    actor2 = DummyActor("actor2")
    sys.spawn(actor1)
    sys.spawn(actor2)
    
    await sys.start_all()
    assert actor1._running is True
    assert actor2._running is True
    
    await sys.stop_all()
    assert actor1._running is False
    assert actor2._running is False

@pytest.mark.asyncio
async def test_actor_hot_spawn():
    sys = ActorSystem.get_instance()
    await sys.start_all()
    
    actor = DummyActor("hot_actor")
    sys.spawn(actor)
    
    # Wait for hot spawn start task to execute
    await asyncio.sleep(0.1)
    assert actor._running is True
    
    await sys.stop_all()
