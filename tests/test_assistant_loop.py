"""Tests for the agentic tool loop in FridayAssistant."""
from typing import Any, Dict, List, Optional

import pytest

from app.core.assistant import MAX_TOOL_ROUNDS, FridayAssistant
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


def _assistant(replies: List[LLMReply]) -> FridayAssistant:
    assistant = FridayAssistant.__new__(FridayAssistant)
    assistant.provider_name = "fake"
    assistant.provider = FakeProvider(replies)
    assistant._pending = {}
    return assistant


@pytest.mark.asyncio
async def test_plain_reply_round_trip():
    assistant = _assistant([LLMReply(text="Hey! All good here.")])
    conv = MemoryManager.create_conversation("t")

    reply = await assistant.chat(conv.id, "hey FRIDAY")

    assert reply == "Hey! All good here."
    stored = MemoryManager.get_messages(conv.id)
    assert [m.role for m in stored] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_tool_round_feeds_result_back():
    assistant = _assistant([
        LLMReply(text="", tool_calls=[ToolCall(id="c1", name="get_current_time", arguments={})]),
        LLMReply(text="It's late — go to bed."),
    ])
    conv = MemoryManager.create_conversation("t")

    reply = await assistant.chat(conv.id, "what time is it?")

    assert reply == "It's late — go to bed."
    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    assert len(fake.requests) == 2
    second = fake.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert str(__import__("datetime").date.today().year) in tool_msgs[0]["content"]
    # only the final text is persisted, not tool traffic
    stored = MemoryManager.get_messages(conv.id)
    assert [m.role for m in stored] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_unknown_tool_name_surfaces_error_to_model():
    assistant = _assistant([
        LLMReply(text="", tool_calls=[ToolCall(id="c1", name="nonexistent_tool", arguments={})]),
        LLMReply(text="done"),
    ])
    conv = MemoryManager.create_conversation("t")
    await assistant.chat(conv.id, "do the thing")

    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    tool_msgs = [m for m in fake.requests[1] if m["role"] == "tool"]
    assert "Unknown tool" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_tool_loop_budget_is_bounded():
    endless = LLMReply(text="", tool_calls=[ToolCall(id="c", name="get_current_time", arguments={})])
    assistant = _assistant([endless] * (MAX_TOOL_ROUNDS + 1))
    conv = MemoryManager.create_conversation("t")

    reply = await assistant.chat(conv.id, "loop forever")

    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    assert len(fake.requests) == MAX_TOOL_ROUNDS + 1  # initial call + N rounds
    assert reply  # a graceful message, not an exception


# ── Tier-2 approval flow ──────────────────────────────────

def _write_call(path, content="hello"):
    return ToolCall(id="w1", name="write_file", arguments={"path": str(path), "content": content})


@pytest.fixture
def write_root(tmp_path, monkeypatch):
    from app.core import toolkit
    monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
    return tmp_path


@pytest.mark.asyncio
async def test_tier2_call_pauses_for_approval_then_executes(write_root):
    target = write_root / "hello.txt"
    assistant = _assistant([
        LLMReply(text="I'll create that file.", tool_calls=[_write_call(target)]),
        LLMReply(text="Done — file created."),
    ])
    conv = MemoryManager.create_conversation("t")

    proposal = await assistant.chat(conv.id, "make me a hello file")
    assert "Approval needed" in proposal
    assert "I'll create that file." in proposal
    assert not target.exists(), "nothing may run before approval"

    reply = await assistant.chat(conv.id, "yes")
    assert reply == "Done — file created."
    assert target.read_text() == "hello"
    # the executed result was fed back to the model
    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    tool_msgs = [m for m in fake.requests[1] if m["role"] == "tool"]
    assert any("Wrote" in m["content"] for m in tool_msgs)


@pytest.mark.asyncio
async def test_tier2_decline_skips_and_informs_model(write_root):
    target = write_root / "hello.txt"
    assistant = _assistant([
        LLMReply(text="", tool_calls=[_write_call(target)]),
        LLMReply(text="Okay, skipping it."),
    ])
    conv = MemoryManager.create_conversation("t")

    await assistant.chat(conv.id, "make the file")
    reply = await assistant.chat(conv.id, "no")

    assert reply == "Okay, skipping it."
    assert not target.exists()
    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    tool_msgs = [m for m in fake.requests[1] if m["role"] == "tool"]
    assert any("declined" in m["content"] for m in tool_msgs)


@pytest.mark.asyncio
async def test_tier2_decline_with_new_question_carries_the_question(write_root):
    target = write_root / "hello.txt"
    assistant = _assistant([
        LLMReply(text="", tool_calls=[_write_call(target)]),
        LLMReply(text="Sure — it's Thursday."),
    ])
    conv = MemoryManager.create_conversation("t")

    await assistant.chat(conv.id, "make the file")
    reply = await assistant.chat(conv.id, "actually, what day is it?")

    assert reply == "Sure — it's Thursday."
    assert not target.exists()
    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    last_request = fake.requests[1]
    assert any("declined" in m["content"] for m in last_request if m["role"] == "tool")
    assert last_request[-1] == {"role": "user", "content": "actually, what day is it?"}


@pytest.mark.asyncio
async def test_tier3_call_is_refused_inline_without_pause():
    assistant = _assistant([
        LLMReply(text="", tool_calls=[ToolCall(id="c1", name="run_shell", arguments={"command": "rm -rf /"})]),
        LLMReply(text="I won't do that — here's what you can do manually."),
    ])
    conv = MemoryManager.create_conversation("t")

    reply = await assistant.chat(conv.id, "wipe my downloads")

    assert reply == "I won't do that — here's what you can do manually."
    assert not assistant._pending  # no approval offered
    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    tool_msgs = [m for m in fake.requests[1] if m["role"] == "tool"]
    assert "Blocked (Tier 3" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_mixed_batch_runs_tier1_and_holds_tier2(write_root):
    target = write_root / "mix.txt"
    assistant = _assistant([
        LLMReply(text="", tool_calls=[
            ToolCall(id="c1", name="get_current_time", arguments={}),
            _write_call(target),
        ]),
        LLMReply(text="All done."),
    ])
    conv = MemoryManager.create_conversation("t")

    proposal = await assistant.chat(conv.id, "note the time into a file")
    assert "Approval needed" in proposal
    assert not target.exists()
    # Tier-1 result already ran and sits in the paused transcript
    pending = assistant._pending[conv.id]
    tool_msgs = [m for m in pending.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and "c1" == tool_msgs[0]["tool_call_id"]

    reply = await assistant.chat(conv.id, "ok")
    assert reply == "All done."
    assert target.exists()


@pytest.mark.asyncio
async def test_slash_command_does_not_consume_pending_approval(write_root):
    target = write_root / "hold.txt"
    assistant = _assistant([
        LLMReply(text="", tool_calls=[_write_call(target)]),
        LLMReply(text="Created."),
    ])
    conv = MemoryManager.create_conversation("t")

    await assistant.chat(conv.id, "make the file")
    help_reply = await assistant.chat(conv.id, "/help")
    assert "FRIDAY Commands" in help_reply
    assert conv.id in assistant._pending  # proposal still live

    reply = await assistant.chat(conv.id, "/approve")
    assert reply == "Created."
    assert target.exists()


@pytest.mark.asyncio
async def test_slash_help_skips_llm():
    assistant = _assistant([])  # any LLM call would pop from an empty list
    conv = MemoryManager.create_conversation("t")

    reply = await assistant.chat(conv.id, "/help")

    assert "FRIDAY Commands" in reply
    assert "/provider" in reply


@pytest.mark.asyncio
async def test_provider_switch_reports_missing_key(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    assistant = _assistant([])
    conv = MemoryManager.create_conversation("t")
    reply = await assistant.chat(conv.id, "/provider claude")

    assert "ANTHROPIC_API_KEY" in reply
