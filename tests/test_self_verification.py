"""Self-verification: tool results carry evidence collected in code — what is
actually on disk / the real exit code — so the model can't declare success on
vibes. The interesting cases are the failures: a non-zero exit must say
FAILED, and a write that doesn't land must say so."""
import pathlib

import pytest

from app.core import task_engine, toolkit
from app.core.shell import execute_command


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
    return tmp_path


# ── shell: exit codes are the verdict, not stdout prose ───

def test_execute_command_nonzero_exit_is_failure():
    success, output = execute_command("ls /nonexistent-friday-path")
    assert success is False
    assert "exit code" in output


def test_execute_command_zero_exit_is_success():
    success, output = execute_command("echo hello")
    assert success is True and "hello" in output


def test_run_shell_result_carries_the_verdict():
    ok = toolkit.execute("run_shell", {"command": "echo hi"})
    assert "[OK (exit 0)]" in ok
    bad = toolkit.execute("run_shell", {"command": "ls /nonexistent-friday-path"})
    assert "[FAILED]" in bad and "exit code" in bad


# ── writes: verified against the disk, not the intent ─────

def test_write_file_reports_verified_bytes(home):
    f = home / "v.txt"
    out = toolkit.execute("write_file", {"path": str(f), "content": "hello"}, approved=True)
    assert "verified 5 bytes on disk" in out


def test_write_file_detects_mismatch_on_disk(home, monkeypatch):
    # simulate a write that silently didn't land (disk trouble, race, ...)
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self: b"???")
    f = home / "v.txt"
    out = toolkit.execute("write_file", {"path": str(f), "content": "hello"}, approved=True)
    assert "WRITE FAILED verification" in out
    assert "Do not assume" in out


def test_edit_file_shows_the_changed_region(home):
    f = home / "e.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    out = toolkit.execute(
        "edit_file",
        {"path": str(f), "old_string": "beta", "new_string": "BETA-NEW"},
        approved=True,
    )
    assert "verified on disk" in out
    assert "2| BETA-NEW" in out           # the evidence, with line numbers
    assert f.read_text() == "alpha\nBETA-NEW\ngamma\n"


def test_edit_snippet_points_at_the_replacement_not_an_earlier_match(home):
    # new_string 'alpha' also exists at line 1 — the snippet must still show
    # the actual replacement site (line 3), not the first occurrence.
    f = home / "e.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    out = toolkit.execute(
        "edit_file",
        {"path": str(f), "old_string": "gamma", "new_string": "alpha"},
        approved=True,
    )
    assert "3| alpha" in out


def test_edit_file_deletion_still_verifies(home):
    f = home / "d.txt"
    f.write_text("keep\ndrop-me\nkeep2\n")
    out = toolkit.execute(
        "edit_file",
        {"path": str(f), "old_string": "drop-me\n", "new_string": ""},
        approved=True,
    )
    assert "verified on disk" in out
    assert f.read_text() == "keep\nkeep2\n"


def test_edit_file_detects_mismatch_on_disk(home, monkeypatch):
    f = home / "e.txt"
    f.write_text("hello world")
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self: "corrupted")
    out = toolkit.execute(
        "edit_file",
        {"path": str(f), "old_string": "world", "new_string": "friday"},
        approved=True,
    )
    assert "EDIT FAILED verification" in out


def test_create_directory_verifies(home):
    out = toolkit.execute("create_directory", {"path": str(home / "sub")}, approved=True)
    assert "verified on disk" in out


# ── task mode: STEP DONE must be evidence-backed ──────────

def test_step_prompt_demands_evidence():
    assert "STEP DONE only when a tool result" in task_engine.STEP_SYSTEM_PROMPT
    assert "never" in task_engine.STEP_SYSTEM_PROMPT
