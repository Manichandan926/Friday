"""
task_engine.py — task mode: plan → execute → verify, surviving restarts.

A plan is rows in SQLite (Plan + PlanStep), created by the CONFIRM-tier
start_task tool — so the whole plan is shown to the user and launched by one
explicit "yes". Execution then happens INSIDE normal chat turns: after each
reply, if a plan is open and nothing is paused waiting on the user, advance()
runs up to MAX_STEPS_PER_TURN steps. Each step is its own small tool loop
with a fresh, token-lean context (goal + prior step results + this step), and
every tool call still passes through toolkit.execute() — the tier gate is
untouched, so a CONFIRM step pauses the whole task for the user's yes exactly
like any other proposal, and a restart merely re-queues the in-flight step
(nothing ran without approval, so a rerun is clean).

When the last step finishes, one tool-free verification pass judges the goal
against the step results before the plan is marked completed.
"""
from typing import List, Optional

from app.core.logger import logger
from app.memory.memory_manager import MemoryManager
from app.memory.models import Plan, PlanStep

# Steps executed per chat turn — bounds worst-case LLM calls (and Groq RPM
# burn) for one user message; the plan continues on the next message.
MAX_STEPS_PER_TURN = 3
# Cap on plan size, enforced by start_task in toolkit.py.
MAX_PLAN_STEPS = 10
# How much of each prior step's result rides in a later step's context.
PRIOR_RESULT_CAP = 280

DONE_MARK = "STEP DONE"
BLOCKED_MARK = "STEP BLOCKED"

STEP_SYSTEM_PROMPT = """\
You are FRIDAY working through one step of a task the user already approved.
Do THIS STEP ONLY, using tools — don't redo earlier steps or jump ahead.
If a tool needs the user's approval, the system pauses and asks them itself;
just call it. Say STEP DONE only when a tool result in THIS step shows the
effect actually happened (a "verified" write, an exit 0, a read-back) — never
on assumption; if the evidence shows failure, that's STEP BLOCKED.
End your reply with exactly one line:
STEP DONE: <one-line summary of what you did or found>
or, only if this step truly cannot be completed:
STEP BLOCKED: <what stopped you>
"""

VERIFY_SYSTEM_PROMPT = """\
You are FRIDAY double-checking your own finished task. Given the goal and
what each step reported, judge honestly whether the goal was achieved.
Start your reply with exactly `VERIFIED:` (goal achieved) or `ISSUES:`
(something is missing or went wrong), then one or two plain sentences.
"""


def _step_messages(plan: Plan, steps: List[PlanStep], step: PlanStep) -> List[dict]:
    """Fresh, minimal context for one step: goal, prior results, the step."""
    prior = [
        f"{s.seq}. [{s.status}] {s.description}"
        + (f" → {(s.result or '')[:PRIOR_RESULT_CAP]}" if s.result else "")
        for s in steps if s.seq < step.seq
    ]
    block = f"TASK GOAL: {plan.goal}\n"
    if prior:
        block += "PROGRESS SO FAR:\n" + "\n".join(prior) + "\n"
    block += f"\nTHIS STEP ({step.seq} of {len(steps)}): {step.description}"
    return [
        {"role": "system", "content": STEP_SYSTEM_PROMPT},
        {"role": "user", "content": block},
    ]


def _conclude(step: PlanStep, text: str, total: int) -> tuple:
    """Mark a step from its loop's final text. Returns (blocked, note_line).

    A missing marker counts as done — models sometimes drop the marker, and
    the verification pass is the net that catches a silently failed step.
    """
    text = text or ""
    if BLOCKED_MARK in text:
        detail = text.split(BLOCKED_MARK, 1)[1].lstrip(": ").strip() or text.strip()
        MemoryManager.update_plan_step(step.id, "failed", result=detail)
        return True, f"❌ Step {step.seq}/{total} blocked: {detail.splitlines()[0][:200]}"
    summary = (text.split(DONE_MARK, 1)[1].lstrip(": ") if DONE_MARK in text else text).strip()
    MemoryManager.update_plan_step(step.id, "done", result=summary or "done")
    short = (summary or "done").splitlines()[0][:160]
    return False, f"✅ Step {step.seq}/{total}: {short}"


def _blocked_help(step: PlanStep) -> str:
    return (
        f"Task paused. Say **skip that step**, **retry it**, or **cancel the "
        f"task** (step id {step.id})."
    )


async def _verify_and_close(assistant, plan: Plan, steps: List[PlanStep]) -> str:
    """One tool-free LLM pass: does the evidence say the goal was achieved?"""
    lines = [
        f"{s.seq}. {s.description} → [{s.status}] {(s.result or '')[:PRIOR_RESULT_CAP]}"
        for s in steps
    ]
    messages = [
        {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
        {"role": "user", "content": f"GOAL: {plan.goal}\nSTEPS:\n" + "\n".join(lines)},
    ]
    try:
        reply = await assistant.provider.chat(messages, tools=None)
        verdict = (reply.text or "").strip() or "VERIFIED: (no detail given)"
    except Exception as e:  # verification must never wedge a finished plan
        logger.warning(f"Task verification failed: {e}")
        verdict = f"(verification skipped: {e})"
    MemoryManager.set_plan_status(plan.id, "completed", result=verdict)
    prefix = ("🏁 Task finished, but with issues" if verdict.startswith("ISSUES")
              else "🏁 Task complete")
    return f"{prefix}: {plan.goal}\n{verdict}"


async def advance(assistant, conversation_id: int,
                  concluded_step_id: Optional[int] = None,
                  last_reply: str = "") -> Optional[str]:
    """Push the open plan forward for this turn. Returns a progress report to
    append to the reply, or None when there's nothing to do.

    concluded_step_id: a step whose paused tool loop was just resolved this
    turn — last_reply is that loop's final text and concludes the step.
    """
    plan = MemoryManager.get_open_plan()
    if plan is None:
        return None

    steps = MemoryManager.get_plan_steps(plan.id)
    total = len(steps)
    notes: List[str] = []

    if concluded_step_id is not None:
        step = next((s for s in steps if s.id == concluded_step_id), None)
        if step is not None and step.status == "running":
            blocked, note = _conclude(step, last_reply, total)
            notes.append(note)
            if blocked:
                MemoryManager.set_plan_status(plan.id, "blocked")
                notes.append(_blocked_help(step))
                return "\n".join(notes)

    # A restart drops the in-memory pause, leaving its step 'running' with no
    # way to conclude — re-queue it (fail-safe: approval-gated work never ran).
    for s in steps:
        if s.status == "running" and s.id != concluded_step_id:
            MemoryManager.update_plan_step(s.id, "pending")

    if plan.status != "active":
        # blocked plans wait for the user's skip/retry/cancel
        return "\n".join(notes) if notes else None

    ran = 0
    while ran < MAX_STEPS_PER_TURN:
        steps = MemoryManager.get_plan_steps(plan.id)
        step = next((s for s in steps if s.status == "pending"), None)
        if step is None:
            notes.append(await _verify_and_close(assistant, plan, steps))
            return "\n".join(notes)

        MemoryManager.update_plan_step(step.id, "running")
        text = await assistant._run_tool_loop(
            conversation_id, _step_messages(plan, steps, step)
        )

        pending = assistant._pending.get(conversation_id)
        if pending is not None:
            # the step's loop paused on a CONFIRM proposal — remember which
            # step it belongs to so its resolution concludes the right step
            pending.step_id = step.id
            notes.append(f"⏸ Step {step.seq}/{total} needs a decision:")
            notes.append(text)
            return "\n".join(notes)

        blocked, note = _conclude(step, text, total)
        notes.append(note)
        if blocked:
            MemoryManager.set_plan_status(plan.id, "blocked")
            notes.append(_blocked_help(step))
            return "\n".join(notes)
        ran += 1

    remaining = sum(
        1 for s in MemoryManager.get_plan_steps(plan.id) if s.status == "pending"
    )
    if remaining:
        notes.append(
            f"…pausing here ({remaining} step{'s' if remaining != 1 else ''} "
            "left) — send any message and I'll keep going."
        )
    return "\n".join(notes) if notes else None


_STATUS_ICONS = {"pending": "⬜", "running": "🔄", "done": "✅",
                 "failed": "❌", "skipped": "⏭️"}


def render_status() -> str:
    """Human-readable state of the open (or most recent) plan. No LLM cost —
    used by the get_task_status tool and the /task command."""
    plan = MemoryManager.get_open_plan()
    if plan is None:
        last = MemoryManager.get_latest_plan()
        if last is None:
            return "No task is running. Ask for something multi-step and I'll plan it."
        return f"No task is running. Last task ({last.status}): {last.goal}"

    steps = MemoryManager.get_plan_steps(plan.id)
    lines = [f"Task ({plan.status}): {plan.goal}"]
    for s in steps:
        line = f"{_STATUS_ICONS.get(s.status, '·')} {s.seq}. {s.description}"
        if s.result and s.status != "pending":
            line += f" — {s.result.splitlines()[0][:120]}"
        lines.append(line)
    done = sum(1 for s in steps if s.status in ("done", "skipped"))
    lines.append(f"{done}/{len(steps)} steps done.")
    return "\n".join(lines)
