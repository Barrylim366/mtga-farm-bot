# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


try:
    project_dir = Path(SPEC).resolve().parent
except Exception:
    project_dir = Path.cwd().resolve()


def add_if_exists(src: str, dst: str = "."):
    path = project_dir / src
    return (str(path), dst) if path.exists() else None


datas = []
for src, dst in [
    ("ui_symbol.png", "."),
    ("cards.json", "."),
    ("cards_metadata.json", "."),
    ("missing_cards.json", "."),
    ("scryfall_cache.json", "."),
    ("scryfall_oracle_cache.json", "."),
    ("scryfall_bulk_metadata.json", "."),
    ("recorded_actions_records.json", "."),
    ("calibration_config.json", "."),
    ("Buttons/.gitkeep", "Buttons"),
    ("Accounts/.gitkeep", "Accounts"),
]:
    item = add_if_exists(src, dst)
    if item is not None:
        datas.append(item)


def safe_collect_submodules(name: str) -> list[str]:
    try:
        return collect_submodules(name)
    except Exception:
        return []


hiddenimports = safe_collect_submodules("pynput")
hiddenimports += [
    "pynput._util",
    "pynput._util.xorg",
    "pynput._util.xorg_keysyms",
    "pynput.keyboard._base",
    "pynput.keyboard._xorg",
    "pynput.mouse._base",
    "pynput.mouse._xorg",
]
hiddenimports = sorted(set(hiddenimports))


a = Analysis(
    ["ui.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BurningLotusBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
