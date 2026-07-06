"""Regression tests for the pure-Python fallback readers in tools.py.

These are the "local body degrades gracefully" promise: when no native daemon
is up, the assistant must still read /proc and /sys directly. That path was
verified live once and had no automated coverage — so a refactor could break
it and only surface in production with the daemon down. Here we force the
daemon off (native functions return None) and assert the fallback still
returns real data, not an error string.

Runs on Linux (reads real /proc, /sys). Battery/thermal assertions accept the
"none present" outcome so the tests pass on desktops and CI without hardware.
"""
import pytest

from app.core import native_bridge, tools


@pytest.fixture
def daemon_off(monkeypatch):
    """Force every native reader to report 'unavailable' so tools.py falls
    back to pure Python."""
    for name in (
        "get_sysinfo_native", "get_procs_native", "get_battery_native",
        "get_network_native", "get_temps_native", "get_health_native",
    ):
        monkeypatch.setattr(native_bridge, name, lambda *a, **k: None)


def test_system_info_fallback_reads_proc(daemon_off):
    out = tools.get_system_info()
    assert "Could not read system info" not in out
    assert "RAM:" in out  # /proc/meminfo parsed by the Python path


def test_top_processes_fallback_lists_processes(daemon_off):
    out = tools.get_top_processes()
    assert "Could not retrieve process list" not in out
    assert len(out.strip().splitlines()) > 1


def test_battery_fallback_returns_status(daemon_off):
    out = tools.get_battery_info()
    assert out.strip()
    assert "Battery" in out or "No battery" in out


def test_network_fallback_returns_something(daemon_off):
    out = tools.get_network_info()
    assert out.strip()
    assert "Could not read network info" not in out


def test_temperature_fallback_returns_something(daemon_off):
    out = tools.get_temperature_info()
    assert out.strip()
    assert "°C" in out or "No thermal sensors" in out


def test_native_result_is_preferred_when_available(monkeypatch):
    # When the daemon answers, its value is used verbatim (native path).
    monkeypatch.setattr(native_bridge, "get_sysinfo_native", lambda: "NATIVE_SYSINFO_MARKER")
    assert tools.get_system_info() == "NATIVE_SYSINFO_MARKER"
