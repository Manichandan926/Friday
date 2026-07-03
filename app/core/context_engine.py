"""
context_engine.py — Context-Aware Request Enrichment for FRIDAY.

Builds rich context around every request before it hits the Policy Engine.
This enables policies like:

    unlock_door:
        only_if:
            owner_home == True
            time < 23:00
            security_mode != "away"

Architecture:
    Request → Context Engine → Enriched Request → Policy Engine → Execution

Context sources:
    - Time (hour, day, weekday, is_night)
    - Location (home, away — based on network/device presence)
    - System state (from StateStore)
    - Device states (from DeviceManager)
    - User profile (from MemoryManager)
    - Security mode (armed, disarmed, away)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logger import logger


class ContextProvider(ABC):
    """Base class for all context data providers."""
    
    @property
    @abstractmethod
    def namespace(self) -> str:
        """Key under which this provider's data appears in context."""
        ...
        
    @abstractmethod
    def provide(self) -> Dict[str, Any]:
        """Return context data dictionary."""
        ...


class ContextEngine:
    """Builds enriched context dictionaries for policy evaluation.

    Collects data from multiple providers and merges them into a single
    context dict that the Policy Engine can evaluate conditions against.

    Usage::

        ctx = ContextEngine()
        ctx.register_provider("time", time_provider)
        ctx.register_provider("system", system_provider)

        context = ctx.build()
        # → {"time": {"hour": 14, ...}, "system": {"cpu_pct": 30, ...}}
    """

    def __init__(self):
        self._providers: Dict[str, ContextProvider] = {}
        self._static_context: Dict[str, Any] = {}

        # Register built-in providers
        self.register_provider(TimeProvider())

    # ── Providers ─────────────────────────────────────────

    def register_provider(self, provider: ContextProvider) -> None:
        """Register a context data provider.

        Args:
            provider: Instance of ContextProvider subclass.
        """
        self._providers[provider.namespace] = provider
        logger.debug(f"ContextEngine: registered provider '{provider.namespace}'")

    def unregister_provider(self, namespace: str) -> None:
        """Remove a context provider."""
        self._providers.pop(namespace, None)

    # ── Static Context ────────────────────────────────────

    def set_static(self, key: str, value: Any) -> None:
        """Set a static context value (e.g. user role, security mode)."""
        self._static_context[key] = value

    def get_static(self, key: str, default: Any = None) -> Any:
        """Get a static context value."""
        return self._static_context.get(key, default)

    # ── Context Building ──────────────────────────────────

    def build(self, extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build a full context dictionary from all providers.

        Returns a flat-ish dict suitable for Policy Engine condition evaluation::

            {
                "time": {"hour": 14, "weekday": "Thursday", "is_night": False},
                "system": {"cpu_pct": 30, "ram_pct": 60},
                "security_mode": "home",
                "owner_home": True,
                ...extras
            }
        """
        context: Dict[str, Any] = {}

        # Static context first
        context.update(self._static_context)

        # Dynamic providers
        for namespace, provider in self._providers.items():
            try:
                data = provider.provide()
                if isinstance(data, dict):
                    context[namespace] = data
                    # Also flatten top-level keys for easier policy conditions
                    for k, v in data.items():
                        if k not in context:
                            context[k] = v
                else:
                    context[namespace] = data
            except Exception as e:
                logger.error(f"ContextEngine: provider '{namespace}' failed: {e}")
                context[namespace] = {}

        # Merge extras
        if extras:
            context.update(extras)

        return context

    def build_for_action(
        self,
        action: str,
        role: str = "owner",
        extras: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build context enriched with action and role metadata."""
        context = self.build(extras)
        context["_action"] = action
        context["_role"] = role
        return context

    # ── Built-in Providers ────────────────────────────────


class TimeProvider(ContextProvider):
    """Provide current time context."""
    
    @property
    def namespace(self) -> str:
        return "time"
        
    def provide(self) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "hour": now.hour,
            "minute": now.minute,
            "weekday": now.strftime("%A"),
            "day": now.day,
            "month": now.month,
            "year": now.year,
            "is_night": now.hour >= 22 or now.hour < 6,
            "is_weekend": now.weekday() >= 5,
            "is_business_hours": 9 <= now.hour < 18 and now.weekday() < 5,
            "iso": now.isoformat(),
        }


class SystemStateProvider(ContextProvider):
    """Provide system health from StateStore."""
    
    def __init__(self, state_store):
        self.store = state_store
        
    @property
    def namespace(self) -> str:
        return "system"
        
    def provide(self) -> Dict[str, Any]:
        health = self.store.get("system", "health") or {}
        return {
            "cpu_pct": health.get("cpu_pct", 0),
            "ram_pct": health.get("ram_pct", 0),
            "disk_pct": health.get("disk_pct", 0),
            "bat_pct": health.get("bat_pct"),
            "system_status": self.store.get("system", "status") or "unknown",
        }


class DeviceStateProvider(ContextProvider):
    """Provide device states from DeviceManager."""
    
    def __init__(self, device_manager):
        self.manager = device_manager
        
    @property
    def namespace(self) -> str:
        return "devices"
        
    def provide(self) -> Dict[str, Any]:
        devices = self.manager.list_devices()
        online_count = sum(1 for d in devices if d.get("status") == "online")
        return {
            "device_count": len(devices),
            "devices_online": online_count,
            "devices_offline": len(devices) - online_count,
        }
