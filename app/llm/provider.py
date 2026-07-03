"""
LLM provider layer for FRIDAY.

Every provider implements two calls:

    generate(messages, **kwargs) -> str        # plain text completion
    chat(messages, tools, **kwargs) -> LLMReply  # tool-aware chat (agentic loop)

Messages use a neutral dict format so the orchestrator never touches
provider-specific shapes:

    {"role": "system"|"user"|"assistant", "content": "..."}

    # assistant turn produced during a tool round additionally carries:
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...],
     "raw_content": <provider-native content, replayed verbatim>}

    # a tool result:
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}
"""
import abc
import json
from typing import Any, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
import httpx

from app.core.config import settings
from app.core.logger import logger
from app.llm.types import LLMReply, ToolCall, ToolSpec


class ProviderNotConfigured(Exception):
    """Raised when the selected provider has no API key configured."""


# ── neutral → provider-format converters (pure functions, unit-testable) ──

def strip_tool_turns(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Reduce a tool-aware transcript to plain text messages for providers
    (or code paths) that only support simple generation."""
    plain = []
    for m in messages:
        if m["role"] == "tool":
            continue
        if m["role"] == "assistant" and m.get("tool_calls") and not m.get("content"):
            continue
        plain.append({"role": m["role"], "content": m.get("content") or ""})
    return plain


def to_openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m["tool_call_id"],
                "content": m["content"],
            })
        elif role == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": m.get("content") or None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in m["tool_calls"]
                ],
            })
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def to_openai_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def to_anthropic_payload(
    messages: List[Dict[str, Any]],
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Split neutral messages into (system_prompt, anthropic_messages).

    Consecutive tool results are merged into a single user turn — the
    Anthropic API requires all results for parallel tool calls in one message.
    """
    system_parts: List[str] = []
    out: List[Dict[str, Any]] = []
    pending_results: List[Dict[str, Any]] = []

    def flush_results():
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m["role"]
        if role == "system":
            system_parts.append(m["content"])
            continue
        if role == "tool":
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            })
            continue
        flush_results()
        if role == "assistant":
            if m.get("raw_content") is not None:
                # Replay provider-native blocks (thinking/tool_use) verbatim.
                out.append({"role": "assistant", "content": m["raw_content"]})
            elif m.get("tool_calls"):
                blocks: List[Dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                blocks.extend(
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in m["tool_calls"]
                )
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": "assistant", "content": m.get("content") or ""})
        else:
            out.append({"role": "user", "content": m.get("content") or ""})
    flush_results()

    system = "\n\n".join(p for p in system_parts if p) or None
    return system, out


def to_anthropic_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


def to_gemini_payload(
    messages: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split neutral messages into (systemInstruction, gemini contents)."""
    system_parts: List[str] = []
    contents: List[Dict[str, Any]] = []
    pending_results: List[Dict[str, Any]] = []

    def flush_results():
        if pending_results:
            contents.append({"role": "user", "parts": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m["role"]
        if role == "system":
            system_parts.append(m["content"])
            continue
        if role == "tool":
            pending_results.append({
                "functionResponse": {
                    "name": m.get("name", ""),
                    "response": {"result": m["content"]},
                }
            })
            continue
        flush_results()
        if role == "assistant":
            if m.get("raw_content") is not None:
                contents.append({"role": "model", "parts": m["raw_content"]})
            elif m.get("tool_calls"):
                parts: List[Dict[str, Any]] = []
                if m.get("content"):
                    parts.append({"text": m["content"]})
                parts.extend(
                    {"functionCall": {"name": c.name, "args": c.arguments}}
                    for c in m["tool_calls"]
                )
                contents.append({"role": "model", "parts": parts})
            else:
                contents.append({"role": "model", "parts": [{"text": m.get("content") or ""}]})
        else:
            contents.append({"role": "user", "parts": [{"text": m.get("content") or ""}]})
    flush_results()

    system = {"parts": [{"text": "\n\n".join(system_parts)}]} if system_parts else None
    return system, contents


def to_gemini_tools(tools: List[ToolSpec]) -> List[Dict[str, Any]]:
    return [{
        "functionDeclarations": [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in tools
        ]
    }]


# ── providers ─────────────────────────────────────────────

class LLMProvider(abc.ABC):
    @abc.abstractmethod
    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Generate a text completion for the given conversation history.

        Args:
            messages: A list of dicts with 'role' and 'content' keys.
            **kwargs: Extra parameters like 'temperature', 'model', or 'max_tokens'.
        """
        pass

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        **kwargs,
    ) -> LLMReply:
        """Tool-aware chat turn. Default: plain generation, no tool support."""
        text = await self.generate(strip_tool_turns(messages), **kwargs)
        return LLMReply(text=text)


class OpenAICompatibleProvider(LLMProvider):
    """Shared implementation for OpenAI-protocol APIs (OpenAI, Groq)."""

    provider_label = "OpenAI-compatible"

    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        reply = await self.chat(messages, tools=None, **kwargs)
        return reply.text

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        **kwargs,
    ) -> LLMReply:
        params: Dict[str, Any] = {
            "model": kwargs.get("model", self.model),
            "messages": to_openai_messages(messages),
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 1024),
        }
        if tools:
            params["tools"] = to_openai_tools(tools)

        try:
            logger.debug(f"Calling {self.provider_label} API with model: {params['model']}")
            response = await self.client.chat.completions.create(**params)
        except Exception as e:
            logger.error(f"{self.provider_label} API call failed: {e}")
            raise

        msg = response.choices[0].message
        calls: List[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return LLMReply(text=msg.content or "", tool_calls=calls)


class GroqProvider(OpenAICompatibleProvider):
    provider_label = "Groq"

    def __init__(self, api_key: str, model: str):
        super().__init__(
            AsyncOpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key),
            model,
        )


class OpenAIProvider(OpenAICompatibleProvider):
    provider_label = "OpenAI"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        super().__init__(AsyncOpenAI(api_key=api_key), model)


class AnthropicProvider(LLMProvider):
    """Claude via the official Anthropic SDK."""

    # Models that accept adaptive thinking. Fable 5 has thinking always on
    # (the parameter must be omitted), and older/smaller models reject it.
    _ADAPTIVE_THINKING = ("opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6", "sonnet-5")

    def __init__(self, api_key: str, model: str = "claude-opus-4-8"):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        reply = await self.chat(messages, tools=None, **kwargs)
        return reply.text

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        **kwargs,
    ) -> LLMReply:
        model = kwargs.get("model", self.model)
        system, converted = to_anthropic_payload(messages)
        # temperature/top_p deliberately not forwarded — rejected on Opus 4.7+.
        params: Dict[str, Any] = {
            "model": model,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "messages": converted,
        }
        if any(marker in model for marker in self._ADAPTIVE_THINKING):
            params["thinking"] = {"type": "adaptive"}
        if system:
            params["system"] = system
        if tools:
            params["tools"] = to_anthropic_tools(tools)

        try:
            logger.debug(f"Calling Claude API with model: {model}")
            response = await self.client.messages.create(**params)
        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            raise

        if response.stop_reason == "refusal":
            return LLMReply(text="I can't help with that one.")

        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in response.content
            if b.type == "tool_use"
        ]
        # Keep native blocks (incl. thinking) for verbatim replay next turn.
        raw = [b.model_dump() for b in response.content] if calls else None
        return LLMReply(text=text, tool_calls=calls, raw_content=raw)


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model

    async def _post(self, model: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.api_key}"
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        reply = await self.chat(messages, tools=None, **kwargs)
        return reply.text

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        **kwargs,
    ) -> LLMReply:
        model = kwargs.get("model", self.model)
        system, contents = to_gemini_payload(messages)

        payload: Dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = system
        if tools:
            payload["tools"] = to_gemini_tools(tools)

        generation_config = {}
        if "temperature" in kwargs:
            generation_config["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            generation_config["maxOutputTokens"] = kwargs["max_tokens"]
        if generation_config:
            payload["generationConfig"] = generation_config

        try:
            logger.debug(f"Calling Gemini API with model: {model}")
            data = await self._post(model, payload)
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            raise

        parts = data["candidates"][0]["content"].get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        calls = [
            # Gemini has no tool-call ids; synthesize stable ones.
            ToolCall(
                id=f"{p['functionCall']['name']}-{i}",
                name=p["functionCall"]["name"],
                arguments=dict(p["functionCall"].get("args") or {}),
            )
            for i, p in enumerate(parts)
            if "functionCall" in p
        ]
        raw = parts if calls else None
        return LLMReply(text=text, tool_calls=calls, raw_content=raw)


# ── factory ───────────────────────────────────────────────

PROVIDER_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def get_llm_provider(provider_name: Optional[str] = None) -> LLMProvider:
    """Factory to retrieve a configured LLM provider instance."""
    provider = (provider_name or settings.DEFAULT_LLM_PROVIDER).lower()

    key_env = PROVIDER_KEY_ENV.get(provider)
    if key_env is None:
        supported = ", ".join(sorted(set(PROVIDER_KEY_ENV)))
        raise ValueError(f"Unsupported LLM provider: {provider}. Supported: {supported}")

    api_key = getattr(settings, key_env, "")
    if not api_key:
        raise ProviderNotConfigured(
            f"No API key configured for '{provider}'. Add {key_env}=<your key> to the "
            f".env file and restart me, or switch providers with /provider <name>."
        )

    if provider == "groq":
        return GroqProvider(api_key=api_key, model=settings.DEFAULT_LLM_MODEL)
    elif provider == "openai":
        return OpenAIProvider(api_key=api_key)
    elif provider == "gemini":
        return GeminiProvider(api_key=api_key)
    else:  # claude / anthropic
        return AnthropicProvider(api_key=api_key, model=settings.ANTHROPIC_MODEL)
