import json
from typing import Dict, Any, Optional

from app.llm.provider import get_llm_provider
from app.core.logger import logger

SYSTEM_PROMPT = """You are the Inbox Agent for the FRIDAY AI assistant.
Your task is to analyze the provided email details and return structured metadata.

You MUST respond ONLY with a raw JSON object containing exactly these keys:
{
  "summary": "A concise 1-2 sentence summary of the email.",
  "category": "One of: interview, placement, internship, exam, certification, project, github, newsletter, promotions",
  "priority": "One of: high, medium, low, ignore",
  "action_items": ["List of tasks extracted from the email. Keep it concise."],
  "deadlines": ["List of dates or deadlines mentioned in the email, formatted as YYYY-MM-DD HH:MM if possible, or relative strings like 'July 20, 10:00 AM'"]
}

Rules for priority assessment:
- If category is interview, placement, internship, or exam -> priority MUST be "high"
- If category is certification or project -> priority MUST be "medium"
- If category is github or newsletter -> priority MUST be "low"
- If category is promotions -> priority MUST be "ignore"

Format the response as raw JSON. Do not include markdown code block syntax (like ```json), introduction, or conversational filler. Output only raw JSON."""

class InboxAgent:
    def __init__(self):
        try:
            self.provider = get_llm_provider()
        except Exception as e:
            logger.error(f"Failed to load LLM provider for Inbox Agent: {e}")
            self.provider = None

    async def analyze_email(self, sender: str, subject: str, body: str) -> Optional[Dict[str, Any]]:
        """Invokes the default LLM to parse raw email body contents and return metadata dictionary."""
        if not self.provider:
            logger.error("LLM Provider is not loaded. Cannot run Inbox Agent.")
            return None

        # Truncate body to prevent token context issues
        truncated_body = body[:2500]
        email_payload = f"From: {sender}\nSubject: {subject}\nBody:\n{truncated_body}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": email_payload}
        ]

        try:
            logger.info("InboxAgent submitting email to LLM for metadata analysis...")
            raw_response = await self.provider.generate(messages, temperature=0.1)
            
            cleaned_response = raw_response.strip()
            # Clean up markdown wrapping if present
            if cleaned_response.startswith("```"):
                lines = cleaned_response.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned_response = "\n".join(lines).strip()

            parsed_data = json.loads(cleaned_response)
            return parsed_data
        except Exception as e:
            logger.error(f"InboxAgent failed to analyze email: {e}")
            return None
