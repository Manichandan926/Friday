"""Tests for read_file / edit_file — FRIDAY's 'read before you write' pair.

The adversarial cases are the point: path escape, credential-shaped paths
(read_file is AUTO and fetch_url is AUTO, so reading secrets = an exfil
channel under prompt injection), binary refusal, and edit_file's
exactly-once match contract."""
import pytest

from app.core import tiers, toolkit
from app.core.tiers import Tier


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the home fence at tmp so tests never touch the real home."""
    monkeypatch.setattr(toolkit, "ALLOWED_WRITE_ROOTS", [tmp_path])
    return tmp_path


# ── read_file ─────────────────────────────────────────────


def test_read_happy_path(home):
    f = home / "notes.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    out = toolkit.execute("read_file", {"path": str(f)})
    assert "alpha" in out and "gamma" in out
    assert "lines 1-3 of 3" in out


def test_read_paging(home):
    f = home / "big.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)))
    out = toolkit.execute("read_file", {"path": str(f), "start_line": 4, "max_lines": 2})
    assert "line4" in out and "line5" in out
    assert "line6" not in out and "line3" not in out


def test_read_outside_home_refused(home):
    out = toolkit.execute("read_file", {"path": "/etc/passwd"})
    assert "argument error" in out and "must be under" in out


def test_read_traversal_refused(home):
    out = toolkit.execute("read_file", {"path": str(home / ".." / ".." / "etc" / "passwd")})
    assert "argument error" in out and "must be under" in out


@pytest.mark.parametrize("relpath", [
    ".env",
    ".env.local",
    ".ssh/id_rsa",
    ".ssh/known_hosts",          # whole dir is fenced, not just key names
    ".aws/credentials",
    "certs/server.pem",
    "backup/id_ed25519.old",
    ".netrc",
    ".git-credentials",
])
def test_credential_paths_refused_even_inside_fence(home, relpath):
    target = home / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("SECRET=hunter2")
    out = toolkit.execute("read_file", {"path": str(target)})
    assert "Refused" in out
    assert "hunter2" not in out  # content must never leak into the reply


def test_env_named_file_is_not_overblocked(home):
    """'environment.md' is not '.env' — the deny-list matches names, not
    substrings, so ordinary files aren't collateral damage."""
    f = home / "environment.md"
    f.write_text("notes about environments")
    out = toolkit.execute("read_file", {"path": str(f)})
    assert "notes about environments" in out


def test_binary_file_refused(home):
    f = home / "blob.bin"
    f.write_bytes(b"\x00\x01\x02PNG")
    out = toolkit.execute("read_file", {"path": str(f)})
    assert "binary" in out


def test_missing_file(home):
    out = toolkit.execute("read_file", {"path": str(home / "nope.txt")})
    assert "Not a file" in out


def test_read_file_is_auto_tier():
    assert tiers.classify("read_file") == Tier.AUTO


# ── edit_file ─────────────────────────────────────────────


def test_edit_requires_approval(home):
    f = home / "a.txt"
    f.write_text("hello world")
    out = toolkit.execute("edit_file", {"path": str(f), "old_string": "world", "new_string": "friday"})
    assert "requires the user's explicit approval" in out
    assert f.read_text() == "hello world"  # untouched


def test_edit_happy_path_with_backup(home):
    f = home / "a.txt"
    f.write_text("hello world")
    out = toolkit.execute(
        "edit_file",
        {"path": str(f), "old_string": "world", "new_string": "friday"},
        approved=True,
    )
    assert "Edited" in out
    assert f.read_text() == "hello friday"
    backups = list(home.glob("a.txt.bak-*"))
    assert len(backups) == 1 and backups[0].read_text() == "hello world"


def test_edit_not_found(home):
    f = home / "a.txt"
    f.write_text("hello world")
    out = toolkit.execute(
        "edit_file",
        {"path": str(f), "old_string": "mars", "new_string": "x"},
        approved=True,
    )
    assert "not found" in out
    assert f.read_text() == "hello world"


def test_edit_ambiguous_match_refused(home):
    f = home / "a.txt"
    f.write_text("aaa bbb aaa")
    out = toolkit.execute(
        "edit_file",
        {"path": str(f), "old_string": "aaa", "new_string": "x"},
        approved=True,
    )
    assert "2 times" in out
    assert f.read_text() == "aaa bbb aaa"


def test_edit_outside_home_refused(home):
    out = toolkit.execute(
        "edit_file",
        {"path": "/etc/hostname", "old_string": "a", "new_string": "b"},
        approved=True,
    )
    assert "argument error" in out and "must be under" in out


def test_edit_file_is_confirm_tier():
    assert tiers.classify("edit_file") == Tier.CONFIRM
