"""Schedule (and cancel) a PC shutdown after an unattended farming round.

Kept out of ui.py on purpose: powering the machine off is the one thing in this
app the user cannot undo by clicking Stop, so it has to be testable without a
Tk main loop, and every path into it has to be visible in one short file.

Two rules the rest of the app relies on:

* Nothing here shuts down immediately. `schedule_shutdown` always hands Windows
  a delay, so `cancel_shutdown` (or `shutdown /a` typed by hand) can still stop
  it. A zero delay is clamped up to the minimum rather than honoured.
* Every function is a no-op that returns False off Windows, so the caller never
  has to branch on the platform.
"""
from __future__ import annotations

import subprocess
import sys

# Long enough that someone walking past the machine can abort it, short enough
# that it is not effectively "never" for a bot that finished at 4am.
DEFAULT_DELAY_SEC = 120
MIN_DELAY_SEC = 30


def is_supported() -> bool:
    """True only where `shutdown.exe` has the flags we use."""
    return sys.platform == "win32"


def _run(args: list[str]) -> bool:
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=15,
            creationflags=creationflags,
        )
        return completed.returncode == 0
    except Exception:
        return False


def schedule_shutdown(delay_seconds: int = DEFAULT_DELAY_SEC, comment: str = "") -> bool:
    """Ask Windows to power the machine off after `delay_seconds`.

    The delay is clamped to at least MIN_DELAY_SEC: an instant shutdown would
    take the abort window away from the user, and no caller wants that badly
    enough to justify the risk.
    """
    if not is_supported():
        return False
    try:
        delay = int(delay_seconds)
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY_SEC
    delay = max(MIN_DELAY_SEC, delay)
    args = ["shutdown", "/s", "/t", str(delay)]
    text = (comment or "").strip()
    if text:
        # /c is capped at 512 chars by shutdown.exe; over that it rejects the
        # whole command, which would silently turn this into a no-op.
        args += ["/c", text[:500]]
    return _run(args)


def cancel_shutdown() -> bool:
    """Abort a pending shutdown. False if there was none (or not on Windows)."""
    if not is_supported():
        return False
    return _run(["shutdown", "/a"])
