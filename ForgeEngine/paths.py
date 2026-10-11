"""Where the Forge engine lives on disk.

The engine (a Java runtime, Forge's jars and card scripts, the compiled advisor)
is not part of the repository: it is about 100 MB packed and is downloaded the
first time "Enable Forge Engine" is ticked, into ``runtime/forge``. ZIP and git
updates never touch ``runtime/``, so it survives every bot update.

``MTGA_FORGE_HOME`` points at another folder with the same layout (a local
build made by ``tools/build_forge_package.py``).

Every path is resolved when it is asked for, never frozen at import time: the
test runner redirects ``runtime/`` after this module may have been imported.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The package layout and advisor protocol this bot expects. Bump together with
# a new release asset whenever the advisor or the package layout changes.
PACKAGE_VERSION = 1


def install_dir() -> Path:
    named = os.environ.get("MTGA_FORGE_HOME", "").strip()
    if named:
        return Path(named)
    from runtime_paths import get_runtime_root

    return get_runtime_root() / "forge"


def run_dir() -> Path:
    """Forge's working folder: its preferences, caches, logs, decisions."""
    return install_dir() / "run"


def assets_dir() -> Path:
    """What Forge calls its assets: the folder that holds ``res/``."""
    return install_dir()


def lib_dir() -> Path:
    return install_dir() / "lib"


def java_executable() -> Path:
    name = "java.exe" if sys.platform.startswith("win") else "java"
    return install_dir() / "jre" / "bin" / name


def manifest_path() -> Path:
    return install_dir() / "forge-package.json"


def read_manifest() -> dict:
    try:
        with manifest_path().open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def installed() -> bool:
    """True when a complete engine of the expected version is on disk."""
    manifest = read_manifest()
    if manifest.get("package_version") != PACKAGE_VERSION:
        return False
    return (
        java_executable().exists()
        and lib_dir().is_dir()
        and (assets_dir() / "res" / "cardsfolder").exists()
    )


def decision_log() -> Path:
    """Every decision Forge was asked for, one JSON object per line."""
    return run_dir() / "decisions.jsonl"


def card_cache() -> Path:
    """The export of Arena's card database, kept between runs."""
    return install_dir() / "cache" / "arena_cards.json"
