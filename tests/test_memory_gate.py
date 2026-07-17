"""Tests for the MemoryAgent pre-gate (has_self_disclosure).

The gate exists to skip a whole LLM call on exchanges with nothing personal
in them. The dangerous failure is skipping a real disclosure, so the positive
cases here are the load-bearing ones."""
import json
from unittest.mock import patch

import pytest

from app.agents.memory_agent import MemoryAgent, has_self_disclosure
from app.memory.memory_manager import MemoryManager


class CountingProvider:
    """Mock provider that counts generate() calls."""

    def __init__(self, response: str = '{"new_memories": []}'):
        self.response = response
        self.calls = 0

    async def generate(self, messages, **kwargs) -> str:
        self.calls += 1
        return self.response


# ── the heuristic itself ──────────────────────────────────


@pytest.mark.parametrize("msg", [
    "I'm preparing for TCS placements",
    "i am studying at KL University",
    "I use Fedora on my laptop",
    "my name is Mani Chandan",
    "remember that I hate spicy food",
    "I live in Hyderabad",
    "call me Mani",
    "I was born in 2003",
    "note that my favourite editor is vim",
    "don't forget my interview prep plan",
])
def test_disclosures_pass_the_gate(msg):
    assert has_self_disclosure(msg) is True


@pytest.mark.parametrize("msg", [
    "what is my ram usage right now?",
    "list the pending tasks",
    "hi",
    "thanks",
    "search the web for llama 4 benchmarks",
    "open firefox",
    "how much disk space is left?",
    "set volume to 50",
])
def test_plain_commands_and_questions_are_gated(msg):
    assert has_self_disclosure(msg) is False


# ── the gate actually saves the LLM call ──────────────────


@pytest.mark.asyncio
async def test_no_disclosure_means_no_llm_call():
    provider = CountingProvider(json.dumps({
        "new_memories": [{"category": "user_info", "content": "should never be saved here"}]
    }))
    with patch("app.agents.memory_agent.get_llm_provider", return_value=provider):
        conv = MemoryManager.create_conversation()
        MemoryManager.add_message(conv.id, "user", "what's my cpu usage?")
        MemoryManager.add_message(conv.id, "assistant", "CPU is at 12%.")

        saved = await MemoryAgent().extract_and_save_memories(conv.id)

    assert saved == 0
    assert provider.calls == 0  # the whole point: no tokens spent


@pytest.mark.asyncio
async def test_disclosure_still_reaches_the_llm():
    provider = CountingProvider(json.dumps({
        "new_memories": [{"category": "user_info", "content": "Preparing for TCS placements"}]
    }))
    with patch("app.agents.memory_agent.get_llm_provider", return_value=provider):
        conv = MemoryManager.create_conversation()
        MemoryManager.add_message(conv.id, "user", "I'm preparing for TCS placements")
        MemoryManager.add_message(conv.id, "assistant", "Good luck!")

        saved = await MemoryAgent().extract_and_save_memories(conv.id)

    assert provider.calls == 1
    assert saved == 1
