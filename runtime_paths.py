from __future__ import annotations

import os
import sys
from pathlib import Path

# Env override for the runtime directory. Two uses:
#   * the test suite points it at a temp dir (tests/__init__.py) -- before this
#     existed, running the suite while the bot was live overwrote the real
#     status.json with the unittest process's pid and appended ~1000 lines of
#     fixture output ("card 999 could not be hovered") straight into
#     runtime/analysis/history.log, i.e. into the first artefact CLAUDE.md tells
#     you to read when debugging real bot behaviour.
#   * capturing one session's artefacts somewhere else, without moving the repo.
RUNTIME_DIR_ENV = "MTGA_RUNTIME_DIR"


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return get_repo_root()


def default_runtime_root() -> Path:
    """Where the runtime dir lives with no override and no test runner."""
    return get_app_root() / "runtime"


_test_runtime_root: Path | None = None


def _running_under_test_runner() -> bool:
    """True when this process is a unittest/pytest run rather than the bot.

    Checked because the env override alone does not survive how the suite is
    actually started. `python -m unittest discover tests` -- the command
    CLAUDE.md prescribes -- points top_level_dir at tests/, so the `tests`
    package is never imported and tests/__init__.py never runs. On 2026-08-21
    that let the suite append 273 lines of fixture output (card 999) straight
    into the live bot's runtime/logs/bot.log while the bot was playing.
    Isolation therefore cannot depend on a package __init__ being imported.

    Deliberately narrow: it looks at what started the process, not at whether
    some test module happens to be loaded, so importing the bot from a REPL or
    from a tool under runtime/ is unaffected.
    """
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    name = getattr(spec, "name", "") or ""
    if name.split(".")[0] in ("unittest", "pytest", "py"):
        return True
    argv0 = os.path.basename((sys.argv[0] if sys.argv else "") or "").lower()
    return argv0 in ("pytest", "pytest.exe", "py.test", "py.test.exe")


def _test_runtime_dir() -> Path:
    """A throwaway runtime dir, one per test process, created on first use."""
    global _test_runtime_root
    if _test_runtime_root is None:
        import tempfile

        _test_runtime_root = Path(
            tempfile.mkdtemp(prefix="mtga-test-runtime-")
        ).resolve()
        # Loud on purpose: a silent redirect is how the old breakage stayed
        # invisible for weeks. If artefacts are missing after a run, this line
        # says where they went.
        print(
            f"[runtime_paths] test runner detected; runtime artefacts go to "
            f"{_test_runtime_root} (set {RUNTIME_DIR_ENV} to choose your own)"
        )
    return _test_runtime_root


def get_runtime_root() -> Path:
    # Read on every call, never cached: the override has to win even when a
    # module already imported this one, and the cost is a dict lookup.
    override = (os.environ.get(RUNTIME_DIR_ENV) or "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    elif _running_under_test_runner():
        path = _test_runtime_dir()
    else:
        path = default_runtime_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_runtime_subdir(*parts: str) -> Path:
    path = get_runtime_root().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_file(*parts: str) -> Path:
    if len(parts) > 1:
        ensure_runtime_subdir(*parts[:-1])
    else:
        get_runtime_root()
    return get_runtime_root().joinpath(*parts)

