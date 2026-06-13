import asyncio
from typing import List, Dict, Optional
from app.memory.memory_manager import MemoryManager
from app.llm.provider import get_llm_provider
from app.core.logger import logger
from app.core.tools import (
    route_to_tools, get_tasks_summary, get_email_summary,
    get_system_info, get_deadlines_summary, get_interviews_summary,
    get_knowledge_items, get_projects_summary, get_applications_summary
)

class FridayAssistant:
    def __init__(self, provider_name: Optional[str] = None):
        try:
            self.provider = get_llm_provider(provider_name)
        except Exception as e:
            logger.error(f"Failed to load LLM provider: {e}")
            self.provider = None

    async def chat(self, conversation_id: int, user_message: str) -> str:
        """Route user message through commands -> tools -> LLM."""
        cleaned_msg = user_message.strip().lower()

        # --- direct slash commands (no LLM needed) ---

        # daily briefing
        if cleaned_msg in ["/brief", "daily briefing", "briefing", "/briefing"]:
            from app.agents.planner_agent import PlannerAgent
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = await PlannerAgent().generate_daily_briefing()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # task plan generation
        if cleaned_msg.startswith(("/plan ", "plan ")):
            from app.agents.task_agent import TaskAgent
            MemoryManager.add_message(conversation_id, "user", user_message)
            plan_request = user_message.replace("/plan", "").replace("plan", "").strip()
            logger.info(f"Routing to TaskAgent: '{plan_request}'")
            tasks = await TaskAgent().generate_tasks_from_plan(plan_request)
            if tasks:
                reply = f"### Plan Generated\nCreated **{len(tasks)}** tasks:\n\n"
                for t in tasks:
                    due_str = t['due_date'].strftime('%Y-%m-%d') if t.get('due_date') else "No deadline"
                    reply += f"- **{t['title']}** ({t['priority'].upper()} | Due: {due_str})\n"
            else:
                reply = "Failed to generate tasks. Check LLM connection."
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # placement applications
        if cleaned_msg in ["/placements", "placements", "/applications", "applications"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_applications_summary()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # deadlines
        if cleaned_msg in ["/deadlines", "deadlines"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_deadlines_summary()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # interviews
        if cleaned_msg in ["/interviews", "interviews"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_interviews_summary()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # tasks
        if cleaned_msg in ["/tasks", "tasks", "/todos"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_tasks_summary()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # emails
        if cleaned_msg in ["/emails", "emails", "/inbox", "inbox"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_email_summary()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # system info
        if cleaned_msg in ["/system", "system info", "system status"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_system_info()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # knowledge vault
        if cleaned_msg in ["/knowledge", "knowledge", "/vault", "vault", "/notes"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_knowledge_items()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # add knowledge: /learn <category> | <title> | <content>
        if cleaned_msg.startswith(("/learn ", "learn ")):
            MemoryManager.add_message(conversation_id, "user", user_message)
            raw = user_message.split(" ", 1)[1] if " " in user_message else ""
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) >= 3:
                cat, title, content = parts[0], parts[1], parts[2]
                tags = parts[3] if len(parts) > 3 else None
                MemoryManager.add_knowledge_item(title=title, category=cat, content=content, tags=tags)
                reply = f"Saved to knowledge vault: [{cat}] {title}"
            else:
                reply = "Usage: /learn category | title | content | tags(optional)\nExample: /learn aws | S3 Bucket Policy | S3 policies use JSON..."
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # search knowledge: /search <query>
        if cleaned_msg.startswith(("/search ", "search ")):
            MemoryManager.add_message(conversation_id, "user", user_message)
            query = user_message.split(" ", 1)[1] if " " in user_message else ""
            if query:
                results = MemoryManager.search_knowledge(query)
                if results:
                    reply = f"Found {len(results)} matches for '{query}':\n\n"
                    for r in results:
                        reply += f"**[{r.category}] {r.title}**\n{r.content[:200]}\n\n"
                else:
                    reply = f"No results for '{query}' in knowledge vault."
            else:
                reply = "Usage: /search <query>"
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # projects
        if cleaned_msg in ["/projects", "projects"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = get_projects_summary()
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # add project: /addproject <name> | <description> | <progress>
        if cleaned_msg.startswith("/addproject "):
            MemoryManager.add_message(conversation_id, "user", user_message)
            raw = user_message.split(" ", 1)[1]
            parts = [p.strip() for p in raw.split("|")]
            name = parts[0]
            desc = parts[1] if len(parts) > 1 else None
            prog = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            MemoryManager.add_project(name=name, description=desc, progress=prog)
            reply = f"Project tracked: {name} ({prog}%)"
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # scan internships
        if cleaned_msg in ["/scan", "scan internships", "scan jobs"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            try:
                from app.plugins.internship_scanner.scanner import scan_and_store
                reply = scan_and_store()
            except ImportError:
                reply = "Internship scanner requires beautifulsoup4. Run: pip install beautifulsoup4"
            except Exception as e:
                reply = f"Scan failed: {e}"
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # direct shell command: /run <command>
        if cleaned_msg.startswith("/run "):
            from app.core.shell import execute_command
            MemoryManager.add_message(conversation_id, "user", user_message)
            cmd = user_message.split(" ", 1)[1].strip()
            success, output = execute_command(cmd)
            reply = f"$ {cmd}\n{output}"
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # help
        if cleaned_msg in ["/help", "help", "/commands"]:
            MemoryManager.add_message(conversation_id, "user", user_message)
            reply = (
                "### FRIDAY Commands\n\n"
                "| Command | Description |\n"
                "| --- | --- |\n"
                "| `/brief` | Daily briefing with tasks, emails, and focus |\n"
                "| `/emails` | Show email inbox summary |\n"
                "| `/tasks` | Show pending tasks |\n"
                "| `/placements` | Placement application tracker |\n"
                "| `/deadlines` | Upcoming application deadlines |\n"
                "| `/interviews` | Active interviews |\n"
                "| `/scan` | Scan web for new internship listings |\n"
                "| `/run command` | Run a safe shell command |\n"
                "| `/system` | System info (RAM, CPU, disk) |\n"
                "| `/knowledge` | View knowledge vault |\n"
                "| `/learn cat \\| title \\| content` | Add to knowledge vault |\n"
                "| `/search query` | Search knowledge vault |\n"
                "| `/projects` | View tracked projects |\n"
                "| `/addproject name \\| desc \\| progress` | Add a project |\n"
                "| `/plan request` | Generate task plan |\n"
                "| `/help` | Show this help |\n"
            )
            MemoryManager.add_message(conversation_id, "assistant", reply)
            return reply

        # --- LLM chat with tool-grounded context ---

        if not self.provider:
            try:
                self.provider = get_llm_provider()
            except Exception as e:
                return "LLM not configured. Add an API key in Settings."

        MemoryManager.add_message(conversation_id, "user", user_message)

        # check if query matches any tool — inject real data into LLM context
        tool_context = route_to_tools(user_message)

        # if no tool matched and it looks like a system/process/terminal question,
        # use the shell agent to autonomously pick and run a command
        shell_keywords = [
            "process", "running", "what is using", "which app", "top process",
            "high memory", "high cpu", "why is", "network", "ip address",
            "wifi", "battery", "temperature", "sensor", "usb", "pci",
            "installed", "version of", "where is", "find file", "disk usage",
            "free space", "who is logged", "open ports", "listening",
        ]
        if not tool_context and any(kw in cleaned_msg for kw in shell_keywords):
            try:
                from app.agents.shell_agent import ShellAgent
                shell_reply = await ShellAgent().answer_with_shell(user_message)
                if shell_reply and "blocked" not in shell_reply.lower():
                    tool_context = f"[SHELL OUTPUT]\n{shell_reply}"
            except Exception as e:
                logger.error(f"Shell agent failed: {e}")

        history = MemoryManager.get_messages(conversation_id)
        messages = self._build_prompt_context(history, tool_context)

        try:
            logger.info(f"Querying LLM for conversation ID: {conversation_id}")
            reply = await self.provider.generate(messages)
        except Exception as e:
            reply = f"LLM error: {e}"
            logger.error(reply)
            return reply

        MemoryManager.add_message(conversation_id, "assistant", reply)

        # background memory extraction (only for general chat, not tool queries)
        if not tool_context:
            from app.agents.memory_agent import MemoryAgent
            asyncio.create_task(MemoryAgent().extract_and_save_memories(conversation_id))

        return reply

    def _build_prompt_context(self, history: List, tool_context: str = "") -> List[Dict[str, str]]:
        """Build system prompt with real data. Prevents hallucination."""
        memories = MemoryManager.get_memory_items()
        memory_str = "\n".join([f"- [{item.category}]: {item.content}" for item in memories])

        system_prompt = (
            "You are FRIDAY, a personal assistant running on the user's Linux laptop.\n\n"
            "CRITICAL RULES:\n"
            "1. NEVER invent, fabricate, or hallucinate data. If you don't have information, say so.\n"
            "2. NEVER make up emails, system stats, kernel versions, or notifications.\n"
            "3. NEVER pretend to execute system commands (kernel updates, reboots, patches).\n"
            "4. You have LIMITED read-only terminal access. Shell output appears in TOOL DATA when available.\n"
            "5. Only reference emails, tasks, or applications if they appear in the TOOL DATA below.\n"
            "6. If no tool data is provided for a topic, say 'I don't have that data right now.'\n"
            "7. For general knowledge questions (coding, concepts, etc.), answer normally and helpfully.\n"
            "8. Be concise and direct. No filler.\n"
            "9. When SHELL OUTPUT is provided, interpret the terminal output accurately for the user.\n"
        )

        if memory_str:
            system_prompt += f"\nUSER PROFILE (verified facts):\n{memory_str}\n"

        if tool_context:
            system_prompt += f"\nTOOL DATA (real, live data from this machine):\n{tool_context}\n"
            system_prompt += "\nUse ONLY the above tool data to answer. Do not add or invent anything.\n"

        formatted = [{"role": "system", "content": system_prompt}]

        for msg in history[-10:]:
            formatted.append({"role": msg.role, "content": msg.content})

        return formatted
