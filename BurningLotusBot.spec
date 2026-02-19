# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['ui.py'],
    pathex=[],
    binaries=[],
    datas=[('images', 'images'), ('Buttons', 'Buttons'), ('cards.json', '.'), ('cards_metadata.json', '.'), ('missing_cards.json', '.'), ('scryfall_cache.json', '.'), ('scryfall_oracle_cache.json', '.'), ('scryfall_bulk_metadata.json', '.'), ('calibration_config.json', '.'), ('recorded_actions_records.json', '.')],
    hiddenimports=[],
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
    name='BurningLotusBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['burning_lotus_icon.ico'],
)
