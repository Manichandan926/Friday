"""Tests for the agentic tool loop in FridayAssistant."""
from typing import Any, Dict, List, Optional

import pytest

from app.core import context
from app.core.assistant import (
    APPROVE,
    DECLINE,
    MAX_TOOL_ROUNDS,
    NEW_REQUEST,
    SYSTEM_PROMPT,
    UNCLEAR,
    FridayAssistant,
    interpret_approval_reply,
)
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


class TestInterpretApprovalReply:
    @pytest.mark.parametrize("reply", [
        "yes", "yes.", "yes!", "Yes, go ahead", "yeah", "yep", "sure",
        "sure thing", "ok", "ok do it", "okay!", "go ahead", "do it",
        "approve", "/approve", "confirm", "k",
    ])
    def test_natural_approvals(self, reply):
        assert interpret_approval_reply(reply) == APPROVE

    @pytest.mark.parametrize("reply", [
        "no", "nope", "nah", "no thanks", "skip", "cancel", "don't", "stop it",
        "never mind", "not now",
    ])
    def test_natural_declines(self, reply):
        assert interpret_approval_reply(reply) == DECLINE

    @pytest.mark.parametrize("reply", [
        "maybe", "not sure", "hmm", "wait", "idk", "i guess maybe",
    ])
    def test_noncommittal_is_unclear(self, reply):
        assert interpret_approval_reply(reply) == UNCLEAR

    @pytest.mark.parametrize("reply", [
        "actually, what day is it?", "what time is it", "show me my tasks first",
        "can you explain what that does?",
    ])
    def test_clear_new_requests(self, reply):
        assert interpret_approval_reply(reply) == NEW_REQUEST


@pytest.mark.asyncio
async def test_tier2_natural_yes_variant_executes(write_root):
    # "yeah" used to fail exact-match and silently drop the action.
    target = write_root / "hello.txt"
    assistant = _assistant([
        LLMReply(text="", tool_calls=[_write_call(target)]),
        LLMReply(text="Done."),
    ])
    conv = MemoryManager.create_conversation("t")

    await assistant.chat(conv.id, "make the file")
    reply = await assistant.chat(conv.id, "yeah, go ahead")

    assert reply == "Done."
    assert target.read_text() == "hello"


@pytest.mark.asyncio
async def test_tier2_unclear_reply_reasks_and_keeps_pending(write_root):
    target = write_root / "hello.txt"
    assistant = _assistant([
        LLMReply(text="", tool_calls=[_write_call(target)]),
        LLMReply(text="Done."),
    ])
    conv = MemoryManager.create_conversation("t")

    await assistant.chat(conv.id, "make the file")
    reask = await assistant.chat(conv.id, "hmm, not sure")

    # re-asked, nothing ran, proposal still alive — no silent drop
    assert "yes" in reask.lower() and "no" in reask.lower()
    assert not target.exists()
    assert conv.id in assistant._pending

    # a subsequent clear yes still resolves it
    reply = await assistant.chat(conv.id, "ok yes")
    assert reply == "Done."
    assert target.read_text() == "hello"


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


# ── context engine (milestone 3) ──────────────────────────

@pytest.mark.asyncio
async def test_system_prompt_is_byte_stable_and_memories_ride_separately():
    MemoryManager.add_memory_item("preference", "Likes Rust for systems work")
    assistant = _assistant([LLMReply(text="hi"), LLMReply(text="again")])
    conv = MemoryManager.create_conversation("t")

    await assistant.chat(conv.id, "tell me about rust")
    await assistant.chat(conv.id, "and about go?")

    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    for request in fake.requests:
        # cacheable prefix: first message is the static prompt, untouched
        assert request[0] == {"role": "system", "content": SYSTEM_PROMPT}
    # volatile context block is its own second system message
    second = fake.requests[0][1]
    assert second["role"] == "system"
    assert "Likes Rust for systems work" in second["content"]


@pytest.mark.asyncio
async def test_memory_recall_is_selective_not_everything():
    for i in range(context.MEMORY_LIMIT + 5):
        MemoryManager.add_memory_item("misc", f"unrelated fact number {i}")
    MemoryManager.add_memory_item("preference", "Favorite editor is Neovim")
    assistant = _assistant([LLMReply(text="ok")])
    conv = MemoryManager.create_conversation("t")

    await assistant.chat(conv.id, "which editor do I use, neovim right?")

    fake: FakeProvider = assistant.provider  # type: ignore[assignment]
    block = fake.requests[0][1]["content"]
    assert "Favorite editor is Neovim" in block  # the relevant one made the cut
    injected = [line for line in block.splitlines() if line.startswith("- [")]
    assert len(injected) == context.MEMORY_LIMIT  # ...but not the whole store


@pytest.mark.asyncio
async def test_rolling_summary_folds_old_turns(monkeypatch):
    monkeypatch.setattr(context, "SUMMARY_TRIGGER", 6)
    monkeypatch.setattr(context, "RECENT_WINDOW", 2)
    assistant = _assistant([LLMReply(text="A concise summary of the old turns.")])
    conv = MemoryManager.create_conversation("t")
    for i in range(3):
        MemoryManager.add_message(conv.id, "user", f"question {i}")
        MemoryManager.add_message(conv.id, "assistant", f"answer {i}")

    await assistant._maybe_summarize(conv.id)

    stored = MemoryManager.get_conversation(conv.id)
    assert stored.summary == "A concise summary of the old turns."
    remaining = MemoryManager.get_messages_after(conv.id, stored.summary_until_id)
    assert [m.content for m in remaining] == ["question 2", "answer 2"]

    # the next prompt carries the summary block plus only the verbatim window
    messages = assistant._build_prompt_context(conv.id)
    assert "A concise summary of the old turns." in messages[1]["content"]
    assert [m["content"] for m in messages[2:]] == ["question 2", "answer 2"]


@pytest.mark.asyncio
async def test_summary_not_triggered_below_threshold():
    assistant = _assistant([])  # any LLM call would pop from an empty list
    conv = MemoryManager.create_conversation("t")
    MemoryManager.add_message(conv.id, "user", "hi")

    await assistant._maybe_summarize(conv.id)

    assert MemoryManager.get_conversation(conv.id).summary is None


@pytest.mark.asyncio
async def test_cost_command_reports_session_usage():
    from app.llm import costs
    from app.llm.types import Usage
    costs.reset()
    costs.record("Groq", "llama-3.3-70b-versatile", Usage(120, 40))

    assistant = _assistant([])
    conv = MemoryManager.create_conversation("t")
    reply = await assistant.chat(conv.id, "/cost")

    assert "llama-3.3-70b-versatile" in reply
    assert "120" in reply


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
