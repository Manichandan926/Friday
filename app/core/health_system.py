"""
health_system.py — Health Check and Monitoring System for FRIDAY.

Provides a unified interface to check the health of all registered services,
devices, and core infrastructure components.

Architecture:
    Service/Device implements HealthCheckable → HealthSystem polls it
    → Exposes aggregated health status to API / UI.
"""

from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.logger import logger
from app.core.service_registry import ServiceRegistry


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthResult:
    """The result of a single health check."""
    component_name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class HealthCheckable(ABC):
    """Interface for components that can be health-checked."""
    @abstractmethod
    async def check_health(self) -> HealthResult:
        ...


class HealthSystem:
    """Central registry and executor for health checks.
    
    Usage::
    
        health = HealthSystem()
        health.register("database", db_health_checker)
        status = await health.check_all()
    """

    def __init__(self, service_registry: Optional[ServiceRegistry] = None):
        self._checkers: Dict[str, HealthCheckable] = {}
        self._registry = service_registry
        self._lock = threading.Lock()

    def register(self, name: str, checker: HealthCheckable) -> None:
        """Register a health checker."""
        with self._lock:
            self._checkers[name] = checker
            logger.debug(f"HealthSystem: Registered checker for '{name}'")

    def unregister(self, name: str) -> None:
        """Unregister a health checker."""
        with self._lock:
            self._checkers.pop(name, None)

    async def check(self, name: str) -> Optional[HealthResult]:
        """Execute a specific health check."""
        with self._lock:
            checker = self._checkers.get(name)
            
        if not checker:
            return None
            
        start = time.perf_counter()
        try:
            # Enforce a strict timeout on health checks (5 seconds)
            result = await asyncio.wait_for(checker.check_health(), timeout=5.0)
            result.latency_ms = (time.perf_counter() - start) * 1000
            return result
        except asyncio.TimeoutError:
            return HealthResult(
                component_name=name,
                status=HealthStatus.UNHEALTHY,
                message="Health check timed out (5s)",
                latency_ms=(time.perf_counter() - start) * 1000
            )
        except Exception as e:
            return HealthResult(
                component_name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
                latency_ms=(time.perf_counter() - start) * 1000
            )

    async def check_all(self) -> Dict[str, HealthResult]:
        """Execute all registered health checks concurrently."""
        with self._lock:
            checkers = list(self._checkers.keys())
            
        if not checkers:
            return {}
            
        results = await asyncio.gather(
            *(self.check(name) for name in checkers),
            return_exceptions=True
        )
        
        output = {}
        for name, result in zip(checkers, results):
            if isinstance(result, Exception):
                output[name] = HealthResult(
                    component_name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Unexpected error: {str(result)}"
                )
            elif result:
                output[name] = result
                
        return output
