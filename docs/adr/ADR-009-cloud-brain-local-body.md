# ADR 009: Cloud-Brain / Local-Body (No Local Inference Yet)

## Status
Accepted (2026-07-03, documented 2026-07-06)

## Context
FRIDAY is a personal assistant meant to run continuously on the owner's
laptop: Intel i3 (10th gen), 8GB RAM, no GPU, Fedora KDE. The "brain" is an
LLM. The obvious purist choice is a fully local model (privacy, no API
cost, works offline).

On this hardware, a local model good enough to reliably drive tool calling
does not fit. An 8B model quantized to run in a few GB either fabricates
instead of calling tools (observed directly — see the model-selection note
below) or evicts everything else on a RAM-constrained machine. The assistant
would be worse *and* heavier.

## Decision
Split the system: **cloud brain, local body.**

- **Brain** = a cloud LLM API, reached through a provider-agnostic seam
  (`app/llm/provider.py`). The orchestrator only ever sees neutral
  `ToolSpec`/`ToolCall`/`LLMReply` types; each provider adapts them. Groq
  (free tier), OpenAI, Gemini, and Anthropic are interchangeable at runtime.
- **Body** = everything local and always-on: the Rust watcher, the Python
  orchestrator, SQLite memory, the scheduler. This is where the leanness
  budget is spent, because this is what runs 24/7.

The brain is deliberately swappable so local inference can slot in later
without touching the orchestrator: implement one `LLMProvider` subclass
pointed at a local runtime, and nothing above it changes.

## Consequences
- Prompts leave the machine. Acceptable for this user and use case; the
  design keeps the door open to local inference for when it isn't.
- Cost and latency now matter, which is why the context engine (rolling
  summary, selective memory recall, cache-friendly prompt layout) and
  per-call cost logging exist — see `app/core/context.py`, `app/llm/costs.py`.
- **Model choice within a provider matters as much as the provider.**
  Observed: `llama-3.1-8b-instant` fabricates answers instead of calling
  tools; `llama-3.3-70b-versatile` calls them correctly. Rule of thumb:
  don't drop the default below ~70B-class while tool calling is in the chat
  path. NOTE: the *code* default in `config.py` is still the 8B model —
  a fresh install must set `DEFAULT_LLM_MODEL` in `.env` (the shipped
  `.env.example` should, and this is called out in `docs/SETUP.md`).
- 8GB leanness is treated as a permanent design constraint, not a phase to
  grow out of.
