"""
Shell Agent — translates natural language queries into safe shell commands.
Uses LLM to decide what command to run, then validates through the shell executor.
"""
import json
from typing import Optional, Dict
from app.llm.provider import get_llm_provider
from app.core.shell import execute_command, is_command_safe
from app.core.logger import logger

SYSTEM_PROMPT = """You are a Linux command generator. The user will ask a question that requires running a shell command to answer.

Your job: return the SINGLE best shell command to answer their question.

Rules:
1. Return ONLY a raw JSON object: {"command": "the shell command", "explanation": "what it does"}
2. Use simple, standard Linux commands (ps, free, df, top, cat, ls, grep, etc.)
3. NEVER use sudo, rm, kill, chmod, chown, reboot, shutdown, or any destructive command.
4. NEVER use commands that modify files or system state.
5. Keep commands short and focused.
6. For process/memory questions, use: ps aux --sort=-%mem | head -15
7. For disk questions, use: df -h or du -sh
8. For network questions, use: ip addr or nmcli
9. For file listing, use: ls -lah
10. Pipe through head/tail/grep to keep output small.

If the question CANNOT be answered with a safe read-only command, return:
{"command": null, "explanation": "Cannot answer this with a safe command."}

No markdown wrapping. Raw JSON only."""


class ShellAgent:
    def __init__(self):
        try:
            self.provider = get_llm_provider()
        except Exception as e:
            logger.error(f"ShellAgent: failed to load LLM provider: {e}")
            self.provider = None

    async def answer_with_shell(self, question: str) -> str:
        """Use LLM to pick a command, validate it, run it, and return the output."""
        if not self.provider:
            return "Shell agent unavailable: no LLM provider configured."

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question}
        ]

        try:
            # step 1: ask LLM what command to run
            logger.info(f"ShellAgent: generating command for: '{question}'")
            raw = await self.provider.generate(messages, temperature=0.1)

            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            parsed = json.loads(cleaned)
            command = parsed.get("command")
            explanation = parsed.get("explanation", "")

            if not command:
                return f"Shell agent: {explanation}"

            # step 2: validate safety
            is_safe, reason = is_command_safe(command)
            if not is_safe:
                logger.warning(f"ShellAgent blocked unsafe command: {command} — {reason}")
                return f"Command blocked for safety: {reason}\nSuggested: {command}"

            # step 3: execute
            success, output = execute_command(command)

            # step 4: format result
            result = f"$ {command}\n"
            if explanation:
                result = f"({explanation})\n$ {command}\n"
            result += output

            return result

        except json.JSONDecodeError:
            logger.error(f"ShellAgent: LLM returned invalid JSON")
            return "Shell agent: could not parse command from LLM."
        except Exception as e:
            logger.error(f"ShellAgent error: {e}")
            return f"Shell agent error: {e}"
