import pytest
import app.core.shell
from app.core.shell import is_command_safe, execute_command

def test_safe_command_allowed():
    safe_cmds = ["ls", "ps", "df", "pwd"]
    for cmd in safe_cmds:
        is_safe, reason = is_command_safe(cmd)
        assert is_safe is True, f"Failed on {cmd}: {reason}"

def test_dangerous_command_blocked():
    dangerous_cmds = ["rm -rf .", "sudo apt-get install", "kill -9 123", "reboot"]
    for cmd in dangerous_cmds:
        is_safe, reason = is_command_safe(cmd)
        assert is_safe is False, f"Allowed dangerous command: {cmd}"
        assert "Blocked" in reason or "not in the safe list" in reason or "dangerous pattern" in reason

def test_pipe_chain_validation():
    cmd = "ps aux | grep python | head -5"
    is_safe, reason = is_command_safe(cmd)
    assert is_safe is True, f"Blocked safe pipe chain: {cmd} - {reason}"

def test_pipe_with_dangerous_blocked():
    cmd = "ls | rm -rf"
    is_safe, reason = is_command_safe(cmd)
    assert is_safe is False, f"Allowed dangerous pipe chain: {cmd}"
    assert "Blocked" in reason or "not in the safe list" in reason

def test_fork_bomb_blocked():
    cmd = ":(){ :|:& };:"
    is_safe, reason = is_command_safe(cmd)
    assert is_safe is False, f"Allowed fork bomb: {cmd}"
    assert "Blocked" in reason

def test_execution_timeout():
    # Override TIMEOUT to be 0.1 seconds to avoid blocking the test
    orig_timeout = app.core.shell.TIMEOUT
    app.core.shell.TIMEOUT = 0.1
    try:
        success, output = execute_command("sleep 1")
        assert success is False
        assert "timed out" in output
    finally:
        app.core.shell.TIMEOUT = orig_timeout

def test_output_truncation():
    # Command generating 4000 characters
    success, output = execute_command("python3 -c \"print('A' * 4000)\"")
    assert success is True
    assert len(output) <= 3100
    assert "... (output truncated)" in output

def test_empty_command_rejected():
    is_safe, reason = is_command_safe("")
    assert is_safe is False
    assert "Empty command" in reason
    
    is_safe_spaces, reason_spaces = is_command_safe("   ")
    assert is_safe_spaces is False
    assert "Empty command" in reason_spaces
