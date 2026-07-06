"""
desktop.py — FRIDAY's desktop-control layer.

Thin, safe wrappers over the small CLI tools a Linux desktop already ships
(or nearly does): media transport, volume, brightness, notifications,
clipboard, launching apps, opening files, playing media. Plus a couple of
pure-Python conveniences (a safe calculator).

Two safety properties matter here:
  * Every external call goes through subprocess with an argv LIST and
    shell=False. User/model input is never interpolated into a shell string,
    so there is no command-injection surface (unlike run_shell, which is
    whitelisted separately).
  * When the backing tool isn't installed, each function says so with an
    install hint instead of raising — the same "degrade, don't crash"
    contract as the native-daemon fallback in native_bridge.

Every function returns a plain string for the model to read.
"""
import ast
import datetime
import operator as _op
import shutil
import subprocess
from pathlib import Path

from app.core.logger import logger

_TIMEOUT = 10  # seconds; desktop CLIs should answer fast or not at all
_SINK = "@DEFAULT_AUDIO_SINK@"
_BACKLIGHT = Path("/sys/class/backlight")


def _which(name: str):
    return shutil.which(name)


def _run(argv, timeout: int = _TIMEOUT):
    """Run argv (shell=False) and return (ok, output). Never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, f"{argv[0]}: not installed"
    except subprocess.TimeoutExpired:
        return False, f"{argv[0]}: timed out"
    except Exception as e:  # pragma: no cover - defensive
        return False, f"{argv[0]}: {e}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return False, err or out or f"{argv[0]}: exit {proc.returncode}"
    return True, out


def _launch(argv):
    """Fire-and-forget a GUI process, detached from FRIDAY's own lifetime so
    it keeps running after the assistant exits. Returns (ok, error)."""
    try:
        subprocess.Popen(
            argv, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True, ""
    except FileNotFoundError:
        return False, f"{argv[0]}: not installed"
    except Exception as e:  # pragma: no cover - defensive
        return False, f"{argv[0]}: {e}"


# ── media transport (playerctl → MPRIS-over-dbus fallback) ─────────────────

# action → (playerctl verb, MPRIS method | None for status)
_MEDIA_ACTIONS = {
    "play": ("play", "Play"),
    "pause": ("pause", "Pause"),
    "play-pause": ("play-pause", "PlayPause"),
    "toggle": ("play-pause", "PlayPause"),
    "next": ("next", "Next"),
    "previous": ("previous", "Previous"),
    "stop": ("stop", "Stop"),
    "status": ("status", None),
}


def media_control(action: str) -> str:
    """Control whatever media player is running (Spotify, browser, mpv, …)."""
    action = (action or "").strip().lower()
    if action not in _MEDIA_ACTIONS:
        return f"Unknown media action '{action}'. Use one of: {', '.join(_MEDIA_ACTIONS)}."
    pc_verb, mpris_method = _MEDIA_ACTIONS[action]

    if _which("playerctl"):
        ok, out = _run(["playerctl", pc_verb])
        if ok:
            if action == "status":
                return f"Player: {out}" if out else "No active media player."
            return f"Media: {action} ✓"
        if "no player" in out.lower():
            return "No active media player found."
        # otherwise fall through to the dbus path
    return _media_via_dbus(action, mpris_method)


def _mpris_players():
    ok, out = _run([
        "dbus-send", "--session", "--dest=org.freedesktop.DBus",
        "--type=method_call", "--print-reply", "/org/freedesktop/DBus",
        "org.freedesktop.DBus.ListNames",
    ])
    if not ok:
        return []
    return [
        line.split('"')[1]
        for line in out.splitlines()
        if "org.mpris.MediaPlayer2." in line and '"' in line
    ]


def _media_via_dbus(action: str, method) -> str:
    players = _mpris_players()
    if not players:
        return "No active media player found (and playerctl isn't installed)."
    dest = players[0]
    if method is None:  # status
        ok, out = _run([
            "dbus-send", "--session", f"--dest={dest}", "--type=method_call",
            "--print-reply", "/org/mpris/MediaPlayer2",
            "org.freedesktop.DBus.Properties.Get",
            "string:org.mpris.MediaPlayer2.Player", "string:PlaybackStatus",
        ])
        if ok and '"' in out:
            return f"Player: {out.split(chr(34))[1]}"
        return "Could not read player status."
    ok, out = _run([
        "dbus-send", "--session", f"--dest={dest}", "--type=method_call",
        "/org/mpris/MediaPlayer2", f"org.mpris.MediaPlayer2.Player.{method}",
    ])
    return f"Media: {action} ✓" if ok else f"Media control failed: {out}"


# ── volume (PipeWire wpctl) ────────────────────────────────────────────────

def get_volume() -> str:
    if not _which("wpctl"):
        return "wpctl (wireplumber) isn't installed — can't read the volume."
    ok, out = _run(["wpctl", "get-volume", _SINK])
    if not ok:
        return f"Could not read volume: {out}"
    muted = "MUTED" in out
    try:
        val = float(out.split("Volume:")[1].split()[0])
    except (IndexError, ValueError):
        return out
    return f"Volume: {round(val * 100)}%" + (" (muted)" if muted else "")


def set_volume(level) -> str:
    if not _which("wpctl"):
        return "wpctl (wireplumber) isn't installed — can't set the volume."
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "Volume level must be a whole number 0–100."
    level = max(0, min(100, level))
    ok, out = _run(["wpctl", "set-volume", _SINK, f"{level}%"])
    return f"Volume set to {level}%." if ok else f"Could not set volume: {out}"


def toggle_mute() -> str:
    if not _which("wpctl"):
        return "wpctl (wireplumber) isn't installed — can't toggle mute."
    ok, out = _run(["wpctl", "set-mute", _SINK, "toggle"])
    if not ok:
        return f"Could not toggle mute: {out}"
    return get_volume()


# ── brightness (sysfs read, brightnessctl to set) ──────────────────────────

def _backlight_device():
    if not _BACKLIGHT.is_dir():
        return None
    devices = sorted(_BACKLIGHT.iterdir())
    return devices[0] if devices else None


def get_brightness() -> str:
    dev = _backlight_device()
    if dev is None:
        return "No backlight device found (external monitor, or unsupported)."
    try:
        cur = int((dev / "brightness").read_text())
        mx = int((dev / "max_brightness").read_text())
    except (OSError, ValueError) as e:
        return f"Could not read brightness: {e}"
    return f"Brightness: {round(cur / mx * 100) if mx else 0}%"


def set_brightness(percent) -> str:
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        return "Brightness must be a whole number 0–100."
    percent = max(1, min(100, percent))  # never 0 → don't black the screen out
    if _which("brightnessctl"):
        ok, out = _run(["brightnessctl", "set", f"{percent}%"])
        return f"Brightness set to {percent}%." if ok else f"Could not set brightness: {out}"
    # No brightnessctl: writing sysfs usually needs root; try, else hint.
    dev = _backlight_device()
    if dev is None:
        return "No backlight device, and brightnessctl isn't installed."
    try:
        mx = int((dev / "max_brightness").read_text())
        (dev / "brightness").write_text(str(max(1, round(mx * percent / 100))))
        return f"Brightness set to {percent}%."
    except PermissionError:
        return ("Can't set brightness: brightnessctl isn't installed and writing "
                "sysfs needs permission. Install it: sudo dnf install brightnessctl")
    except (OSError, ValueError) as e:
        return f"Could not set brightness: {e}"


# ── notifications ──────────────────────────────────────────────────────────

def send_notification(title: str, message: str = "") -> str:
    title = (title or "").strip()
    if not title:
        return "A notification needs a title."
    if not _which("notify-send"):
        return "notify-send isn't installed — can't show a desktop notification."
    ok, out = _run(["notify-send", "-a", "FRIDAY", title, message or ""])
    return "Notification sent." if ok else f"Could not send notification: {out}"


# ── clipboard (wl-clipboard) ───────────────────────────────────────────────

def get_clipboard() -> str:
    if not _which("wl-paste"):
        return "wl-paste isn't installed — can't read the clipboard."
    ok, out = _run(["wl-paste", "--no-newline"])
    if not ok or not out:
        return "Clipboard is empty."
    return f"Clipboard:\n{out}"


def set_clipboard(text: str) -> str:
    if not _which("wl-copy"):
        return "wl-copy isn't installed — can't set the clipboard."
    text = text or ""
    try:
        subprocess.run(["wl-copy"], input=text, text=True, timeout=_TIMEOUT, check=True)
    except FileNotFoundError:
        return "wl-copy isn't installed."
    except Exception as e:
        return f"Could not set clipboard: {e}"
    return f"Copied {len(text)} characters to the clipboard."


# ── launching / opening (CONFIRM-tier at the toolkit) ──────────────────────

def open_app(name: str) -> str:
    """Launch a desktop app by its .desktop id, e.g. 'firefox', 'org.gnome.Nautilus'."""
    name = (name or "").strip()
    if not name:
        return "No application name given."
    if not _which("gtk-launch"):
        return "gtk-launch isn't installed — can't launch apps by name."
    app_id = name[:-8] if name.endswith(".desktop") else name
    ok, out = _run(["gtk-launch", app_id])
    return f"Launched {name}." if ok else f"Could not launch '{name}': {out}"


def _resolve_openable(target: str, verb: str):
    """Shared validation for open_path/play_media. Returns (arg, error).

    Accepts http/https URLs and local paths under the user's home directory.
    Rejects other URL schemes and paths outside home. Exactly one of (arg,
    error) is non-empty."""
    target = (target or "").strip()
    if not target:
        return None, f"Nothing to {verb}."
    low = target.lower()
    if low.startswith(("http://", "https://")):
        return target, None
    if "://" in target:
        scheme = target.split("://", 1)[0]
        return None, f"Refusing to {verb} a '{scheme}://' URL — only http/https links and local files are allowed."
    try:
        p = Path(target).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        return None, f"Bad path: {e}"
    if not p.is_relative_to(Path.home()):
        return None, f"Refusing to {verb} '{p}' — only paths under your home directory are allowed."
    if not p.exists():
        return None, f"Nothing at {p}."
    return str(p), None


def open_path(target: str) -> str:
    """Open a file, folder, or http/https URL with the desktop default app."""
    if not _which("xdg-open"):
        return "xdg-open isn't installed."
    arg, err = _resolve_openable(target, "open")
    if err:
        return err
    ok, out = _run(["xdg-open", arg])
    return f"Opened {arg}." if ok else f"Could not open {arg}: {out}"


def play_media(target: str) -> str:
    """Play a local media file (under home) or an http/https stream in mpv."""
    if not _which("mpv"):
        return "mpv isn't installed — can't play media."
    arg, err = _resolve_openable(target, "play")
    if err:
        return err
    ok, out = _launch(["mpv", "--no-terminal", arg])
    return f"Playing {arg} in mpv." if ok else f"Could not start mpv: {out}"


# ── screenshot ─────────────────────────────────────────────────────────────

def take_screenshot() -> str:
    """Capture the full screen to a fresh timestamped PNG under ~/Pictures/
    Screenshots. Never overwrites anything, so it's additive/reversible."""
    if not _which("gnome-screenshot"):
        # GNOME's Wayland session blocks silent captures over gdbus/portal
        # unless a real screenshot tool is present; name the one-line fix.
        return ("Can't take a screenshot: gnome-screenshot isn't installed "
                "(GNOME/Wayland blocks silent captures otherwise). Install it: "
                "sudo dnf install gnome-screenshot")
    dest_dir = Path.home() / "Pictures" / "Screenshots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"friday-{stamp}.png"
    ok, out = _run(["gnome-screenshot", "-f", str(dest)], timeout=15)
    if ok and dest.exists():
        return f"Screenshot saved to {dest}."
    return f"Screenshot failed: {out or 'no file was produced'}"


# ── calculator (pure Python, no shell) ─────────────────────────────────────

_OPS = {
    ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod, ast.Pow: _op.pow,
    ast.USub: _op.neg, ast.UAdd: _op.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError("only numbers are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("numbers too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")


def calculate(expression: str) -> str:
    """Evaluate a plain arithmetic expression (+ - * / // % ** and parentheses)."""
    expr = (expression or "").strip()
    if not expr:
        return "No expression to evaluate."
    try:
        result = _eval_node(ast.parse(expr, mode="eval").body)
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError, OverflowError) as e:
        return f"Can't evaluate '{expr}': {e}"
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return f"{expr} = {result}"
