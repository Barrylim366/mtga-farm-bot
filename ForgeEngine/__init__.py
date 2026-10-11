"""Forge as an optional decision layer for the bot ("Enable Forge Engine").

At each decision the current Arena position is rebuilt inside Forge, Forge's
AI is asked what it would do, and the bot carries that out with its own
clicks. Where Forge has no answer (still starting, timed out, crashed, or a
choice the bot cannot carry out) the bot decides exactly as it always did.

Forge (https://github.com/Card-Forge/forge) is GPL-3.0; the advisor in
``forge_advisor/`` that links it is too. The bot talks to it as a separate
program over stdin/stdout.
"""
from __future__ import annotations

import threading
from typing import Callable

_hooks = None
_hooks_lock = threading.Lock()


def make_ai(base, enabled: Callable[[], bool]):
    """The bot's AI wrapped so Forge is asked first while ``enabled()`` is true."""
    from ForgeEngine.forge_ai import ForgeAI

    return ForgeAI(base, enabled)


def get_hooks():
    global _hooks
    with _hooks_lock:
        if _hooks is None:
            from ForgeEngine.hooks import ForgeHooks

            _hooks = ForgeHooks()
        return _hooks


def apply(controller, enabled: bool, log_path: str | None = None, data_dir_hint: str | None = None) -> bool:
    """Switch the engine on or off for one controller. Returns whether it is on.

    On: the controller's hooks are set and the advisor is started in the
    background. Off: the hooks are cleared, so the controller runs its own
    code only, and a running advisor is stopped to free its memory.
    """
    from ForgeEngine import paths
    from ForgeEngine.bridge import shared, log

    bridge = shared()
    if enabled and not paths.installed():
        log("engine enabled but not installed; the bot's own decisions are used")
        enabled = False
    if enabled:
        bridge.configure(log_path=log_path, data_dir_hint=data_dir_hint)
        if controller is not None:
            controller.decision_hooks = get_hooks()
        bridge.start_async()
    else:
        if controller is not None:
            controller.decision_hooks = None
        if bridge.client is not None:
            threading.Thread(target=bridge.close, name="forge-advisor-stop", daemon=True).start()
    return enabled


def bridge_progress() -> tuple[bool, bool]:
    """(advisor ready, advisor starting) for the settings window."""
    from ForgeEngine.bridge import shared

    return shared().progress()


def shutdown() -> None:
    """Stop the advisor process (bot exit)."""
    from ForgeEngine.bridge import shared

    shared().close()
