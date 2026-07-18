"""
routine_runner.py — autonomous routines: FRIDAY working unattended.

A Routine is an instruction the user approved once (add_routine is CONFIRM)
that runs on a schedule with nobody watching. The trust model needs no new
machinery: the runner executes tool calls with approved=False always, so
toolkit.execute() lets AUTO tools work and refuses CONFIRM/NEVER exactly as
it would for a confused model — an unattended run simply cannot cross a tier.
The model is told to report anything it couldn't do instead of retrying.

Each run's report becomes a Notification (category "routine"): it pops as a
desktop toast and surfaces in the next chat via the speak-first briefing.

Guard rails: a due run is CONSUMED (last_run_at set) before anything else, so
a crash costs one run and can never retry-storm; runs are skipped (but still
consumed) when the day's token burn is already near the limit.
"""
import datetime
from typing import List, Optional

from app.core import analytics, desktop, tool_router, toolkit
from app.core.logger import logger
from app.memory.memory_manager import MemoryManager
from app.memory.models import Routine

MAX_ROUTINE_ROUNDS = 6          # same budget as a chat turn
MIN_INTERVAL_MINUTES = 15       # unattended LLM burn needs a floor
MAX_ROUTINES = 10
BUDGET_FRACTION = 0.9           # skip runs past 90% of the daily token limit
REPORT_CAP = 1500

ROUTINE_SYSTEM_PROMPT = """\
You are FRIDAY running a scheduled routine for your user, unattended — they
will read your report later, so write it to be read cold. Use your tools to
do the routine now. Actions needing approval are refused automatically while
nobody is here: don't retry them, just note what you would have done. Finish
with a short plain report of what you found and did (and anything refused).
"""


def _local_now() -> datetime.datetime:
    # Routines are wall-clock concepts ("8am my time") — local naive
    # throughout, self-contained (see the Routine model docstring).
    return datetime.datetime.now()


def is_due(routine: Routine, now: Optional[datetime.datetime] = None) -> bool:
    """Whether this routine should run now. Daily routines catch up: due any
    time after time_of_day on a day they haven't run yet (laptop was off at
    8am → runs on wake)."""
    if not routine.enabled:
        return False
    now = now or _local_now()
    if routine.schedule_type == "daily":
        if not routine.time_of_day:
            return False
        hh, mm = routine.time_of_day.split(":")
        due_time = datetime.time(int(hh), int(mm))
        ran_today = (routine.last_run_at is not None
                     and routine.last_run_at.date() == now.date())
        return now.time() >= due_time and not ran_today
    if routine.schedule_type == "interval":
        if not routine.interval_minutes:
            return False
        if routine.last_run_at is None:
            return True
        elapsed = now - routine.last_run_at
        return elapsed >= datetime.timedelta(minutes=routine.interval_minutes)
    return False


def _over_budget() -> bool:
    day_start = analytics._now().replace(hour=0, minute=0, second=0, microsecond=0)
    tin, tout, _calls = MemoryManager.sum_usage_tokens(
        created_after=day_start, provider=analytics.DEFAULT_FORECAST_PROVIDER
    )
    return (tin + tout) >= BUDGET_FRACTION * analytics.DEFAULT_DAILY_TOKEN_LIMIT


def _notify(title: str, message: str) -> None:
    """DB notification (→ next chat's speak-first briefing) + desktop toast."""
    MemoryManager.add_notification(title=title, message=message, category="routine")
    try:
        desktop.send_notification(title, message[:200])
    except Exception as e:  # a toast failing must not fail the run
        logger.warning(f"Routine toast failed: {e}")


async def _run(routine: Routine, provider) -> str:
    """One headless tool loop. approved is never passed, so the tier gate
    holds CONFIRM/NEVER shut on its own."""
    from app.core.assistant import _cap_tool_result, _result_limit

    messages: List[dict] = [
        {"role": "system", "content": ROUTINE_SYSTEM_PROMPT},
        {"role": "user", "content": f"Routine '{routine.name}': {routine.instruction}"},
    ]
    tools = tool_router.select_specs(routine.instruction)

    reply = await provider.chat(messages, tools=tools)
    for _ in range(MAX_ROUTINE_ROUNDS):
        if not reply.tool_calls:
            break
        messages.append({
            "role": "assistant",
            "content": reply.text,
            "tool_calls": reply.tool_calls,
            "raw_content": reply.raw_content,
        })
        for call in reply.tool_calls:
            result = toolkit.execute(call.name, call.arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": _cap_tool_result(result, _result_limit(call.name)),
            })
        reply = await provider.chat(messages, tools=tools)
    return (reply.text or "(routine produced no report)").strip()


async def run_due_routines(provider) -> int:
    """Run every due routine once. Returns how many ran (or were consumed)."""
    now = _local_now()
    consumed = 0
    for routine in MemoryManager.get_routines(enabled_only=True):
        if not is_due(routine, now):
            continue
        # consume FIRST: a crash below costs this one run, never a retry storm
        MemoryManager.touch_routine(routine.id, now)
        consumed += 1

        if _over_budget():
            logger.warning(f"Routine '{routine.name}' skipped: token budget nearly spent.")
            _notify(f"Routine skipped: {routine.name}",
                    "Today's token budget is nearly spent, so this run was skipped. "
                    "It will run again at its next scheduled time.")
            continue

        try:
            report = await _run(routine, provider)
        except Exception as e:
            logger.error(f"Routine '{routine.name}' failed: {e}")
            report = f"The routine hit an error and did not finish: {e}"
        _notify(f"Routine: {routine.name}", report[:REPORT_CAP])
        logger.info(f"Routine '{routine.name}' ran: {report[:120]}")
    return consumed
