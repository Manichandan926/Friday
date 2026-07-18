"""Phase-2 knowledge search — SQLite FTS5 full-text index over the vault.

MemoryManager.search_knowledge moved from a LIKE substring scan to a BM25-ranked
FTS5 index (app/memory/database.ensure_knowledge_fts), kept in sync by triggers.
These cover: it finds by title/content/tag, prefix-matches, ranks by relevance,
OR-combines terms for recall, stays consistent across insert/update/delete,
handles hostile/empty queries without crashing, and still returns results via
the LIKE fallback when the FTS index is absent."""
import pytest
from sqlalchemy import text

from app.memory import database
from app.memory.memory_manager import MemoryManager as MM


def _add(title, category, content, tags=None):
    return MM.add_knowledge_item(title=title, category=category, content=content, tags=tags)


# ── it actually finds things, across every indexed column ─────────────────

def test_finds_by_title():
    _add("Kubernetes Operators", "aws", "reconcile loops and CRDs", "k8s")
    assert any("Kubernetes" in r.title for r in MM.search_knowledge("kubernetes"))


def test_finds_by_content():
    _add("Ops notes", "aws", "the reconcile loop watches desired state", "k8s")
    assert any(r.title == "Ops notes" for r in MM.search_knowledge("reconcile"))


def test_finds_by_tag():
    _add("Random title", "general", "unrelated body text", "graphql,api")
    assert any("Random title" == r.title for r in MM.search_knowledge("graphql"))


def test_prefix_matching():
    """A search for a stem finds longer tokens: 'sql' → 'SQLAlchemy'."""
    _add("ORM guide", "dsa", "SQLAlchemy session patterns", "python")
    assert any(r.title == "ORM guide" for r in MM.search_knowledge("sql"))


def test_no_match_returns_empty():
    _add("Networking", "aws", "VPC and subnets", "net")
    assert MM.search_knowledge("photosynthesis") == []


# ── ranking + recall ──────────────────────────────────────────────────────

def test_bm25_ranks_more_relevant_first():
    _add("Cooking log", "general", "I once used python at work, briefly")
    _add("Python Generators", "dsa", "python python generators yield lazily")
    results = MM.search_knowledge("python")
    assert results, "expected matches"
    assert results[0].title == "Python Generators"


def test_multi_term_is_or_for_recall():
    _add("Graph theory", "dsa", "adjacency lists and BFS")
    _add("SQL joins", "dsa", "inner and outer joins")
    titles = {r.title for r in MM.search_knowledge("graph sql")}
    assert {"Graph theory", "SQL joins"} <= titles


# ── the index stays in sync with the table (triggers) ─────────────────────

def test_insert_is_searchable_immediately():
    _add("Fresh entry", "ai", "transformers and attention", "ml")
    assert any(r.title == "Fresh entry" for r in MM.search_knowledge("attention"))


def test_update_reindexes_new_terms_and_drops_old():
    item = _add("Draft", "general", "alpha content here", "x")
    with database.get_db_session() as s:
        row = s.get(type(item), item.id)
        row.content = "omega content here"
    assert MM.search_knowledge("alpha") == []
    assert any(r.id == item.id for r in MM.search_knowledge("omega"))


def test_delete_removes_from_index():
    item = _add("Doomed", "general", "ephemeral zebra note", "tmp")
    assert MM.search_knowledge("zebra")
    MM.delete_knowledge_item(item.id)
    assert MM.search_knowledge("zebra") == []


# ── hostile / degenerate queries must not crash ───────────────────────────

@pytest.mark.parametrize("q", ["", "   ", "*", '"', "AND", "OR", "NOT", "c++", "a AND b", "(", "^foo", "-bar"])
def test_hostile_queries_return_a_list_without_raising(q):
    _add("Safe", "general", "some indexed words", "tag")
    result = MM.search_knowledge(q)
    assert isinstance(result, list)


def test_fts_operator_in_query_is_treated_as_a_term_not_syntax():
    """'OR'/'AND' typed by the user are terms to search for, not FTS operators —
    quoting each term guarantees no injection into the MATCH grammar."""
    _add("Boolean logic", "dsa", "truth tables for and or not gates")
    # must not raise, and finds the note that literally contains 'or'
    assert any(r.title == "Boolean logic" for r in MM.search_knowledge("AND"))


# ── graceful degradation: LIKE fallback when the FTS index is gone ─────────

def test_like_fallback_when_fts_index_absent():
    _add("Kubernetes notes", "aws", "pods and services", "k8s")
    with database.engine.begin() as conn:
        for trig in ("knowledge_fts_ai", "knowledge_fts_ad", "knowledge_fts_au"):
            conn.execute(text(f"DROP TRIGGER IF EXISTS {trig}"))
        conn.execute(text("DROP TABLE IF EXISTS knowledge_fts"))
    try:
        results = MM.search_knowledge("kubernetes")
        assert any("Kubernetes" in r.title for r in results), "LIKE fallback should still find it"
    finally:
        database.ensure_knowledge_fts()  # rebuild for the next test


def test_ensure_knowledge_fts_is_idempotent():
    database.ensure_knowledge_fts()
    database.ensure_knowledge_fts()  # second call must be a no-op, not an error
    _add("Idempotent", "general", "still searchable", "ok")
    assert any(r.title == "Idempotent" for r in MM.search_knowledge("searchable"))
