"""Tests for proactive "speak first" nudges.

The background scheduler queues notifications; on a fresh chat turn FRIDAY
should weave the *unread* ones into the context (a system-role briefing) and
mark exactly those read, so they surface once. These tests drive the real
notifications table (via the test DB) and a scripted FakeProvider that records
the messages it was handed, so we can assert the briefing actually reached the
model — and that an empty queue costs nothing.
"""
from typing import Any, Dict, List, Optional

import pytest

from app.core.assistant import FridayAssistant, pending_nudges_message
from app.llm.provider import LLMProvider
from app.llm.types import LLMReply, ToolSpec
from app.memory.memory_manager import MemoryManager


class FakeProvider(LLMProvider):
    def __init__(self, replies: List[LLMReply]):
        self.replies = list(replies)
        self.requests: List[List[Dict[str, Any]]] = []

    async def generate(self, messages, **kwargs) -> str:
        return (await self.chat(messages, **kwargs)).text

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
    a = FridayAssistant.__new__(FridayAssistant)
    a.provider_name = "fake"
    a.provider = FakeProvider(replies)
    a._pending = {}
    return a


# ── the helper ────────────────────────────────────────────

def test_no_notifications_costs_nothing():
    assert pending_nudges_message(MemoryManager.create_conversation("t").id) is None


def test_briefing_includes_unread_and_marks_them_read():
    conv = MemoryManager.create_conversation("t")
    MemoryManager.add_notification("Battery Critical", "At 8%.", category="system")
    MemoryManager.add_notification("Deadline tomorrow", "ACME role.", category="deadline")

    msg = pending_nudges_message(conv.id)
    assert msg is not None
    assert msg["role"] == "system"
    assert "Battery Critical" in msg["content"]
    assert "Deadline tomorrow" in msg["content"]

    # Surfaced once: a second call has nothing left to say.
    assert pending_nudges_message(conv.id) is None
    assert MemoryManager.get_notifications(unread_only=True) == []


def test_mark_read_by_id_only_touches_those():
    keep = MemoryManager.add_notification("keep", "unread", category="system")
    drop = MemoryManager.add_notification("drop", "read me", category="system")
    MemoryManager.mark_notifications_read([drop.id])
    unread = MemoryManager.get_notifications(unread_only=True)
    assert [n.id for n in unread] == [keep.id]


# ── end-to-end through chat() ─────────────────────────────

@pytest.mark.asyncio
async def test_chat_surfaces_nudges_in_context():
    assistant = _assistant([LLMReply(text="Heads up — plug in, battery's low.")])
    conv = MemoryManager.create_conversation("t")
    MemoryManager.add_notification("Battery Critical", "At 8%.", category="system")

    await assistant.chat(conv.id, "what's up?")

    systems = " ".join(
        m["content"] for m in assistant.provider.requests[0] if m["role"] == "system"
    )
    assert "Battery Critical" in systems


@pytest.mark.asyncio
async def test_nudges_reach_even_a_bare_greeting():
    # A social-only "hi" disables tools, but should still get the briefing.
    assistant = _assistant([LLMReply(text="Hey! Also, your disk is almost full.")])
    conv = MemoryManager.create_conversation("t")
    MemoryManager.add_notification("Disk Space Critical", "At 93%.", category="system")

    await assistant.chat(conv.id, "hi")

    systems = " ".join(
        m["content"] for m in assistant.provider.requests[0] if m["role"] == "system"
    )
    assert "Disk Space Critical" in systems
