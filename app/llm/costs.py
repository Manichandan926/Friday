"""
costs.py — per-call token/cost accounting (milestone 3).

Every provider reports its response's token usage here. Each call gets one
log line (grep for "LLM usage"), and a session-wide running total backs the
/cost command. Totals are in-memory only — they reset with the process,
which is the honest scope for "what has this session cost me?".
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional

from app.core.logger import logger
from app.llm.types import Usage

# USD per million tokens (input, output). Matched by substring so dated
# model ids ("claude-opus-4-8-20260115") still resolve. Prices drift —
# update this table when providers change theirs; unknown models log
# tokens with no cost estimate rather than a wrong one.
# Note: Groq's free tier bills $0 — entries here are their paid rates,
# so treat Groq costs as "what this would cost if paid".
PRICES: Dict[str, tuple] = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "gpt-4o-mini": (0.15, 0.60),
    "gemini-1.5-flash": (0.075, 0.30),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
}


def estimate_cost(model: str, usage: Usage) -> Optional[float]:
    """USD estimate for one call, or None when the model isn't priced."""
    for key, (in_price, out_price) in PRICES.items():
        if key in model:
            return (usage.input_tokens * in_price + usage.output_tokens * out_price) / 1_000_000
    return None


@dataclass
class _ModelTotal:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    cost_known: bool = True


@dataclass
class _Session:
    by_model: Dict[str, _ModelTotal] = field(default_factory=lambda: defaultdict(_ModelTotal))


_session = _Session()


def record(provider: str, model: str, usage: Usage) -> None:
    """Log one call's usage and fold it into the session totals."""
    cost = estimate_cost(model, usage)
    cost_str = f" cost=${cost:.6f}" if cost is not None else " cost=unknown"
    logger.info(
        f"LLM usage provider={provider} model={model} "
        f"in={usage.input_tokens} out={usage.output_tokens}{cost_str}"
    )
    total = _session.by_model[f"{provider}/{model}"]
    total.calls += 1
    total.input_tokens += usage.input_tokens
    total.output_tokens += usage.output_tokens
    if cost is None:
        total.cost_known = False
    else:
        total.cost += cost


def session_report() -> str:
    """Human-readable session totals for the /cost command."""
    if not _session.by_model:
        return "No LLM calls this session yet."
    lines = ["### Session LLM usage\n"]
    grand_cost = 0.0
    all_known = True
    for name, t in sorted(_session.by_model.items()):
        cost_str = f"${t.cost:.4f}" + ("" if t.cost_known else " (partial — some calls unpriced)")
        lines.append(
            f"- **{name}** — {t.calls} calls, "
            f"{t.input_tokens:,} in / {t.output_tokens:,} out tokens, {cost_str}"
        )
        grand_cost += t.cost
        all_known = all_known and t.cost_known
    suffix = "" if all_known else " (known models only)"
    lines.append(f"\n**Total: ${grand_cost:.4f}**{suffix}")
    lines.append("_Groq free tier bills $0 — its line shows the equivalent paid rate._")
    return "\n".join(lines)


def reset() -> None:
    """Clear session totals (tests)."""
    _session.by_model.clear()
