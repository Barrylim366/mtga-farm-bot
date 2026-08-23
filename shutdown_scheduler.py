"""Schedule (and cancel) a PC shutdown after an unattended farming round.

Kept out of ui.py on purpose: powering the machine off is the one thing in this
app the user cannot undo by clicking Stop, so it has to be testable without a
Tk main loop, and every path into it has to be visible in one short file.

Three rules the rest of the app relies on:

* Nothing here shuts down immediately. `schedule_shutdown` always hands the OS
  a delay, so `cancel_shutdown` (or `shutdown /a` / `shutdown -c` typed by
  hand) can still stop it. A zero delay is clamped up rather than honoured.
* Every function is a no-op that returns False on a platform we cannot power
  off, so the caller never has to branch on the platform.
* The shutdown is handed to the OS, not to a timer in this process: closing the
  app during the two minutes must not silently un-arm it.

Windows and Linux both use their own `shutdown` binary, only the flags differ
(`/s /t <seconds>` vs. `-h +<minutes>`; `/a` vs. `-c`). macOS is deliberately
not supported: its `shutdown` needs root, and there is no way to get that
non-interactively from a background run.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

# Long enough that someone walking past the machine can abort it, short enough
# that it is not effectively "never" for a bot that finished at 4am.
DEFAULT_DELAY_SEC = 120
MIN_DELAY_SEC = 30


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _have_shutdown_binary() -> bool:
    return shutil.which("shutdown") is not None


def is_supported() -> bool:
    """True where we have a `shutdown` binary with the flags we use.

    On Linux that binary is normally systemd's, and an unprivileged call goes
    through logind/polkit -- which grants power-off to the active local session
    without a password on a desktop, but can refuse it (headless, remote SSH,
    a locked-down policy). That refusal only shows up when the command actually
    runs, so it cannot be part of this answer; `schedule_shutdown` returning
    False is what the caller has to react to.
    """
    if _is_windows():
        return True
    if _is_linux():
        return _have_shutdown_binary()
    return False


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


def _schedule_args(delay: int, comment: str) -> list[str]:
    if _is_windows():
        args = ["shutdown", "/s", "/t", str(delay)]
        text = (comment or "").strip()
        if text:
            # /c is capped at 512 chars by shutdown.exe; over that it rejects
            # the whole command, which would silently turn this into a no-op.
            args += ["/c", text[:500]]
        return args
    # Linux: minute granularity only, so round *up* -- never deliver the
    # shutdown earlier than the caller's delay. `--no-wall` because the wall
    # broadcast is a separate polkit action (set-wall-message) that can be
    # denied where the plain power-off is allowed; losing a message nobody is
    # sitting in front of must not cost us the shutdown. `comment` therefore
    # has no place to go here -- the UI dialog already carries the reason.
    minutes = max(1, -(-delay // 60))
    return ["shutdown", "-h", f"+{minutes}", "--no-wall"]


def schedule_shutdown(delay_seconds: int = DEFAULT_DELAY_SEC, comment: str = "") -> bool:
    """Ask the OS to power the machine off after `delay_seconds`.

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
    return _run(_schedule_args(delay, comment))


def cancel_shutdown() -> bool:
    """Abort a pending shutdown. False if the platform has no way to do it.

    On Linux this is a no-op that still succeeds when nothing was scheduled,
    so it is safe to call unconditionally.
    """
    if not is_supported():
        return False
    return _run(["shutdown", "/a"] if _is_windows() else ["shutdown", "-c"])


def cancel_command_hint() -> str:
    """The command a user can type themselves if our cancel did not work."""
    return "shutdown /a" if _is_windows() else "shutdown -c"
