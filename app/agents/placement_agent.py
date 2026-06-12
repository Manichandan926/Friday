import json
import datetime
from typing import Optional, Dict, Any

from app.llm.provider import get_llm_provider
from app.core.logger import logger
from app.memory.memory_manager import MemoryManager

SYSTEM_PROMPT = """You are the Placement Agent for the FRIDAY AI assistant.
Your task is to extract details of a job/internship application or interview invitation from the email details.

You MUST respond ONLY with a raw JSON object containing exactly these keys:
{
  "company": "Name of the company (e.g., Paytm, TCS, Google)",
  "role": "Job title or role (e.g., Software Engineering Intern, Junior Developer)",
  "status": "One of: applied, interviewing, offer, rejected",
  "days_until_deadline": null or integer  // Number of days from now for any deadline or interview schedule date
}

If status is an interview invitation, status should be "interviewing".
If it is a rejection mail, status should be "rejected".
If it is a job offer, status should be "offer".
Otherwise, status should default to "applied".

Format the response as raw JSON. Do not include markdown code block syntax (like ```json), introduction, or conversational filler. Output only raw JSON."""

class PlacementAgent:
    def __init__(self):
        try:
            self.provider = get_llm_provider()
        except Exception as e:
            logger.error(f"Failed to load LLM provider for Placement Agent: {e}")
            self.provider = None

    async def parse_and_save_application(self, sender: str, subject: str, body: str, source_email_id: str) -> Optional[Dict[str, Any]]:
        """Parses email content, extracts company/role metadata, and registers the application in SQLite."""
        if not self.provider:
            return None

        # Truncate content
        truncated_body = body[:2000]
        email_payload = f"From: {sender}\nSubject: {subject}\nBody:\n{truncated_body}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": email_payload}
        ]

        try:
            logger.info("PlacementAgent analyzing email for corporate application tracking...")
            raw_response = await self.provider.generate(messages, temperature=0.1)
            
            cleaned_response = raw_response.strip()
            if cleaned_response.startswith("```"):
                lines = cleaned_response.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned_response = "\n".join(lines).strip()

            parsed_data = json.loads(cleaned_response)
            
            company = parsed_data.get("company")
            role = parsed_data.get("role")
            status = parsed_data.get("status", "applied").lower()
            days_offset = parsed_data.get("days_until_deadline")

            if company and role:
                deadline = None
                if days_offset is not None:
                    try:
                        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                        deadline = now + datetime.timedelta(days=int(days_offset))
                    except Exception:
                        pass
                
                # Check for existing application of the same company and role to avoid duplication
                existing_apps = MemoryManager.get_applications()
                is_duplicate = any(
                    company.lower() in app.company.lower() and role.lower() in app.role.lower()
                    for app in existing_apps
                )

                if not is_duplicate:
                    app_record = MemoryManager.add_application(
                        company=company,
                        role=role,
                        status=status,
                        deadline=deadline,
                        source_email_id=source_email_id
                    )
                    logger.info(f"PlacementAgent registered application: {role} at {company} (Status: {status.upper()})")
                    return {
                        "id": app_record.id,
                        "company": app_record.company,
                        "role": app_record.role,
                        "status": app_record.status
                    }
                else:
                    logger.debug(f"PlacementAgent skipped duplicate application log: {role} at {company}")
            
            return None
        except Exception as e:
            logger.error(f"PlacementAgent failed to process application extraction: {e}")
            return None
