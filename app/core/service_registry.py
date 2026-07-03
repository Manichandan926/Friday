"""
service_registry.py — Centralized Service Registry for FRIDAY.

Replaces scattered singletons with a proper dependency injection container.
Every core service (EventBus, PolicyEngine, PermissionSystem, DeviceManager,
PluginManager, StateStore, ContextEngine) registers here and is retrieved
by name — no global objects, no import-time coupling.

Architecture:
    ServiceRegistry
        ├── register("event_bus", EventBus())
        ├── get("event_bus") → EventBus
        ├── get_or_create("event_bus", factory) → EventBus
        └── shutdown() → calls .stop()/.shutdown() on all services

This prevents 'global object hell' as FRIDAY scales to dozens of subsystems.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Type

from app.core.logger import logger


class ServiceRegistry:
    """Thread-safe centralized service container.

    Usage::

        registry = ServiceRegistry.get_instance()
        registry.register("event_bus", my_bus)
        bus = registry.get("event_bus")

        # Or with lazy creation:
        bus = registry.get_or_create("event_bus", lambda: EventBus())
    """

    _instance: Optional[ServiceRegistry] = None
    _lock = threading.Lock()

    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable] = {}
        self._shutdown_order: List[str] = []   # LIFO shutdown order
        self._creating: Set[str] = set()       # For cycle detection
        self._svc_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> ServiceRegistry:
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Tear down and reset (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown()
            cls._instance = None

    # ── Registration ──────────────────────────────────────

    def register(self, name: str, service: Any) -> None:
        """Register a live service instance."""
        with self._svc_lock:
            if name in self._services:
                logger.warning(f"ServiceRegistry: overwriting service '{name}'")
            self._services[name] = service
            if name not in self._shutdown_order:
                self._shutdown_order.append(name)
        logger.debug(f"ServiceRegistry: registered '{name}'")

    def register_factory(self, name: str, factory: Callable[[], Any]) -> None:
        """Register a lazy factory function for a service."""
        with self._svc_lock:
            self._factories[name] = factory
        logger.debug(f"ServiceRegistry: registered factory for '{name}'")

    # ── Retrieval ─────────────────────────────────────────

    def get(self, name: str) -> Optional[Any]:
        """Get a service by name. Returns None if not found."""
        with self._svc_lock:
            svc = self._services.get(name)
            if svc is not None:
                return svc
            # Try factory
            factory = self._factories.get(name)
            if factory:
                if name in self._creating:
                    raise RuntimeError(f"ServiceRegistry: Cyclic dependency detected for '{name}'")
                self._creating.add(name)
                try:
                    svc = factory()
                    self._services[name] = svc
                    if name not in self._shutdown_order:
                        self._shutdown_order.append(name)
                    return svc
                finally:
                    self._creating.remove(name)
        return None

    def get_or_create(self, name: str, factory: Callable[[], Any]) -> Any:
        """Get existing service or create via factory if absent."""
        with self._svc_lock:
            if name in self._services:
                return self._services[name]
                
            if name in self._creating:
                raise RuntimeError(f"ServiceRegistry: Cyclic dependency detected for '{name}'")
            self._creating.add(name)
            try:
                svc = factory()
                self._services[name] = svc
                if name not in self._shutdown_order:
                    self._shutdown_order.append(name)
                return svc
            finally:
                self._creating.remove(name)

    def require(self, name: str) -> Any:
        """Get a service or raise RuntimeError if missing."""
        svc = self.get(name)
        if svc is None:
            raise RuntimeError(f"Required service '{name}' not registered")
        return svc

    # ── Typed Accessors (convenience) ─────────────────────

    def get_typed(self, name: str, expected_type: Type) -> Any:
        """Get a service and verify its type."""
        svc = self.get(name)
        if svc is not None and not isinstance(svc, expected_type):
            raise TypeError(
                f"Service '{name}' is {type(svc).__name__}, expected {expected_type.__name__}"
            )
        return svc

    # ── Lifecycle ─────────────────────────────────────────

    def shutdown(self) -> None:
        """Shutdown all services in reverse registration order.

        Calls .stop(), .shutdown(), or .deactivate_all() if the method exists.
        """
        with self._svc_lock:
            for name in reversed(self._shutdown_order):
                svc = self._services.get(name)
                if svc is None:
                    continue
                try:
                    if hasattr(svc, "stop"):
                        svc.stop()
                    elif hasattr(svc, "shutdown"):
                        svc.shutdown()
                    elif hasattr(svc, "deactivate_all"):
                        svc.deactivate_all()
                    logger.debug(f"ServiceRegistry: stopped '{name}'")
                except Exception as e:
                    logger.error(f"ServiceRegistry: error stopping '{name}': {e}")

            self._services.clear()
            self._factories.clear()
            self._shutdown_order.clear()
        logger.info("ServiceRegistry: all services shut down")

    # ── Introspection ─────────────────────────────────────

    def list_services(self) -> List[str]:
        """List all registered service names."""
        with self._svc_lock:
            return list(self._services.keys())

    def has(self, name: str) -> bool:
        """Check if a service is registered."""
        with self._svc_lock:
            return name in self._services or name in self._factories

    def __contains__(self, name: str) -> bool:
        return self.has(name)

    def __repr__(self) -> str:
        services = self.list_services()
        return f"ServiceRegistry(services={services})"
