"""
event_types.py — Canonical event type constants for FRIDAY.

Using a string enum so that event types are human-readable in logs,
databases, and middleware pipelines, while still being type-safe
and autocomplete-friendly.
"""

from enum import unique, Enum


@unique
class EventType(str, Enum):
    """Every event flowing through the FRIDAY Event Bus must use one of
    these canonical type strings. New domains (home, security, vision)
    simply add entries here — no other code changes required.
    """

    # ── Email ─────────────────────────────────────────────
    EMAIL_RECEIVED      = "email.received"
    EMAIL_PROCESSED     = "email.processed"

    # ── Tasks ─────────────────────────────────────────────
    TASK_CREATED        = "task.created"
    TASK_UPDATED        = "task.updated"
    TASK_COMPLETED      = "task.completed"
    TASK_DELETED        = "task.deleted"

    # ── Applications / Placement ──────────────────────────
    APPLICATION_CREATED = "application.created"
    APPLICATION_UPDATED = "application.updated"

    # ── Memory / Knowledge ────────────────────────────────
    MEMORY_LEARNED      = "memory.learned"
    KNOWLEDGE_ADDED     = "knowledge.added"
    KNOWLEDGE_UPDATED   = "knowledge.updated"

    # ── Projects ──────────────────────────────────────────
    PROJECT_CREATED     = "project.created"
    PROJECT_UPDATED     = "project.updated"
    PROJECT_COMPLETED   = "project.completed"

    # ── System / Infrastructure ───────────────────────────
    SYSTEM_ALERT        = "system.alert"
    SYSTEM_HEALTH       = "system.health"
    SYSTEM_STARTUP      = "system.startup"
    SYSTEM_SHUTDOWN     = "system.shutdown"

    # ── Notifications ─────────────────────────────────────
    NOTIFICATION_CREATED = "notification.created"

    # ── Devices / IoT (Phase 5+) ──────────────────────────
    DEVICE_CONNECTED    = "device.connected"
    DEVICE_DISCONNECTED = "device.disconnected"
    DEVICE_EVENT        = "device.event"
    DEVICE_COMMAND      = "device.command"

    # ── Security (Phase 6+) ───────────────────────────────
    SECURITY_ALERT      = "security.alert"
    SECURITY_MOTION     = "security.motion"
    SECURITY_INTRUSION  = "security.intrusion"

    # ── Plugins ───────────────────────────────────────────
    PLUGIN_LOADED       = "plugin.loaded"
    PLUGIN_UNLOADED     = "plugin.unloaded"
    PLUGIN_ERROR        = "plugin.error"

    # ── Agents ────────────────────────────────────────────
    AGENT_REGISTERED    = "agent.registered"
    AGENT_UNREGISTERED  = "agent.unregistered"
    AGENT_INVOKED       = "agent.invoked"

    # ── Scheduler ─────────────────────────────────────────
    JOB_STARTED         = "job.started"
    JOB_COMPLETED       = "job.completed"
    JOB_FAILED          = "job.failed"
