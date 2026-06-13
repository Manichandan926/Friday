"""
Native monitor bridge — connects to the C friday_monitor daemon
via Unix socket for instant system data.

Falls back to Python /proc reading if the daemon is not running.
"""
import socket
import subprocess
import os
import time
from typing import Optional
from app.core.logger import logger

SOCK_PATH = "/tmp/friday_monitor.sock"
TIMEOUT = 2.0


def _query_daemon(command: str) -> Optional[str]:
    """Send a command to the C daemon and return the response."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)
        sock.connect(SOCK_PATH)
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


def is_daemon_running() -> bool:
    """Check if the native monitor daemon is reachable."""
    if not os.path.exists(SOCK_PATH):
        return False
    return _query_daemon("HEALTH") is not None


def get_sysinfo_native() -> Optional[str]:
    """Get system info from the C daemon."""
    return _query_daemon("SYSINFO")


def get_procs_native() -> Optional[str]:
    """Get top processes from the C daemon."""
    return _query_daemon("PROCS")


def get_health_native() -> Optional[dict]:
    """Get health metrics as a dict from the C daemon."""
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
    """Get battery info from the C daemon."""
    return _query_daemon("BATTERY")


def get_network_native() -> Optional[str]:
    """Get network info from the C daemon."""
    return _query_daemon("NETWORK")


def get_temps_native() -> Optional[str]:
    """Get temperature info from the C daemon."""
    return _query_daemon("TEMPS")


def _cleanup_stale() -> None:
    """Kill any orphaned friday_monitor processes and remove stale socket."""
    try:
        # kill existing C daemon instances (SIGKILL for reliable cleanup)
        subprocess.run(
            ["pkill", "-9", "-x", "friday_monitor"],
            capture_output=True, timeout=2
        )
        time.sleep(0.3)
    except Exception:
        pass

    # remove stale socket file
    try:
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)
    except Exception:
        pass


def start_daemon() -> bool:
    """Start the native monitor daemon with retry. Cleans up stale instances."""
    # always clean up stale processes/sockets first
    _cleanup_stale()

    binary_paths = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "native", "friday_monitor"),
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
                logger.info(f"Launched native monitor daemon: {path}")

                # retry connection with backoff
                for attempt in range(8):
                    time.sleep(0.5)
                    if is_daemon_running():
                        logger.info(f"Native monitor connected (attempt {attempt + 1})")
                        return True

                logger.warning("Native monitor started but socket not responding after 4s")
                return False
            except Exception as e:
                logger.error(f"Failed to start native monitor: {e}")

    return False
