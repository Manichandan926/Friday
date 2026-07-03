"""
workflow_engine.py — Automation Workflow Engine for FRIDAY.

Evaluates triggers and conditions to execute automated actions.
This is the heart of smart home and system automation.

Architecture:
    Trigger (Event) → Conditions Check → Actions (Commands)

Example:
    Workflow(
        name="Night Security",
        trigger=EventTrigger("device.motion_detected"),
        conditions=[
            TimeCondition(after="23:00", before="06:00"),
            StateCondition("system.security_mode", "armed")
        ],
        actions=[
            CommandAction("turn_on_lights", {"zone": "exterior"}),
            CommandAction("send_notification", {"msg": "Motion detected outside!"})
        ]
    )
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from app.core.events.event import Event
from app.core.events.event_bus import EventBus
from app.core.logger import logger
from app.core.service_registry import ServiceRegistry


class Condition(ABC):
    """Abstract condition for a workflow."""
    @abstractmethod
    def evaluate(self, event: Event, state: Dict[str, Any]) -> bool:
        ...


class Action(ABC):
    """Abstract action to execute when a workflow triggers."""
    @abstractmethod
    async def execute(self, event: Event) -> None:
        ...


class TimeCondition(Condition):
    """Evaluates if the current time is within a window."""
    def __init__(self, after: str, before: str):
        self.after = time.fromisoformat(after)
        self.before = time.fromisoformat(before)

    def evaluate(self, event: Event, state: Dict[str, Any]) -> bool:
        now = datetime.now().time()
        if self.after < self.before:
            return self.after <= now <= self.before
        else: # Crosses midnight
            return now >= self.after or now <= self.before


class StateCondition(Condition):
    """Evaluates if a value in the StateStore matches expected."""
    def __init__(self, key_path: str, expected_value: Any):
        self.key_path = key_path.split(".")
        self.expected_value = expected_value

    def evaluate(self, event: Event, state: Dict[str, Any]) -> bool:
        current = state
        for key in self.key_path:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return False
        return current == self.expected_value


class EventCondition(Condition):
    """Evaluates payload fields of the triggering event."""
    def __init__(self, field_name: str, expected_value: Any):
        self.field_name = field_name
        self.expected_value = expected_value

    def evaluate(self, event: Event, state: Dict[str, Any]) -> bool:
        return event.payload.get(self.field_name) == self.expected_value


class EventTrigger:
    """Listens for a specific event type."""
    def __init__(self, event_type: str):
        self.event_type = event_type


@dataclass
class Workflow:
    """A complete automation workflow."""
    name: str
    trigger: EventTrigger
    conditions: List[Condition] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    active: bool = True


class WorkflowEngine:
    """Executes automation workflows based on event triggers.

    Usage::

        engine = WorkflowEngine(event_bus, state_store)
        engine.register(my_workflow)
    """

    def __init__(self, event_bus: Optional[EventBus] = None, state_store: Optional[Any] = None):
        self._bus = event_bus
        self._store = state_store
        self._workflows: List[Workflow] = []
        self._trigger_map: Dict[str, List[Workflow]] = {}

        if self._bus:
            # Subscribe to all events to evaluate workflows
            self._bus.subscribe("*", self._on_event)

    def register(self, workflow: Workflow) -> None:
        """Register a new workflow."""
        self._workflows.append(workflow)
        evt_type = workflow.trigger.event_type
        if evt_type not in self._trigger_map:
            self._trigger_map[evt_type] = []
        self._trigger_map[evt_type].append(workflow)
        logger.info(f"WorkflowEngine: registered '{workflow.name}' on '{evt_type}'")

    def unregister(self, workflow_name: str) -> None:
        """Remove a workflow by name."""
        self._workflows = [w for w in self._workflows if w.name != workflow_name]
        # Rebuild trigger map
        self._trigger_map.clear()
        for w in self._workflows:
            evt_type = w.trigger.event_type
            if evt_type not in self._trigger_map:
                self._trigger_map[evt_type] = []
            self._trigger_map[evt_type].append(w)

    async def _on_event(self, event: Event) -> None:
        """Handle incoming events and trigger workflows."""
        workflows = self._trigger_map.get(str(event.event_type), [])
        if not workflows:
            return

        state_snapshot = self._store.state if self._store else {}

        for workflow in workflows:
            if not workflow.active:
                continue

            # Evaluate conditions
            conditions_met = True
            for condition in workflow.conditions:
                try:
                    if not condition.evaluate(event, state_snapshot):
                        conditions_met = False
                        break
                except Exception as e:
                    logger.error(f"WorkflowEngine: condition failed in '{workflow.name}': {e}")
                    conditions_met = False
                    break

            if conditions_met:
                logger.info(f"WorkflowEngine: Triggering workflow '{workflow.name}'")
                for action in workflow.actions:
                    try:
                        await action.execute(event)
                    except Exception as e:
                        logger.error(f"WorkflowEngine: action failed in '{workflow.name}': {e}")
