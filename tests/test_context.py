"""Tests for the context engine (app/core/context.py) and cost accounting."""
from types import SimpleNamespace

import pytest

from app.core import context
from app.llm import costs
from app.llm.types import Usage


def _mem(category, content):
    return SimpleNamespace(category=category, content=content)


def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


class TestSelectMemories:
    # newest-first, as MemoryManager.get_memory_items() returns them
    MEMORIES = [
        _mem("preference", "Prefers dark roast coffee"),
        _mem("goal", "Preparing for placement interviews at Amazon"),
        _mem("preference", "Likes Rust for systems programming"),
        _mem("profile", "Studies at a college in Vizag"),
    ]

    def test_keyword_overlap_ranks_first(self):
        out = context.select_memories(self.MEMORIES, "how is my rust project going?", limit=2)
        assert out[0].content == "Likes Rust for systems programming"

    def test_no_overlap_falls_back_to_recency(self):
        out = context.select_memories(self.MEMORIES, "hey", limit=2)
        assert [m.content for m in out] == [
            "Prefers dark roast coffee",
            "Preparing for placement interviews at Amazon",
        ]

    def test_limit_is_respected_and_defaults(self):
        many = [_mem("misc", f"fact number {i}") for i in range(30)]
        assert len(context.select_memories(many, "anything")) == context.MEMORY_LIMIT
        assert len(context.select_memories(many, "anything", limit=3)) == 3

    def test_empty_store_is_fine(self):
        assert context.select_memories([], "hello") == []


class TestContextBlock:
    def test_empty_when_nothing_to_say(self):
        assert context.context_block(None, []) == ""

    def test_summary_and_memories_both_render(self):
        block = context.context_block("They discussed Rust.", [_mem("goal", "Ship FRIDAY")])
        assert "summarized" in block
        assert "They discussed Rust." in block
        assert "- [goal] Ship FRIDAY" in block


class TestBuildMessages:
    def test_stable_prefix_then_volatile_then_turns(self):
        msgs = context.build_messages(
            "STATIC", "old stuff", [_mem("goal", "Ship it")], [_msg("user", "hi")]
        )
        assert msgs[0] == {"role": "system", "content": "STATIC"}
        assert msgs[1]["role"] == "system" and "old stuff" in msgs[1]["content"]
        assert msgs[2] == {"role": "user", "content": "hi"}

    def test_no_context_block_when_empty(self):
        msgs = context.build_messages("STATIC", None, [], [_msg("user", "hi")])
        assert [m["role"] for m in msgs] == ["system", "user"]


class _SummaryProvider:
    """Records the generate() request; returns a canned summary."""

    def __init__(self, reply="the new summary"):
        self.reply = reply
        self.requests = []

    async def generate(self, messages, **kwargs):
        self.requests.append(messages)
        return self.reply


@pytest.mark.asyncio
async def test_summarize_folds_old_summary_and_turns():
    provider = _SummaryProvider()
    out = await context.summarize(
        provider, "user likes Rust", [_msg("user", "what about Go?"), _msg("assistant", "Go is fine.")]
    )
    assert out == "the new summary"
    prompt = provider.requests[0]
    assert prompt[0]["role"] == "system"
    body = prompt[1]["content"]
    assert "user likes Rust" in body
    assert "user: what about Go?" in body
    assert "assistant: Go is fine." in body


@pytest.mark.asyncio
async def test_summarize_truncates_huge_messages():
    provider = _SummaryProvider()
    await context.summarize(provider, None, [_msg("user", "x" * 5000)])
    body = provider.requests[0][1]["content"]
    assert "x" * (context.SUMMARY_SNIPPET_CHARS + 1) not in body
    assert "[…]" in body


# ── cost accounting ───────────────────────────────────────

class TestCosts:
    def setup_method(self):
        costs.reset()

    def test_known_model_gets_a_cost_estimate(self):
        cost = costs.estimate_cost("llama-3.3-70b-versatile", Usage(1_000_000, 1_000_000))
        assert cost == pytest.approx(0.59 + 0.79)

    def test_dated_model_id_matches_by_substring(self):
        assert costs.estimate_cost("claude-opus-4-8-20260115", Usage(1000, 0)) is not None

    def test_unknown_model_reports_none(self):
        assert costs.estimate_cost("mystery-model", Usage(1000, 1000)) is None

    def test_session_report_accumulates(self):
        assert "No LLM calls" in costs.session_report()
        costs.record("Groq", "llama-3.3-70b-versatile", Usage(100, 50))
        costs.record("Groq", "llama-3.3-70b-versatile", Usage(200, 100))
        report = costs.session_report()
        assert "2 calls" in report
        assert "300" in report and "150" in report

    def test_unpriced_model_flagged_in_report(self):
        costs.record("Groq", "mystery-model", Usage(10, 10))
        assert "unpriced" in costs.session_report()

    def test_usage_persists_across_a_simulated_restart(self):
        costs.reset()
        costs.record("Groq", "llama-3.3-70b-versatile", Usage(100, 50))
        costs.record("Groq", "llama-3.3-70b-versatile", Usage(200, 100))

        # Simulate a restart: the in-memory session is gone, the DB is not.
        costs.reset()
        assert "No LLM calls" in costs.session_report()  # session view is empty

        all_time = costs.all_time_report()
        assert "llama-3.3-70b-versatile" in all_time
        assert "2 calls" in all_time
        assert "300" in all_time and "150" in all_time  # summed across the two calls
        assert "All-time total" in all_time

    def test_usage_report_shows_session_and_all_time(self):
        costs.reset()
        costs.record("Groq", "llama-3.3-70b-versatile", Usage(10, 5))
        report = costs.usage_report()
        assert "This session" in report
        assert "across restarts" in report  # the persisted section header
