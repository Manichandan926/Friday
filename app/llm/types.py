"""Neutral chat types shared by all LLM providers.

These keep the orchestrator provider-agnostic: the agentic loop only ever
sees ToolSpec / ToolCall / LLMReply, and each provider adapts them to its
own wire format (OpenAI function calling, Anthropic tool use, Gemini
function declarations).
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolSpec:
    """A tool the model may call. input_schema is a JSON Schema object."""
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMReply:
    """One assistant turn: text, requested tool calls, or both.

    raw_content holds the provider-native assistant content when it must be
    replayed verbatim on the next request (e.g. Claude thinking/tool_use
    blocks, Gemini functionCall parts). Providers that don't need this
    leave it as None.
    """
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_content: Any = None
