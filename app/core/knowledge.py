"""Knowledge-vault taxonomy — canonical categories and tag/category cleanup.

The Phase-2 roadmap names a category set (AWS, DSA, AI, Projects, Research,
Career). We steer new notes toward it *without being lossy*: an unknown
category is lowercased and kept, never rejected — the vault should never drop
information because a label wasn't on a list. A small alias map folds the
obvious synonyms ('project' → 'projects', 'interview' → 'career', 'ml' → 'ai').

Tags are stored as a comma-separated string on KnowledgeItem; these helpers are
the one place that string is parsed/produced, so tag semantics stay consistent
across search, browse, and listing.
"""
from typing import List, Optional

# The canonical categories, in the order worth showing them. "general" is the
# catch-all for notes that don't fit — always last.
CANONICAL_CATEGORIES = ("aws", "dsa", "ai", "projects", "research", "career", "general")

_CATEGORY_ALIASES = {
    "project": "projects",
    "interview": "career", "interviews": "career",
    "placement": "career", "placements": "career", "job": "career", "jobs": "career",
    "ml": "ai", "machine-learning": "ai", "machinelearning": "ai", "genai": "ai",
    "algo": "dsa", "algorithms": "dsa", "datastructures": "dsa", "data-structures": "dsa",
    "cloud": "aws",
    "paper": "research", "papers": "research",
}


def normalize_category(raw: Optional[str]) -> str:
    """Lowercase/trim a category and fold known aliases. Empty → 'general'.
    Unknown-but-nonempty categories are kept as-is (lossless)."""
    c = (raw or "").strip().lower()
    if not c:
        return "general"
    return _CATEGORY_ALIASES.get(c, c)


def normalize_tags(raw: Optional[str]) -> Optional[str]:
    """Comma-separated tags → cleaned, lowercased, de-duplicated,
    order-preserving comma string. None/empty → None."""
    if not raw:
        return None
    seen: List[str] = []
    for t in str(raw).split(","):
        t = t.strip().lower()
        if t and t not in seen:
            seen.append(t)
    return ",".join(seen) or None


def split_tags(raw: Optional[str]) -> List[str]:
    """Parse a stored tag string into a clean list (same rules as normalize)."""
    normalized = normalize_tags(raw)
    return normalized.split(",") if normalized else []
