"""
middleware.py — Middleware pipeline for the FRIDAY Event Bus.

Middleware functions execute in order *before* an event reaches subscribers.
Each middleware can inspect, modify, log, or reject an event.

Pipeline flow:
    Publish → LoggingMiddleware → MetricsMiddleware → PermissionMiddleware → Subscribers
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Dict

from app.core.events.event import Event
from app.core.logger import logger


class Middleware(ABC):
    """Base class for event bus middleware.

    Implement `process()` to inspect or mutate events before dispatch.
    Return the event to continue the pipeline, or None to drop it.
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    async def process(self, event: Event) -> Event | None:
        """Process an event. Return None to silently drop it."""
        ...


class LoggingMiddleware(Middleware):
    """Logs every event flowing through the bus."""

    async def process(self, event: Event) -> Event | None:
        logger.debug(
            f"[EventBus] {event.event_type} | src={event.source} "
            f"prio={event.priority.name} id={event.event_id[:8]}"
        )
        return event


class MetricsMiddleware(Middleware):
    """Tracks event counts and processing latency per event type.

    Access counters via `.counts` and average latency via `.avg_latency_ms`.
    """

    def __init__(self):
        self.counts: Dict[str, int] = {}
        self._start_times: Dict[str, float] = {}
        self._total_latency: Dict[str, float] = {}

    async def process(self, event: Event) -> Event | None:
        etype = str(event.event_type)
        self.counts[etype] = self.counts.get(etype, 0) + 1
        self._start_times[event.event_id] = time.monotonic()
        return event

    def record_completion(self, event: Event) -> None:
        """Called after all subscribers finish to record latency."""
        start = self._start_times.pop(event.event_id, None)
        if start is not None:
            elapsed = (time.monotonic() - start) * 1000  # ms
            etype = str(event.event_type)
            self._total_latency[etype] = self._total_latency.get(etype, 0) + elapsed

    def avg_latency_ms(self, event_type: str) -> float:
        """Average latency in milliseconds for a given event type."""
        count = self.counts.get(event_type, 0)
        if count == 0:
            return 0.0
        return self._total_latency.get(event_type, 0) / count

    def summary(self) -> Dict[str, Dict[str, float]]:
        """Return a summary of all event type metrics."""
        return {
            etype: {
                "count": self.counts.get(etype, 0),
                "avg_latency_ms": round(self.avg_latency_ms(etype), 2),
            }
            for etype in self.counts
        }
