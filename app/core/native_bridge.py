"""
Native monitor bridge — connects to FRIDAY's native watcher daemon
via Unix socket for instant system data and file events.

Preference order per query: the Rust friday_watcher (stats + inotify),
then the legacy C friday_monitor (stats only, kept until the Rust daemon
has proven itself), then the caller's pure-Python fallback.
"""
import os
import socket
import subprocess
import time
from typing import List, Optional

from app.core.logger import logger

WATCHER_SOCK = os.environ.get("FRIDAY_WATCHER_SOCK", "/tmp/friday_watcher.sock")
MONITOR_SOCK = "/tmp/friday_monitor.sock"
TIMEOUT = 2.0


def _query_socket(sock_path: str, command: str) -> Optional[str]:
    """Send one command to a daemon socket and return the response."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)
        sock.connect(sock_path)
        sock.sendall(f"{command}\n".encode())

        chunks = []
        while True:
            try:
                data = sock.recv(8192)
                if not data:
                    break
                chunks.append(data.decode())
                # after first chunk, use short timeout to detect end of response
                sock.settimeout(0.1)
            except socket.timeout:
                # timeout means daemon finished sending
                break

        sock.close()
        result = "".join(chunks)
        return result if result.strip() else None
    except (socket.error, OSError):
        return None


def _query_daemon(command: str) -> Optional[str]:
    """Stat query: Rust watcher first, legacy C monitor as fallback."""
    return _query_socket(WATCHER_SOCK, command) or _query_socket(MONITOR_SOCK, command)


def _query_watcher(command: str) -> Optional[str]:
    """Watcher-only command (file watching) — the C daemon can't serve these."""
    return _query_socket(WATCHER_SOCK, command)


def is_daemon_running() -> bool:
    """Check if a native daemon is reachable."""
    return _query_daemon("HEALTH") is not None


def get_sysinfo_native() -> Optional[str]:
    """Get system info from the native daemon."""
    return _query_daemon("SYSINFO")


def get_procs_native() -> Optional[str]:
    """Get top processes from the native daemon."""
    return _query_daemon("PROCS")


def get_health_native() -> Optional[dict]:
    """Get health metrics as a dict from the native daemon."""
    raw = _query_daemon("HEALTH")
    if not raw:
        return None

    result = {}
    for line in raw.strip().splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            try:
                result[key.strip()] = float(val.strip())
            except ValueError:
                result[key.strip()] = val.strip()
    return result


def get_battery_native() -> Optional[str]:
    """Get battery info from the native daemon."""
    return _query_daemon("BATTERY")


def get_network_native() -> Optional[str]:
    """Get network info from the native daemon."""
    return _query_daemon("NETWORK")


def get_temps_native() -> Optional[str]:
    """Get temperature info from the native daemon."""
    return _query_daemon("TEMPS")


# ── file watching (Rust watcher only) ─────────────────────

def watch_directory(path: str) -> Optional[str]:
    """Start watching a directory; returns the daemon's confirmation."""
    return _query_watcher(f"WATCH {path}")


def unwatch_directory(path: str) -> Optional[str]:
    """Stop watching a directory."""
    return _query_watcher(f"UNWATCH {path}")


def list_watches() -> Optional[List[str]]:
    """Currently watched directories, or None if the watcher is down."""
    raw = _query_watcher("WATCHES")
    if raw is None:
        return None
    lines = [l for l in raw.strip().splitlines() if l and l != "none"]
    return lines


def get_file_events() -> Optional[List[str]]:
    """Drain queued file events ("ts=... action=... path=..." lines).

    Returns [] when nothing happened, None when the watcher is down.
    Reading consumes the events — the daemon's queue is emptied.
    """
    raw = _query_watcher("EVENTS")
    if raw is None:
        return None
    return [l for l in raw.strip().splitlines() if l and l != "none"]


# ── daemon lifecycle ──────────────────────────────────────

_DAEMON_NAMES = ("friday_watcher", "friday_monitor")


def _cleanup_stale() -> None:
    """Kill orphaned daemon processes and remove stale sockets."""
    for name in _DAEMON_NAMES:
        try:
            subprocess.run(["pkill", "-9", "-x", name], capture_output=True, timeout=2)
        except Exception:
            pass
    time.sleep(0.3)

    for sock_path in (WATCHER_SOCK, MONITOR_SOCK):
        try:
            if os.path.exists(sock_path):
                os.unlink(sock_path)
        except Exception:
            pass


def start_daemon() -> bool:
    """Start a native daemon, preferring the Rust watcher over the C monitor."""
    _cleanup_stale()

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    binary_paths = [
        os.path.join(project_root, "native", "watcher", "target", "release", "friday_watcher"),
        os.path.expanduser("~/.local/bin/friday_watcher"),
        os.path.join(project_root, "native", "friday_monitor"),
        os.path.expanduser("~/.local/bin/friday_monitor"),
    ]

    for path in binary_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            try:
                subprocess.Popen(
                    [path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                logger.info(f"Launched native daemon: {path}")

                # retry connection with backoff
                for attempt in range(8):
                    time.sleep(0.5)
                    if is_daemon_running():
                        logger.info(f"Native daemon connected (attempt {attempt + 1})")
                        return True

                logger.warning("Native daemon started but socket not responding after 4s")
                return False
            except Exception as e:
                logger.error(f"Failed to start native daemon: {e}")

    return False
