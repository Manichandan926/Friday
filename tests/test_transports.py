import pytest
import asyncio
from unittest.mock import patch
from app.core.transports.transport import TransportMessage
from app.core.transports.mqtt_transport import MQTTTransport

class MockMessage:
    def __init__(self, topic, payload, qos=0, retain=False):
        self.topic = topic
        self.payload = payload
        self.qos = qos
        self.retain = retain

class MockClient:
    def __init__(self, *args, **kwargs):
        self._queue = asyncio.Queue()
        self.subscribed_topics = []
        self.published_messages = []
        self.entered = False
        self.exited = False

    @property
    def messages(self):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Retrieve message from queue
        try:
            return await self._queue.get()
        except asyncio.CancelledError:
            raise StopAsyncIteration

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True

    async def publish(self, topic, payload, qos, retain):
        self.published_messages.append({
            "topic": topic,
            "payload": payload,
            "qos": qos,
            "retain": retain
        })

    async def subscribe(self, topic):
        self.subscribed_topics.append(topic)

def test_transport_message_dataclass():
    msg = TransportMessage(topic="test/topic", payload=b"hello", qos=1, retain=True)
    assert msg.topic == "test/topic"
    assert msg.payload == b"hello"
    assert msg.qos == 1
    assert msg.retain is True

@pytest.mark.asyncio
async def test_mqtt_transport_connect_disconnect():
    mock_client = MockClient()
    
    with patch("aiomqtt.Client", return_value=mock_client):
        transport = MQTTTransport(host="localhost", port=1883)
        
        connected = await transport.connect()
        assert connected is True
        assert transport._running is True
        assert mock_client.entered is True
        
        await transport.disconnect()
        assert transport._running is False
        assert mock_client.exited is True

@pytest.mark.asyncio
async def test_mqtt_transport_publish():
    mock_client = MockClient()
    
    with patch("aiomqtt.Client", return_value=mock_client):
        transport = MQTTTransport()
        await transport.connect()
        
        msg = TransportMessage(topic="test/pub", payload=b"data_test", qos=2, retain=False)
        success = await transport.publish(msg)
        
        assert success is True
        assert len(mock_client.published_messages) == 1
        assert mock_client.published_messages[0]["topic"] == "test/pub"
        assert mock_client.published_messages[0]["payload"] == b"data_test"
        assert mock_client.published_messages[0]["qos"] == 2
        
        await transport.disconnect()

@pytest.mark.asyncio
async def test_mqtt_transport_subscribe_callback():
    mock_client = MockClient()
    
    with patch("aiomqtt.Client", return_value=mock_client):
        transport = MQTTTransport()
        
        # Connect first
        await transport.connect()
        
        received_messages = []
        async def cb(msg):
            received_messages.append(msg)
            
        # Subscribe
        sub_success = await transport.subscribe("test/sub", cb)
        assert sub_success is True
        
        # The subscription should have been forwarded to aiomqtt Client
        assert "test/sub" in mock_client.subscribed_topics
        
        # Simulate incoming message by pushing to the mock client queue
        simulated_msg = MockMessage(topic="test/sub", payload=b"payload_val", qos=1, retain=False)
        await mock_client._queue.put(simulated_msg)
        
        # Wait a short duration for listen loop to route it
        await asyncio.sleep(0.05)
        
        assert len(received_messages) == 1
        assert received_messages[0].topic == "test/sub"
        assert received_messages[0].payload == b"payload_val"
        
        await transport.disconnect()
