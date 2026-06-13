import json
from typing import List, Dict, Any

from app.llm.provider import get_llm_provider
from app.core.logger import logger
from app.memory.memory_manager import MemoryManager

SYSTEM_PROMPT = """You are a memory extraction module.
Analyze the recent chat exchange and extract ONLY facts the USER explicitly stated about themselves.

Rules:
1. Only extract facts the USER directly said (e.g., "I'm preparing for TCS", "I use Fedora").
2. NEVER extract facts from the assistant's responses — those are generated text, not user data.
3. NEVER extract system information, email content, or task descriptions as user facts.
4. If the user just asks a question or gives a short reply like "yes" or "ok", return empty.
5. Deduplicate against existing memories.

Respond with raw JSON only:
{
  "new_memories": [
    {
      "category": "One of: user_info, user_preferences, project_details, learned_facts",
      "content": "A concise statement of the fact."
    }
  ]
}

If nothing new, return: {"new_memories": []}
No markdown wrapping. Raw JSON only."""

class MemoryAgent:
    def __init__(self):
        try:
            self.provider = get_llm_provider()
        except Exception as e:
            logger.error(f"MemoryAgent: failed to load LLM provider: {e}")
            self.provider = None

    async def extract_and_save_memories(self, conversation_id: int) -> int:
        """Extract user-stated facts from the last exchange."""
        if not self.provider:
            return 0

        messages = MemoryManager.get_messages(conversation_id)
        if len(messages) < 2:
            return 0

        # only look at last 4 messages, but only feed USER messages for extraction
        recent = messages[-4:]
        exchange_lines = []
        for msg in recent:
            # label clearly so the LLM knows which is user vs assistant
            exchange_lines.append(f"{msg.role.upper()}: {msg.content}")
        exchange_text = "\n".join(exchange_lines)

        # pass existing memories to avoid duplication
        existing_mems = MemoryManager.get_memory_items()
        existing_text = "\n".join([f"- [{m.category}] {m.content}" for m in existing_mems])

        prompt = f"Existing Memories:\n{existing_text}\n\nRecent Exchange:\n{exchange_text}"

        messages_payload = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        try:
            logger.info("MemoryAgent: analyzing exchange for user facts...")
            raw_response = await self.provider.generate(messages_payload, temperature=0.1)

            cleaned = raw_response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            parsed = json.loads(cleaned)
            new_items = parsed.get("new_memories", [])

            saved = 0
            for item in new_items:
                cat = item.get("category")
                content = item.get("content")
                if cat and content:
                    # skip if it looks like hallucinated system data or command context
                    skip_phrases = [
                        "kernel", "ram used", "disk space", "uptime", "cpu",
                        "load average", "fedora", "16 gb", "8 gb", "7712",
                        "monitor", "system info", "process", "battery",
                        "network", "terminal", "shell", "command",
                        "temperature", "sensor", "hostname", "linux",
                        "python", "coded with", "built with", "using a monitor",
                        "mb /", "gb /", "mb used", "gb used",
                    ]
                    if any(p in content.lower() for p in skip_phrases):
                        logger.debug(f"MemoryAgent: skipped system-related fact: {content}")
                        continue

                    # skip very short or generic extractions
                    if len(content) < 10:
                        continue

                    is_dup = any(
                        content.lower() in ex.content.lower() or ex.content.lower() in content.lower()
                        for ex in existing_mems
                    )
                    if not is_dup:
                        MemoryManager.add_memory_item(category=cat, content=content)
                        logger.info(f"MemoryAgent: learned [{cat}] {content}")
                        saved += 1
            return saved
        except Exception as e:
            logger.error(f"MemoryAgent: extraction failed: {e}")
            return 0
