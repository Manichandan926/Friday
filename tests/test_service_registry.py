import pytest
from app.core.service_registry import ServiceRegistry

class DummyService:
    def __init__(self, name, stop_log=None):
        self.name = name
        self.stop_log = stop_log
    
    def stop(self):
        if self.stop_log is not None:
            self.stop_log.append(self.name)

def test_register_and_get():
    registry = ServiceRegistry.get_instance()
    registry.reset()
    registry = ServiceRegistry.get_instance()
    
    svc = DummyService("auth")
    registry.register("auth_service", svc)
    
    assert registry.has("auth_service") is True
    assert registry.get("auth_service") is svc

def test_get_or_create():
    registry = ServiceRegistry.get_instance()
    registry.reset()
    registry = ServiceRegistry.get_instance()
    
    created_count = 0
    def factory():
        nonlocal created_count
        created_count += 1
        return DummyService("lazy")
        
    svc1 = registry.get_or_create("lazy_service", factory)
    svc2 = registry.get_or_create("lazy_service", factory)
    
    assert svc1 is svc2
    assert created_count == 1
    assert svc1.name == "lazy"

def test_require_missing_raises():
    registry = ServiceRegistry.get_instance()
    registry.reset()
    registry = ServiceRegistry.get_instance()
    
    with pytest.raises(RuntimeError, match="Required service 'missing' not registered"):
        registry.require("missing")

def test_get_typed_checks_type():
    registry = ServiceRegistry.get_instance()
    registry.reset()
    registry = ServiceRegistry.get_instance()
    
    svc = DummyService("typed")
    registry.register("typed_service", svc)
    
    # Correct type
    assert registry.get_typed("typed_service", DummyService) is svc
    
    # Incorrect type
    with pytest.raises(TypeError, match="expected int"):
        registry.get_typed("typed_service", int)

def test_cyclic_dependency_detected():
    registry = ServiceRegistry.get_instance()
    registry.reset()
    registry = ServiceRegistry.get_instance()
    
    registry.register_factory("A", lambda: registry.get("B"))
    registry.register_factory("B", lambda: registry.get("A"))
    
    with pytest.raises(RuntimeError, match="Cyclic dependency detected for 'A'"):
        registry.get("A")

def test_shutdown_order():
    registry = ServiceRegistry.get_instance()
    registry.reset()
    registry = ServiceRegistry.get_instance()
    
    stop_log = []
    svcA = DummyService("A", stop_log)
    svcB = DummyService("B", stop_log)
    svcC = DummyService("C", stop_log)
    
    registry.register("service_a", svcA)
    registry.register("service_b", svcB)
    registry.register("service_c", svcC)
    
    registry.shutdown()
    
    # LIFO: last registered should be stopped first
    assert stop_log == ["C", "B", "A"]
