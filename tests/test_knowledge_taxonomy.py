"""Phase-2 knowledge organization — category taxonomy + tagging + browse.

Covers the pure normalizers (app/core/knowledge), that saving normalizes
category/tags, the category/tag listings and exact-tag browse
(MemoryManager), and the browse_knowledge / list_knowledge_topics tools end to
end through the tier gate (toolkit.execute)."""
import pytest

from app.core import toolkit
from app.core.knowledge import (
    CANONICAL_CATEGORIES, normalize_category, normalize_tags, split_tags,
)
from app.memory.memory_manager import MemoryManager as MM


# ── pure normalizers ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("AWS", "aws"), ("  Dsa ", "dsa"), ("project", "projects"),
    ("interview", "career"), ("interviews", "career"), ("ml", "ai"),
    ("cloud", "aws"), ("papers", "research"), ("", "general"), (None, "general"),
    ("quantum-computing", "quantum-computing"),  # unknown kept, lossless
])
def test_normalize_category(raw, expected):
    assert normalize_category(raw) == expected


def test_canonical_categories_are_the_roadmap_set():
    assert set(CANONICAL_CATEGORIES) >= {"aws", "dsa", "ai", "projects", "research", "career"}


@pytest.mark.parametrize("raw,expected", [
    ("Graph, PathFinding", "graph,pathfinding"),
    ("  a , a , b ", "a,b"),                 # trim + dedup
    ("SQL,nosql,SQL", "sql,nosql"),          # case-insensitive dedup, order kept
    ("", None), (None, None), ("  ,  ,", None),
])
def test_normalize_tags(raw, expected):
    assert normalize_tags(raw) == expected


def test_split_tags():
    assert split_tags("A, b ,a") == ["a", "b"]
    assert split_tags(None) == []


# ── normalization happens on save ─────────────────────────────────────────

def test_save_normalizes_category_and_tags():
    k = MM.add_knowledge_item(title="T", category="Interview", content="c",
                              tags="Behavioral, STAR, behavioral")
    assert k.category == "career"
    assert k.tags == "behavioral,star"


# ── listings ──────────────────────────────────────────────────────────────

def test_list_categories_counts_most_populated_first():
    MM.add_knowledge_item(title="a", category="dsa", content="x")
    MM.add_knowledge_item(title="b", category="dsa", content="y")
    MM.add_knowledge_item(title="c", category="aws", content="z")
    cats = dict(MM.list_knowledge_categories())
    assert cats == {"dsa": 2, "aws": 1}
    assert MM.list_knowledge_categories()[0][0] == "dsa"  # most-populated first


def test_list_tags_counts_and_orders():
    MM.add_knowledge_item(title="a", category="dsa", content="x", tags="graph,tree")
    MM.add_knowledge_item(title="b", category="dsa", content="y", tags="graph")
    tags = MM.list_knowledge_tags()
    assert tags[0] == ("graph", 2)           # most-used first
    assert ("tree", 1) in tags


# ── exact-tag browse (no substring bleed) ─────────────────────────────────

def test_get_by_tag_is_exact_not_substring():
    sql = MM.add_knowledge_item(title="SQL", category="dsa", content="joins", tags="sql,db")
    nosql = MM.add_knowledge_item(title="NoSQL", category="aws", content="dynamo", tags="nosql,db")
    got = {it.id for it in MM.get_knowledge_by_tag("sql")}
    assert got == {sql.id}                    # must NOT include the 'nosql' note
    assert {it.id for it in MM.get_knowledge_by_tag("db")} == {sql.id, nosql.id}


def test_get_by_tag_empty_returns_empty():
    MM.add_knowledge_item(title="a", category="dsa", content="x", tags="t")
    assert MM.get_knowledge_by_tag("") == []


# ── tools through the gate ────────────────────────────────────────────────

def test_browse_knowledge_tool_filters_by_category_and_tag():
    MM.add_knowledge_item(title="Dijkstra", category="dsa", content="shortest path", tags="graph")
    MM.add_knowledge_item(title="S3", category="aws", content="object store", tags="storage")

    by_cat = toolkit.execute("browse_knowledge", {"category": "dsa"})
    assert "Dijkstra" in by_cat and "S3" not in by_cat

    by_tag = toolkit.execute("browse_knowledge", {"tag": "storage"})
    assert "S3" in by_tag and "Dijkstra" not in by_tag

    both = toolkit.execute("browse_knowledge", {"category": "aws", "tag": "graph"})
    assert "No knowledge vault entries" in both  # graph note is dsa, not aws


def test_list_knowledge_topics_tool():
    MM.add_knowledge_item(title="a", category="career", content="x", tags="star")
    out = toolkit.execute("list_knowledge_topics", {})
    assert "career" in out and "#star" in out


def test_browse_empty_vault():
    assert "No knowledge vault entries" in toolkit.execute("browse_knowledge", {"category": "dsa"})
