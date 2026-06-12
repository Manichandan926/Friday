import datetime
from app.llm.provider import get_llm_provider
from app.core.logger import logger
from app.memory.memory_manager import MemoryManager

SYSTEM_PROMPT = """You are the Planner Agent for the FRIDAY AI assistant.
Your task is to compile a highly personalized, motivating, and clear Daily Briefing for the user.

Use the provided user facts, recent emails summaries, and pending tasks to write a brief, clean daily schedule/report.
Keep the style supportive, productive, and structured with clean sections.

Include:
1. A friendly morning greeting referencing the user's name.
2. A summary of active high/medium priority emails.
3. A breakdown of urgent tasks due soon.
4. A recommendation for what to focus on today based on their active projects/goals.

Format the response in clean Markdown. Do not include introductory conversational fluff or code block wrap. Output only the markdown briefing directly."""

class PlannerAgent:
    def __init__(self):
        try:
            self.provider = get_llm_provider()
        except Exception as e:
            logger.error(f"Failed to load LLM provider for Planner Agent: {e}")
            self.provider = None

    async def generate_daily_briefing(self) -> str:
        """Retrieves profile preferences, pending tasks, and recent emails, compiling them into a Markdown briefing."""
        if not self.provider:
            return "Planner service offline. Configure a valid LLM API key to enable daily briefings."

        # 1. Retrieve Memories
        memories = MemoryManager.get_memory_items()
        mem_text = "\n".join([f"- [{m.category}] {m.content}" for m in memories])

        # 2. Retrieve Emails (recent 5)
        emails = MemoryManager.get_emails()
        email_lines = []
        for e in emails[:5]:
            email_lines.append(f"- From: {e.sender} | Subject: {e.subject} | Priority: {e.priority.upper()} | Summary: {e.body_summary}")
        emails_text = "\n".join(email_lines)

        # 3. Retrieve Pending Tasks
        pending_tasks = MemoryManager.get_tasks(status="pending")
        task_lines = []
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        for t in pending_tasks:
            due_str = t.due_date.strftime("%Y-%m-%d %H:%M") if t.due_date else "No deadline"
            # Calculate if overdue
            overdue_flag = " (OVERDUE)" if t.due_date and t.due_date < now else ""
            task_lines.append(f"- {t.title} [Priority: {t.priority.upper()}] Due: {due_str}{overdue_flag}")
        tasks_text = "\n".join(task_lines)

        # Build compilation payload
        prompt = f"""
USER PROFILE & FACTS:
{mem_text or "No facts learned yet."}

RECENT SUMMARIZED EMAILS:
{emails_text or "No emails in database."}

PENDING TASKS:
{tasks_text or "No pending tasks."}
"""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        try:
            logger.info("PlannerAgent generating daily briefing...")
            briefing = await self.provider.generate(messages, temperature=0.3)
            return briefing.strip()
        except Exception as e:
            logger.error(f"PlannerAgent failed to generate briefing: {e}")
            return "Encountered an error preparing your briefing. Please check connection logs."
