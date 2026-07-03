"""
assistant.py — FRIDAY's conversational core.

Flow: explicit slash commands run locally for free; everything else goes to
the LLM through an agentic tool loop — the model decides which tools it
needs (system stats, tasks, emails, shell diagnostics, ...), FRIDAY executes
them, and the model answers from real data.
"""
import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core import tiers, toolkit
from app.core.logger import logger
from app.core.tiers import Tier
from app.llm.provider import ProviderNotConfigured, get_llm_provider
from app.llm.types import ToolCall
from app.memory.memory_manager import MemoryManager

# How many rounds of tool calls one user message may trigger.
MAX_TOOL_ROUNDS = 6

# Replies that count as approval of a pending Tier-2 proposal.
APPROVAL_WORDS = {
    "yes", "y", "ok", "okay", "approve", "/approve",
    "go ahead", "do it", "sure", "confirm", "yes please",
}
# Bare declines: pending actions are skipped and nothing else was asked.
DECLINE_WORDS = {"no", "n", "nope", "skip", "cancel", "don't", "dont", "deny"}


@dataclass
class PendingApproval:
    """A paused tool round awaiting the user's yes/no.

    In-memory only: a restart drops pending proposals, which fails safe —
    nothing runs without a fresh proposal in the new session.
    """
    messages: List[Dict[str, Any]]
    calls: List[ToolCall]

SYSTEM_PROMPT = """\
You are FRIDAY — a personal AI assistant your person built themselves, running \
on their Linux laptop (Fedora KDE): a Python orchestrator with a native C \
system monitor, thinking through a cloud LLM.

Who you are:
- Less corporate tool, more sharp and dependable friend — the kind who \
actually listens, remembers things, and calls it straight. Warm, direct, a \
little playful when it fits. No filler, no flattery, no lecture mode.
- You care about their goals — placements, projects, study — the way a \
brother would: celebrate wins, flag slipping deadlines honestly, never nag.

Ground rules (non-negotiable):
1. Never invent data. Anything about this machine, its files or processes, \
the user's tasks, emails, applications, or the current date/time must come \
from a tool result in this conversation. Don't have it? Call a tool or say \
you don't have it.
2. Check, don't guess — when facts are needed, use your tools. Read tool \
output carefully; if a tool fails or returns nothing useful, say so plainly.
3. Actions are tiered, and the system enforces this — not you. Low-risk \
actions (reading anything, creating tasks, saving notes and memories) are \
yours to take freely; they're always logged. Medium-risk actions (writing \
files, creating folders, shell commands that change anything) go through an \
automatic approval step: when the user wants one done, just call the tool — \
the system pauses and asks them for a yes itself, so don't ask permission in \
prose first, and never retry an action the user declined. High-risk actions \
(deleting files, credentials, money, anything hard to undo) are never \
executed — explain what you'd recommend and how they can do it themselves.
4. General knowledge (code, concepts, advice) needs no tools — just answer.
5. This is a chat with a friend, not a report. Keep it conversational and \
tight; skip headers and bullet walls unless they genuinely help.
"""

HELP_TEXT = (
    "### FRIDAY Commands\n\n"
    "Just talk to me normally — I can check the system, your tasks, emails, "
    "deadlines, and more on my own. Slash commands are free shortcuts that "
    "skip the LLM:\n\n"
    "| Command | Description |\n"
    "| --- | --- |\n"
    "| `/brief` | Daily briefing with tasks, emails, and focus |\n"
    "| `/plan request` | Generate a task plan |\n"
    "| `/run command` | Run a safe shell command directly |\n"
    "| `/learn cat \\| title \\| content` | Add to knowledge vault |\n"
    "| `/search query` | Search knowledge vault |\n"
    "| `/addproject name \\| desc \\| progress` | Track a project |\n"
    "| `/scan` | Scan the web for internship listings |\n"
    "| `/notifications` | View proactive alerts |\n"
    "| `/provider name` | Switch LLM provider (groq, openai, gemini, claude) |\n"
    "| `/help` | Show this help |\n"
)


class FridayAssistant:
    def __init__(self, provider_name: Optional[str] = None):
        self.provider_name = provider_name
        self._pending: Dict[int, PendingApproval] = {}
        try:
            self.provider = get_llm_provider(provider_name)
        except Exception as e:
            logger.error(f"Failed to load LLM provider: {e}")
            self.provider = None

    async def chat(self, conversation_id: int, user_message: str) -> str:
        """Route a user message: slash commands locally, everything else
        through the LLM tool loop (pausing for Tier-2 approvals)."""
        cleaned_msg = user_message.strip().lower()

        # Slash commands first — /help etc. must not consume a pending approval.
        if cleaned_msg.startswith("/") and cleaned_msg not in APPROVAL_WORDS:
            reply = await self._handle_command(user_message, cleaned_msg)
            if reply is not None:
                MemoryManager.add_message(conversation_id, "user", user_message)
                MemoryManager.add_message(conversation_id, "assistant", reply)
                return reply

        # ── LLM chat with tools ──
        if not self.provider:
            try:
                self.provider = get_llm_provider(self.provider_name)
            except ProviderNotConfigured as e:
                return str(e)
            except Exception as e:
                return f"LLM not configured: {e}"

        MemoryManager.add_message(conversation_id, "user", user_message)

        pending = self._pending.pop(conversation_id, None)
        if pending is not None:
            reply = await self._resolve_pending(conversation_id, pending, user_message, cleaned_msg)
        else:
            history = MemoryManager.get_messages(conversation_id)
            messages = self._build_prompt_context(history)
            reply = await self._run_tool_loop(conversation_id, messages)

        MemoryManager.add_message(conversation_id, "assistant", reply)

        # background memory extraction
        from app.agents.memory_agent import MemoryAgent
        asyncio.create_task(MemoryAgent().extract_and_save_memories(conversation_id))

        return reply

    async def _resolve_pending(
        self,
        conversation_id: int,
        pending: PendingApproval,
        user_message: str,
        cleaned_msg: str,
    ) -> str:
        """The user answered a Tier-2 proposal: execute or decline the held
        calls, then hand control back to the model."""
        approved = cleaned_msg in APPROVAL_WORDS
        messages = pending.messages

        for call in pending.calls:
            if approved:
                result = toolkit.execute(call.name, call.arguments, approved=True)
            else:
                logger.info(f"AUDIT tier=2 tool={call.name} args={call.arguments} decision=DECLINED")
                result = "The user declined this action. Do not retry it; acknowledge and move on."
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": result,
            })

        # A reply that is neither approval nor a bare "no" is a new request —
        # pass it along so the model can answer it after acknowledging.
        if not approved and cleaned_msg not in DECLINE_WORDS:
            messages.append({"role": "user", "content": user_message})

        return await self._run_tool_loop(conversation_id, messages)

    # ── agentic tool loop ─────────────────────────────────

    async def _run_tool_loop(self, conversation_id: int, messages: List[Dict[str, Any]]) -> str:
        tools = toolkit.specs()

        try:
            reply = await self.provider.chat(messages, tools=tools)
        except ProviderNotConfigured as e:
            return str(e)
        except Exception as e:
            # Some free-tier models reject tool definitions — degrade to plain chat.
            logger.warning(f"Tool-enabled call failed ({e}); retrying without tools.")
            try:
                reply = await self.provider.chat(messages, tools=None)
            except Exception as e2:
                logger.error(f"LLM call failed: {e2}")
                return f"I hit an error talking to the LLM: {e2}"

        for _ in range(MAX_TOOL_ROUNDS):
            if not reply.tool_calls:
                break

            messages.append({
                "role": "assistant",
                "content": reply.text,
                "tool_calls": reply.tool_calls,
                "raw_content": reply.raw_content,
            })

            needs_approval: List[ToolCall] = []
            for call in reply.tool_calls:
                if toolkit.has_tool(call.name) and tiers.classify(call.name, call.arguments) == Tier.CONFIRM:
                    # Held back for the user's yes — no result appended yet.
                    needs_approval.append(call)
                    continue
                # AUTO runs; NEVER comes back as a Tier-3 refusal the model
                # relays. Unknown tool names surface as errors the same way.
                result = toolkit.execute(call.name, call.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": result,
                })

            if needs_approval:
                self._pending[conversation_id] = PendingApproval(messages, needs_approval)
                return self._proposal_text(reply.text, needs_approval)

            try:
                reply = await self.provider.chat(messages, tools=tools)
            except Exception as e:
                logger.error(f"LLM call failed mid tool loop: {e}")
                return f"I got partway through checking that, then hit an error: {e}"
        else:
            return reply.text or (
                "That took more digging than I'm allowed in one go — "
                "mind narrowing it down?"
            )

        return reply.text or "(no reply)"

    @staticmethod
    def _proposal_text(model_text: str, calls: List[ToolCall]) -> str:
        lines = [model_text.strip()] if model_text and model_text.strip() else []
        lines.append("⏸ **Approval needed** — I want to:")
        for i, call in enumerate(calls, 1):
            lines.append(f"  {i}. {tiers.describe_call(call.name, call.arguments)}")
        lines.append('Reply **yes** to approve, anything else to skip.')
        return "\n".join(lines)

    def _build_prompt_context(self, history: List) -> List[Dict[str, Any]]:
        """System prompt + long-term memories + recent turns."""
        system_prompt = SYSTEM_PROMPT

        memories = MemoryManager.get_memory_items()
        if memories:
            memory_str = "\n".join(f"- [{m.category}]: {m.content}" for m in memories)
            system_prompt += f"\nWhat you know about your person (verified facts):\n{memory_str}\n"

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            messages.append({"role": msg.role, "content": msg.content})
        return messages

    # ── slash commands (no LLM cost) ──────────────────────

    async def _handle_command(self, user_message: str, cleaned_msg: str) -> Optional[str]:
        """Handle an explicit /command. Returns None if unrecognized so the
        message falls through to the LLM."""

        if cleaned_msg in ("/help", "/commands"):
            return HELP_TEXT

        if cleaned_msg in ("/brief", "/briefing"):
            from app.agents.planner_agent import PlannerAgent
            return await PlannerAgent().generate_daily_briefing()

        if cleaned_msg.startswith("/plan "):
            from app.agents.task_agent import TaskAgent
            plan_request = user_message.split(" ", 1)[1].strip()
            logger.info(f"Routing to TaskAgent: '{plan_request}'")
            tasks = await TaskAgent().generate_tasks_from_plan(plan_request)
            if not tasks:
                return "Failed to generate tasks. Check LLM connection."
            reply = f"### Plan Generated\nCreated **{len(tasks)}** tasks:\n\n"
            for t in tasks:
                due_str = t["due_date"].strftime("%Y-%m-%d") if t.get("due_date") else "No deadline"
                reply += f"- **{t['title']}** ({t['priority'].upper()} | Due: {due_str})\n"
            return reply

        if cleaned_msg.startswith("/run "):
            from app.core.shell import execute_command
            cmd = user_message.split(" ", 1)[1].strip()
            success, output = execute_command(cmd)
            return f"$ {cmd}\n{output}"

        if cleaned_msg.startswith("/learn "):
            raw = user_message.split(" ", 1)[1]
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) >= 3:
                cat, title, content = parts[0], parts[1], parts[2]
                tags = parts[3] if len(parts) > 3 else None
                MemoryManager.add_knowledge_item(title=title, category=cat, content=content, tags=tags)
                return f"Saved to knowledge vault: [{cat}] {title}"
            return (
                "Usage: /learn category | title | content | tags(optional)\n"
                "Example: /learn aws | S3 Bucket Policy | S3 policies use JSON..."
            )

        if cleaned_msg.startswith("/search "):
            query = user_message.split(" ", 1)[1].strip()
            results = MemoryManager.search_knowledge(query)
            if not results:
                return f"No results for '{query}' in knowledge vault."
            reply = f"Found {len(results)} matches for '{query}':\n\n"
            for r in results:
                reply += f"**[{r.category}] {r.title}**\n{r.content[:200]}\n\n"
            return reply

        if cleaned_msg.startswith("/addproject "):
            raw = user_message.split(" ", 1)[1]
            parts = [p.strip() for p in raw.split("|")]
            name = parts[0]
            desc = parts[1] if len(parts) > 1 else None
            prog = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            MemoryManager.add_project(name=name, description=desc, progress=prog)
            return f"Project tracked: {name} ({prog}%)"

        if cleaned_msg == "/scan":
            try:
                from app.plugins.internship_scanner.scanner import scan_and_store
                return scan_and_store()
            except ImportError:
                return "Internship scanner requires beautifulsoup4. Run: pip install beautifulsoup4"
            except Exception as e:
                return f"Scan failed: {e}"

        if cleaned_msg in ("/notifications", "/alerts"):
            notifs = MemoryManager.get_notifications(limit=20)
            if not notifs:
                return "No notifications."
            unread = sum(1 for n in notifs if not n.is_read)
            reply = f"### Notifications ({unread} unread)\n\n"
            for n in notifs:
                marker = "🔵" if not n.is_read else "⚪"
                time_str = n.created_at.strftime("%b %d %H:%M")
                reply += f"{marker} **{n.title}** ({time_str})\n{n.message}\n\n"
            MemoryManager.mark_notifications_read()
            return reply

        if cleaned_msg.startswith("/provider"):
            parts = user_message.split(maxsplit=1)
            if len(parts) < 2:
                current = self.provider_name or "default from .env"
                return (
                    f"Current provider: {current}.\n"
                    "Usage: /provider <groq|openai|gemini|claude>"
                )
            name = parts[1].strip().lower()
            try:
                self.provider = get_llm_provider(name)
                self.provider_name = name
                return f"Switched to provider: {name}"
            except (ProviderNotConfigured, ValueError) as e:
                return str(e)

        return None  # unknown /command → let the LLM take it
