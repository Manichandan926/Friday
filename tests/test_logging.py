"""Interactive terminal mode must keep the console clean for chat.

Log lines still go to the rotating file; they just stop hitting the console
so they don't interleave with the conversation. --headless keeps console
logging (it's a service with no chat to protect) — that's exercised by the
default handler set, which this test restores.
"""
import logging

import pytest

from app.core import logger as logmod


def _console_handlers(lg):
    return [
        h for h in lg.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]


def _file_handlers(lg):
    return [h for h in lg.handlers if isinstance(h, logging.FileHandler)]


@pytest.fixture
def restore_handlers():
    lg = logging.getLogger("friday")
    saved = list(lg.handlers)
    yield lg
    lg.handlers[:] = saved


def test_disable_console_logging_detaches_console_keeps_file(restore_handlers):
    lg = restore_handlers
    # Default setup has both a console and a file handler.
    assert _console_handlers(lg), "expected a console handler before disabling"
    assert _file_handlers(lg), "expected a file handler before disabling"

    logmod.disable_console_logging()

    # Console gone, file log preserved — nothing lost, just not on stdout/stderr.
    assert not _console_handlers(lg), "console handler must be detached"
    assert _file_handlers(lg), "file handler must survive"


def test_disable_console_logging_is_idempotent(restore_handlers):
    logmod.disable_console_logging()
    logmod.disable_console_logging()  # second call must not raise
    assert not _console_handlers(restore_handlers)
