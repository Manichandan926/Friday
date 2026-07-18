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
#
# INVARIANT: this set must stay byte-for-byte identical to the WHITELIST[] in
# native/command_validator.c — the C validator is the production fast path and
# this is the pure-Python fallback, so any divergence means FRIDAY's shell
# behaviour changes depending on whether the .so is built. A guardrail test
# (tests/test_shell.py::test_python_and_native_whitelists_match) fails on drift.
SAFE_COMMANDS = {
    # system monitoring
    "ps", "top", "htop", "free", "df", "du", "uname", "uptime", "who", "whoami",
    "hostname", "date", "cal", "lsblk", "lscpu", "lsmem", "vmstat", "iostat", "sar",
    # hardware
    "sensors", "lsusb", "lspci", "lshw", "dmidecode", "acpi", "upower",
    # network
    "ip", "ifconfig", "nmcli", "ping", "nslookup", "dig", "traceroute",
    "ss", "netstat", "iwconfig", "iw",
    # files
    "cat", "head", "tail", "wc", "file", "stat", "ls", "find", "which",
    "whereis", "type", "echo", "env", "printenv", "readlink", "realpath",
    "md5sum", "sha256sum", "strings", "touch", "mkdir", "pwd",
    # text processing / pipe filters
    "grep", "sed", "awk", "cut", "sort", "uniq", "tr", "xargs", "tee", "sleep",
    # development
    "python3", "python", "java", "javac", "gcc", "g++", "node", "npm", "cargo",
    "git", "pip", "pip3", "rustc", "go",
    # system services
    "systemctl", "journalctl", "loginctl", "timedatectl", "hostnamectl",
    # display
    "neofetch", "screenfetch", "fastfetch",
    # process inspection
    "lsof", "pgrep", "pmap", "strace",
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


# Shell constructs that run an *embedded* command the surface parser never
# sees: command substitution, process substitution, parameter expansion.
# Because commands run under `shell=True`, what shlex tokenizes is NOT what
# executes — `echo $(rm -rf ~)` looks like a harmless echo but runs `rm`.
# Any command containing one of these is refused outright: the whitelist and
# tier classifier can't reason about what's inside, so the safe move is to
# not run it at all. (Legitimate substitution belongs in a real script, not
# a one-off command FRIDAY runs on your behalf.)
_SUBSTITUTION_TOKENS = ("$(", "${", "`", "<(", ">(", "$((")


def has_command_substitution(command: str) -> bool:
    """True if the command smuggles an embedded command via substitution."""
    return any(tok in command for tok in _SUBSTITUTION_TOKENS)


# Control operators that CHAIN a second command onto a whitelisted first one.
# Because commands run under shell=True, `ls && curl evil` runs `curl` even
# though only `ls` is whitelisted: the checks below validate the first token
# (and pipe segments) but never see what follows `;`, `&`, `&&`, or a newline.
# Pipes ('|') are deliberately NOT here — they're validated per-segment below.
# Everything else that separates commands is refused outright, the same
# fail-closed stance as command substitution. (`&&` and `&` are both caught by
# the bare `&`; `;;` by `;`.)
_CHAINING_TOKENS = (";", "&", "\n", "\r")


def has_command_chaining(command: str) -> bool:
    """True if the command chains another command via ; & or a newline."""
    return any(tok in command for tok in _CHAINING_TOKENS)


# Several whitelisted "read-only" commands are actually programmable engines
# that can delete, write, or execute WITHOUT a separate command token the
# whitelist/tier logic could catch:
#   * find … -delete / -exec / -execdir / -ok / -fprintf / -fls  (delete, run, write)
#   * awk 'BEGIN{system("…")}' or `… | "sh"` or `"cmd" | getline`  (arbitrary exec)
#   * sed -i / --in-place, or the e/w/W/r/R script commands         (overwrite, run, write)
#   * a bare system(...) call embedded anywhere
# Their base token (find/awk/sed) is whitelisted, so without this they tier as
# AUTO and run unattended. Refuse the whole command — same fail-closed stance as
# substitution/chaining; legitimate edits go through the CONFIRM-gated,
# home-fenced write_file/edit_file tools instead.
_EFFECTFUL_PATTERNS = [
    r"\bfind\b[^|]*\s-(delete|exec|execdir|ok|okdir|fprintf?|fls|fprint0)\b",
    r"\b[gm]?awk\b[^|]*(system\s*\(|\|\s*[\"']|[\"']\s*\|\s*getline)",
    r"\b[gs]?sed\b[^|]*(\s-i\b|--in-place)",
    r"\b[gs]?sed\b[^|]*['\"][0-9,$ ]*[ewWrR]\s+\S",
    r"\bsystem\s*\(",
]

# Credential-bearing paths must never be read through the shell either — the
# read_file tool refuses these, but cat/head/tail/strings/… are AUTO and would
# otherwise be an exfil path (read a key, smuggle it out via fetch_url). This
# mirrors _secret_reason() in toolkit.py; keep the two in sync.
_SECRET_READ_PATTERNS = [
    r"\.ssh\b", r"\.gnupg\b", r"\.aws\b", r"\.kube\b", r"\.password-store\b",
    r"\bid_(rsa|ed25519|ecdsa)\b", r"\.env(rc)?\b", r"\.netrc\b", r"\.pgpass\b",
    r"\.git-credentials\b", r"\.(pem|key|p12|pfx|kdbx)\b", r"\bcredentials\b",
]


# Compiled once into a single alternation each, so the hot path does one regex
# pass rather than a dozen — keeps these guards off the native fast-path's back.
_EFFECTFUL_RE = re.compile("|".join(_EFFECTFUL_PATTERNS), re.IGNORECASE)
_SECRET_READ_RE = re.compile("|".join(_SECRET_READ_PATTERNS), re.IGNORECASE)


def has_effectful_toolflag(command: str) -> bool:
    """True if a whitelisted command smuggles a delete/write/exec via its own
    flags or scripting (find -delete, awk system(), sed -i/e/w, …)."""
    return _EFFECTFUL_RE.search(command) is not None


def reads_credential_path(command: str) -> bool:
    """True if the command references a credential-shaped path (never read those)."""
    return _SECRET_READ_RE.search(command) is not None


def is_command_safe(command: str) -> Tuple[bool, str]:
    """
    Validate if a shell command is safe to execute.
    Returns (is_safe, reason).

    Uses native C validator when available (10-50x faster),
    falls back to Python regex validation otherwise.
    """
    if not command or not command.strip():
        return False, "Empty command."

    # Refuse shell substitution BEFORE anything else — including the native
    # validator — so no faster/looser check can wave it through. This is a
    # parsing-integrity guarantee, not a heuristic: it must not be bypassable.
    if has_command_substitution(command):
        return False, "Blocked: command substitution/expansion is not allowed."

    # Refuse command chaining for the same reason and just as early: `ls &&
    # curl evil` would otherwise pass because only the first token is checked,
    # then run the whole chain under shell=True. Before the native validator
    # too, so nothing looser can wave it through.
    if has_command_chaining(command):
        return False, "Blocked: chaining commands with ; & or newlines is not allowed."

    # Refuse programmable-command escapes (find -delete, awk system(), sed -i/e/w,
    # …) and credential reads BEFORE the native validator too — the base token is
    # whitelisted, so only these dedicated checks can catch the real effect.
    if has_effectful_toolflag(command):
        return False, ("Blocked: this command can delete, write, or execute via its "
                       "own flags/scripting — use the write_file/edit_file tools for edits.")
    if reads_credential_path(command):
        return False, ("Blocked: refusing to read a credential-shaped path through the "
                       "shell (keys/.env/.ssh/…). FRIDAY never reads credential files.")

    # try native C validator first (binary search + strstr, no regex)
    try:
        from app.core.fast_ops import is_command_safe_native
        result = is_command_safe_native(command)
        if result is not None:
            return result
    except Exception:
        pass

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

        # Success means the command SAID it succeeded (exit 0) — stdout prose
        # alone must never pass for evidence that something worked.
        success = result.returncode == 0
        if not output:
            output = "(command completed with no output)"
        if not success:
            output = f"(exit code {result.returncode})\n{output}"

        return success, output

    except subprocess.TimeoutExpired:
        logger.warning(f"ShellExecutor timeout: {command}")
        return False, f"Command timed out after {TIMEOUT}s."
    except Exception as e:
        logger.error(f"ShellExecutor error: {e}")
        return False, f"Execution error: {e}"
