"""
fast_ops.py — ctypes bindings for native C acceleration modules.

Loads shared libraries from native/ directory and provides
Python-friendly wrappers. Falls back to pure Python if
the C libraries aren't compiled.
"""
import ctypes
import os
from typing import Tuple, Optional
from app.core.logger import logger

_NATIVE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "native"
)


# --- command validator ---

_validator_lib: Optional[ctypes.CDLL] = None

def _load_validator() -> Optional[ctypes.CDLL]:
    """Load command_validator.so once."""
    global _validator_lib
    if _validator_lib is not None:
        return _validator_lib

    so_path = os.path.join(_NATIVE_DIR, "command_validator.so")
    if not os.path.isfile(so_path):
        return None

    try:
        lib = ctypes.CDLL(so_path)
        lib.validate_command.argtypes = [
            ctypes.c_char_p,    # command string
            ctypes.c_char_p,    # reason buffer (output)
            ctypes.c_int,       # reason buffer size
        ]
        lib.validate_command.restype = ctypes.c_int
        _validator_lib = lib
        logger.info("Loaded native command validator (C)")
        return lib
    except Exception as e:
        logger.warning(f"Could not load command_validator.so: {e}")
        return None


def is_command_safe_native(command: str) -> Tuple[bool, str]:
    """
    Validate a shell command using the C validator.

    Returns (is_safe, reason). Falls back to None if library is unavailable.
    """
    lib = _load_validator()
    if lib is None:
        return None  # signal caller to use Python fallback

    reason_buf = ctypes.create_string_buffer(512)
    result = lib.validate_command(
        command.encode("utf-8"),
        reason_buf,
        512
    )

    reason = reason_buf.value.decode("utf-8", errors="replace")
    if result == 1:
        return True, "OK"
    else:
        return False, reason or "Blocked by native validator."
