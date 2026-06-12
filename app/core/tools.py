"""
Built-in tools that return real system/database data.
Each tool returns a plain-text string that gets injected into LLM context.
"""
import os
import json
import datetime
from app.memory.memory_manager import MemoryManager
from app.core.logger import logger


def get_system_info() -> str:
    """Read actual system stats from /proc and os."""
    lines = []

    try:
        with open("/etc/hostname", "r") as f:
            lines.append(f"Hostname: {f.read().strip()}")
    except Exception:
        pass

    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    lines.append(f"OS: {line.split('=', 1)[1].strip().strip('\"')}")
                    break
    except Exception:
        pass

    try:
        with open("/proc/version", "r") as f:
            version_str = f.read().strip().split()[2]
            lines.append(f"Kernel: {version_str}")
    except Exception:
        pass

    try:
        with open("/proc/uptime", "r") as f:
            secs = int(float(f.read().split()[0]))
            h, m = divmod(secs // 60, 60)
            d, h = divmod(h, 24)
            lines.append(f"Uptime: {d}d {h}h {m}m")
    except Exception:
        pass

    try:
        with open("/proc/loadavg", "r") as f:
            load1 = f.read().split()[0]
        cores = os.cpu_count() or 1
        pct = min(round((float(load1) / cores) * 100, 1), 100.0)
        lines.append(f"CPU: {cores} cores, load {load1} ({pct}%)")
    except Exception:
        pass

    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        total_mb = meminfo["MemTotal"] // 1024
        avail_mb = meminfo["MemAvailable"] // 1024
        used_mb = total_mb - avail_mb
        lines.append(f"RAM: {used_mb} MB / {total_mb} MB ({round(used_mb/total_mb*100, 1)}%)")
    except Exception:
        pass

    try:
        st = os.statvfs("/")
        total_gb = round((st.f_blocks * st.f_frsize) / (1024**3), 1)
        free_gb = round((st.f_bavail * st.f_frsize) / (1024**3), 1)
        used_gb = round(total_gb - free_gb, 1)
        lines.append(f"Disk (/): {used_gb} GB / {total_gb} GB ({round(used_gb/total_gb*100, 1)}%)")
    except Exception:
        pass

    return "\n".join(lines) if lines else "Could not read system info."


def get_email_summary() -> str:
    """Pull real emails from the database."""
    emails = MemoryManager.get_emails()
    if not emails:
        return "No emails in database. Gmail sync runs every 5 minutes automatically."

    lines = [f"Total emails stored: {len(emails)}"]
    high = [e for e in emails if e.priority == "high"]
    med = [e for e in emails if e.priority == "medium"]
    if high:
        lines.append(f"High priority: {len(high)}")
    if med:
        lines.append(f"Medium priority: {len(med)}")
    lines.append("")

    for e in emails[:5]:
        lines.append(f"- [{e.priority.upper()}] {e.subject}")
        lines.append(f"  From: {e.sender}")
        lines.append(f"  Summary: {e.body_summary}")
        lines.append("")

    return "\n".join(lines)


def get_tasks_summary() -> str:
    """Pull real tasks from the database."""
    pending = MemoryManager.get_tasks(status="pending")
    completed = MemoryManager.get_tasks(status="completed")

    if not pending and not completed:
        return "No tasks in database."

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    lines = [f"Pending: {len(pending)} | Completed: {len(completed)}"]

    if pending:
        lines.append("")
        for t in pending:
            due_str = ""
            if t.due_date:
                delta = t.due_date - now
                if delta.total_seconds() < 0:
                    due_str = " [OVERDUE]"
                elif delta.days <= 2:
                    due_str = f" [Due in {delta.days}d {delta.seconds//3600}h]"
                else:
                    due_str = f" [Due: {t.due_date.strftime('%b %d')}]"
            lines.append(f"- [{t.priority.upper()}] {t.title}{due_str}")

    return "\n".join(lines)


def get_applications_summary() -> str:
    """Pull real placement applications with analytics and deadline risk."""
    apps = MemoryManager.get_applications()
    if not apps:
        return "No placement applications tracked yet."

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # analytics breakdown
    by_status = {}
    for a in apps:
        by_status.setdefault(a.status, []).append(a)

    lines = [f"Total applications: {len(apps)}"]
    lines.append("Pipeline breakdown:")
    for status in ["applied", "interviewing", "offer", "rejected"]:
        count = len(by_status.get(status, []))
        if count:
            bar = "█" * count
            lines.append(f"  {status.capitalize():14s} {count:3d}  {bar}")
    lines.append("")

    # deadline risk detection
    critical = []
    upcoming = []
    for a in apps:
        if a.deadline and a.status not in ("rejected", "offer"):
            delta = a.deadline - now
            days_left = delta.days
            if days_left < 0:
                critical.append(f"  ⚠ {a.company} | {a.role} — DEADLINE PASSED ({abs(days_left)}d ago)")
            elif days_left <= 3:
                critical.append(f"  ⚠ {a.company} | {a.role} — {days_left}d remaining — CRITICAL")
            elif days_left <= 7:
                upcoming.append(f"  → {a.company} | {a.role} — {days_left}d remaining")

    if critical:
        lines.append("CRITICAL DEADLINES:")
        lines.extend(critical)
        lines.append("")

    if upcoming:
        lines.append("Upcoming deadlines:")
        lines.extend(upcoming)
        lines.append("")

    # full list
    lines.append("All applications:")
    for a in apps:
        deadline_str = f" (Deadline: {a.deadline.strftime('%b %d')})" if a.deadline else ""
        lines.append(f"  - {a.company} | {a.role} | {a.status.upper()}{deadline_str}")

    return "\n".join(lines)


def get_deadlines_summary() -> str:
    """Show only applications with upcoming or passed deadlines."""
    apps = MemoryManager.get_applications()
    if not apps:
        return "No applications tracked."

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    with_deadlines = [a for a in apps if a.deadline and a.status not in ("rejected", "offer")]

    if not with_deadlines:
        return "No active deadlines."

    with_deadlines.sort(key=lambda a: a.deadline)
    lines = ["Application deadlines (sorted by urgency):"]
    lines.append("")

    for a in with_deadlines:
        delta = a.deadline - now
        days = delta.days
        if days < 0:
            tag = f"PASSED ({abs(days)}d ago)"
        elif days == 0:
            tag = "TODAY"
        elif days <= 3:
            tag = f"CRITICAL ({days}d left)"
        elif days <= 7:
            tag = f"Soon ({days}d left)"
        else:
            tag = f"{days}d left"
        lines.append(f"  [{tag}] {a.company} — {a.role} ({a.deadline.strftime('%b %d')})")

    return "\n".join(lines)


def get_interviews_summary() -> str:
    """Show only applications in interviewing status."""
    apps = MemoryManager.get_applications(status="interviewing")
    if not apps:
        return "No active interviews."

    lines = [f"Active interviews: {len(apps)}"]
    lines.append("")
    for a in apps:
        dl = f" (Deadline: {a.deadline.strftime('%b %d')})" if a.deadline else ""
        lines.append(f"  - {a.company} | {a.role}{dl}")
    return "\n".join(lines)


def get_memory_facts() -> str:
    """Pull stored memory facts."""
    memories = MemoryManager.get_memory_items()
    if not memories:
        return "No facts stored yet."

    lines = []
    for m in memories:
        lines.append(f"- [{m.category}] {m.content}")
    return "\n".join(lines)


def get_knowledge_items() -> str:
    """Pull stored knowledge vault items."""
    items = MemoryManager.get_knowledge_items()
    if not items:
        return "Knowledge vault is empty."

    lines = [f"Knowledge items: {len(items)}"]
    by_cat = {}
    for k in items:
        by_cat.setdefault(k.category, []).append(k)

    for cat, entries in by_cat.items():
        lines.append(f"\n[{cat.upper()}]")
        for e in entries[:5]:
            preview = e.content[:120] + "..." if len(e.content) > 120 else e.content
            lines.append(f"  - {e.title}: {preview}")
    return "\n".join(lines)


def get_projects_summary() -> str:
    """Pull tracked projects with progress."""
    projects = MemoryManager.get_projects()
    if not projects:
        return "No projects tracked."

    lines = [f"Tracked projects: {len(projects)}"]
    lines.append("")
    for p in projects:
        bar_filled = int(p.progress / 10)
        bar_empty = 10 - bar_filled
        bar = "█" * bar_filled + "░" * bar_empty
        lines.append(f"  {p.name}: {bar} {p.progress}% ({p.status})")
        if p.description:
            lines.append(f"    {p.description}")
    return "\n".join(lines)


# intent keywords mapped to tool functions
TOOL_ROUTES = {
    "system": {
        "keywords": ["ram", "cpu", "memory usage", "disk", "uptime", "kernel", "system info",
                      "system status", "what is my ram", "how much ram", "system"],
        "handler": get_system_info,
        "label": "SYSTEM INFO"
    },
    "emails": {
        "keywords": ["email", "inbox", "mail", "gmail", "unread", "messages"],
        "handler": get_email_summary,
        "label": "EMAIL SUMMARY"
    },
    "tasks": {
        "keywords": ["task", "todo", "to-do", "pending", "reminders", "due"],
        "handler": get_tasks_summary,
        "label": "TASKS"
    },
    "applications": {
        "keywords": ["application", "placement", "internship", "interview", "job",
                      "company"],
        "handler": get_applications_summary,
        "label": "APPLICATIONS"
    },
    "deadlines": {
        "keywords": ["deadline", "urgent", "critical"],
        "handler": get_deadlines_summary,
        "label": "DEADLINES"
    },
    "knowledge": {
        "keywords": ["knowledge", "notes", "study", "vault", "preparation", "aws notes",
                      "interview questions"],
        "handler": get_knowledge_items,
        "label": "KNOWLEDGE VAULT"
    },
    "projects": {
        "keywords": ["project", "progress", "milestone", "portfolio"],
        "handler": get_projects_summary,
        "label": "PROJECTS"
    },
    "memory": {
        "keywords": ["what do you know about me", "my preferences", "my facts",
                      "what have you learned"],
        "handler": get_memory_facts,
        "label": "MEMORY"
    }
}


def route_to_tools(user_message: str) -> str:
    """Match user query to tools and return real data for LLM context."""
    msg_lower = user_message.strip().lower()
    matched_outputs = []

    for tool_name, config in TOOL_ROUTES.items():
        for kw in config["keywords"]:
            if kw in msg_lower:
                try:
                    output = config["handler"]()
                    matched_outputs.append(f"[{config['label']}]\n{output}")
                except Exception as e:
                    logger.error(f"Tool '{tool_name}' failed: {e}")
                break

    return "\n\n".join(matched_outputs)
