"""Autonomous routines — app/core/routine_runner.py plus the
add_routine/list_routines/update_routine tools.

What matters: creating a standing unattended behavior is CONFIRM-gated; the
due logic never double-fires; an unattended run cannot cross a tier (CONFIRM
tools are refused mid-routine because nothing is ever approved); a run is
consumed before it executes so failures can't retry-storm; and near-budget
days skip the run but still consume it."""
import datetime
from typing import Any, Dict, List, Optional

import pytest

from app.core import routine_runner, tiers, toolkit
from app.core.routine_runner import is_due, run_due_routines
from app.core.tiers import Tier
from app.llm.types import LLMReply, ToolCall, ToolSpec
from app.memory.memory_manager import MemoryManager


class FakeProvider:
    def __init__(self, replies: List[LLMReply]):
        self.replies = list(replies)
        self.requests: List[List[Dict[str, Any]]] = []

    async def chat(self, messages, tools: Optional[List[ToolSpec]] = None, **kwargs) -> LLMReply:
        self.requests.append([dict(m) for m in messages])
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def no_desktop_toasts(monkeypatch):
    monkeypatch.setattr(routine_runner.desktop, "send_notification",
                        lambda *a, **k: "Notification sent.")


def _routine(**kw):
    defaults = dict(name="brief", instruction="check the system stats",
                    schedule_type="interval", interval_minutes=30)
    defaults.update(kw)
    return MemoryManager.add_routine(**defaults)


# ── tiers and creation ────────────────────────────────────

def test_add_routine_is_confirm_and_refused_unapproved():
    assert tiers.classify("add_routine") == Tier.CONFIRM
    out = toolkit.execute("add_routine", {
        "name": "x", "instruction": "y", "schedule_type": "daily", "time_of_day": "08:00",
    })
    assert "requires the user's explicit approval" in out
    assert MemoryManager.get_routines() == []


def test_helper_tools_are_auto():
    assert tiers.classify("list_routines") == Tier.AUTO
    assert tiers.classify("update_routine") == Tier.AUTO


@pytest.mark.parametrize("args,fragment", [
    ({"name": "x", "instruction": "y", "schedule_type": "daily", "time_of_day": "8am"},
     "HH:MM"),
    ({"name": "x", "instruction": "y", "schedule_type": "daily"}, "HH:MM"),
    ({"name": "x", "instruction": "y", "schedule_type": "interval", "interval_minutes": 5},
     "at least 15"),
    ({"name": "x", "instruction": "y", "schedule_type": "weekly"}, "daily"),
    ({"name": "", "instruction": "y", "schedule_type": "daily", "time_of_day": "08:00"},
     "non-empty"),
])
def test_add_routine_validation(args, fragment):
    out = toolkit.execute("add_routine", args, approved=True)
    assert "argument error" in out and fragment in out
    assert MemoryManager.get_routines() == []


def test_add_routine_rejects_duplicate_names_and_enforces_cap(monkeypatch):
    ok = toolkit.execute("add_routine", {
        "name": "brief", "instruction": "y", "schedule_type": "daily", "time_of_day": "08:00",
    }, approved=True)
    assert "created" in ok
    dup = toolkit.execute("add_routine", {
        "name": "Brief", "instruction": "z", "schedule_type": "daily", "time_of_day": "09:00",
    }, approved=True)
    assert "already exists" in dup

    monkeypatch.setattr(routine_runner, "MAX_ROUTINES", 1)
    capped = toolkit.execute("add_routine", {
        "name": "other", "instruction": "z", "schedule_type": "daily", "time_of_day": "09:00",
    }, approved=True)
    assert "limit reached" in capped


def test_update_routine_enable_disable_delete():
    r = _routine()
    assert "disabled" in toolkit.execute(
        "update_routine", {"routine_id": r.id, "action": "disable"})
    assert MemoryManager.get_routine(r.id).enabled is False
    assert "enabled" in toolkit.execute(
        "update_routine", {"routine_id": r.id, "action": "enable"})
    assert "deleted" in toolkit.execute(
        "update_routine", {"routine_id": r.id, "action": "delete"})
    assert MemoryManager.get_routine(r.id) is None


# ── due logic ─────────────────────────────────────────────

def test_daily_due_only_after_time_and_once_per_day():
    r = _routine(schedule_type="daily", time_of_day="08:00", interval_minutes=None)
    day = datetime.datetime(2026, 7, 18, 0, 0)
    assert is_due(r, day.replace(hour=7, minute=59)) is False
    assert is_due(r, day.replace(hour=8, minute=0)) is True
    assert is_due(r, day.replace(hour=23, minute=0)) is True  # catch-up after a day offline

    MemoryManager.touch_routine(r.id, day.replace(hour=8, minute=1))
    r = MemoryManager.get_routine(r.id)
    assert is_due(r, day.replace(hour=9)) is False            # already ran today
    assert is_due(r, day.replace(hour=8) + datetime.timedelta(days=1)) is True


def test_interval_due_respects_spacing():
    r = _routine(interval_minutes=30)
    now = datetime.datetime(2026, 7, 18, 12, 0)
    assert is_due(r, now) is True                              # never ran
    MemoryManager.touch_routine(r.id, now)
    r = MemoryManager.get_routine(r.id)
    assert is_due(r, now + datetime.timedelta(minutes=29)) is False
    assert is_due(r, now + datetime.timedelta(minutes=30)) is True


def test_disabled_routine_is_never_due():
    r = _routine()
    MemoryManager.set_routine_enabled(r.id, False)
    assert is_due(MemoryManager.get_routine(r.id)) is False


# ── the unattended run ────────────────────────────────────

@pytest.mark.asyncio
async def test_run_executes_auto_tools_and_reports_as_notification():
    _routine(instruction="note the current time")
    provider = FakeProvider([
        LLMReply(text="", tool_calls=[ToolCall(id="c1", name="get_current_time", arguments={})]),
        LLMReply(text="Checked the time; all quiet."),
    ])
    ran = await run_due_routines(provider)
    assert ran == 1
    notifs = MemoryManager.get_notifications()
    assert any(n.category == "routine" and "all quiet" in n.message for n in notifs)
    # the run was consumed
    assert MemoryManager.get_routines()[0].last_run_at is not None


@pytest.mark.asyncio
async def test_unattended_run_cannot_cross_a_tier(tmp_path, monkeypatch):
    """The load-bearing one: a CONFIRM tool called mid-routine is refused by
    the gate (nothing is ever approved unattended), and the file never lands."""
    monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
    target = tmp_path / "evil.txt"
    _routine(instruction="write a file")
    provider = FakeProvider([
        LLMReply(text="", tool_calls=[ToolCall(
            id="w1", name="write_file",
            arguments={"path": str(target), "content": "x"},
        )]),
        LLMReply(text="I couldn't write the file — it needs your approval."),
    ])
    await run_due_routines(provider)
    assert not target.exists()
    refusal = [m for m in provider.requests[1] if m["role"] == "tool"][0]
    assert "requires the user's explicit approval" in refusal["content"]
    notifs = MemoryManager.get_notifications()
    assert any("needs your approval" in n.message for n in notifs)


@pytest.mark.asyncio
async def test_failed_run_is_consumed_not_retried():
    _routine()

    class BoomProvider:
        async def chat(self, *a, **k):
            raise RuntimeError("provider down")

    ran = await run_due_routines(BoomProvider())
    assert ran == 1
    r = MemoryManager.get_routines()[0]
    assert r.last_run_at is not None, "a crash must consume the run (no retry storm)"
    notifs = MemoryManager.get_notifications()
    assert any("error" in n.message for n in notifs)

    # immediately after, nothing is due — no second attempt this window
    assert await run_due_routines(BoomProvider()) == 0


@pytest.mark.asyncio
async def test_over_budget_skips_but_consumes(monkeypatch):
    _routine()
    monkeypatch.setattr(routine_runner, "_over_budget", lambda: True)

    called = []

    class NeverProvider:
        async def chat(self, *a, **k):
            called.append(1)

    ran = await run_due_routines(NeverProvider())
    assert ran == 1 and called == [], "no LLM call on a skipped run"
    notifs = MemoryManager.get_notifications()
    assert any("token budget" in n.message for n in notifs)
    assert MemoryManager.get_routines()[0].last_run_at is not None
