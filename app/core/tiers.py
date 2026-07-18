"""
tiers.py — FRIDAY's risk-tier permission model, as inspectable config.

Every action the assistant can take belongs to exactly one tier:

    Tier 1  AUTO     low risk, reversible   → act immediately, log it
    Tier 2  CONFIRM  medium risk            → propose, execute only after
                                              the user's explicit "yes"
    Tier 3  NEVER    high risk / hard to    → never executed, even with
            undo                              approval; analysis only

Enforcement happens in two places: the chat loop (which pauses for Tier-2
approval) and toolkit.execute() itself (which refuses unapproved Tier-2 and
all Tier-3 calls regardless of caller). The prompt merely *describes* this;
the code is what guarantees it.
"""
import shlex
from enum import IntEnum
from typing import Any, Dict, Optional

from app.core.shell import has_command_chaining, has_command_substitution, is_command_safe


class Tier(IntEnum):
    AUTO = 1     # act + audit log
    CONFIRM = 2  # propose + wait for explicit approval
    NEVER = 3    # analysis and recommendation only


# ── static tool → tier map ────────────────────────────────
# Every tool registered in app/core/toolkit.py must appear here
# (tests/test_tiers.py enforces that). Unlisted names classify as CONFIRM.

TOOL_TIERS: Dict[str, Tier] = {
    # read-only diagnostics
    "get_system_info": Tier.AUTO,
    "get_top_processes": Tier.AUTO,
    "get_battery_info": Tier.AUTO,
    "get_network_info": Tier.AUTO,
    "get_temperature_info": Tier.AUTO,
    "get_current_time": Tier.AUTO,
    # personal data reads
    "get_tasks": Tier.AUTO,
    "get_emails": Tier.AUTO,
    "get_applications": Tier.AUTO,
    "get_deadlines": Tier.AUTO,
    "get_interviews": Tier.AUTO,
    "get_knowledge": Tier.AUTO,
    "search_knowledge": Tier.AUTO,
    "list_knowledge_topics": Tier.AUTO,
    "browse_knowledge": Tier.AUTO,
    "get_projects": Tier.AUTO,
    "get_notifications": Tier.AUTO,
    # web reads — outbound network, but read-only and non-destructive. See the
    # exfil/injection ceiling noted where these tools are registered; flip
    # either to CONFIRM here if that tradeoff isn't wanted.
    "web_search": Tier.AUTO,
    "fetch_url": Tier.AUTO,
    # analytics — read-only aggregates over our own data
    "analyze_productivity": Tier.AUTO,
    "forecast_token_usage": Tier.AUTO,
    # routines — creating a standing unattended behavior gets one explicit
    # yes; runs themselves execute with nothing approved, so the gate holds
    # them to AUTO tools. Stopping/removing autonomy is always safe.
    "add_routine": Tier.CONFIRM,
    "list_routines": Tier.AUTO,
    "update_routine": Tier.AUTO,
    # task mode — the plan itself is the approval surface: one "yes" on
    # start_task launches autonomous execution, but every step's tool calls
    # still pass this same tier gate individually.
    "start_task": Tier.CONFIRM,
    "get_task_status": Tier.AUTO,
    "update_task_step": Tier.AUTO,  # only redirects an already-approved plan
    "cancel_task": Tier.AUTO,       # stopping is always safe
    # reversible database writes
    "add_task": Tier.AUTO,
    "complete_task": Tier.AUTO,
    "remember_fact": Tier.AUTO,
    "save_knowledge": Tier.AUTO,
    "add_project": Tier.AUTO,
    "add_application": Tier.AUTO,
    # file watching — passive observation via the native watcher daemon
    "watch_directory": Tier.AUTO,
    "unwatch_directory": Tier.AUTO,
    "list_watched_directories": Tier.AUTO,
    "get_file_events": Tier.AUTO,
    # desktop control — reversible, low-harm device/UI state
    "media_control": Tier.AUTO,
    "get_volume": Tier.AUTO,
    "set_volume": Tier.AUTO,
    "toggle_mute": Tier.AUTO,
    "get_brightness": Tier.AUTO,
    "set_brightness": Tier.AUTO,
    "send_notification": Tier.AUTO,
    "take_screenshot": Tier.AUTO,  # additive: writes a fresh timestamped PNG
    # AUTO because capture is gated by GNOME's per-shot portal consent dialog —
    # an OS-enforced click the model cannot talk past (stronger than a chat
    # "yes"). The description comes back untrusted-fenced like web content.
    "look_at_screen": Tier.AUTO,
    "get_clipboard": Tier.AUTO,
    "set_clipboard": Tier.AUTO,
    "calculate": Tier.AUTO,
    "set_reminder": Tier.AUTO,  # just creates a task, like add_task
    # desktop control — launches/opens things → confirm first
    "open_app": Tier.CONFIRM,
    "open_path": Tier.CONFIRM,
    "play_media": Tier.CONFIRM,
    # filesystem writes (backed up on overwrite, home-dir only)
    # read-only, home-fenced, refuses credential-shaped paths inside the tool
    "read_file": Tier.AUTO,
    "write_file": Tier.CONFIRM,
    "edit_file": Tier.CONFIRM,
    "create_directory": Tier.CONFIRM,
    # shell: tier depends on the command — see classify_command()
    "run_shell": Tier.CONFIRM,  # placeholder; classify() overrides dynamically
}

# Whitelisted commands that can change state (write files, install packages,
# push code, manage services). Runnable, but only with user approval.
WRITE_CAPABLE = {
    "touch", "mkdir", "tee",
    "git", "pip", "pip3", "npm", "cargo", "go", "rustc",
    "python", "python3", "node", "java", "javac", "gcc", "g++",
    "systemctl",
}


def classify_command(command: str) -> Tier:
    """Tier a shell command: read-only → AUTO, write-capable-but-whitelisted
    → CONFIRM, blocked patterns or unknown commands → NEVER."""
    if not command or not command.strip():
        return Tier.NEVER

    # Command substitution / expansion means the surface command isn't what
    # runs — the base-command and whitelist checks below would reason about
    # `echo` while `$(...)` executes something else entirely. That's a
    # compromised parsing surface, so it's NEVER, not merely CONFIRM.
    # (is_command_safe() also enforces this; kept explicit here so the tier
    # classifier states the rule itself and doesn't depend on call order.)
    if has_command_substitution(command):
        return Tier.NEVER

    # Same for command chaining: `ls && curl evil` would classify by its first
    # token ('ls' → AUTO) while the chain runs arbitrary commands under
    # shell=True. Refuse the whole thing rather than mis-tier it.
    if has_command_chaining(command):
        return Tier.NEVER

    # Reuse the existing validator (C-accelerated): anything it blocks —
    # dangerous patterns or non-whitelisted commands — is Tier 3.
    safe, _reason = is_command_safe(command)
    if not safe:
        return Tier.NEVER

    stripped = command.strip()

    # Output redirection writes files, whatever the base command is.
    if ">" in stripped:
        return Tier.CONFIRM

    # Check the base command of every pipe segment.
    for segment in stripped.split("|"):
        segment = segment.strip()
        if not segment:
            continue
        try:
            base = shlex.split(segment)[0].split("/")[-1]
        except (ValueError, IndexError):
            return Tier.NEVER
        if base in WRITE_CAPABLE:
            return Tier.CONFIRM

    return Tier.AUTO


def classify(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Tier:
    """Tier a tool call. run_shell is classified per-command; unlisted tool
    names default to CONFIRM (a new tool must earn AUTO explicitly)."""
    if tool_name == "run_shell":
        return classify_command((arguments or {}).get("command", ""))
    return TOOL_TIERS.get(tool_name, Tier.CONFIRM)


def describe_call(tool_name: str, arguments: Optional[Dict[str, Any]]) -> str:
    """One-line human-readable description of a proposed tool call."""
    args = arguments or {}
    if tool_name == "run_shell":
        return f"run shell command: `{args.get('command', '')}`"
    if tool_name == "write_file":
        content = args.get("content", "") or ""
        return f"write file `{args.get('path', '?')}` ({len(content)} chars)"
    if tool_name == "edit_file":
        old = args.get("old_string", "") or ""
        new = args.get("new_string", "") or ""
        return (f"edit file `{args.get('path', '?')}` "
                f"(replace {len(old)} chars with {len(new)})")
    if tool_name == "create_directory":
        return f"create directory `{args.get('path', '?')}`"
    if tool_name == "open_app":
        return f"open the app `{args.get('name', '?')}`"
    if tool_name == "open_path":
        return f"open `{args.get('target', '?')}`"
    if tool_name == "play_media":
        return f"play `{args.get('target', '?')}` in mpv"
    if tool_name == "add_routine":
        when = (f"every day at {args.get('time_of_day', '?')}"
                if args.get("schedule_type") == "daily"
                else f"every {args.get('interval_minutes', '?')} minutes")
        return (f"create the routine '{args.get('name', '?')}' — runs {when}, "
                f"unattended: {args.get('instruction', '?')}")
    if tool_name == "start_task":
        steps = args.get("steps") or []
        lines = [f"start a {len(steps)}-step task: {args.get('goal', '?')}"]
        lines += [f"       {i}) {s}" for i, s in enumerate(steps, 1)]
        return "\n".join(lines)
    rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"{tool_name}({rendered})"
