"""
assistant.py — FRIDAY's conversational core.

Flow: explicit slash commands run locally for free; everything else goes to
the LLM through an agentic tool loop — the model decides which tools it
needs (system stats, tasks, emails, shell diagnostics, ...), FRIDAY executes
them, and the model answers from real data.
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core import context, tiers, toolkit
from app.core.logger import logger
from app.core.tiers import Tier
from app.llm.provider import ProviderNotConfigured, get_llm_provider
from app.llm.types import ToolCall
from app.memory.memory_manager import MemoryManager

# How many rounds of tool calls one user message may trigger.
MAX_TOOL_ROUNDS = 6

# Resolving a pending Tier-2 proposal is intent classification, not exact
# string matching. First-word (or first-two-word phrase) sets, matched after
# punctuation is stripped, so "yeah", "yes.", "Yes, go ahead" and "ok do it"
# all approve — while a genuinely non-committal reply ("maybe", "not sure",
# "wait") is treated as UNCLEAR and re-asked rather than silently dropping
# the held action.
_APPROVAL_WORDS = {
    "yes", "y", "yeah", "yep", "yup", "ya", "ok", "okay", "k", "kk",
    "sure", "approve", "approved", "confirm", "confirmed", "affirmative", "aye",
}
_APPROVAL_PHRASES = {
    "go ahead", "do it", "do that", "sounds good", "please do", "yes please",
    "go for it", "make it so", "go on", "carry on",
}
_DECLINE_WORDS = {
    "no", "n", "nope", "nah", "naw", "don't", "dont", "cancel",
    "skip", "stop", "deny", "negative", "abort", "nevermind",
}
_DECLINE_PHRASES = {
    "no thanks", "never mind", "forget it", "leave it", "not now",
    "don't bother", "dont bother", "hold off", "not yet",
}
# Reply clearly poses a different question/instruction → skip the held action
# and answer it (not a silent drop: the model is told the action was declined).
_QUESTION_WORDS = {
    "what", "when", "where", "why", "how", "who", "whom", "whose", "which",
    "can", "could", "would", "should", "is", "are", "am", "was", "were",
    "does", "did", "will", "tell", "show", "give", "explain", "list", "find",
}

# Outcomes of interpreting a reply to a pending proposal.
APPROVE, DECLINE, NEW_REQUEST, UNCLEAR = "approve", "decline", "new_request", "unclear"


def interpret_approval_reply(raw: str) -> str:
    """Classify a reply to a pending Tier-2 proposal.

    Returns APPROVE / DECLINE / NEW_REQUEST / UNCLEAR. UNCLEAR is the case
    that must never silently drop the pending action — the caller re-asks.
    """
    cleaned = raw.strip().lower()
    if cleaned == "/approve":
        return APPROVE
    # strip punctuation to bare words: "yes, go ahead!" -> "yes go ahead"
    norm = re.sub(r"[^\w\s']", " ", cleaned).strip()
    if not norm:
        return UNCLEAR

    tokens = norm.split()
    first = tokens[0]
    first_two = " ".join(tokens[:2])

    if first in _APPROVAL_WORDS or first_two in _APPROVAL_PHRASES:
        return APPROVE
    if first in _DECLINE_WORDS or first_two in _DECLINE_PHRASES:
        return DECLINE

    # A real question or a multi-word instruction is a deliberate new request.
    if cleaned.rstrip().endswith("?") or first in _QUESTION_WORDS or len(tokens) >= 4:
        return NEW_REQUEST

    # Short, non-committal ("maybe", "not sure", "hmm", "wait") — don't guess.
    return UNCLEAR


@dataclass
class PendingApproval:
    """A paused tool round awaiting the user's yes/no.

    In-memory only: a restart drops pending proposals, which fails safe —
    nothing runs without a fresh proposal in the new session.
    """
    messages: List[Dict[str, Any]]
    calls: List[ToolCall]

SYSTEM_PROMPT = """\
You are FRIDAY, a personal AI assistant your person built, running on their \
Linux laptop (Fedora, GNOME/Wayland). You act through tools: system stats; \
their tasks, emails, applications and notes; shell diagnostics; and desktop \
control (media, volume, brightness, notifications, clipboard, open apps/files, \
play media, reminders, math). Asked to do one of these? Just call the tool — \
don't narrate the steps.

Voice: a sharp, dependable friend — warm, direct, a little playful; no filler \
or flattery. You care about their placements, projects and study like a \
brother would: celebrate wins, flag slipping deadlines honestly, never nag.

Rules (the system enforces #3, not you):
1. Never invent facts. Anything about this machine, their data, or the current \
date/time must come from a tool result in this conversation — else call a tool \
or say you don't have it.
2. Read tool output carefully; if a tool fails or returns nothing, say so.
3. Actions are tiered. Low-risk (reads, notes/memories, media/volume/\
brightness, notifications, clipboard, reminders) you do freely. Medium-risk \
(writing files, making folders, opening apps/files, playing media, \
state-changing shell) — just call the tool; the system pauses and asks the \
user for a yes itself, so don't ask in prose first, and never retry something \
they declined. High-risk (deleting, credentials, anything hard to undo) is \
never executed — say what you'd recommend instead.
4. General knowledge (code, concepts, advice) needs no tools — just answer.
5. Keep it conversational and tight; skip headers and bullet walls unless they \
genuinely help.
"""

# A purely social message (greeting, thanks, acknowledgment) never needs a
# tool, so we skip shipping the ~2.9k-token tool catalogue for it. Conservative
# by design: tools are dropped ONLY when every word is social — any real word
# (a request, a noun, a question) keeps the full toolset, so capability is
# never lost, only wasted tokens.
_SOCIAL_WORDS = {
    "hi", "hii", "hey", "helo", "hello", "hlo", "yo", "sup", "hola", "namaste",
    "good", "morning", "afternoon", "evening", "night", "gm", "gn", "morn",
    "goodnight", "goodmorning", "gnite", "nite",
    "thanks", "thank", "thankyou", "thx", "ty", "cheers", "welcome",
    "ok", "okay", "k", "kk", "cool", "nice", "great", "awesome", "sweet",
    "lol", "haha", "hehe", "hmm", "ah", "oh", "yay",
    "friday", "bro", "man", "buddy", "dude", "mate", "pal",
    "please", "pls", "yeah", "yep", "yup", "ya", "sure",
    "bye", "goodbye", "cya", "gg", "np", "cool", "there",
}


def looks_social_only(message: str) -> bool:
    """True if the message is nothing but greeting/acknowledgment words."""
    words = re.findall(r"[a-z']+", message.lower())
    return bool(words) and all(w in _SOCIAL_WORDS for w in words)


# Cap on a single tool result kept in the in-loop message history. The model
# sees enough to answer, but a huge dump (a long shell output, a big list)
# doesn't re-ride verbatim through every subsequent tool round.
MAX_TOOL_RESULT_CHARS = 1200


def _cap_tool_result(text: str) -> str:
    if text and len(text) > MAX_TOOL_RESULT_CHARS:
        return text[:MAX_TOOL_RESULT_CHARS] + "\n[… truncated]"
    return text


# "Speak first": the background scheduler queues notifications (due tasks,
# deadlines, RAM/disk/battery, priority emails) into the notifications table.
# On a fresh turn we weave any *unread* ones into the context so FRIDAY opens
# by mentioning them — the JARVIS "heads up…" moment — then mark exactly those
# read so they surface once, not re-billed every turn. Zero tokens when the
# queue is empty (the common path), so this never taxes the TPD budget idly.
MAX_SESSION_NUDGES = 5


def pending_nudges_message(conversation_id: int) -> Optional[Dict[str, Any]]:
    """Return a system-role briefing of unread notifications (and mark them
    read), or None if there's nothing to surface."""
    try:
        notifs = MemoryManager.get_notifications(unread_only=True, limit=MAX_SESSION_NUDGES)
    except Exception as e:  # a briefing must never break the actual reply
        logger.error(f"pending_nudges_message: {e}")
        return None
    if not notifs:
        return None

    lines = [f"• [{n.category}] {n.title}: {n.message}" for n in notifs]
    MemoryManager.mark_notifications_read([n.id for n in notifs])
    return {
        "role": "system",
        "content": (
            "Proactive briefing from your background monitors. Open your reply "
            "by surfacing what matters here, briefly and in your own voice — "
            "don't dump it verbatim or narrate what doesn't matter:\n"
            + "\n".join(lines)
        ),
    }

HELP_TEXT = r"""## 🤖 FRIDAY — what I can do

**Just talk to me in plain English.** You don't need to memorize anything
below — it's a map, not a syntax you have to learn.

### 💬 Ask me anything
Questions, coding, advice, explanations — I just answer. I also know your
tasks, emails, deadlines, applications, notes, and this laptop's live status,
so things like *"what's due this week?"* or *"how much RAM am I using?"* work.

### 🖥️ Control your laptop — just ask (these happen instantly)
- **Media & sound** — *"pause the music"*, *"next song"*, *"set volume to 40"*, *"mute"*
- **Screen** — *"dim to 30%"*, *"how bright is it?"*, *"take a screenshot"*
- **Reminders** — *"remind me in 20 minutes to stretch"*
- **Notifications** — *"notify me the download is done"*
- **Clipboard** — *"what's in my clipboard?"*, *"copy my email to the clipboard"*
- **Quick math** — *"what's 1200 * 0.18 + 50?"*

### ✅ Actions I confirm first (I'll ask before doing them)
- **Open an app** — *"open Firefox"*, *"open the files app"*
- **Open a file or link** — *"open my Downloads"*, *"open github.com"*
- **Play media** — *"play ~/Music/song.mp3"*
- **Write a file, make a folder, or change something with a shell command**

You'll see a **⏸ Approval needed** box — reply **yes** (or *"yeah"*, *"ok"*,
*"go ahead"*) to run it, anything else to skip.

### ⛔ Things I never do
Delete files, run `sudo`, or anything hard to undo. I'll tell you how to do it
yourself instead. This limit is enforced in code, not just my judgment.

---

### ⌨️ Slash commands — instant shortcuts that skip the AI (free)

| Command | What it does |
| --- | --- |
| `/help` | Show this help |
| `/brief` | Your daily briefing — tasks, emails, focus |
| `/plan goal` | Turn a goal into a task plan — e.g. `/plan finish my resume` |
| `/run command` | Run a safe, read-only shell command — e.g. `/run df -h` |
| `/search query` | Search your saved notes — e.g. `/search s3 policy` |
| `/learn cat \| title \| content` | Save a note — e.g. `/learn aws \| S3 \| policies are JSON` |
| `/addproject name \| desc \| progress` | Track a project |
| `/scan` | Scan the web for internship listings |
| `/notifications` | Show proactive alerts |
| `/provider name` | Switch AI brain — groq, openai, gemini, claude |
| `/cost` | Token usage & cost (this session + all-time) |
| `exit` | Quit FRIDAY |
"""


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
        # ("/approve" classifies as APPROVE, so it falls through to the pending.)
        if cleaned_msg.startswith("/") and interpret_approval_reply(user_message) != APPROVE:
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
            messages = self._build_prompt_context(conversation_id)
            # "Speak first": fold any queued proactive notifications into this
            # turn so FRIDAY opens by mentioning them (even on a bare "hi").
            nudge = pending_nudges_message(conversation_id)
            if nudge:
                messages.append(nudge)
            # Skip the tool catalogue entirely for pure greetings/acks — big
            # TPD saving on the cheapest turns, no capability lost.
            tools_enabled = not looks_social_only(user_message)
            reply = await self._run_tool_loop(conversation_id, messages, tools_enabled)

        MemoryManager.add_message(conversation_id, "assistant", reply)

        # background housekeeping: memory extraction + rolling summary
        from app.agents.memory_agent import MemoryAgent
        asyncio.create_task(MemoryAgent().extract_and_save_memories(conversation_id))
        asyncio.create_task(self._maybe_summarize(conversation_id))

        return reply

    async def _resolve_pending(
        self,
        conversation_id: int,
        pending: PendingApproval,
        user_message: str,
        cleaned_msg: str,
    ) -> str:
        """The user answered a Tier-2 proposal. Four outcomes:
        approve → run held calls; decline → skip and tell the model; a clear
        new request → skip and answer it; anything genuinely non-committal →
        re-ask, keeping the proposal alive (never a silent drop)."""
        intent = interpret_approval_reply(user_message)

        if intent == UNCLEAR:
            # Put the proposal back exactly as it was and ask again — the
            # held calls are untouched, so nothing is lost or wrongly run.
            self._pending[conversation_id] = pending
            return self._reask_text(pending.calls)

        approved = intent == APPROVE
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
                "content": _cap_tool_result(result),
            })

        # A clear new request (not a bare decline) rides along so the model
        # can answer it after acknowledging the skipped action.
        if intent == NEW_REQUEST:
            messages.append({"role": "user", "content": user_message})

        return await self._run_tool_loop(conversation_id, messages)

    @staticmethod
    def _reask_text(calls: List[ToolCall]) -> str:
        lines = ["I didn't catch that as a yes or no, so I've held off. I still want to:"]
        for i, call in enumerate(calls, 1):
            lines.append(f"  {i}. {tiers.describe_call(call.name, call.arguments)}")
        lines.append('Reply **yes** to go ahead, **no** to skip it.')
        return "\n".join(lines)

    # ── agentic tool loop ─────────────────────────────────

    async def _run_tool_loop(self, conversation_id: int, messages: List[Dict[str, Any]],
                             tools_enabled: bool = True) -> str:
        tools = toolkit.specs() if tools_enabled else None

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
                    "content": _cap_tool_result(result),
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

    def _build_prompt_context(self, conversation_id: int) -> List[Dict[str, Any]]:
        """Layered prompt, stable to volatile: byte-identical system prompt
        first (cache-friendly prefix), then a context block with the rolling
        summary and the memories relevant to the latest message, then the
        verbatim window of unsummarized turns."""
        conv = MemoryManager.get_conversation(conversation_id)
        summary = conv.summary if conv else None
        after_id = (conv.summary_until_id if conv else None) or 0

        history = MemoryManager.get_messages_after(conversation_id, after_id)
        recent = history[-context.RECENT_WINDOW:]

        query = next((m.content for m in reversed(recent) if m.role == "user"), "")
        memories = context.select_memories(
            MemoryManager.get_memory_items(limit=context.MEMORY_CANDIDATE_CAP), query
        )

        return context.build_messages(SYSTEM_PROMPT, summary, memories, recent)

    async def _maybe_summarize(self, conversation_id: int) -> None:
        """Fold old turns into the rolling summary once enough pile up.

        Runs in the background after a reply; never raises (a failed
        summary just means the fold retries after the next message).
        """
        try:
            conv = MemoryManager.get_conversation(conversation_id)
            if conv is None or self.provider is None:
                return
            after_id = conv.summary_until_id or 0
            history = MemoryManager.get_messages_after(conversation_id, after_id)
            if len(history) < context.SUMMARY_TRIGGER:
                return
            to_fold = history[:-context.RECENT_WINDOW]
            if not to_fold:
                return
            summary = await context.summarize(self.provider, conv.summary, to_fold)
            if summary:
                MemoryManager.set_summary(conversation_id, summary, to_fold[-1].id)
                logger.info(
                    f"Folded {len(to_fold)} messages into the summary of "
                    f"conversation {conversation_id} (until id {to_fold[-1].id})"
                )
        except Exception as e:
            logger.warning(f"Rolling summary update failed: {e}")

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

        if cleaned_msg in ("/cost", "/usage"):
            from app.llm import costs
            return costs.usage_report()

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
