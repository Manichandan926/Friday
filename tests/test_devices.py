import pytest
import asyncio
import time
from typing import Dict, Any, Set
from app.core.devices import (
    DeviceManager, Device, DeviceDriver, DeviceAction, DeviceActionResult,
    DeviceType, DeviceStatus, DeviceStateCache
)
from app.core.events.event_bus import EventBus
from app.core.events.event_types import EventType
from app.core.policy_engine import PolicyEngine, Decision, PolicyDecision
from app.memory.database import get_db_session
from app.memory.models import DeviceRecord

class DummyDriver(DeviceDriver):
    def __init__(self):
        self.connected = False
        self.status_queries = 0

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def execute(self, action: DeviceAction) -> DeviceActionResult:
        if action.command == "fail":
            return DeviceActionResult(success=False, message="Execution failed")
        return DeviceActionResult(success=True, message=f"Executed {action.command}")

    async def get_status(self) -> Dict[str, Any]:
        self.status_queries += 1
        return {"power": "on"}

class DummyDevice(Device):
    @property
    def device_id(self) -> str:
        return "dummy_01"

    @property
    def name(self) -> str:
        return "Dummy Light"

    @property
    def device_type(self) -> DeviceType:
        return DeviceType.LIGHT

    @property
    def capabilities(self) -> Set[str]:
        return {"turn_on", "turn_off"}

@pytest.mark.asyncio
async def test_device_registration():
    bus = EventBus.get_instance()
    await bus.start()
    
    events_received = []
    bus.subscribe(EventType.DEVICE_CONNECTED, lambda e: events_received.append(e))
    bus.subscribe(EventType.DEVICE_DISCONNECTED, lambda e: events_received.append(e))
    
    dm = DeviceManager(bus=bus)
    device = DummyDevice(DummyDriver())
    
    # 1. Register device
    registered = dm.register(device)
    assert registered is True
    
    # Check DB persistence
    with get_db_session() as session:
        record = session.query(DeviceRecord).filter_by(device_id="dummy_01").first()
        assert record is not None
        assert record.name == "Dummy Light"
        assert record.device_type == "light"
        
    # Wait for DEVICE_CONNECTED event
    await asyncio.sleep(0.05)
    assert len(events_received) == 1
    assert events_received[0].payload["device_id"] == "dummy_01"
    
    # 2. Unregister device
    unregistered = dm.unregister("dummy_01")
    assert unregistered is True
    
    # Wait for DEVICE_DISCONNECTED event
    await asyncio.sleep(0.05)
    assert len(events_received) == 2
    assert events_received[1].payload["device_id"] == "dummy_01"
    
    await bus.stop()

@pytest.mark.asyncio
async def test_device_state_ttl_cache():
    driver = DummyDriver()
    device = DummyDevice(driver)
    
    # Set cache TTL to 0.2 seconds
    dm = DeviceManager(cache_ttl=0.2)
    dm.register(device)
    
    # 1. First query: cache miss, queries hardware
    status1 = await dm.get_status("dummy_01")
    assert status1["power"] == "on"
    assert driver.status_queries == 1
    
    # 2. Second query: cache hit, does NOT query hardware
    status2 = await dm.get_status("dummy_01")
    assert status2["power"] == "on"
    assert driver.status_queries == 1
    
    # 3. Wait for TTL to expire
    await asyncio.sleep(0.25)
    
    # 4. Third query: cache miss, queries hardware again
    status3 = await dm.get_status("dummy_01")
    assert status3["power"] == "on"
    assert driver.status_queries == 2

@pytest.mark.asyncio
async def test_device_action_policy_allow():
    policy = PolicyEngine()
    policy.add_rule("device.light.turn_on", role="owner", allowed=True)
    
    dm = DeviceManager(policy_engine=policy)
    device = DummyDevice(DummyDriver())
    dm.register(device)
    
    action = DeviceAction(command="turn_on", params={})
    res = await dm.execute("dummy_01", action, role="owner")
    
    assert res.success is True
    assert res.message == "Executed turn_on"

@pytest.mark.asyncio
async def test_device_action_policy_deny():
    policy = PolicyEngine()
    policy.add_rule("device.light.turn_on", role="guest", allowed=False)
    
    dm = DeviceManager(policy_engine=policy)
    device = DummyDevice(DummyDriver())
    dm.register(device)
    
    action = DeviceAction(command="turn_on", params={})
    res = await dm.execute("dummy_01", action, role="guest")
    
    assert res.success is False
    assert "Policy denied" in res.message

@pytest.mark.asyncio
async def test_device_action_event():
    bus = EventBus.get_instance()
    await bus.start()
    
    events = []
    bus.subscribe(EventType.DEVICE_EVENT, lambda e: events.append(e))
    
    dm = DeviceManager(bus=bus)
    device = DummyDevice(DummyDriver())
    dm.register(device)
    
    action = DeviceAction(command="turn_on", params={})
    await dm.execute("dummy_01", action)
    
    # Wait for DEVICE_EVENT event
    await asyncio.sleep(0.05)
    
    assert len(events) == 1
    assert events[0].payload["device_id"] == "dummy_01"
    assert events[0].payload["command"] == "turn_on"
    assert events[0].payload["success"] is True
    
    await bus.stop()
