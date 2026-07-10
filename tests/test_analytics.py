"""Tests for analytics trends/forecasts (app/core/analytics.py).

These drive the real tables through the SQL aggregate helpers on
MemoryManager, seeding tasks and usage records at controlled timestamps so the
trend/forecast arithmetic is deterministic. No LLM, no network.
"""
import datetime

from app.core import analytics, tiers, toolkit
from app.core.tiers import Tier
from app.memory.memory_manager import MemoryManager


def _naive_utc(**delta):
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(**delta)


# ── productivity ──────────────────────────────────────────

def test_no_tasks_reports_nothing_to_trend():
    out = analytics.analyze_productivity()
    assert "No tasks tracked" in out


def test_completion_rate_and_overdue():
    # 3 tasks: 1 completed, 1 pending future, 1 pending overdue.
    MemoryManager.add_task("done thing", "", "medium", None)
    done_id = [t.id for t in MemoryManager.get_tasks() if t.title == "done thing"][0]
    MemoryManager.update_task_status(done_id, "completed")
    MemoryManager.add_task("future", "", "medium", _naive_utc(days=-2))  # due in future
    MemoryManager.add_task("late", "", "high", _naive_utc(days=2))       # due 2d ago

    out = analytics.analyze_productivity(days=7)
    assert "33.3%" in out          # 1 of 3 completed
    assert "Overdue: 1" in out     # the 'late' one


def test_bounded_helper_counts_overdue_only_pending():
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    MemoryManager.add_task("overdue-pending", "", "high", now - datetime.timedelta(days=1))
    overdue = MemoryManager.count_tasks(status="pending", due_before=now)
    assert overdue >= 1


# ── token forecast ────────────────────────────────────────

def test_forecast_with_no_usage():
    out = analytics.forecast_token_usage(daily_limit=100_000, provider="Groq")
    assert "0 / 100,000" in out
    assert "project" in out.lower()


def test_forecast_counts_todays_tokens_case_insensitive():
    MemoryManager.add_usage_record("Groq", "llama", 40_000, 10_000, 0.0, False)
    out = analytics.forecast_token_usage(daily_limit=100_000, provider="groq")  # lowercase
    assert "50,000 / 100,000" in out
    assert "50.0%" in out


def test_forecast_flags_being_over_limit():
    MemoryManager.add_usage_record("Groq", "llama", 90_000, 20_000, 0.0, False)
    out = analytics.forecast_token_usage(daily_limit=100_000)
    assert "over the daily limit" in out


def test_sum_usage_tokens_ignores_other_providers():
    MemoryManager.add_usage_record("OpenAI", "gpt", 5_000, 5_000, 0.0, False)
    tin, tout, calls = MemoryManager.sum_usage_tokens(provider="Groq")
    # OpenAI's 10k must not be counted under Groq
    assert (tin + tout) == 0 and calls == 0


# ── wiring ────────────────────────────────────────────────

def test_analytics_tools_registered_and_auto():
    for name in ("analyze_productivity", "forecast_token_usage"):
        assert name in toolkit._REGISTRY
        assert tiers.classify(name) is Tier.AUTO
