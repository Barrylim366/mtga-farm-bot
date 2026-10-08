"""Where the pieces of the Forge integration live.

The integration sits inside the bot's repository. Two large things do not: a clone of Forge and a
JDK with Maven. Each is looked for inside the repository first and then next to it, so both
"everything in one folder" and "bot, forge and tools side by side" work. BURNING_FORGE_HOME names a
folder to look in before either.

    Forge clone:   <home>/forge
    JDK + Maven:   <home>/forge_tools, or <home>/tools next to the repository
                   (inside the repository `tools/` is the bot's own scripts folder, so it is not used)
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADVISOR_DIR = REPO / "forge_advisor"


def _homes() -> list[Path]:
    homes = []
    if os.environ.get("BURNING_FORGE_HOME"):
        homes.append(Path(os.environ["BURNING_FORGE_HOME"]))
    return homes + [REPO, REPO.parent]


def forge_dir() -> Path:
    """The Forge clone (the folder holding forge-gui/), or where it is expected when missing."""
    for home in _homes():
        if (home / "forge" / "forge-gui").is_dir():
            return home / "forge"
    return REPO / "forge"


def tools_dir() -> Path:
    """The folder holding the unpacked jdk-* and apache-maven-*, or where it is expected when missing."""
    for home in _homes():
        for name in ("forge_tools", "tools"):
            if glob.glob(str(home / name / "jdk-*")):
                return home / name
    return REPO / "forge_tools"
