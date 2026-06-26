"""
event.py — Core Event data structure for FRIDAY's Event Bus.

Uses a priority-aware dataclass with a unique ID, correlation tracking,
and a frozen payload. Priority is an IntEnum so that heapq naturally
sorts CRITICAL (0) before LOW (3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, Optional


class Priority(IntEnum):
    """Event urgency levels. Lower value = higher priority.

    heapq is a min-heap, so CRITICAL (0) is dispatched first.
    """
    CRITICAL = 0   # Door opened, intrusion, fire
    HIGH     = 1   # Battery critical, security alert
    MEDIUM   = 2   # Email received, task created
    LOW      = 3   # Plugin loaded, metric log


@dataclass(order=False, frozen=False)
class Event:
    """Immutable-ish event flowing through the FRIDAY event bus.

    Attributes:
        event_type:      Canonical event name (from EventType enum or str).
        source:          Module/agent that produced the event.
        payload:         Arbitrary data dict carried by the event.
        priority:        Dispatch priority (CRITICAL > HIGH > MEDIUM > LOW).
        event_id:        UUID4 auto-generated unique identifier.
        timestamp:       UTC creation time.
        correlation_id:  Optional chain ID linking related events together.
        metadata:        Optional extra context (e.g. retry count, ttl).
    """
    event_type: str
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: Priority = Priority.MEDIUM

    # auto-generated fields
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other: Event) -> bool:
        """Compare by priority for heapq ordering.
        
        If priorities are equal, break tie by timestamp (earlier first).
        """
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.timestamp < other.timestamp

    def __le__(self, other: Event) -> bool:
        return self == other or self < other

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (for persistence / logging)."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "payload": self.payload,
            "priority": self.priority.name,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Event:
        """Reconstruct an Event from a serialized dict."""
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            source=data["source"],
            payload=data.get("payload", {}),
            priority=Priority[data.get("priority", "MEDIUM")],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            correlation_id=data.get("correlation_id"),
            metadata=data.get("metadata", {}),
        )
