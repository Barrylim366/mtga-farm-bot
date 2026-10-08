"""Start mtga-farm-bot with Forge choosing its plays.

    .venv\\Scripts\\python.exe scripts\\start_bot.py          # the bot's window
    .venv\\Scripts\\python.exe scripts\\start_bot.py --cli    # its command-line runner

None of the bot's own files are edited: its modules are imported, a handful of functions are swapped
in memory, and then its normal entry point runs. Starting ui.py directly gives the plain bot.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT


def main() -> None:
    cli = "--cli" in sys.argv[1:]
    target = BOT / ("run_bot.py" if cli else "ui.py")
    if not target.exists():
        sys.exit(f"mtga-farm-bot not found at {BOT}")
    os.chdir(BOT)
    # the bot's folder first, as if it had been started from there (forge_bridge lives in it too)
    sys.path[:] = [str(BOT)] + [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]

    from forge_bridge.forge_ai import install

    install()

    if not cli:
        from forge_bridge import moves_window

        moves_window.install()

    # The hooks reach into the bot's internals by name, so a new bot version can break them without
    # warning. Updating is therefore a deliberate step: the startup check reports "nothing new"
    # unless MTGA_BOT_UPDATES=on.
    if (os.environ.get("MTGA_BOT_UPDATES") or "off").strip().lower() not in ("on", "1", "true", "yes"):
        import update_checker  # type: ignore

        update_checker.check_for_updates = lambda: update_checker.UpdateCheckResult(
            update_available=False, error="updates are switched off by start_bot.py (MTGA_BOT_UPDATES=on to allow)"
        )

    # sys.argv is left alone: the bot restarts itself with it after an update, which brings it back here
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
