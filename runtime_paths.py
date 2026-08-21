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


def get_runtime_root() -> Path:
    # Read on every call, never cached: the override has to win even when a
    # module already imported this one, and the cost is a dict lookup.
    override = (os.environ.get(RUNTIME_DIR_ENV) or "").strip()
    path = Path(override).expanduser().resolve() if override else get_app_root() / "runtime"
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

