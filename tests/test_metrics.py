import pytest
from app.core.metrics import MetricsRegistry, Counter, Gauge, Histogram

def test_counter_increment():
    registry = MetricsRegistry.get_instance()
    # Use unique name
    c = registry.get_counter("test_counter", "A test counter")
    assert c.get_current() == 0.0
    
    c.inc()
    assert c.get_current() == 1.0
    
    c.inc(2.5, labels={"method": "GET"})
    assert c.get_current(labels={"method": "GET"}) == 2.5
    
    # Increment same labeled counter
    c.inc(1.5, labels={"method": "GET"})
    assert c.get_current(labels={"method": "GET"}) == 4.0
    
    # Base count remains 1.0 (unlabeled)
    assert c.get_current() == 1.0

def test_counter_negative_rejected():
    registry = MetricsRegistry.get_instance()
    c = registry.get_counter("test_counter_neg")
    with pytest.raises(ValueError, match="Counters can only increment"):
        c.inc(-1.0)

def test_gauge_set():
    registry = MetricsRegistry.get_instance()
    g = registry.get_gauge("test_gauge", "A test gauge")
    
    g.set(42.0, labels={"host": "localhost"})
    vals = g.get_values()
    assert len(vals) == 1
    assert vals[0].value == 42.0
    assert vals[0].labels == {"host": "localhost"}
    
    g.set(100.0, labels={"host": "localhost"})
    vals = g.get_values()
    assert len(vals) == 2
    assert vals[-1].value == 100.0

def test_histogram_observe():
    registry = MetricsRegistry.get_instance()
    h = registry.get_histogram("test_histogram", "A test histogram", buckets=[1.0, 5.0, 10.0])
    
    h.observe(0.5, labels={"api": "auth"})
    h.observe(3.0, labels={"api": "auth"})
    h.observe(12.0, labels={"api": "auth"})
    
    assert h._count == 3
    assert h._sum == 15.5
    
    # Buckets:
    # 0.5 fits in <= 1.0, <= 5.0, <= 10.0
    # 3.0 fits in <= 5.0, <= 10.0
    # 12.0 fits in none
    assert h._counts[1.0] == 1
    assert h._counts[5.0] == 2
    assert h._counts[10.0] == 2

def test_export_json():
    # Setup fresh instance or clear metrics to ensure predictable export
    registry = MetricsRegistry.get_instance()
    registry._metrics.clear()
    
    c = registry.get_counter("http_req_total", "HTTP requests")
    c.inc(10.0, labels={"code": "200"})
    
    g = registry.get_gauge("system_cpu", "CPU usage")
    g.set(45.5, labels={"core": "0"})
    
    h = registry.get_histogram("request_latency", "Latency", buckets=[1.0, 2.0])
    h.observe(0.5)
    
    exported = registry.export_json()
    
    assert "http_req_total" in exported
    assert exported["http_req_total"]["type"] == "counter"
    assert exported["http_req_total"]["counts"][str([("code", "200")])] == 10.0
    
    assert "system_cpu" in exported
    assert exported["system_cpu"]["type"] == "gauge"
    assert exported["system_cpu"]["latest"] == 45.5
    assert exported["system_cpu"]["labels"] == {"core": "0"}
    
    assert "request_latency" in exported
    assert exported["request_latency"]["type"] == "histogram"
    assert exported["request_latency"]["count"] == 1
    assert exported["request_latency"]["sum"] == 0.5
    assert exported["request_latency"]["buckets"][1.0] == 1

def test_registry_singleton():
    reg1 = MetricsRegistry.get_instance()
    reg2 = MetricsRegistry.get_instance()
    assert reg1 is reg2

def test_history_bounded():
    c = Counter("bounded_counter")
    for _ in range(1005):
        c.inc(1.0)
        
    assert len(c.get_values()) == 1000
    
    g = Gauge("bounded_gauge")
    for i in range(1005):
        g.set(float(i))
        
    assert len(g.get_values()) == 1000
    
    h = Histogram("bounded_histogram")
    for i in range(1005):
        h.observe(float(i))
        
    assert len(h.get_values()) == 1000
    assert h._count == 1005 # Total count is still tracked fully
