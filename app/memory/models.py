from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import String, Text, ForeignKey, DateTime, Float, Boolean, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

def utc_now_naive() -> datetime:
    """Returns a timezone-naive UTC datetime for SQLAlchemy compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase):
    pass

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), default="New Conversation")
    # Rolling summary of older turns; messages with id <= summary_until_id
    # are covered by it and stay out of the prompt window.
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_until_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

    messages: Mapped[List["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", lazy="selectin"
    )

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

class MemoryItem(Base):
    """Long-term facts, preferences, user profile data."""
    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

class Email(Base):
    """Analyzed email summaries from Gmail."""
    __tablename__ = "emails"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    sender: Mapped[str] = mapped_column(String(255))
    body_summary: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    priority: Mapped[str] = mapped_column(String(50), default="medium")
    action_items: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)

class Task(Base):
    """Action items and reminders."""
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(50), default="medium", index=True)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_email_id: Mapped[Optional[str]] = mapped_column(ForeignKey("emails.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

class Application(Base):
    """Placement/internship application tracking."""
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(100), default="applied", index=True)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_email_id: Mapped[Optional[str]] = mapped_column(ForeignKey("emails.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

class KnowledgeItem(Base):
    """Personal knowledge vault — notes, prep material, references."""
    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(100))  # aws, dsa, interview, project, general
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # comma-separated
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

class Project(Base):
    """Project tracking with milestones and progress."""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(100), default="active")  # active, paused, completed
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

class UsageRecord(Base):
    """One LLM API call's token usage and cost, for the running /cost total.

    Persisted so /cost survives restarts instead of resetting each session.
    """
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)          # USD estimate
    cost_known: Mapped[bool] = mapped_column(Boolean, default=True)  # False if model unpriced
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, index=True)


class Notification(Base):
    """Proactive alert history."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), default="general")  # deadline, email, system, task
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class Plan(Base):
    """A task-mode plan: a user-approved goal FRIDAY executes step by step.

    status: active → (completed | cancelled), or blocked while a failed step
    waits for the user to say skip/retry/cancel. Persisted so a restart
    resumes the plan instead of forgetting it.
    """
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="active", index=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # verification verdict
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    steps: Mapped[List["PlanStep"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin"
    )


class PlanStep(Base):
    """One ordered step of a Plan. status: pending → running → done,
    or failed (blocks the plan) / skipped (user's call)."""
    __tablename__ = "plan_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)

    plan: Mapped["Plan"] = relationship(back_populates="steps")


# ── Phase 2.5: Core Infrastructure Models ────────────────────────────

class EventStore(Base):
    """Persisted event log for critical events (lightweight event sourcing)."""
    __tablename__ = "event_store"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    source: Mapped[str] = mapped_column(String(100))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    correlation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, index=True)


class PolicyRule(Base):
    """Rules evaluated by the Policy Engine before actions execute."""
    __tablename__ = "policy_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(100), index=True)   # e.g. "unlock_door", "delete_file"
    role: Mapped[str] = mapped_column(String(50), default="owner") # owner, guest, plugin
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_mfa: Mapped[bool] = mapped_column(Boolean, default=False)
    conditions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON conditions
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class DeviceRecord(Base):
    """Registered devices for the Device Abstraction Layer."""
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    device_type: Mapped[str] = mapped_column(String(100))   # light, sensor, camera, switch
    protocol: Mapped[str] = mapped_column(String(50), default="mqtt")  # mqtt, http, zigbee
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="offline")  # online, offline, error
    config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON config
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, onupdate=utc_now_naive)


class StateSnapshot(Base):
    """Stores periodic snapshots of the StateStore for crash recovery."""
    __tablename__ = "state_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    state_data: Mapped[str] = mapped_column(Text)  # JSON dumped state
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive)


class AuditRecord(Base):
    """Immutable audit log for security events and critical actions."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, index=True)
    actor: Mapped[str] = mapped_column(String(100), index=True)      # e.g. "owner", "guest", "plugin:weather"
    action: Mapped[str] = mapped_column(String(100))                 # e.g. "unlock_door", "delete_file"
    resource: Mapped[str] = mapped_column(String(255))               # e.g. "device:front_door"
    decision: Mapped[str] = mapped_column(String(50))                # e.g. "ALLOW", "DENY"
    source_ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    context_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # JSON payload
