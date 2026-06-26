"""
state_store.py — Redux-like State Store for FRIDAY.

Provides a single source of truth for the entire system state.
All UI components, agents, devices, and remote clients read from
this store instead of querying scattered sources.

Architecture:
    Events → Reducers → State Store → Subscribers (UI / Agents / API)

Concepts:
    - State:    Immutable snapshot of the system at a point in time.
    - Reducer:  Pure function that takes (state, event) → new_state.
    - Selector: Function that extracts a slice of state for consumers.

This becomes critical when mobile apps, web dashboards, and multiple
clients need to observe the same consistent state.
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from app.core.logger import logger
from app.core.events.event import Event
from app.core.events.event_bus import EventBus


# Type aliases
StateDict = Dict[str, Any]
Reducer = Callable[[StateDict, Any], StateDict]
Selector = Callable[[StateDict], Any]
StateListener = Callable[[StateDict, StateDict], None]  # (old_state, new_state)


def _default_initial_state() -> StateDict:
    """Default initial state tree for FRIDAY."""
    return {
        # System
        "system": {
            "status": "running",
            "uptime_start": datetime.now(timezone.utc).isoformat(),
            "health": {"cpu_pct": 0, "ram_pct": 0, "disk_pct": 0, "bat_pct": None},
        },
        # Counters
        "counters": {
            "events_processed": 0,
            "commands_executed": 0,
            "errors": 0,
        },
        # Active entities
        "devices": {},       # device_id → {status, last_action, last_seen}
        "agents": {},        # agent_name → {active, last_invoked}
        "plugins": {},       # plugin_name → {active, version}
        # UI state
        "notifications": {
            "unread_count": 0,
            "latest": None,
        },
        # Tasks summary
        "tasks": {
            "pending": 0,
            "completed": 0,
            "overdue": 0,
        },
        # Email summary
        "email": {
            "unread": 0,
            "latest_subject": None,
        },
    }


class StateStore:
    """Thread-safe, reducer-based state container.

    Usage::

        store = StateStore()

        # Register a reducer
        store.register_reducer("system", system_reducer)

        # Dispatch an event/action
        store.dispatch({"type": "HEALTH_UPDATED", "payload": {...}})

        # Read state
        health = store.select(lambda s: s["system"]["health"])

        # Subscribe to changes
        store.subscribe(on_state_change)
    """

    def __init__(self, initial_state: Optional[StateDict] = None):
        self._state: StateDict = initial_state or _default_initial_state()
        self._lock = threading.RLock()

        # domain → reducer function
        self._reducers: Dict[str, Reducer] = {}

        # change listeners
        self._listeners: List[StateListener] = []

        # selective listeners: set of state keys → listener
        self._key_listeners: Dict[str, List[StateListener]] = {}

        self._version = 0
        self._bus: Optional[EventBus] = None

    def bind_to_bus(self, bus: EventBus) -> None:
        """Bind StateStore to the EventBus (Event Sourcing).
        
        This makes the StateStore a materialized view of the event stream.
        Instead of calling store.update() manually, components publish
        events, which the store naturally reduces into the current state.
        """
        if self._bus is not None:
            return
        self._bus = bus
        self._bus.subscribe("*", self._on_bus_event)
        logger.info("StateStore: bound to EventBus (Event Sourcing active).")

    def _on_bus_event(self, event: Event) -> None:
        """Process an event from the bus through all reducers."""
        action = {
            "type": event.event_type,
            "payload": event.payload,
            "source": event.source
        }
        # We dispatch internally to run reducers
        self.dispatch(action)

    # ── State Access ──────────────────────────────────────

    @property
    def state(self) -> StateDict:
        """Get a deep copy of the current state (immutable read)."""
        with self._lock:
            return copy.deepcopy(self._state)

    @property
    def version(self) -> int:
        """Monotonically increasing version counter."""
        with self._lock:
            return self._version

    def select(self, selector: Selector) -> Any:
        """Extract a slice of state using a selector function.

        Usage::

            cpu = store.select(lambda s: s["system"]["health"]["cpu_pct"])
            unread = store.select(lambda s: s["notifications"]["unread_count"])
        """
        with self._lock:
            try:
                return selector(self._state)
            except (KeyError, TypeError, IndexError):
                return None

    def get(self, *keys: str, default: Any = None) -> Any:
        """Navigate nested state by key path.

        Usage::

            store.get("system", "health", "cpu_pct")  # → 31.5
            store.get("devices", "light_01", "status") # → "online"
        """
        with self._lock:
            current = self._state
            for key in keys:
                if isinstance(current, dict):
                    current = current.get(key)
                    if current is None:
                        return default
                else:
                    return default
            return current

    # ── Reducers ──────────────────────────────────────────

    def register_reducer(self, domain: str, reducer: Reducer) -> None:
        """Register a reducer for a state domain.

        A reducer is a pure function: (state_slice, action) → new_state_slice

        Example::

            def system_reducer(state, action):
                if action["type"] == "HEALTH_UPDATED":
                    return {**state, "health": action["payload"]}
                return state

            store.register_reducer("system", system_reducer)
        """
        self._reducers[domain] = reducer
        logger.debug(f"StateStore: registered reducer for '{domain}'")

    # ── Dispatch ──────────────────────────────────────────

    def dispatch(self, action: Dict[str, Any]) -> None:
        """Dispatch an action through all registered reducers.

        Action format::

            {"type": "ACTION_NAME", "payload": {...}, "source": "..."}
        """
        with self._lock:
            old_state = copy.deepcopy(self._state)

            # Run action through each domain reducer
            for domain, reducer in self._reducers.items():
                domain_state = self._state.get(domain, {})
                try:
                    new_domain_state = reducer(domain_state, action)
                    if new_domain_state is not domain_state:
                        self._state[domain] = new_domain_state
                except Exception as e:
                    logger.error(f"StateStore: reducer '{domain}' failed: {e}")

            # Global counter
            self._state.setdefault("counters", {})
            self._state["counters"]["events_processed"] = (
                self._state["counters"].get("events_processed", 0) + 1
            )

            self._version += 1

        # Notify listeners outside the lock
        self._notify_listeners(old_state, self._state)

    def update(self, *keys: str, value: Any) -> None:
        """Direct state mutation by key path.
        
        In an Event Sourced architecture, this is anti-pattern, but provided
        for convenience. If bound to a bus, this will publish a state.updated event
        rather than mutating directly (to maintain the event log).
        """
        if self._bus:
            import asyncio
            payload = {"keys": keys, "value": value}
            event = Event(event_type="state.updated", source="state_store", payload=payload)
            # Publish safely whether in an async context or not
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self._bus.publish_sync, event)
            except RuntimeError:
                self._bus.publish_sync(event)
                
        with self._lock:
            old_state = copy.deepcopy(self._state)
            current = self._state
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            current[keys[-1]] = value
            self._version += 1

        self._notify_listeners(old_state, self._state)

    # ── Subscriptions ─────────────────────────────────────

    def subscribe(self, listener: StateListener) -> Callable:
        """Subscribe to all state changes. Returns an unsubscribe function."""
        self._listeners.append(listener)

        def unsubscribe():
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def subscribe_key(self, key: str, listener: StateListener) -> Callable:
        """Subscribe to changes in a specific top-level state key."""
        if key not in self._key_listeners:
            self._key_listeners[key] = []
        self._key_listeners[key].append(listener)

        def unsubscribe():
            if key in self._key_listeners and listener in self._key_listeners[key]:
                self._key_listeners[key].remove(listener)

        return unsubscribe

    def _notify_listeners(self, old_state: StateDict, new_state: StateDict) -> None:
        """Notify global and key-specific listeners."""
        # Global listeners
        for listener in self._listeners:
            try:
                listener(old_state, new_state)
            except Exception as e:
                logger.error(f"StateStore: listener error: {e}")

        # Key-specific listeners
        for key, listeners in self._key_listeners.items():
            old_val = old_state.get(key)
            new_val = new_state.get(key)
            if old_val != new_val:
                for listener in listeners:
                    try:
                        listener(old_state, new_state)
                    except Exception as e:
                        logger.error(f"StateStore: key listener '{key}' error: {e}")

    # ── Snapshot ──────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Full serializable snapshot of current state + metadata."""
        with self._lock:
            return {
                "version": self._version,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": copy.deepcopy(self._state),
            }
