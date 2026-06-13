"""
Sandboxed shell executor for FRIDAY.
Runs commands as the current user with safety checks.
"""
import subprocess
import shlex
import re
from typing import Optional, Tuple
from app.core.logger import logger

# max execution time per command (seconds)
TIMEOUT = 5

# commands that are always safe to run (read-only system queries)
SAFE_COMMANDS = {
    "ps", "top", "htop", "free", "df", "du", "uname", "uptime", "who", "whoami",
    "hostname", "date", "cal", "lsblk", "lscpu", "lsmem", "ip", "ifconfig",
    "nmcli", "ping", "nslookup", "dig", "traceroute",
    "cat", "head", "tail", "less", "wc", "file", "stat", "ls", "find", "which",
    "whereis", "type", "echo", "env", "printenv",
    "python3", "python", "java", "javac", "gcc", "g++", "node", "npm", "cargo",
    "git", "pip", "pip3",
    "systemctl", "journalctl",
    "sensors", "lsusb", "lspci",
    "neofetch", "screenfetch", "fastfetch",
}

# patterns that should NEVER be allowed
BLOCKED_PATTERNS = [
    r"\brm\b",           # delete files
    r"\bsudo\b",         # privilege escalation
    r"\bsu\b",           # switch user
    r"\bdd\b",           # disk write
    r"\bmkfs\b",         # format disk
    r"\bfdisk\b",        # partition
    r"\bchmod\b",        # change permissions
    r"\bchown\b",        # change ownership
    r"\bkill\b",         # kill processes
    r"\bkillall\b",      # kill processes
    r"\bpkill\b",        # kill processes
    r"\breboot\b",       # reboot
    r"\bshutdown\b",     # shutdown
    r"\bpoweroff\b",     # power off
    r"\binit\b",         # init system
    r"\bmv\s+/",         # move system files
    r"\bcp\s+/",         # overwrite system files
    r"\b>\s*/",          # redirect to system paths
    r"\bmkdir\s+-p\s+/", # create system directories
    r"\bwget\b.*\|\s*sh", # download and execute
    r"\bcurl\b.*\|\s*sh", # download and execute
    r"\beval\b",         # eval arbitrary code
    r"\bexec\b",         # exec arbitrary code
    r":(){",             # fork bomb
    r"\bformat\b",       # format
    r"\bnc\b.*-e",       # netcat reverse shell
    r"\/dev\/sd",        # raw disk access
    r"\/dev\/nvme",      # raw disk access
    r"\bpasswd\b",       # change password
    r"\buseradd\b",      # add user
    r"\buserdel\b",      # delete user
]


def is_command_safe(command: str) -> Tuple[bool, str]:
    """
    Validate if a shell command is safe to execute.
    Returns (is_safe, reason).
    """
    if not command or not command.strip():
        return False, "Empty command."

    cmd_stripped = command.strip()

    # check blocked patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_stripped, re.IGNORECASE):
            return False, f"Blocked: matches dangerous pattern '{pattern}'"

    # check if base command is in whitelist
    try:
        parts = shlex.split(cmd_stripped)
        base_cmd = parts[0].split("/")[-1]  # handle full paths like /usr/bin/ps
    except ValueError:
        return False, "Could not parse command."

    # pipe chains: validate each segment
    if "|" in cmd_stripped:
        segments = cmd_stripped.split("|")
        for seg in segments:
            seg = seg.strip()
            if seg:
                try:
                    seg_parts = shlex.split(seg)
                    seg_base = seg_parts[0].split("/")[-1]
                except ValueError:
                    return False, f"Could not parse pipe segment: {seg}"
                if seg_base not in SAFE_COMMANDS:
                    # allow common text tools in pipes
                    pipe_safe = {"grep", "awk", "sed", "sort", "uniq", "cut", "tr", "head", "tail", "wc", "tee", "xargs"}
                    if seg_base not in pipe_safe:
                        return False, f"Command '{seg_base}' is not in the safe list."
        return True, "OK"

    if base_cmd in SAFE_COMMANDS:
        return True, "OK"

    # allow some common patterns even if not in whitelist
    extra_safe = {"grep", "awk", "sed", "sort", "uniq", "cut", "tr", "tee", "xargs"}
    if base_cmd in extra_safe:
        return True, "OK"

    return False, f"Command '{base_cmd}' is not in the safe list. Allowed: {', '.join(sorted(SAFE_COMMANDS)[:15])}..."


def execute_command(command: str) -> Tuple[bool, str]:
    """
    Execute a validated shell command and return (success, output).
    """
    is_safe, reason = is_command_safe(command)
    if not is_safe:
        logger.warning(f"ShellExecutor blocked: {command} — {reason}")
        return False, f"Command blocked: {reason}"

    try:
        logger.info(f"ShellExecutor running: {command}")
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env=None  # inherit current user env
        )

        output = ""
        if result.stdout:
            output += result.stdout.strip()
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr.strip()

        # truncate very long output
        if len(output) > 3000:
            output = output[:3000] + "\n... (output truncated)"

        if not output:
            output = "(command completed with no output)"

        return True, output

    except subprocess.TimeoutExpired:
        logger.warning(f"ShellExecutor timeout: {command}")
        return False, f"Command timed out after {TIMEOUT}s."
    except Exception as e:
        logger.error(f"ShellExecutor error: {e}")
        return False, f"Execution error: {e}"
