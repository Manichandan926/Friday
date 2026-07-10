"""
analytics.py — trends and forecasts over FRIDAY's own persisted data.

The existing summaries (get_tasks, get_deadlines, /cost) answer "what is the
state now?". These answer "which way is it going, and where will it end up?" —
the pattern-recognition / forecasting slice of a JARVIS, done over the tables
FRIDAY already keeps (tasks, usage_records). No new storage, no heavy deps:
aggregation happens in SQL (MemoryManager.count_tasks / sum_usage_tokens), so
this stays cheap even as the tables grow — same scale discipline as the rest
of the read path.

Every function returns a plain string for the model to read.
"""
import datetime

from app.memory.memory_manager import MemoryManager

# Groq's free tier is ~100k tokens/day; the forecast defaults to this but takes
# an override. It's the provider we ship on and the one that actually bites.
DEFAULT_DAILY_TOKEN_LIMIT = 100_000
DEFAULT_FORECAST_PROVIDER = "Groq"


def _now() -> datetime.datetime:
    # Match the scheduler's naive-UTC convention (see CLAUDE.md invariant).
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def analyze_productivity(days: int = 7) -> str:
    """Task throughput, completion rate, overdue load, and the trend in tasks
    created this window vs the one before it."""
    days = max(1, min(int(days), 90))
    now = _now()
    window_start = now - datetime.timedelta(days=days)
    prev_start = now - datetime.timedelta(days=2 * days)

    total = MemoryManager.count_tasks()
    if total == 0:
        return "No tasks tracked yet, so there's no productivity trend to show."

    completed = MemoryManager.count_tasks(status="completed")
    pending = MemoryManager.count_tasks(status="pending")
    overdue = MemoryManager.count_tasks(status="pending", due_before=now)
    created_now = MemoryManager.count_tasks(created_after=window_start)
    created_prev = MemoryManager.count_tasks(
        created_after=prev_start, created_before=window_start
    )

    rate = round(100 * completed / total, 1)
    delta = created_now - created_prev
    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "▬")

    lines = [
        f"Productivity (last {days} day{'s' if days != 1 else ''}):",
        f"- Completion rate: {rate}% ({completed} done / {total} total, {pending} pending)",
        f"- New tasks this window: {created_now} {arrow} "
        f"(vs {created_prev} the prior {days}d, {delta:+d})",
    ]
    if overdue:
        lines.append(f"- ⚠ Overdue: {overdue} pending task(s) past their due date")
    else:
        lines.append("- No overdue tasks — you're on top of due dates")
    return "\n".join(lines)


def forecast_token_usage(daily_limit: int = DEFAULT_DAILY_TOKEN_LIMIT,
                         provider: str = DEFAULT_FORECAST_PROVIDER) -> str:
    """Project today's LLM token burn against the daily limit and estimate when
    (if) the limit will be hit at the current rate."""
    daily_limit = max(1, int(daily_limit))
    now = _now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tin, tout, calls = MemoryManager.sum_usage_tokens(
        created_after=day_start, provider=provider
    )
    used = tin + tout
    pct = round(100 * used / daily_limit, 1)

    elapsed_h = max((now - day_start).total_seconds() / 3600, 0.05)
    rate_per_h = used / elapsed_h
    projected_eod = int(rate_per_h * 24)

    lines = [
        f"{provider} token usage today: {used:,} / {daily_limit:,} ({pct}%) "
        f"across {calls} call(s).",
        f"- Burn rate: ~{int(rate_per_h):,} tokens/hour",
        f"- Projected by end of day: ~{projected_eod:,} tokens",
    ]
    if used >= daily_limit:
        lines.append("- 🔴 Already at/over the daily limit — expect rate-limiting.")
    elif rate_per_h <= 0:
        lines.append("- No usage yet today; nothing to project.")
    else:
        hours_left = (daily_limit - used) / rate_per_h
        eta = now + datetime.timedelta(hours=hours_left)
        if eta.date() > now.date():
            lines.append("- 🟢 At this rate you won't hit the limit today.")
        else:
            lines.append(f"- 🟡 At this rate you'd hit the limit around {eta.strftime('%H:%M')}.")
    return "\n".join(lines)
