"""Tests for the risk-tier permission model (app/core/tiers.py + toolkit gate)."""
from pathlib import Path

import pytest

from app.core import tiers, toolkit
from app.core.tiers import Tier


class TestClassifyCommand:
    def test_read_only_command_is_auto(self):
        assert tiers.classify_command("ps aux --sort=-%mem | head -5") == Tier.AUTO
        assert tiers.classify_command("df -h") == Tier.AUTO
        assert tiers.classify_command("cat /proc/meminfo") == Tier.AUTO

    def test_redirect_bumps_to_confirm(self):
        assert tiers.classify_command("ps aux > ~/procs.txt") == Tier.CONFIRM
        assert tiers.classify_command("echo hi >> ~/log.txt") == Tier.CONFIRM

    def test_write_capable_commands_are_confirm(self):
        assert tiers.classify_command("touch ~/x.txt") == Tier.CONFIRM
        assert tiers.classify_command("mkdir -p ~/proj") == Tier.CONFIRM
        assert tiers.classify_command("git status") == Tier.CONFIRM
        assert tiers.classify_command("pip install requests") == Tier.CONFIRM
        assert tiers.classify_command("cat notes.md | tee copy.md") == Tier.CONFIRM

    def test_destructive_and_unknown_commands_are_never(self):
        assert tiers.classify_command("rm -rf /") == Tier.NEVER
        assert tiers.classify_command("sudo reboot") == Tier.NEVER
        assert tiers.classify_command("dd if=/dev/zero of=/dev/sda") == Tier.NEVER
        assert tiers.classify_command("some_unknown_binary --flag") == Tier.NEVER
        assert tiers.classify_command("") == Tier.NEVER


class TestClassify:
    def test_read_and_db_tools_are_auto(self):
        assert tiers.classify("get_system_info") == Tier.AUTO
        assert tiers.classify("add_task") == Tier.AUTO

    def test_file_writes_are_confirm(self):
        assert tiers.classify("write_file", {"path": "~/x", "content": "y"}) == Tier.CONFIRM
        assert tiers.classify("create_directory", {"path": "~/x"}) == Tier.CONFIRM

    def test_run_shell_is_classified_per_command(self):
        assert tiers.classify("run_shell", {"command": "uptime"}) == Tier.AUTO
        assert tiers.classify("run_shell", {"command": "touch x"}) == Tier.CONFIRM
        assert tiers.classify("run_shell", {"command": "rm x"}) == Tier.NEVER

    def test_unlisted_tool_defaults_to_confirm(self):
        assert tiers.classify("brand_new_tool") == Tier.CONFIRM

    def test_every_registered_tool_has_an_explicit_tier(self):
        # Keeps the visible config honest: a new tool must be tiered on purpose.
        missing = [s.name for s in toolkit.specs() if s.name not in tiers.TOOL_TIERS]
        assert not missing, f"tools without a TOOL_TIERS entry: {missing}"


class TestExecuteGate:
    def test_confirm_tool_refused_without_approval(self, tmp_path, monkeypatch):
        monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
        target = tmp_path / "gate.txt"
        out = toolkit.execute("write_file", {"path": str(target), "content": "hi"})
        assert "approval" in out.lower()
        assert not target.exists()

    def test_confirm_tool_runs_when_approved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
        target = tmp_path / "sub" / "gate.txt"
        out = toolkit.execute("write_file", {"path": str(target), "content": "hi"}, approved=True)
        assert "Wrote" in out
        assert target.read_text() == "hi"

    def test_never_tool_refused_even_with_approval(self):
        out = toolkit.execute("run_shell", {"command": "rm -rf /"}, approved=True)
        assert "Blocked (Tier 3" in out

    def test_overwrite_creates_backup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
        target = tmp_path / "note.txt"
        target.write_text("old")
        out = toolkit.execute("write_file", {"path": str(target), "content": "new"}, approved=True)
        assert target.read_text() == "new"
        backups = list(tmp_path.glob("note.txt.bak-*"))
        assert len(backups) == 1 and backups[0].read_text() == "old"
        assert "backed up" in out

    def test_write_outside_allowed_roots_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
        out = toolkit.execute("write_file", {"path": "/etc/evil", "content": "x"}, approved=True)
        assert "argument error" in out
        assert not Path("/etc/evil").exists()

    def test_create_directory_approved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
        out = toolkit.execute("create_directory", {"path": str(tmp_path / "a" / "b")}, approved=True)
        assert (tmp_path / "a" / "b").is_dir()
        assert "Directory ready" in out
