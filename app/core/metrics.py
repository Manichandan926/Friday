"""
metrics.py — Centralized Metrics System for FRIDAY.

Provides a unified interface for recording counters, gauges, and histograms.
The metrics system can output to Prometheus, SQLite, or simply expose an API
endpoint for a monitoring dashboard.

Architecture:
    Components → MetricsRegistry → Prometheus / JSON endpoint
"""

from __future__ import annotations

import time
import threading
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from app.core.logger import logger


@dataclass
class MetricValue:
    """A recorded metric value."""
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Metric:
    """Base class for metrics."""
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._lock = threading.Lock()
        self._values: List[MetricValue] = []
        
    def get_values(self) -> List[MetricValue]:
        with self._lock:
            return list(self._values)


class Counter(Metric):
    """A metric that only goes up (e.g. requests served, errors)."""
    
    def __init__(self, name: str, description: str = ""):
        super().__init__(name, description)
        self._counts: Dict[str, float] = {}  # serialized labels -> count
        
    def inc(self, amount: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        """Increment the counter."""
        if amount < 0:
            raise ValueError("Counters can only increment.")
        
        lbls = labels or {}
        key = str(sorted(lbls.items()))
        
        with self._lock:
            if key not in self._counts:
                self._counts[key] = 0.0
            self._counts[key] += amount
            
            # Store point-in-time value for timeseries
            self._values.append(MetricValue(self._counts[key], lbls))
            # Keep history bound
            if len(self._values) > 1000:
                self._values = self._values[-1000:]
                
    def get_current(self, labels: Optional[Dict[str, str]] = None) -> float:
        """Get the current count for specific labels."""
        lbls = labels or {}
        key = str(sorted(lbls.items()))
        with self._lock:
            return self._counts.get(key, 0.0)


class Gauge(Metric):
    """A metric that can go up and down (e.g. CPU usage, queue size)."""
    
    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Set the gauge to a specific value."""
        lbls = labels or {}
        with self._lock:
            self._values.append(MetricValue(value, lbls))
            if len(self._values) > 1000:
                self._values = self._values[-1000:]


class Histogram(Metric):
    """A metric that tracks distributions of events (e.g. request latencies)."""
    
    def __init__(self, name: str, description: str = "", buckets: List[float] = None):
        super().__init__(name, description)
        self.buckets = buckets or [0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0]
        self._counts: Dict[float, int] = {b: 0 for b in self.buckets}
        self._sum = 0.0
        self._count = 0
        
    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Observe a new value."""
        lbls = labels or {}
        with self._lock:
            self._sum += value
            self._count += 1
            for b in self.buckets:
                if value <= b:
                    self._counts[b] += 1
            self._values.append(MetricValue(value, lbls))
            if len(self._values) > 1000:
                self._values = self._values[-1000:]


class MetricsRegistry:
    """Central registry for all system metrics.
    
    Usage::
    
        metrics = MetricsRegistry.get_instance()
        req_counter = metrics.get_counter("http_requests_total")
        req_counter.inc(labels={"method": "GET", "endpoint": "/api/v1/status"})
    """
    
    _instance: Optional[MetricsRegistry] = None
    _lock = threading.Lock()
    
    def __init__(self):
        self._metrics: Dict[str, Metric] = {}
        self._reg_lock = threading.Lock()
        
    @classmethod
    def get_instance(cls) -> MetricsRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
        
    def get_counter(self, name: str, description: str = "") -> Counter:
        with self._reg_lock:
            if name not in self._metrics:
                self._metrics[name] = Counter(name, description)
            metric = self._metrics[name]
            if not isinstance(metric, Counter):
                raise TypeError(f"Metric '{name}' exists but is not a Counter")
            return metric
            
    def get_gauge(self, name: str, description: str = "") -> Gauge:
        with self._reg_lock:
            if name not in self._metrics:
                self._metrics[name] = Gauge(name, description)
            metric = self._metrics[name]
            if not isinstance(metric, Gauge):
                raise TypeError(f"Metric '{name}' exists but is not a Gauge")
            return metric
            
    def get_histogram(self, name: str, description: str = "", buckets: List[float] = None) -> Histogram:
        with self._reg_lock:
            if name not in self._metrics:
                self._metrics[name] = Histogram(name, description, buckets)
            metric = self._metrics[name]
            if not isinstance(metric, Histogram):
                raise TypeError(f"Metric '{name}' exists but is not a Histogram")
            return metric
            
    def export_json(self) -> Dict[str, Any]:
        """Export all current metrics as a JSON-serializable dictionary."""
        result = {}
        with self._reg_lock:
            for name, metric in self._metrics.items():
                if isinstance(metric, Counter):
                    result[name] = {
                        "type": "counter",
                        "description": metric.description,
                        "counts": {k: v for k, v in metric._counts.items()}
                    }
                elif isinstance(metric, Gauge):
                    latest = metric.get_values()[-1] if metric.get_values() else None
                    result[name] = {
                        "type": "gauge",
                        "description": metric.description,
                        "latest": latest.value if latest else None,
                        "labels": latest.labels if latest else {}
                    }
                elif isinstance(metric, Histogram):
                    result[name] = {
                        "type": "histogram",
                        "description": metric.description,
                        "count": metric._count,
                        "sum": metric._sum,
                        "buckets": metric._counts
                    }
        return result
