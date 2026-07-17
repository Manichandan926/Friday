"""Tests for selective tool exposure (app/core/tool_router.py).

The router is a token optimization with a hard safety property: it must be
fail-open (unknown message → full catalogue) and it must never reference or
orphan a tool. The guardrail tests here mirror the tiers guardrail — you
cannot add a tool without routing it, and you cannot route a ghost."""
import pytest

from app.core import tool_router, toolkit


def _all_registered() -> set:
    return {s.name for s in toolkit.specs()}


def _all_routed() -> set:
    routed = set(tool_router.CORE_TOOLS)
    for _keywords, tools in tool_router.GROUPS.values():
        routed |= tools
    return routed


# ── guardrails (mirror test_tiers.py) ─────────────────────


def test_every_registered_tool_is_routed():
    """A tool missing from every group only ships on fallback-all turns —
    a silent capability downgrade. Adding a tool means routing it."""
    missing = _all_registered() - _all_routed()
    assert not missing, f"registered but in no router group/core: {sorted(missing)}"


def test_router_references_no_ghost_tools():
    """Every name the router knows must actually be registered."""
    ghosts = _all_routed() - _all_registered()
    assert not ghosts, f"routed but not registered: {sorted(ghosts)}"


# ── routing behaviour ─────────────────────────────────────


def test_system_question_routes_to_system_subset():
    names = tool_router.select_tool_names("what is my RAM usage right now?")
    assert names is not None
    assert "get_system_info" in names
    assert "set_volume" not in names
    assert "web_search" not in names
    # and it's actually a saving, not the whole catalogue
    assert len(names) < len(_all_registered()) // 2


def test_core_tools_ride_along_with_any_subset():
    names = tool_router.select_tool_names("check my battery")
    assert names is not None
    assert tool_router.CORE_TOOLS <= names


def test_unmatched_message_fails_open_to_all():
    """No keyword match must mean the FULL catalogue — never an empty set."""
    assert tool_router.select_tool_names("ummm interesting philosophy bro") is None
    specs = tool_router.select_specs("ummm interesting philosophy bro")
    assert {s.name for s in specs} == _all_registered()


def test_empty_message_fails_open():
    assert tool_router.select_tool_names("") is None
    assert tool_router.select_tool_names(None) is None


def test_multiple_groups_union():
    names = tool_router.select_tool_names("remind me to check the battery before my interview")
    assert names is not None
    assert "set_reminder" in names        # tasks
    assert "get_battery_info" in names    # system
    assert "get_interviews" in names      # career


def test_keywords_match_whole_words_only():
    """'ip' must not fire inside 'recipe' — short keywords need boundaries."""
    assert tool_router.select_tool_names("describe that biryani recipe again") is None


def test_select_specs_returns_valid_subset():
    specs = tool_router.select_specs("what's my cpu doing?")
    names = {s.name for s in specs}
    assert names  # non-empty
    assert names <= _all_registered()
    assert "get_top_processes" in names
