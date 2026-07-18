"""Phase-2 knowledge graph — edges between notes + FTS-based suggestions.

Covers MemoryManager.add_knowledge_link / get_knowledge_links /
suggest_related_knowledge, edge pruning on note delete, and the
link_knowledge / get_related_knowledge tools through the tier gate."""
from app.core import toolkit
from app.memory.memory_manager import MemoryManager as MM


def _k(title, category="dsa", content="body", tags=None):
    return MM.add_knowledge_item(title=title, category=category, content=content, tags=tags)


# ── edges ─────────────────────────────────────────────────────────────────

def test_link_two_notes():
    a, b = _k("A"), _k("B")
    link = MM.add_knowledge_link(a.id, b.id, "prerequisite")
    assert link is not None and link.relation == "prerequisite"


def test_self_link_refused():
    a = _k("A")
    assert MM.add_knowledge_link(a.id, a.id) is None


def test_missing_endpoint_refused():
    a = _k("A")
    assert MM.add_knowledge_link(a.id, 99999) is None
    assert MM.add_knowledge_link(99999, a.id) is None


def test_duplicate_edge_is_idempotent():
    a, b = _k("A"), _k("B")
    first = MM.add_knowledge_link(a.id, b.id, "Related")   # normalized to 'related'
    second = MM.add_knowledge_link(a.id, b.id, "related")
    assert first.id == second.id
    assert len(MM.get_knowledge_links(a.id)) == 1


def test_relation_normalized_and_defaulted():
    a, b = _k("A"), _k("B")
    assert MM.add_knowledge_link(a.id, b.id, "  PART_OF ").relation == "part_of"
    c = _k("C")
    assert MM.add_knowledge_link(a.id, c.id).relation == "related"  # default


def test_links_report_both_directions():
    a, b = _k("A"), _k("B")
    MM.add_knowledge_link(a.id, b.id, "prerequisite")
    a_links = MM.get_knowledge_links(a.id)
    b_links = MM.get_knowledge_links(b.id)
    assert [(o.id, rel, d) for o, rel, d in a_links] == [(b.id, "prerequisite", "→")]
    assert [(o.id, rel, d) for o, rel, d in b_links] == [(a.id, "prerequisite", "←")]


# ── pruning on delete ─────────────────────────────────────────────────────

def test_delete_note_prunes_its_edges():
    a, b, c = _k("A"), _k("B"), _k("C")
    MM.add_knowledge_link(a.id, b.id)   # a as source
    MM.add_knowledge_link(c.id, a.id)   # a as target
    MM.delete_knowledge_item(a.id)
    assert MM.get_knowledge_links(b.id) == []   # edge gone
    assert MM.get_knowledge_links(c.id) == []   # edge gone
    assert MM.get_knowledge_links(a.id) == []   # note gone


# ── FTS-based suggestions ─────────────────────────────────────────────────

def test_suggest_related_uses_content_similarity():
    a = _k("Dijkstra shortest path", content="weighted graph", tags="graph")
    b = _k("BFS traversal", content="graph traversal", tags="graph")
    _k("AWS S3 buckets", category="aws", content="object storage", tags="storage")
    sugg_ids = {s.id for s in MM.suggest_related_knowledge(a.id)}
    assert b.id in sugg_ids            # shares 'graph'
    assert a.id not in sugg_ids        # never suggests itself


def test_suggest_missing_note_is_empty():
    assert MM.suggest_related_knowledge(99999) == []


# ── tools through the gate ────────────────────────────────────────────────

def test_link_and_related_tools_end_to_end():
    a = _k("Graphs 101", content="nodes and edges", tags="graph")
    b = _k("Dijkstra", content="shortest path on a graph", tags="graph")
    c = _k("Trees", content="graph without cycles", tags="graph")

    out = toolkit.execute("link_knowledge",
                          {"source_id": a.id, "target_id": b.id, "relation": "prerequisite"})
    assert "Linked" in out and "prerequisite" in out

    related = toolkit.execute("get_related_knowledge", {"item_id": a.id})
    assert "Linked:" in related and f"(#{b.id})" in related       # explicit edge
    assert "Similar (by content):" in related and f"(#{c.id})" in related  # FTS suggestion
    # b is linked, so it must not be duplicated under Similar
    assert related.count(f"(#{b.id})") == 1


def test_link_tool_reports_bad_ids():
    a = _k("A")
    out = toolkit.execute("link_knowledge", {"source_id": a.id, "target_id": 99999})
    assert "Couldn't link" in out


def test_get_related_empty():
    a = _k("Lonely", content="zxqw unique tokens", tags="none")
    out = toolkit.execute("get_related_knowledge", {"item_id": a.id})
    assert "no links yet" in out
