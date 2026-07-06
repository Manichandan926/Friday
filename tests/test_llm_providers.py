"""Tests for the provider-agnostic tool-calling layer (app/llm/provider.py)."""
import json
from types import SimpleNamespace

import pytest

from app.llm.provider import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    ProviderNotConfigured,
    get_llm_provider,
    strip_tool_turns,
    to_anthropic_payload,
    to_anthropic_tools,
    to_gemini_payload,
    to_gemini_tools,
    to_openai_messages,
    to_openai_tools,
)
from app.llm.types import LLMReply, ToolCall, ToolSpec


WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description="Get the weather.",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)

# A transcript covering: system, user, assistant-with-tool-calls, two tool results.
TRANSCRIPT = [
    {"role": "system", "content": "You are FRIDAY."},
    {"role": "user", "content": "Weather in two cities?"},
    {
        "role": "assistant",
        "content": "Checking.",
        "tool_calls": [
            ToolCall(id="c1", name="get_weather", arguments={"city": "Vizag"}),
            ToolCall(id="c2", name="get_weather", arguments={"city": "Delhi"}),
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "name": "get_weather", "content": "31C sunny"},
    {"role": "tool", "tool_call_id": "c2", "name": "get_weather", "content": "35C hazy"},
]


class TestStripToolTurns:
    def test_drops_tool_messages_and_empty_tool_call_turns(self):
        msgs = TRANSCRIPT + [{"role": "assistant", "content": "", "tool_calls": [ToolCall("x", "y", {})]}]
        plain = strip_tool_turns(msgs)
        assert all(m["role"] in ("system", "user", "assistant") for m in plain)
        assert plain[-1]["content"] == "Checking."  # kept: has text


class TestOpenAIConversion:
    def test_messages(self):
        out = to_openai_messages(TRANSCRIPT)
        assert out[0] == {"role": "system", "content": "You are FRIDAY."}
        asst = out[2]
        assert asst["tool_calls"][0]["function"]["name"] == "get_weather"
        assert json.loads(asst["tool_calls"][0]["function"]["arguments"]) == {"city": "Vizag"}
        assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "31C sunny"}

    def test_tools(self):
        out = to_openai_tools([WEATHER_TOOL])
        assert out[0]["type"] == "function"
        assert out[0]["function"]["parameters"]["required"] == ["city"]


class TestAnthropicConversion:
    def test_system_extracted_and_tool_results_merged(self):
        system, msgs = to_anthropic_payload(TRANSCRIPT)
        assert system == "You are FRIDAY."
        assert msgs[0] == {"role": "user", "content": "Weather in two cities?"}
        # assistant turn carries text + both tool_use blocks
        blocks = msgs[1]["content"]
        assert blocks[0] == {"type": "text", "text": "Checking."}
        assert [b["id"] for b in blocks[1:]] == ["c1", "c2"]
        # both tool results merged into ONE user message
        assert len(msgs) == 3
        results = msgs[2]["content"]
        assert msgs[2]["role"] == "user"
        assert [r["tool_use_id"] for r in results] == ["c1", "c2"]

    def test_raw_content_replayed_verbatim(self):
        raw = [{"type": "thinking", "thinking": "", "signature": "sig"},
               {"type": "tool_use", "id": "c1", "name": "t", "input": {}}]
        _, msgs = to_anthropic_payload([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [ToolCall("c1", "t", {})], "raw_content": raw},
        ])
        assert msgs[1]["content"] is raw

    def test_tools(self):
        out = to_anthropic_tools([WEATHER_TOOL])
        assert out[0] == {
            "name": "get_weather",
            "description": "Get the weather.",
            "input_schema": WEATHER_TOOL.input_schema,
        }


class TestGeminiConversion:
    def test_payload(self):
        system, contents = to_gemini_payload(TRANSCRIPT)
        assert system == {"parts": [{"text": "You are FRIDAY."}]}
        assert contents[1]["role"] == "model"
        parts = contents[1]["parts"]
        assert parts[0] == {"text": "Checking."}
        assert parts[1]["functionCall"]["args"] == {"city": "Vizag"}
        # both function responses merged into one user turn
        assert len(contents) == 3
        assert [p["functionResponse"]["name"] for p in contents[2]["parts"]] == ["get_weather", "get_weather"]

    def test_tools(self):
        out = to_gemini_tools([WEATHER_TOOL])
        assert out[0]["functionDeclarations"][0]["name"] == "get_weather"


# ── provider response parsing (stubbed clients, no network) ──

def _openai_style_response(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.mark.asyncio
async def test_openai_compatible_chat_parses_tool_calls():
    captured = {}

    async def fake_create(**params):
        captured.update(params)
        tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="get_weather", arguments='{"city": "Vizag"}'),
        )
        return _openai_style_response(content=None, tool_calls=[tc])

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    provider.model = "test-model"

    reply = await provider.chat(
        [{"role": "user", "content": "weather?"}], tools=[WEATHER_TOOL]
    )
    assert captured["tools"][0]["function"]["name"] == "get_weather"
    assert reply.tool_calls == [ToolCall(id="call_1", name="get_weather", arguments={"city": "Vizag"})]
    assert reply.text == ""


class _Block(SimpleNamespace):
    def model_dump(self):
        return dict(vars(self))


@pytest.mark.asyncio
async def test_anthropic_chat_parses_blocks_and_keeps_raw():
    captured = {}

    async def fake_create(**params):
        captured.update(params)
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _Block(type="text", text="On it."),
                _Block(type="tool_use", id="toolu_1", name="get_weather", input={"city": "Vizag"}),
            ],
        )

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    provider.model = "claude-opus-4-8"

    reply = await provider.chat(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "weather?"}],
        tools=[WEATHER_TOOL],
    )
    assert captured["system"] == "sys"
    assert captured["thinking"] == {"type": "adaptive"}
    assert "temperature" not in captured
    assert reply.text == "On it."
    assert reply.tool_calls == [ToolCall(id="toolu_1", name="get_weather", arguments={"city": "Vizag"})]
    assert reply.raw_content is not None  # native blocks kept for replay


@pytest.mark.asyncio
async def test_anthropic_refusal_returns_safe_text():
    async def fake_create(**params):
        return SimpleNamespace(stop_reason="refusal", content=[])

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    provider.model = "claude-opus-4-8"

    reply = await provider.chat([{"role": "user", "content": "x"}])
    assert reply.text
    assert not reply.tool_calls


@pytest.mark.asyncio
async def test_openai_compatible_chat_parses_usage():
    from app.llm import costs
    costs.reset()

    async def fake_create(**params):
        resp = _openai_style_response(content="hi", tool_calls=None)
        resp.usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30)
        return resp

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    provider.model = "llama-3.3-70b-versatile"

    reply = await provider.chat([{"role": "user", "content": "hey"}])
    assert reply.usage.input_tokens == 120
    assert reply.usage.output_tokens == 30
    assert "llama-3.3-70b-versatile" in costs.session_report()


@pytest.mark.asyncio
async def test_anthropic_chat_parses_usage():
    from app.llm import costs
    costs.reset()

    async def fake_create(**params):
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[_Block(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=200, output_tokens=50),
        )

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=fake_create))
    provider.model = "claude-opus-4-8"

    reply = await provider.chat([{"role": "user", "content": "hey"}])
    assert reply.usage.input_tokens == 200
    assert reply.usage.output_tokens == 50


@pytest.mark.asyncio
async def test_missing_usage_leaves_reply_usage_none():
    async def fake_create(**params):
        return _openai_style_response(content="hi", tool_calls=None)

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    provider.model = "test-model"

    reply = await provider.chat([{"role": "user", "content": "hey"}])
    assert reply.usage is None


class TestFactory:
    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError):
            get_llm_provider("skynet")

    def test_missing_key_raises_provider_not_configured(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
        with pytest.raises(ProviderNotConfigured) as exc:
            get_llm_provider("claude")
        assert "ANTHROPIC_API_KEY" in str(exc.value)

    def test_claude_provider_constructed(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
        provider = get_llm_provider("claude")
        assert isinstance(provider, AnthropicProvider)
        assert provider.model == settings.ANTHROPIC_MODEL
