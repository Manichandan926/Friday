import pytest
import asyncio
import json
from app.core.actors.system import ActorSystem
from app.core.actors.mqtt import MQTTActor
from app.core.actors.dead_letter import DeadLetterActor
from app.core.actors.actor import ActorMessage
from app.core.events.event_bus import EventBus
from app.core.events.event import Event, Priority
from app.core.transports.transport import Transport, TransportMessage

class DummyTransport(Transport):
    def __init__(self):
        self.connected = False
        self.published = []
        self.subscribed = {}

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def publish(self, message: TransportMessage) -> bool:
        self.published.append(message)
        return True

    async def subscribe(self, topic: str, callback) -> bool:
        self.subscribed[topic] = callback
        return True

@pytest.mark.asyncio
async def test_inbound_mqtt_to_event():
    bus = EventBus.get_instance()
    await bus.start()
    
    events_received = []
    bus.subscribe("device.telemetry", lambda e: events_received.append(e))
    
    transport = DummyTransport()
    actor = MQTTActor(transport, bus)
    
    sys = ActorSystem.get_instance()
    sys.spawn(actor)
    await sys.start_all()
    
    # 1. Simulate inbound MQTT message
    # Should subscribe to "friday/devices/#"
    assert "friday/devices/#" in transport.subscribed
    callback = transport.subscribed["friday/devices/#"]
    
    tm = TransportMessage(
        topic="friday/devices/light_01/telemetry",
        payload=b'{"power": "on", "brightness": 80}'
    )
    await callback(tm)
    
    # Wait for event to propagate on bus
    await asyncio.sleep(0.05)
    
    assert len(events_received) == 1
    event = events_received[0]
    assert event.event_type == "device.telemetry"
    assert event.source == "mqtt:light_01"
    assert event.payload["power"] == "on"
    assert event.payload["brightness"] == 80
    
    await sys.stop_all()
    await bus.stop()

@pytest.mark.asyncio
async def test_outbound_event_to_mqtt():
    bus = EventBus.get_instance()
    await bus.start()
    
    transport = DummyTransport()
    actor = MQTTActor(transport, bus)
    
    sys = ActorSystem.get_instance()
    sys.spawn(actor)
    await sys.start_all()
    
    # 2. Publish device.command on event bus, MQTTActor should forward to MQTT
    event = Event(
        event_type="device.command",
        source="user_interface",
        payload={
            "device_id": "light_02",
            "command": "set_color",
            "params": {"color": "red"}
        }
    )
    await bus.publish(event)
    
    # Wait for message to be routed
    await asyncio.sleep(0.05)
    
    assert len(transport.published) == 1
    published_msg = transport.published[0]
    assert published_msg.topic == "friday/devices/light_02/command"
    
    data = json.loads(published_msg.payload.decode())
    assert data["command"] == "set_color"
    assert data["params"]["color"] == "red"
    assert published_msg.qos == 1
    
    await sys.stop_all()
    await bus.stop()

@pytest.mark.asyncio
async def test_malformed_mqtt_to_dead_letter():
    bus = EventBus.get_instance()
    await bus.start()
    
    transport = DummyTransport()
    actor = MQTTActor(transport, bus)
    
    dlq_actor = DeadLetterActor()
    
    sys = ActorSystem.get_instance()
    sys.spawn(actor)
    sys.spawn(dlq_actor)
    await sys.start_all()
    
    # Simulate malformed MQTT payload (invalid JSON)
    callback = transport.subscribed["friday/devices/#"]
    tm = TransportMessage(
        topic="friday/devices/light_01/telemetry",
        payload=b'invalid{json'
    )
    await callback(tm)
    
    # Wait for DeadLetterActor to capture
    await asyncio.sleep(0.05)
    
    failures = dlq_actor.get_failures()
    assert len(failures) == 1
    assert failures[0]["sender"] == "mqtt_actor"
    assert failures[0]["error_type"] == "PARSE_ERROR"
    assert failures[0]["payload"]["topic"] == "friday/devices/light_01/telemetry"
    assert failures[0]["payload"]["payload"] == b"invalid{json"
    
    await sys.stop_all()
    await bus.stop()

@pytest.mark.asyncio
async def test_topic_parsing():
    # Verify how MQTTActor splits topics and routes them
    bus = EventBus.get_instance()
    await bus.start()
    
    events = []
    bus.subscribe("device.status", lambda e: events.append(e))
    
    transport = DummyTransport()
    actor = MQTTActor(transport, bus)
    
    sys = ActorSystem.get_instance()
    sys.spawn(actor)
    await sys.start_all()
    
    callback = transport.subscribed["friday/devices/#"]
    
    # Topic split check for topic with different parts
    tm = TransportMessage(
        topic="friday/devices/switch_99/status",
        payload=b'{"state": "off"}'
    )
    await callback(tm)
    
    await asyncio.sleep(0.05)
    
    assert len(events) == 1
    assert events[0].source == "mqtt:switch_99"
    assert events[0].event_type == "device.status"
    assert events[0].payload["state"] == "off"
    
    await sys.stop_all()
    await bus.stop()
