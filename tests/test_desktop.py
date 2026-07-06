"""Tests for the desktop-control layer (app/core/desktop.py) and its tools.

External CLIs are never actually invoked: `desktop._run` / `desktop._launch`
are monkeypatched to spies that record the argv, and `desktop._which` is
patched to control which tools appear installed. That lets us assert both the
exact command built and the graceful-degradation message when a tool is
absent, without touching the real audio/display/clipboard.
"""
import datetime
from pathlib import Path

import pytest

from app.core import desktop, tiers, toolkit
from app.core.tiers import Tier


@pytest.fixture
def spy_run(monkeypatch):
    """Record argv passed to _run; return a canned (ok, output)."""
    calls = []

    def fake_run(argv, timeout=desktop._TIMEOUT):
        calls.append(list(argv))
        return fake_run.result

    fake_run.result = (True, "")
    fake_run.calls = calls
    monkeypatch.setattr(desktop, "_run", fake_run)
    return fake_run


@pytest.fixture
def spy_launch(monkeypatch):
    calls = []

    def fake_launch(argv):
        calls.append(list(argv))
        return fake_launch.result

    fake_launch.result = (True, "")
    fake_launch.calls = calls
    monkeypatch.setattr(desktop, "_launch", fake_launch)
    return fake_launch


def _installed(monkeypatch, *names):
    """Make only the named tools appear installed."""
    present = set(names)
    monkeypatch.setattr(desktop, "_which", lambda n: f"/usr/bin/{n}" if n in present else None)


class TestMediaControl:
    def test_play_uses_playerctl(self, monkeypatch, spy_run):
        _installed(monkeypatch, "playerctl")
        spy_run.result = (True, "")
        assert "✓" in desktop.media_control("play")
        assert spy_run.calls[-1] == ["playerctl", "play"]

    def test_status_reports_state(self, monkeypatch, spy_run):
        _installed(monkeypatch, "playerctl")
        spy_run.result = (True, "Playing")
        assert "Playing" in desktop.media_control("status")

    def test_unknown_action_is_rejected_without_calling_out(self, monkeypatch, spy_run):
        _installed(monkeypatch, "playerctl")
        out = desktop.media_control("explode")
        assert "Unknown media action" in out
        assert spy_run.calls == []

    def test_falls_back_to_dbus_when_no_playerctl(self, monkeypatch, spy_run):
        _installed(monkeypatch, "dbus-send")
        # First _run is ListNames (returns a player), second is the method call.
        outputs = iter([
            (True, 'string "org.mpris.MediaPlayer2.spotify"'),
            (True, ""),
        ])
        monkeypatch.setattr(desktop, "_run", lambda argv, timeout=desktop._TIMEOUT: next(outputs))
        assert "✓" in desktop.media_control("next")


class TestVolume:
    def test_get_volume_parses_wpctl(self, monkeypatch, spy_run):
        _installed(monkeypatch, "wpctl")
        spy_run.result = (True, "Volume: 0.45")
        assert desktop.get_volume() == "Volume: 45%"

    def test_get_volume_shows_muted(self, monkeypatch, spy_run):
        _installed(monkeypatch, "wpctl")
        spy_run.result = (True, "Volume: 0.00 [MUTED]")
        assert "muted" in desktop.get_volume()

    def test_set_volume_clamps_and_formats(self, monkeypatch, spy_run):
        _installed(monkeypatch, "wpctl")
        desktop.set_volume(250)
        assert spy_run.calls[-1] == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "100%"]

    def test_set_volume_rejects_non_numbers(self, monkeypatch, spy_run):
        _installed(monkeypatch, "wpctl")
        assert "whole number" in desktop.set_volume("loud")
        assert spy_run.calls == []

    def test_absent_wpctl_degrades(self, monkeypatch):
        _installed(monkeypatch)  # nothing installed
        assert "isn't installed" in desktop.get_volume()


class TestBrightness:
    def _fake_backlight(self, monkeypatch, tmp_path, cur, mx):
        (tmp_path / "brightness").write_text(str(cur))
        (tmp_path / "max_brightness").write_text(str(mx))
        monkeypatch.setattr(desktop, "_backlight_device", lambda: tmp_path)

    def test_get_brightness_percent(self, monkeypatch, tmp_path):
        self._fake_backlight(monkeypatch, tmp_path, 96, 192)
        assert desktop.get_brightness() == "Brightness: 50%"

    def test_set_brightness_uses_brightnessctl(self, monkeypatch, spy_run):
        _installed(monkeypatch, "brightnessctl")
        desktop.set_brightness(70)
        assert spy_run.calls[-1] == ["brightnessctl", "set", "70%"]

    def test_set_brightness_never_zero(self, monkeypatch, spy_run):
        _installed(monkeypatch, "brightnessctl")
        desktop.set_brightness(0)
        assert spy_run.calls[-1] == ["brightnessctl", "set", "1%"]


class TestNotifications:
    def test_send_notification(self, monkeypatch, spy_run):
        _installed(monkeypatch, "notify-send")
        assert desktop.send_notification("Hi", "there") == "Notification sent."
        assert spy_run.calls[-1] == ["notify-send", "-a", "FRIDAY", "Hi", "there"]

    def test_empty_title_rejected(self, monkeypatch, spy_run):
        _installed(monkeypatch, "notify-send")
        assert "needs a title" in desktop.send_notification("")
        assert spy_run.calls == []


class TestClipboard:
    def test_get_clipboard(self, monkeypatch, spy_run):
        _installed(monkeypatch, "wl-paste")
        spy_run.result = (True, "hello")
        assert "hello" in desktop.get_clipboard()

    def test_get_clipboard_empty(self, monkeypatch, spy_run):
        _installed(monkeypatch, "wl-paste")
        spy_run.result = (False, "")
        assert desktop.get_clipboard() == "Clipboard is empty."

    def test_set_clipboard(self, monkeypatch):
        _installed(monkeypatch, "wl-copy")
        seen = {}

        def fake_run(argv, input=None, text=None, timeout=None, check=None):
            seen["argv"], seen["input"] = argv, input

        monkeypatch.setattr(desktop.subprocess, "run", fake_run)
        out = desktop.set_clipboard("copy me")
        assert seen["argv"] == ["wl-copy"] and seen["input"] == "copy me"
        assert "7 characters" in out


class TestScreenshot:
    def test_missing_tool_gives_install_hint(self, monkeypatch):
        _installed(monkeypatch)  # gnome-screenshot absent
        out = desktop.take_screenshot()
        assert "gnome-screenshot" in out and "dnf install" in out

    def test_captures_to_home_pictures(self, monkeypatch, tmp_path):
        _installed(monkeypatch, "gnome-screenshot")
        monkeypatch.setattr(desktop.Path, "home", classmethod(lambda cls: tmp_path))
        calls = []

        def fake_run(argv, timeout=desktop._TIMEOUT):
            calls.append(list(argv))
            Path(argv[-1]).write_bytes(b"PNG")  # pretend the capture landed
            return True, ""

        monkeypatch.setattr(desktop, "_run", fake_run)
        out = desktop.take_screenshot()
        assert "Screenshot saved to" in out
        assert calls[-1][0] == "gnome-screenshot" and calls[-1][1] == "-f"
        saved = tmp_path / "Pictures" / "Screenshots"
        assert list(saved.glob("friday-*.png"))


class TestOpenApp:
    def test_launch_by_name(self, monkeypatch, spy_run):
        _installed(monkeypatch, "gtk-launch")
        desktop.open_app("firefox")
        assert spy_run.calls[-1] == ["gtk-launch", "firefox"]

    def test_desktop_suffix_stripped(self, monkeypatch, spy_run):
        _installed(monkeypatch, "gtk-launch")
        desktop.open_app("org.gnome.Nautilus.desktop")
        assert spy_run.calls[-1] == ["gtk-launch", "org.gnome.Nautilus"]

    def test_empty_name_rejected(self, monkeypatch, spy_run):
        _installed(monkeypatch, "gtk-launch")
        assert "No application" in desktop.open_app("  ")
        assert spy_run.calls == []


class TestOpenPathFencing:
    def test_http_url_opens(self, monkeypatch, spy_run):
        _installed(monkeypatch, "xdg-open")
        desktop.open_path("https://example.com")
        assert spy_run.calls[-1] == ["xdg-open", "https://example.com"]

    def test_path_outside_home_refused(self, monkeypatch, spy_run):
        _installed(monkeypatch, "xdg-open")
        out = desktop.open_path("/etc/passwd")
        assert "home directory" in out
        assert spy_run.calls == []

    def test_non_web_scheme_refused(self, monkeypatch, spy_run):
        _installed(monkeypatch, "xdg-open")
        out = desktop.open_path("file:///etc/passwd")
        assert "Refusing" in out
        assert spy_run.calls == []

    def test_local_file_under_home_opens(self, monkeypatch, spy_run, tmp_path):
        _installed(monkeypatch, "xdg-open")
        monkeypatch.setattr(desktop.Path, "home", classmethod(lambda cls: tmp_path))
        f = tmp_path / "notes.txt"
        f.write_text("x")
        desktop.open_path(str(f))
        assert spy_run.calls[-1] == ["xdg-open", str(f)]


class TestPlayMedia:
    def test_stream_url_plays(self, monkeypatch, spy_launch):
        _installed(monkeypatch, "mpv")
        desktop.play_media("https://stream.example/song.mp3")
        assert spy_launch.calls[-1] == ["mpv", "--no-terminal", "https://stream.example/song.mp3"]

    def test_file_outside_home_refused(self, monkeypatch, spy_launch):
        _installed(monkeypatch, "mpv")
        assert "home directory" in desktop.play_media("/etc/hosts")
        assert spy_launch.calls == []


class TestCalculate:
    def test_basic(self):
        assert desktop.calculate("2 + 3 * 4") == "2 + 3 * 4 = 14"

    def test_parentheses_and_power(self):
        assert desktop.calculate("(1 + 2) ** 3") == "(1 + 2) ** 3 = 27"

    def test_float_division(self):
        assert desktop.calculate("10 / 4") == "10 / 4 = 2.5"

    def test_rejects_names_and_calls(self):
        assert "Can't evaluate" in desktop.calculate("__import__('os').system('x')")

    def test_rejects_giant_power(self):
        assert "Can't evaluate" in desktop.calculate("9 ** 9 ** 9")

    def test_division_by_zero(self):
        assert "Can't evaluate" in desktop.calculate("1/0")


class TestTiersAndGate:
    def test_new_tools_are_tiered(self):
        assert tiers.classify("media_control") == Tier.AUTO
        assert tiers.classify("set_volume") == Tier.AUTO
        assert tiers.classify("set_reminder") == Tier.AUTO
        assert tiers.classify("open_app") == Tier.CONFIRM
        assert tiers.classify("play_media") == Tier.CONFIRM

    def test_open_app_refused_without_approval(self, monkeypatch, spy_run):
        _installed(monkeypatch, "gtk-launch")
        out = toolkit.execute("open_app", {"name": "firefox"})
        assert "approval" in out.lower()
        assert spy_run.calls == []

    def test_open_app_runs_when_approved(self, monkeypatch, spy_run):
        _installed(monkeypatch, "gtk-launch")
        out = toolkit.execute("open_app", {"name": "firefox"}, approved=True)
        assert "Launched" in out
        assert spy_run.calls[-1] == ["gtk-launch", "firefox"]

    def test_media_control_runs_via_toolkit_auto(self, monkeypatch, spy_run):
        _installed(monkeypatch, "playerctl")
        out = toolkit.execute("media_control", {"action": "pause"})
        assert "✓" in out


class TestSetReminder:
    def test_creates_future_task(self):
        from app.memory.memory_manager import MemoryManager

        out = toolkit.execute("set_reminder", {"minutes": 15, "message": "drink water"})
        assert "Reminder set" in out and "15 min" in out
        tasks = [t for t in MemoryManager.get_tasks(status="pending") if t.title == "drink water"]
        assert tasks, "reminder task should have been created"
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        assert tasks[0].due_date > now

    def test_rejects_non_positive_minutes(self):
        assert "at least 1 minute" in toolkit.execute("set_reminder", {"minutes": 0, "message": "x"})
