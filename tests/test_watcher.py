"""Tests for the Rust watcher daemon and its Python bridge.

Spawns a private daemon instance on a per-test socket (FRIDAY_WATCHER_SOCK)
so a live daemon is never touched. Skipped entirely when the release binary
hasn't been built (cargo build --release in native/watcher/).
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

from app.core import native_bridge, tiers, toolkit
from app.core.tiers import Tier

BINARY = (
    Path(__file__).resolve().parent.parent
    / "native" / "watcher" / "target" / "release" / "friday_watcher"
)

pytestmark = pytest.mark.skipif(
    not BINARY.is_file(),
    reason="friday_watcher not built (cargo build --release in native/watcher/)",
)


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    """A private watcher instance on its own socket."""
    sock = str(tmp_path / "watcher.sock")
    proc = subprocess.Popen(
        [str(BINARY)],
        env={**os.environ, "FRIDAY_WATCHER_SOCK": sock},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    monkeypatch.setattr(native_bridge, "WATCHER_SOCK", sock)
    for _ in range(40):
        if native_bridge._query_watcher("PING"):
            break
        time.sleep(0.05)
    else:
        proc.kill()
        pytest.fail("watcher did not come up within 2s")
    yield proc
    proc.kill()
    proc.wait(timeout=5)


class TestStatCommands:
    def test_health_parses_to_floats(self, watcher):
        health = native_bridge.get_health_native()
        assert health is not None
        for key in ("ram_pct", "ram_used", "ram_total", "disk_pct", "cpu_pct", "load"):
            assert isinstance(health[key], float), key

    def test_sysinfo_has_expected_lines(self, watcher):
        info = native_bridge.get_sysinfo_native()
        for prefix in ("Hostname:", "OS:", "Kernel:", "Uptime:", "CPU:", "RAM:", "Disk (/):"):
            assert prefix in info, prefix

    def test_procs_lists_processes(self, watcher):
        procs = native_bridge.get_procs_native()
        assert procs.startswith("PID")
        assert len(procs.strip().splitlines()) > 1

    def test_unknown_command_reports_error(self, watcher):
        assert "ERR" in native_bridge._query_watcher("BOGUS")


class TestFileWatching:
    def test_watch_event_unwatch_flow(self, watcher, tmp_path):
        target = tmp_path / "observed"
        target.mkdir()

        reply = native_bridge.watch_directory(str(target))
        assert reply.startswith("OK watching")
        assert str(target) in "\n".join(native_bridge.list_watches())

        (target / "hello.txt").write_text("hi")
        time.sleep(0.3)

        events = native_bridge.get_file_events()
        assert any("action=created" in e and "hello.txt" in e for e in events)
        assert native_bridge.get_file_events() == []  # reading drains the queue

        assert native_bridge.unwatch_directory(str(target)).startswith("OK unwatched")
        assert native_bridge.list_watches() == []

    def test_watch_missing_directory_is_an_error(self, watcher, tmp_path):
        reply = native_bridge.watch_directory(str(tmp_path / "nope"))
        assert reply.startswith("ERR")


class TestToolkitIntegration:
    def test_watch_tools_are_tier_auto(self):
        for name in ("watch_directory", "unwatch_directory",
                     "list_watched_directories", "get_file_events"):
            assert tiers.classify(name) == Tier.AUTO, name

    def test_toolkit_watch_roundtrip(self, watcher, tmp_path, monkeypatch):
        # tmp_path is under /tmp, outside the home fence — allow it for the test.
        monkeypatch.setattr(toolkit, "ALLOWED_WATCH_ROOTS", [tmp_path])
        target = tmp_path / "tk"
        target.mkdir()
        assert "OK watching" in toolkit.execute("watch_directory", {"path": str(target)})

        (target / "note.md").write_text("x")
        time.sleep(0.3)
        events = toolkit.execute("get_file_events", {})
        assert "note.md" in events

        assert "OK unwatched" in toolkit.execute("unwatch_directory", {"path": str(target)})

    def test_daemon_down_degrades_gracefully(self, monkeypatch, tmp_path):
        monkeypatch.setattr(native_bridge, "WATCHER_SOCK", str(tmp_path / "no.sock"))
        out = toolkit.execute("get_file_events", {})
        assert "isn't running" in out
