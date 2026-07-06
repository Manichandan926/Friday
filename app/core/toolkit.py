"""
toolkit.py — the tool registry FRIDAY's brain calls during a chat turn.

The LLM decides which tools it needs; execute() runs them and returns plain
text for the model to read. Milestone-1 scope is deliberately limited to
read-only diagnostics plus low-risk, easily-reversible database writes
(tasks, memories, knowledge, projects). File writes, package installs, and
anything harder to undo stay out until the permission-tier system lands.

Every execution is logged — the foundation of the Tier-1 "act, but leave a
trail" contract.
"""
import datetime
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core import tiers, tools as data_tools
from app.core.logger import logger
from app.core.tiers import Tier
from app.llm.types import ToolSpec
from app.memory.memory_manager import MemoryManager

# Filesystem writes are confined to these roots (tests point this at tmp).
ALLOWED_WRITE_ROOTS = [Path.home()]


@dataclass
class Tool:
    spec: ToolSpec
    handler: Callable[..., str]


_REGISTRY: Dict[str, Tool] = {}


def _register(
    name: str,
    description: str,
    params: Optional[Dict[str, Dict[str, Any]]] = None,
    required: Optional[List[str]] = None,
):
    """Decorator: register a handler under a JSON-Schema tool spec."""
    schema: Dict[str, Any] = {"type": "object", "properties": params or {}}
    if required:
        schema["required"] = required

    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        _REGISTRY[name] = Tool(ToolSpec(name, description, schema), fn)
        return fn

    return decorator


def _parse_date(value: Optional[str], label: str) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} must be an ISO date like 2026-07-15 or 2026-07-15T18:00")


# ── system & environment (read-only) ─────────────────────

_register("get_system_info", "Live system stats for this laptop: hostname, OS, kernel, uptime, CPU load, RAM, and disk usage.")(data_tools.get_system_info)
_register("get_top_processes", "The processes currently using the most memory on this laptop.")(data_tools.get_top_processes)
_register("get_battery_info", "Current battery percentage, charging status, and time estimate.")(data_tools.get_battery_info)
_register("get_network_info", "Network interfaces with their IP addresses and up/down state.")(data_tools.get_network_info)
_register("get_temperature_info", "CPU and thermal-zone temperatures.")(data_tools.get_temperature_info)
_register("get_current_time", "The current local date and time. Use this whenever dates or times matter.")(data_tools.get_system_time)


@_register(
    "run_shell",
    "Run a read-only diagnostic shell command on this laptop (ps, df, ls, cat, ip, "
    "journalctl, ...). Commands are validated against a safety whitelist; anything "
    "destructive is blocked. Pipe through head/grep to keep output small.",
    params={"command": {"type": "string", "description": "The shell command to run."}},
    required=["command"],
)
def _run_shell(command: str) -> str:
    from app.core.shell import execute_command
    success, output = execute_command(command)
    return f"$ {command}\n{output}"


# ── personal data (read) ──────────────────────────────────

_register("get_tasks", "The user's task list: pending and completed tasks with priorities and due dates.")(data_tools.get_tasks_summary)
_register("get_emails", "Recent emails synced from the user's Gmail, with priorities and summaries.")(data_tools.get_email_summary)
_register("get_applications", "The user's placement/internship application tracker with pipeline status and deadline risk.")(data_tools.get_applications_summary)
_register("get_deadlines", "Upcoming and overdue application deadlines, sorted by urgency.")(data_tools.get_deadlines_summary)
_register("get_interviews", "Applications currently in the interviewing stage.")(data_tools.get_interviews_summary)
_register("get_knowledge", "Overview of the user's knowledge vault (saved notes by category).")(data_tools.get_knowledge_items)
_register("get_projects", "The user's tracked personal projects with progress.")(data_tools.get_projects_summary)


@_register(
    "search_knowledge",
    "Full-text search the user's knowledge vault for saved notes matching a query.",
    params={"query": {"type": "string", "description": "Search terms."}},
    required=["query"],
)
def _search_knowledge(query: str) -> str:
    results = MemoryManager.search_knowledge(query)
    if not results:
        return f"No knowledge vault entries match '{query}'."
    lines = [f"{len(results)} match(es) for '{query}':"]
    for r in results:
        lines.append(f"- [{r.category}] {r.title}: {r.content[:200]}")
    return "\n".join(lines)


@_register(
    "get_notifications",
    "The user's recent proactive notifications/alerts from FRIDAY's background jobs.",
)
def _get_notifications() -> str:
    notifs = MemoryManager.get_notifications(limit=20)
    if not notifs:
        return "No notifications."
    lines = []
    for n in notifs:
        marker = "unread" if not n.is_read else "read"
        lines.append(f"- [{marker}] {n.title} ({n.created_at.strftime('%b %d %H:%M')}): {n.message}")
    return "\n".join(lines)


# ── personal data (low-risk, reversible writes) ───────────

@_register(
    "add_task",
    "Create a task on the user's task list. Use when the user asks to be reminded "
    "of something or to track a to-do.",
    params={
        "title": {"type": "string", "description": "Short task title."},
        "description": {"type": "string", "description": "Optional details."},
        "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "Defaults to medium."},
        "due_date": {"type": "string", "description": "Optional ISO date/time, e.g. 2026-07-15 or 2026-07-15T18:00."},
    },
    required=["title"],
)
def _add_task(title: str, description: str = None, priority: str = "medium", due_date: str = None) -> str:
    task = MemoryManager.add_task(
        title=title, description=description, priority=priority,
        due_date=_parse_date(due_date, "due_date"),
    )
    due = f", due {task.due_date.strftime('%b %d %H:%M')}" if task.due_date else ""
    return f"Task created: '{task.title}' ({task.priority}{due})."


@_register(
    "complete_task",
    "Mark a task as completed. Get the task id from get_tasks first if unsure.",
    params={"task_id": {"type": "integer", "description": "The task's numeric id."}},
    required=["task_id"],
)
def _complete_task(task_id: int) -> str:
    task = MemoryManager.update_task_status(task_id, "completed")
    return f"Task {task_id} marked completed." if task else f"No task with id {task_id}."


@_register(
    "remember_fact",
    "Save a lasting fact about the user (preference, goal, personal detail) to "
    "long-term memory. Use for things worth knowing in future conversations.",
    params={
        "category": {"type": "string", "description": "e.g. user_info, preference, goal."},
        "content": {"type": "string", "description": "The fact, as one clear sentence."},
    },
    required=["category", "content"],
)
def _remember_fact(category: str, content: str) -> str:
    MemoryManager.add_memory_item(category, content)
    return f"Remembered ({category}): {content}"


@_register(
    "save_knowledge",
    "Save a note to the user's knowledge vault (study notes, snippets, reference material).",
    params={
        "title": {"type": "string", "description": "Note title."},
        "category": {"type": "string", "description": "e.g. aws, dsa, interviews."},
        "content": {"type": "string", "description": "The note body."},
        "tags": {"type": "string", "description": "Optional comma-separated tags."},
    },
    required=["title", "category", "content"],
)
def _save_knowledge(title: str, category: str, content: str, tags: str = None) -> str:
    MemoryManager.add_knowledge_item(title=title, category=category, content=content, tags=tags)
    return f"Saved to knowledge vault: [{category}] {title}"


@_register(
    "add_project",
    "Start tracking a personal project with optional progress percentage.",
    params={
        "name": {"type": "string", "description": "Project name."},
        "description": {"type": "string", "description": "Optional one-line description."},
        "progress": {"type": "integer", "description": "Completion percent 0-100, default 0."},
    },
    required=["name"],
)
def _add_project(name: str, description: str = None, progress: int = 0) -> str:
    MemoryManager.add_project(name=name, description=description, progress=progress)
    return f"Project tracked: {name} ({progress}%)."


@_register(
    "add_application",
    "Track a new placement/internship application for the user.",
    params={
        "company": {"type": "string", "description": "Company name."},
        "role": {"type": "string", "description": "Role/position applied for."},
        "status": {"type": "string", "enum": ["applied", "interviewing", "offer", "rejected"], "description": "Defaults to applied."},
        "deadline": {"type": "string", "description": "Optional ISO deadline date."},
    },
    required=["company", "role"],
)
def _add_application(company: str, role: str, status: str = "applied", deadline: str = None) -> str:
    MemoryManager.add_application(
        company=company, role=role, status=status,
        deadline=_parse_date(deadline, "deadline"),
    )
    return f"Application tracked: {company} — {role} ({status})."


# ── filesystem writes (Tier 2: require approval) ─────────

def _safe_write_path(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not any(path.is_relative_to(root.resolve()) for root in ALLOWED_WRITE_ROOTS):
        allowed = ", ".join(str(r) for r in ALLOWED_WRITE_ROOTS)
        raise ValueError(f"path must be under {allowed}; got {path}")
    return path


@_register(
    "write_file",
    "Write a text file on this laptop (inside the user's home directory only). "
    "Parent directories are created automatically — no need to create them "
    "first. If the file already exists, a timestamped backup is created before "
    "overwriting. The user is asked for approval before this runs.",
    params={
        "path": {"type": "string", "description": "Destination path, e.g. ~/notes/todo.md."},
        "content": {"type": "string", "description": "Full text content to write."},
    },
    required=["path", "content"],
)
def _write_file(path: str, content: str) -> str:
    dest = _safe_write_path(path)
    backup_note = ""
    if dest.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = dest.with_name(f"{dest.name}.bak-{stamp}")
        shutil.copy2(dest, backup)
        backup_note = f" (previous version backed up to {backup})"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    return f"Wrote {len(content)} chars to {dest}{backup_note}."


@_register(
    "create_directory",
    "Create a directory (inside the user's home directory only). Parent "
    "directories are created as needed. The user is asked for approval first.",
    params={"path": {"type": "string", "description": "Directory path, e.g. ~/projects/new."}},
    required=["path"],
)
def _create_directory(path: str) -> str:
    dest = _safe_write_path(path)
    dest.mkdir(parents=True, exist_ok=True)
    return f"Directory ready: {dest}"


# ── file watching (Rust watcher daemon) ───────────────────

_WATCHER_DOWN = (
    "The file watcher daemon isn't running, so file watching is unavailable "
    "right now. It starts automatically with FRIDAY."
)


@_register(
    "watch_directory",
    "Start watching a directory for file changes (created/modified/deleted/"
    "moved). Passive observation only — nothing is touched. Events are "
    "collected in the background and read with get_file_events.",
    params={"path": {"type": "string", "description": "Directory to watch, e.g. ~/Downloads."}},
    required=["path"],
)
def _watch_directory(path: str) -> str:
    from app.core import native_bridge
    reply = native_bridge.watch_directory(str(Path(path).expanduser()))
    return reply.strip() if reply else _WATCHER_DOWN


@_register(
    "unwatch_directory",
    "Stop watching a directory previously added with watch_directory.",
    params={"path": {"type": "string", "description": "Directory to stop watching."}},
    required=["path"],
)
def _unwatch_directory(path: str) -> str:
    from app.core import native_bridge
    reply = native_bridge.unwatch_directory(str(Path(path).expanduser()))
    return reply.strip() if reply else _WATCHER_DOWN


@_register(
    "list_watched_directories",
    "List the directories currently being watched for file changes.",
)
def _list_watched_directories() -> str:
    from app.core import native_bridge
    watches = native_bridge.list_watches()
    if watches is None:
        return _WATCHER_DOWN
    return "\n".join(watches) if watches else "No directories are being watched."


@_register(
    "get_file_events",
    "File changes seen in watched directories since the last check. Reading "
    "consumes the events. Each line: ts=<epoch> action=<created|modified|"
    "deleted|moved_in|moved_out> path=<file>.",
)
def _get_file_events() -> str:
    from app.core import native_bridge
    events = native_bridge.get_file_events()
    if events is None:
        return _WATCHER_DOWN
    return "\n".join(events) if events else "No file events since the last check."


# ── public API ────────────────────────────────────────────

def specs() -> List[ToolSpec]:
    """All tool specs, for handing to the LLM provider."""
    return [t.spec for t in _REGISTRY.values()]


def has_tool(name: str) -> bool:
    return name in _REGISTRY


def execute(name: str, arguments: Optional[Dict[str, Any]], approved: bool = False) -> str:
    """Run a tool by name, enforcing its risk tier. Always returns text
    (errors included) so the model can read and react to failures.

    The chat loop is the normal enforcement point for Tier-2 approval, but
    this gate never trusts the caller: unapproved CONFIRM calls and all
    NEVER calls are refused here regardless of who asks.
    """
    tool = _REGISTRY.get(name)
    if tool is None:
        return f"Unknown tool: {name}"

    tier = tiers.classify(name, arguments)
    if tier == Tier.NEVER:
        logger.warning(f"AUDIT tier=3 tool={name} args={arguments} decision=DENY")
        return (
            "Blocked (Tier 3 — high risk): FRIDAY never executes this kind of "
            "action, even with approval. Explain to the user what you would "
            "recommend and how they could do it manually themselves."
        )
    if tier == Tier.CONFIRM and not approved:
        logger.warning(f"AUDIT tier=2 tool={name} args={arguments} decision=NEEDS_APPROVAL")
        return (
            "Not executed: this action requires the user's explicit approval, "
            "and none was given."
        )

    allowed = tool.spec.input_schema.get("properties", {})
    kwargs = {k: v for k, v in (arguments or {}).items() if k in allowed}
    logger.info(f"AUDIT tier={int(tier)} tool={name} args={kwargs} decision=ALLOW")

    try:
        result = tool.handler(**kwargs)
        return result if isinstance(result, str) else str(result)
    except (TypeError, ValueError) as e:
        return f"Tool '{name}' argument error: {e}"
    except Exception as e:
        logger.error(f"Toolkit: '{name}' failed: {e}")
        return f"Tool '{name}' failed: {e}"
