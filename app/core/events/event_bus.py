"""
event_bus.py — Multi-Queue Priority Event Bus for FRIDAY.

Evolved from single heapq to 4 independent deques — one per priority level.
This prevents low-priority event floods (cameras, sensors) from creating
backpressure on CRITICAL/HIGH events.

Architecture:
    Publish → Priority-specific deque → Dispatcher Thread → Middleware → Subscribers

Complexity:
    Publish:  O(1) — deque.append()
    Dispatch: O(1) — deque.popleft()

Queue structure:
    ┌─────────────┐
    │  CRITICAL    │  ← Drained FIRST (intrusion, fire, door unlock)
    ├─────────────┤
    │  HIGH        │  ← Battery critical, security alerts
    ├─────────────┤
    │  MEDIUM      │  ← Email received, task created
    ├─────────────┤
    │  LOW         │  ← Plugin loaded, metric log, sensor data
    └─────────────┘
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from typing import Any, Callable, Dict, Deque, List, Optional, Set, Union

from app.core.events.event import Event, Priority
from app.core.events.middleware import Middleware, MetricsMiddleware
from app.core.events.subscriber import FunctionSubscriber, Subscriber
from app.core.logger import logger


MAX_QUEUE_SIZE = {
    Priority.CRITICAL.value: 1000,
    Priority.HIGH.value: 5000,
    Priority.MEDIUM.value: 10000,
    Priority.LOW.value: 10000,
}

# Event types that should be persisted to the event_store table
_PERSISTENT_EVENT_TYPES: Set[str] = set()


def mark_persistent(*event_types: str) -> None:
    """Register event types that should be written to the event store."""
    for et in event_types:
        _PERSISTENT_EVENT_TYPES.add(str(et))


class EventBus:
    """Multi-queue priority-dispatched event bus.

    Uses 4 independent deques instead of a single heapq to achieve O(1)
    publish and dispatch while preventing low-priority floods from
    blocking critical events.

    Usage::

        bus = EventBus.get_instance()
        bus.subscribe("task.created", my_handler)
        await bus.publish(Event(event_type="task.created", source="scheduler"))
    """

    _instance: Optional[EventBus] = None
    _lock = threading.Lock()

    def __init__(self):
        # 4 independent priority queues — O(1) push/pop
        self._queues: Dict[int, Deque[Event]] = {
            Priority.CRITICAL.value: deque(),
            Priority.HIGH.value: deque(),
            Priority.MEDIUM.value: deque(),
            Priority.LOW.value: deque(),
        }
        self._queue_lock = threading.Lock()

        # subscriber registry: event_type → list of subscribers
        self._subscribers: Dict[str, List[Any]] = {}
        self._sub_lock = threading.Lock()

        # middleware pipeline (executed in order)
        self._middleware: List[Middleware] = []

        # dispatcher task control
        self._running = False
        self._dispatch_task: Optional[asyncio.Task] = None
        self._event_available: Optional[asyncio.Event] = None
        
        # ring buffer cache (O(1) memory lookup for recent events)
        self._history: Deque[Event] = deque(maxlen=10000)
        
        # main event loop reference
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # stats
        self._total_published = 0
        self._total_dispatched = 0

    @classmethod
    def get_instance(cls) -> EventBus:
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (useful for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.stop()
            cls._instance = None

    # ── Lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        """Start the async dispatcher task."""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._event_available = asyncio.Event()
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("EventBus dispatcher started (async native).")

    async def stop(self) -> None:
        """Gracefully stop the dispatcher."""
        if not self._running:
            return
        self._running = False
        if self._event_available:
            self._event_available.set()  # wake the task
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        logger.info("EventBus dispatcher stopped.")

    # ── Subscriptions ─────────────────────────────────────

    def subscribe(
        self,
        event_type: Union[str, List[str]],
        handler: Union[Subscriber, Callable],
    ) -> None:
        """Register a handler for one or more event types.

        Args:
            event_type: Single type string, or list of type strings.
                        Use "*" for a wildcard (receives all events).
            handler:    A Subscriber instance, or a callable (sync/async).
        """
        types = [event_type] if isinstance(event_type, str) else event_type

        # Wrap plain callables
        if isinstance(handler, Subscriber):
            sub = handler
        elif hasattr(handler, "subscriber"):
            sub = handler.subscriber
        else:
            sub = FunctionSubscriber(handler, [str(t) for t in types])

        with self._sub_lock:
            for t in types:
                t_str = str(t)
                if t_str not in self._subscribers:
                    self._subscribers[t_str] = []
                if sub not in self._subscribers[t_str]:
                    self._subscribers[t_str].append(sub)

        logger.debug(f"EventBus: subscribed {sub.name} → {types}")

    def unsubscribe(self, event_type: str, handler: Any) -> None:
        """Remove a handler from an event type."""
        with self._sub_lock:
            t_str = str(event_type)
            if t_str in self._subscribers:
                self._subscribers[t_str] = [
                    s for s in self._subscribers[t_str] if s is not handler
                ]

    # ── Middleware ─────────────────────────────────────────

    def add_middleware(self, mw: Middleware) -> None:
        """Add a middleware to the processing pipeline."""
        self._middleware.append(mw)
        logger.debug(f"EventBus: middleware added → {mw.name}")

    # ── Publishing ────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """Publish an event to the bus. O(1) deque append."""
        self._enforce_backpressure(event)
        with self._queue_lock:
            self._queues[event.priority.value].append(event)
            self._history.append(event)
            self._total_published += 1
        if self._event_available:
            self._event_available.set()

    def publish_sync(self, event: Event) -> None:
        """Synchronous publish for non-async contexts."""
        self._enforce_backpressure(event)
        with self._queue_lock:
            self._queues[event.priority.value].append(event)
            self._history.append(event)
            self._total_published += 1
        if self._loop and self._event_available:
            self._loop.call_soon_threadsafe(self._event_available.set)

    def get_recent_events(self, limit: int = 100) -> List[Event]:
        """O(1) retrieval of recent events from the ring buffer."""
        with self._queue_lock:
            history_list = list(self._history)
            return history_list[-limit:]

    def _enforce_backpressure(self, event: Event) -> None:
        """Apply backpressure policies if queues are full."""
        prio_val = event.priority.value
        max_size = MAX_QUEUE_SIZE[prio_val]
        
        with self._queue_lock:
            q = self._queues[prio_val]
            if len(q) >= max_size:
                if event.priority in (Priority.LOW, Priority.MEDIUM):
                    # Drop oldest
                    dropped = q.popleft()
                    logger.warning(
                        f"EventBus: {event.priority.name} queue full. "
                        f"Dropped oldest event {dropped.event_id[:8]}"
                    )
                elif event.priority == Priority.HIGH:
                    # Reject
                    logger.warning(
                        f"EventBus: HIGH queue full. Rejecting {event.event_id[:8]}"
                    )
                    raise RuntimeError("EventBus: HIGH priority queue is full")
                elif event.priority == Priority.CRITICAL:
                    # Emergency persist and drop from memory
                    dropped = q.popleft()
                    self._persist_event(dropped)
                    logger.error(
                        f"EventBus: CRITICAL queue full. "
                        f"Persisted and dropped {dropped.event_id[:8]} from memory"
                    )

    # ── Dispatcher ────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        """Async task: drains queues in priority order."""
        while self._running:
            if not self._event_available:
                await asyncio.sleep(0.1)
                continue
                
            await self._event_available.wait()
            self._event_available.clear()

            while self._running:
                event = self._pop_event()
                if event is None:
                    break
                # Fire and forget task to ensure we don't block the loop
                asyncio.create_task(self._dispatch_event(event))

    def _pop_event(self) -> Optional[Event]:
        """Pop the highest-priority event. O(1) per queue check.

        Drains CRITICAL first, then HIGH, then MEDIUM, then LOW.
        """
        with self._queue_lock:
            for prio_value in (
                Priority.CRITICAL.value,
                Priority.HIGH.value,
                Priority.MEDIUM.value,
                Priority.LOW.value,
            ):
                q = self._queues[prio_value]
                if q:
                    self._total_dispatched += 1
                    return q.popleft()
        return None

    async def _dispatch_event(self, event: Event) -> None:
        """Run event through middleware pipeline, then fan out to subscribers."""
        # ── Middleware pipeline ──
        current = event
        for mw in self._middleware:
            try:
                result = await mw.process(current)
                if result is None:
                    logger.debug(
                        f"EventBus: event {event.event_id[:8]} dropped by {mw.name}"
                    )
                    return
                current = result
            except Exception as e:
                logger.error(f"EventBus: middleware {mw.name} error: {e}")

        # ── Persist critical events ──
        if str(current.event_type) in _PERSISTENT_EVENT_TYPES:
            self._persist_event(current)

        # ── Fan out to subscribers ──
        handlers = self._get_handlers(str(current.event_type))
        for sub in handlers:
            try:
                await sub.handle(current)
            except Exception as e:
                logger.error(
                    f"EventBus: subscriber {sub.name} failed on "
                    f"{current.event_type}: {e}"
                )

        # ── Record metrics ──
        for mw in self._middleware:
            if isinstance(mw, MetricsMiddleware):
                mw.record_completion(current)

    def _get_handlers(self, event_type: str) -> List[Any]:
        """Collect handlers for a specific event type + wildcard subscribers."""
        with self._sub_lock:
            specific = list(self._subscribers.get(event_type, []))
            wildcard = list(self._subscribers.get("*", []))
        return specific + wildcard

    # ── Event Persistence ─────────────────────────────────

    def _persist_event(self, event: Event) -> None:
        """Write event to the event_store SQLite table."""
        try:
            from app.memory.database import get_db_session
            from app.memory.models import EventStore

            with get_db_session() as session:
                record = EventStore(
                    event_id=event.event_id,
                    event_type=str(event.event_type),
                    source=event.source,
                    payload=json.dumps(event.payload),
                    priority=event.priority.name,
                    correlation_id=event.correlation_id,
                    created_at=event.timestamp.replace(tzinfo=None)
                    if event.timestamp.tzinfo
                    else event.timestamp,
                )
                session.add(record)
            logger.debug(f"EventBus: persisted event {event.event_id[:8]}")
        except Exception as e:
            logger.error(f"EventBus: failed to persist event: {e}")

    # ── Introspection ─────────────────────────────────────

    def subscriber_count(self, event_type: Optional[str] = None) -> int:
        """Count subscribers for a specific type, or all subscribers."""
        with self._sub_lock:
            if event_type:
                return len(self._subscribers.get(str(event_type), []))
            return sum(len(subs) for subs in self._subscribers.values())

    def pending_count(self) -> int:
        """Number of events waiting across all queues."""
        with self._queue_lock:
            return sum(len(q) for q in self._queues.values())

    def pending_by_priority(self) -> Dict[str, int]:
        """Pending event count per priority level."""
        with self._queue_lock:
            return {
                Priority(pv).name: len(q) for pv, q in self._queues.items()
            }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict[str, int]:
        """Bus-level statistics."""
        return {
            "total_published": self._total_published,
            "total_dispatched": self._total_dispatched,
            "pending": self.pending_count(),
            "subscribers": self.subscriber_count(),
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Return metrics from the MetricsMiddleware if installed."""
        for mw in self._middleware:
            if isinstance(mw, MetricsMiddleware):
                return mw.summary()
        return {}
