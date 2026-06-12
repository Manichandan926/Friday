import json
import datetime
from typing import List, Dict, Any, Optional

from app.llm.provider import get_llm_provider
from app.core.logger import logger
from app.memory.memory_manager import MemoryManager

SYSTEM_PROMPT = """You are the Task Agent for the FRIDAY AI assistant.
Your job is to translate a planning request into a structured list of tasks.

You MUST respond ONLY with a raw JSON object containing exactly this key:
{
  "tasks": [
    {
      "title": "Short title of the task",
      "description": "Elaborated details of what needs to be done",
      "priority": "One of: high, medium, low",
      "days_from_now": 2  // Number of days from now when this task is due. Integer.
    }
  ]
}

Format the response as raw JSON. Do not include markdown code block syntax (like ```json), introduction, or conversational filler. Output only raw JSON."""

class TaskAgent:
    def __init__(self):
        try:
            self.provider = get_llm_provider()
        except Exception as e:
            logger.error(f"Failed to load LLM provider for Task Agent: {e}")
            self.provider = None

    async def generate_tasks_from_plan(self, user_request: str) -> List[Dict[str, Any]]:
        """Parses a natural language instruction and populates corresponding tasks in SQLite."""
        if not self.provider:
            return []

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Create a structured plan for: {user_request}"}
        ]

        try:
            logger.info(f"TaskAgent parsing task instruction: '{user_request}'")
            raw_response = await self.provider.generate(messages, temperature=0.2)
            
            cleaned_response = raw_response.strip()
            if cleaned_response.startswith("```"):
                lines = cleaned_response.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned_response = "\n".join(lines).strip()

            parsed_data = json.loads(cleaned_response)
            task_list = parsed_data.get("tasks", [])
            
            created_tasks = []
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

            for t in task_list:
                title = t.get("title")
                desc = t.get("description")
                priority = t.get("priority", "medium")
                days_offset = t.get("days_from_now", 1)

                if title:
                    due_date = now + datetime.timedelta(days=int(days_offset))
                    task = MemoryManager.add_task(
                        title=title,
                        description=desc,
                        priority=priority,
                        due_date=due_date
                    )
                    created_tasks.append({
                        "id": task.id,
                        "title": task.title,
                        "priority": task.priority,
                        "due_date": task.due_date
                    })
                    logger.info(f"TaskAgent created planned task: '{title}' (due in {days_offset} days)")
            
            return created_tasks
        except Exception as e:
            logger.error(f"TaskAgent failed to generate plan: {e}")
            return []
