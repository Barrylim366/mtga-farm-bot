@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo [1/4] Checking Nuitka installation...
python -m pip show nuitka >nul 2>&1
if errorlevel 1 (
  echo Nuitka not found. Installing Nuitka build dependencies...
  python -m pip install nuitka ordered-set zstandard
  if errorlevel 1 (
    echo Failed to install Nuitka dependencies.
    exit /b %errorlevel%
  )
)

echo [2/4] Preparing data include arguments...
set "DATA_ARGS="
set "DATA_ARGS=!DATA_ARGS! --include-data-dir=images=images"
set "DATA_ARGS=!DATA_ARGS! --include-data-dir=Buttons=Buttons"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=cards.json=cards.json"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=cards_metadata.json=cards_metadata.json"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=missing_cards.json=missing_cards.json"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=scryfall_cache.json=scryfall_cache.json"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=scryfall_oracle_cache.json=scryfall_oracle_cache.json"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=scryfall_bulk_metadata.json=scryfall_bulk_metadata.json"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=calibration_config.json=calibration_config.json"
set "DATA_ARGS=!DATA_ARGS! --include-data-files=config/public_key.jwk=config/public_key.jwk"
if exist "recorded_actions_records.json" (
  set "DATA_ARGS=!DATA_ARGS! --include-data-files=recorded_actions_records.json=recorded_actions_records.json"
) else (
  echo Note: recorded_actions_records.json not found, skipping include.
)

echo [3/4] Building BurningLotusBot.exe with Nuitka (standalone)...
python -m nuitka ^
  --standalone ^
  --assume-yes-for-downloads ^
  --windows-console-mode=disable ^
  --enable-plugin=tk-inter ^
  --output-dir=dist_nuitka ^
  --output-filename=BurningLotusBot.exe ^
  --windows-icon-from-ico=burning_lotus_icon.ico ^
  !DATA_ARGS! ^
  ui.py

if errorlevel 1 (
  echo Standard compiler path failed. Retrying with Zig backend...
  python -m nuitka ^
    --standalone ^
    --assume-yes-for-downloads ^
    --windows-console-mode=disable ^
    --enable-plugin=tk-inter ^
    --zig ^
    --output-dir=dist_nuitka ^
    --output-filename=BurningLotusBot.exe ^
    --windows-icon-from-ico=burning_lotus_icon.ico ^
    !DATA_ARGS! ^
    ui.py
  if errorlevel 1 (
    echo Nuitka build failed including Zig fallback.
    exit /b %errorlevel%
  )
)

echo [4/4] Build complete.
echo Executable: dist_nuitka\ui.dist\BurningLotusBot.exe
echo.
echo Copy the whole folder "dist_nuitka\ui.dist" to another Windows laptop.

endlocal
