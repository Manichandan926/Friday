"""Task mode (plan → execute → verify) — app/core/task_engine.py plus the
start_task/get_task_status/update_task_step/cancel_task tools.

What matters here: the plan is CONFIRM-gated (one "yes" launches it, nothing
before), steps execute through the normal tool loop with the tier gate intact
(a CONFIRM step pauses the whole task), a blocked step parks the plan for the
user, the per-turn step budget holds, and a restart re-queues in-flight steps
instead of losing or double-running them."""
from typing import Any, Dict, List, Optional

import pytest

from app.core import task_engine, tiers, tool_router, toolkit
from app.core.assistant import FridayAssistant
from app.core.tiers import Tier
from app.llm.provider import LLMProvider
from app.llm.types import LLMReply, ToolCall, ToolSpec
from app.memory.memory_manager import MemoryManager


class FakeProvider(LLMProvider):
    """Returns scripted replies; records every request it receives."""

    def __init__(self, replies: List[LLMReply]):
        self.replies = list(replies)
        self.requests: List[List[Dict[str, Any]]] = []

    async def generate(self, messages, **kwargs) -> str:
        reply = await self.chat(messages, **kwargs)
        return reply.text

    async def chat(self, messages, tools: Optional[List[ToolSpec]] = None, **kwargs) -> LLMReply:
        self.requests.append([dict(m) for m in messages])
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def no_background_memory_agent(monkeypatch):
    async def noop(self, conversation_id):
        return None

    from app.agents.memory_agent import MemoryAgent
    monkeypatch.setattr(MemoryAgent, "extract_and_save_memories", noop)


@pytest.fixture
def write_root(tmp_path, monkeypatch):
    monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
    return tmp_path


def _assistant(replies: List[LLMReply]) -> FridayAssistant:
    assistant = FridayAssistant.__new__(FridayAssistant)
    assistant.provider_name = "fake"
    assistant.provider = FakeProvider(replies)
    assistant._pending = {}
    return assistant


def _plan_call(goal="tidy my notes", steps=("make a list", "save the list")):
    return ToolCall(id="p1", name="start_task",
                    arguments={"goal": goal, "steps": list(steps)})


# ── the gate: nothing runs before the user's yes ──────────

def test_start_task_is_confirm_and_refused_unapproved():
    assert tiers.classify("start_task") == Tier.CONFIRM
    out = toolkit.execute("start_task", {"goal": "g", "steps": ["a"]})
    assert "requires the user's explicit approval" in out
    assert MemoryManager.get_open_plan() is None


def test_helper_tools_are_auto():
    for name in ("get_task_status", "update_task_step", "cancel_task"):
        assert tiers.classify(name) == Tier.AUTO


def test_start_task_validates_arguments():
    bad_empty = toolkit.execute("start_task", {"goal": "g", "steps": []}, approved=True)
    assert "argument error" in bad_empty
    too_many = toolkit.execute(
        "start_task",
        {"goal": "g", "steps": [f"s{i}" for i in range(task_engine.MAX_PLAN_STEPS + 1)]},
        approved=True,
    )
    assert "argument error" in too_many
    assert MemoryManager.get_open_plan() is None


def test_only_one_open_plan_at_a_time():
    toolkit.execute("start_task", {"goal": "first", "steps": ["a"]}, approved=True)
    out = toolkit.execute("start_task", {"goal": "second", "steps": ["b"]}, approved=True)
    assert "already in progress" in out
    assert MemoryManager.get_open_plan().goal == "first"


def test_describe_call_renders_the_numbered_plan():
    text = tiers.describe_call("start_task", {"goal": "g", "steps": ["one", "two"]})
    assert "2-step task" in text and "1) one" in text and "2) two" in text


def test_task_tools_are_routed_for_multistep_phrasing():
    names = tool_router.select_tool_names("set up my notes folder then add an index")
    assert names is not None and "start_task" in names


# ── full flow: propose → approve → execute → verify ───────

@pytest.mark.asyncio
async def test_full_task_flow_happy_path():
    assistant = _assistant([
        LLMReply(text="Here's my plan.", tool_calls=[_plan_call()]),
        LLMReply(text="Task started — on it."),
        LLMReply(text="STEP DONE: made the list"),
        LLMReply(text="STEP DONE: saved the list"),
        LLMReply(text="VERIFIED: both steps check out."),
    ])
    conv = MemoryManager.create_conversation("t")

    proposal = await assistant.chat(conv.id, "tidy my notes: make a list then save it")
    assert "Approval needed" in proposal and "2-step task" in proposal
    assert MemoryManager.get_open_plan() is None, "no plan before approval"

    reply = await assistant.chat(conv.id, "yes")
    assert "✅ Step 1/2" in reply and "✅ Step 2/2" in reply
    assert "🏁 Task complete" in reply and "VERIFIED" in reply

    plan = MemoryManager.get_latest_plan()
    assert plan.status == "completed" and plan.result.startswith("VERIFIED")
    assert [s.status for s in MemoryManager.get_plan_steps(plan.id)] == ["done", "done"]

    # each step ran in its own fresh, minimal context
    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    step_req = fake.requests[2]
    assert step_req[0]["content"] == task_engine.STEP_SYSTEM_PROMPT
    assert "THIS STEP (1 of 2)" in step_req[1]["content"]
    # ...and the second step saw the first step's result
    assert "made the list" in fake.requests[3][1]["content"]


@pytest.mark.asyncio
async def test_blocked_step_parks_the_plan_then_skip_resumes():
    assistant = _assistant([
        LLMReply(text="", tool_calls=[_plan_call(steps=("fetch data", "summarize"))]),
        LLMReply(text="Starting."),
        LLMReply(text="STEP BLOCKED: no network connection"),
    ])
    conv = MemoryManager.create_conversation("t")
    await assistant.chat(conv.id, "fetch then summarize please")
    reply = await assistant.chat(conv.id, "yes")

    assert "❌ Step 1/2 blocked" in reply and "skip" in reply.lower()
    plan = MemoryManager.get_open_plan()
    steps = MemoryManager.get_plan_steps(plan.id)
    assert plan.status == "blocked"
    assert [s.status for s in steps] == ["failed", "pending"]
    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    assert fake.replies == [], "no verification pass for a blocked plan"

    # "skip that step" → model calls update_task_step → plan resumes same turn
    fake.replies.extend([
        LLMReply(text="", tool_calls=[ToolCall(
            id="s1", name="update_task_step",
            arguments={"step_id": steps[0].id, "action": "skip"},
        )]),
        LLMReply(text="Skipped it."),
        LLMReply(text="STEP DONE: summarized what we had"),
        LLMReply(text="VERIFIED: goal met minus the skipped fetch."),
    ])
    reply = await assistant.chat(conv.id, "skip that step")
    assert "✅ Step 2/2" in reply and "🏁" in reply
    plan = MemoryManager.get_latest_plan()
    assert plan.status == "completed"
    assert [s.status for s in MemoryManager.get_plan_steps(plan.id)] == ["skipped", "done"]


@pytest.mark.asyncio
async def test_confirm_step_pauses_task_and_resumes_after_yes(write_root):
    target = write_root / "hello.txt"
    plan = MemoryManager.create_plan("make a hello file", ["write the file"])
    step = MemoryManager.get_plan_steps(plan.id)[0]
    assistant = _assistant([
        LLMReply(text="Working on it."),
        LLMReply(text="", tool_calls=[ToolCall(
            id="w1", name="write_file",
            arguments={"path": str(target), "content": "hello"},
        )]),
        LLMReply(text="STEP DONE: wrote the file"),
        LLMReply(text="VERIFIED: file exists with content."),
    ])
    conv = MemoryManager.create_conversation("t")

    reply = await assistant.chat(conv.id, "get going on the task")
    assert "⏸ Step 1/1 needs a decision" in reply and "Approval needed" in reply
    assert not target.exists(), "CONFIRM step must not run before approval"
    assert assistant._pending[conv.id].step_id == step.id

    reply = await assistant.chat(conv.id, "yes")
    assert target.read_text() == "hello"
    assert "✅ Step 1/1" in reply and "🏁 Task complete" in reply
    assert MemoryManager.get_latest_plan().status == "completed"


@pytest.mark.asyncio
async def test_steps_per_turn_budget_pauses_and_resumes():
    MemoryManager.create_plan("big job", [f"step {i}" for i in range(1, 6)])
    done = lambda i: LLMReply(text=f"STEP DONE: did {i}")  # noqa: E731
    assistant = _assistant([
        LLMReply(text="On it."), done(1), done(2), done(3),
    ])
    conv = MemoryManager.create_conversation("t")

    reply = await assistant.chat(conv.id, "start working")
    assert "pausing here (2 steps left)" in reply
    statuses = [s.status for s in MemoryManager.get_plan_steps(MemoryManager.get_open_plan().id)]
    assert statuses == ["done", "done", "done", "pending", "pending"]

    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    fake.replies.extend([
        LLMReply(text="Continuing."), done(4), done(5),
        LLMReply(text="VERIFIED: all five ran."),
    ])
    reply = await assistant.chat(conv.id, "keep going")
    assert "🏁 Task complete" in reply
    assert MemoryManager.get_latest_plan().status == "completed"


@pytest.mark.asyncio
async def test_restart_requeues_inflight_step():
    # a step left 'running' by a crash/restart (its paused context is gone)
    plan = MemoryManager.create_plan("resume me", ["step a", "step b"])
    steps = MemoryManager.get_plan_steps(plan.id)
    MemoryManager.update_plan_step(steps[0].id, "running")

    assistant = _assistant([  # a fresh assistant = fresh process
        LLMReply(text="Picking the task back up."),
        LLMReply(text="STEP DONE: redid a"),
        LLMReply(text="STEP DONE: did b"),
        LLMReply(text="VERIFIED: complete."),
    ])
    conv = MemoryManager.create_conversation("t")
    reply = await assistant.chat(conv.id, "continue the task")

    assert "✅ Step 1/2" in reply and "🏁 Task complete" in reply
    assert MemoryManager.get_latest_plan().status == "completed"


# ── the small tools and free commands ─────────────────────

def test_cancel_task_and_status_render():
    assert toolkit.execute("cancel_task", {}) == "No task is in progress."
    plan = MemoryManager.create_plan("cancellable", ["a", "b"])
    status = task_engine.render_status()
    assert "cancellable" in status and "0/2 steps done" in status
    out = toolkit.execute("cancel_task", {})
    assert "cancelled" in out
    assert MemoryManager.get_plan(plan.id).status == "cancelled"
    assert "No task is running" in task_engine.render_status()


def test_update_task_step_retry_reactivates_blocked_plan():
    plan = MemoryManager.create_plan("retry me", ["flaky step"])
    step = MemoryManager.get_plan_steps(plan.id)[0]
    MemoryManager.update_plan_step(step.id, "failed", result="boom")
    MemoryManager.set_plan_status(plan.id, "blocked")

    out = toolkit.execute("update_task_step", {"step_id": step.id, "action": "retry"}, approved=True)
    assert "retry" in out
    assert MemoryManager.get_plan(plan.id).status == "active"
    assert MemoryManager.get_plan_step(step.id).status == "pending"


@pytest.mark.asyncio
async def test_slash_task_is_free_and_does_not_touch_pending():
    MemoryManager.create_plan("visible goal", ["only step"])
    assistant = _assistant([])  # any LLM call would pop from an empty list
    conv = MemoryManager.create_conversation("t")
    reply = await assistant.chat(conv.id, "/task")
    assert "visible goal" in reply
