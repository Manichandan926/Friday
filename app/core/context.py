"""
context.py — FRIDAY's context engine (milestone 3).

Decides what goes into each LLM call, under two constraints:

1. Token budget: conversation history is a rolling summary plus a short
   verbatim window, and only the memories relevant to the current message
   are injected — not the whole store.
2. Cache friendliness: the prompt is layered stable-to-volatile. The static
   system prompt is byte-identical on every call; everything that changes
   (summary, recalled memories) lives in a separate context block after it,
   so provider-side prompt caching can reuse the stable prefix.
"""
import re
from typing import Any, Dict, List, Optional

# Verbatim turns kept in the prompt; older ones live in the rolling summary.
RECENT_WINDOW = 12
# Unsummarized messages that trigger folding the old ones into the summary.
SUMMARY_TRIGGER = 24
# Max memory items injected per prompt.
MEMORY_LIMIT = 10
# Candidate facts pulled (newest-first) for relevance scoring each turn. The
# scorer is O(candidates), so this caps per-turn recall cost no matter how big
# the store grows — scoring the 500 most-recent facts is ~6ms; scoring all
# 200k+ is ~1.5s. ponytail: recency-capped recall means a highly-relevant but
# very old fact (beyond the newest 500) won't surface. Upgrade path when that
# bites: a keyword index (SQLite FTS5) or embeddings to fetch candidates by
# relevance in SQL instead of by recency.
MEMORY_CANDIDATE_CAP = 500
# A message longer than this is truncated inside the summarization transcript.
SUMMARY_SNIPPET_CHARS = 600

# Words too common to signal relevance when matching memories to a message.
_STOPWORDS = {
    "the", "and", "for", "you", "your", "are", "was", "were", "have", "has",
    "had", "this", "that", "with", "from", "what", "when", "where", "which",
    "who", "how", "why", "can", "could", "would", "should", "will", "just",
    "about", "into", "over", "then", "than", "them", "they", "there", "here",
    "not", "but", "all", "any", "get", "got", "did", "does", "doing", "want",
    "need", "please", "hey", "him", "her", "his", "she", "its", "our", "out",
    "now", "today", "tomorrow", "yesterday", "friday",
}


def _keywords(text: str) -> set:
    return {
        w for w in re.findall(r"[a-z0-9']+", text.lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def select_memories(memories: List[Any], query: str, limit: int = None) -> List[Any]:
    """Pick the memories most relevant to the current message.

    Scoring is keyword overlap between the message and each memory's
    category + content; ties break toward newer items. When nothing
    overlaps (e.g. "hey"), this degrades to the most recent `limit`
    memories — a sane default for a companion that should still know
    the basics. Expects `memories` ordered newest-first, as
    MemoryManager.get_memory_items() returns them.
    """
    if limit is None:
        limit = MEMORY_LIMIT
    q = _keywords(query)
    scored = [
        (len(q & _keywords(f"{m.category} {m.content}")), -i, m)
        for i, m in enumerate(memories)
    ]
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [m for _, _, m in scored[:limit]]


def context_block(summary: Optional[str], memories: List[Any]) -> str:
    """The volatile context layer: rolling summary + recalled memories.

    Kept OUT of the static system prompt so its churn never invalidates
    the cacheable prefix. Empty string when there's nothing to say.
    """
    parts = []
    if summary:
        parts.append(f"Earlier in this conversation (summarized):\n{summary}")
    if memories:
        lines = "\n".join(f"- [{m.category}] {m.content}" for m in memories)
        parts.append(
            f"What you know about your person (verified facts, most relevant first):\n{lines}"
        )
    return "\n\n".join(parts)


def build_messages(
    system_prompt: str,
    summary: Optional[str],
    memories: List[Any],
    recent: List[Any],
) -> List[Dict[str, Any]]:
    """Assemble the neutral message list, stable prefix first."""
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    block = context_block(summary, memories)
    if block:
        messages.append({"role": "system", "content": block})
    messages.extend({"role": m.role, "content": m.content} for m in recent)
    return messages


# ── rolling summary ───────────────────────────────────────

SUMMARY_PROMPT = """\
You maintain the running summary of a conversation between a user and their \
personal assistant. Fold the new turns into the current summary and return \
ONLY the updated summary — no preamble, no commentary.

Keep it under 200 words. Preserve, in order of importance:
- facts about the user and decisions that were made
- open threads: pending questions, promised follow-ups, unfinished tasks
- concrete details (names, dates, numbers, file paths) still likely to matter
Drop pleasantries and anything the new turns made obsolete."""


async def summarize(provider, old_summary: Optional[str], messages: List[Any]) -> str:
    """Fold `messages` (oldest unsummarized turns) into the running summary.

    One cheap text-only LLM call; returns the new summary, or "" on an
    empty reply so callers keep the old one.
    """
    lines = []
    for m in messages:
        content = m.content or ""
        if len(content) > SUMMARY_SNIPPET_CHARS:
            content = content[:SUMMARY_SNIPPET_CHARS] + " […]"
        lines.append(f"{m.role}: {content}")
    transcript = "\n".join(lines)

    text = await provider.generate([
        {"role": "system", "content": SUMMARY_PROMPT},
        {
            "role": "user",
            "content": (
                f"Current summary:\n{old_summary or '(none yet)'}\n\n"
                f"New turns to fold in:\n{transcript}\n\n"
                "Updated summary:"
            ),
        },
    ])
    return (text or "").strip()
