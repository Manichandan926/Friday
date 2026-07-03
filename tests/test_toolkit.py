"""Tests for the tool registry (app/core/toolkit.py)."""
import datetime

from app.core import toolkit


class TestSpecs:
    def test_all_specs_are_valid_json_schema_objects(self):
        specs = toolkit.specs()
        assert specs, "registry should not be empty"
        for spec in specs:
            assert spec.name and spec.description
            assert spec.input_schema["type"] == "object"
            assert isinstance(spec.input_schema.get("properties", {}), dict)

    def test_file_write_tools_require_confirmation(self):
        # File writes exist since milestone 2 but only behind Tier-2 approval.
        from app.core import tiers
        from app.core.tiers import Tier
        names = {s.name for s in toolkit.specs()}
        assert "write_file" in names and "create_directory" in names
        assert tiers.classify("write_file") == Tier.CONFIRM
        assert tiers.classify("create_directory") == Tier.CONFIRM


class TestExecute:
    def test_unknown_tool(self):
        assert "Unknown tool" in toolkit.execute("launch_missiles", {})

    def test_get_current_time_returns_date(self):
        out = toolkit.execute("get_current_time", {})
        assert str(datetime.date.today().year) in out

    def test_unexpected_arguments_are_filtered(self):
        out = toolkit.execute("get_current_time", {"bogus": 1, "extra": "x"})
        assert str(datetime.date.today().year) in out

    def test_missing_required_argument_reports_error(self):
        out = toolkit.execute("search_knowledge", {})
        assert "argument error" in out

    def test_run_shell_blocks_destructive_commands(self):
        out = toolkit.execute("run_shell", {"command": "rm -rf /"})
        assert "blocked" in out.lower()

    def test_run_shell_allows_safe_command(self):
        out = toolkit.execute("run_shell", {"command": "echo friday"})
        assert "friday" in out

    def test_bad_date_reports_error_not_exception(self):
        out = toolkit.execute("add_task", {"title": "X", "due_date": "next tuesday"})
        assert "argument error" in out


class TestDbTools:
    def test_add_and_list_tasks(self):
        created = toolkit.execute("add_task", {"title": "Ship milestone 1", "priority": "high"})
        assert "Ship milestone 1" in created
        listed = toolkit.execute("get_tasks", {})
        assert "Ship milestone 1" in listed

    def test_remember_fact(self):
        out = toolkit.execute("remember_fact", {"category": "preference", "content": "Likes Rust."})
        assert "Likes Rust." in out

    def test_add_application_with_deadline(self):
        out = toolkit.execute(
            "add_application",
            {"company": "Acme", "role": "SDE Intern", "deadline": "2026-08-01"},
        )
        assert "Acme" in out
        listed = toolkit.execute("get_applications", {})
        assert "Acme" in listed
