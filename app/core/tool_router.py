"""
Selective tool exposure — pick which tool schemas ride along with a message.

Shipping all ~47 tool specs costs ~3k input tokens on EVERY LLM call; most
turns need a handful. This router matches the user's message against keyword
groups and sends only the relevant subsets (plus a tiny always-on core).

FAIL-OPEN by design: a message that matches no group gets the full catalogue,
so the worst case is exactly the old behaviour — never a lost capability,
only saved tokens on the turns we're sure about.

INVARIANT: every registered tool must appear in CORE_TOOLS or some group —
a guardrail test (tests/test_tool_router.py::test_every_registered_tool_is_routed)
fails otherwise, so a new tool can't silently become fallback-only.
"""
import re
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from app.core import toolkit
from app.llm.types import ToolSpec

# Cheap schemas the model reaches for regardless of topic — always included
# whenever a subset is sent.
CORE_TOOLS: FrozenSet[str] = frozenset({
    "get_current_time", "calculate", "remember_fact",
})

# group name -> (keywords, tool names). Keywords are matched as whole words
# (case-insensitive) so "ip" doesn't fire on "recipe". Multiple groups can
# match; the union is sent. Be generous with keywords — a false positive
# costs a few hundred tokens, a false negative loses a capability this turn.
GROUPS: Dict[str, Tuple[Tuple[str, ...], FrozenSet[str]]] = {
    "system": (
        ("ram", "memory", "cpu", "disk", "storage", "space", "battery",
         "network", "wifi", "internet", "ip", "process", "processes",
         "temperature", "temp", "system", "uptime", "kernel", "slow",
         "usage", "laptop", "shell", "command", "terminal", "run"),
        frozenset({
            "get_system_info", "get_top_processes", "get_battery_info",
            "get_network_info", "get_temperature_info", "run_shell",
        }),
    ),
    "tasks": (
        ("task", "tasks", "todo", "remind", "reminder", "reminders",
         "deadline", "deadlines", "due", "project", "projects", "complete",
         "completed", "done", "pending", "overdue", "productivity",
         "schedule", "plan", "step", "steps", "goal", "then", "cancel",
         "continue", "resume", "status", "skip", "retry", "set up",
         "workflow"),
        frozenset({
            "get_tasks", "add_task", "complete_task", "get_deadlines",
            "set_reminder", "get_projects", "add_project",
            "analyze_productivity", "start_task", "get_task_status",
            "update_task_step", "cancel_task",
        }),
    ),
    "career": (
        ("application", "applications", "apply", "applied", "company",
         "job", "jobs", "interview", "interviews", "placement", "email",
         "emails", "mail", "inbox", "recruiter", "offer", "resume"),
        frozenset({
            "get_applications", "add_application", "get_interviews",
            "get_emails",
        }),
    ),
    "knowledge": (
        ("know", "knowledge", "remember", "recall", "forget", "note",
         "notes", "fact", "facts", "learn", "learned", "memory",
         "notification", "notifications", "alert", "alerts"),
        frozenset({
            "get_knowledge", "search_knowledge", "save_knowledge",
            "remember_fact", "get_notifications",
        }),
    ),
    "web": (
        ("search", "google", "web", "website", "url", "http", "https",
         "online", "news", "latest", "browse", "internet", "wikipedia",
         "look", "weather", "price", "release", "docs", "documentation"),
        frozenset({"web_search", "fetch_url"}),
    ),
    "files": (
        ("file", "files", "folder", "folders", "directory", "directories",
         "write", "save", "create", "watch", "watching", "path",
         "document", "documents", "downloads", "read", "edit", "change",
         "fix", "update", "code", "script", "config", "readme", "log",
         "logs", "line", "lines"),
        frozenset({
            "read_file", "edit_file", "write_file", "create_directory",
            "watch_directory", "unwatch_directory",
            "list_watched_directories", "get_file_events", "open_path",
        }),
    ),
    "desktop": (
        ("volume", "mute", "unmute", "brightness", "screen", "screenshot",
         "clipboard", "copy", "paste", "play", "pause", "skip", "music",
         "song", "video", "media", "open", "launch", "app", "notify",
         "notification", "see", "look", "looking", "watching", "display",
         "window", "dialog", "error"),
        frozenset({
            "media_control", "get_volume", "set_volume", "toggle_mute",
            "get_brightness", "set_brightness", "send_notification",
            "take_screenshot", "look_at_screen", "get_clipboard",
            "set_clipboard", "open_app", "open_path", "play_media",
        }),
    ),
    "analytics": (
        ("token", "tokens", "usage", "quota", "limit", "forecast",
         "trend", "trends", "analytics", "insight", "insights", "stats",
         "statistics", "burn", "productivity"),
        frozenset({"forecast_token_usage", "analyze_productivity"}),
    ),
}

# precompile one word-boundary regex per group
_GROUP_PATTERNS = {
    name: re.compile(r"\b(?:" + "|".join(map(re.escape, keywords)) + r")\b",
                     re.IGNORECASE)
    for name, (keywords, _tools) in GROUPS.items()
}


def select_tool_names(user_message: Optional[str]) -> Optional[Set[str]]:
    """Names of tools relevant to this message, or None meaning 'send all'
    (unknown context / no group matched — fail open)."""
    if not user_message or not user_message.strip():
        return None

    matched: Set[str] = set()
    for name, (_keywords, tools) in GROUPS.items():
        if _GROUP_PATTERNS[name].search(user_message):
            matched |= tools

    if not matched:
        return None
    return matched | CORE_TOOLS


def select_specs(user_message: Optional[str]) -> List[ToolSpec]:
    """Tool specs to hand the provider for this turn (registry order kept)."""
    names = select_tool_names(user_message)
    all_specs = toolkit.specs()
    if names is None:
        return all_specs
    return [s for s in all_specs if s.name in names]
