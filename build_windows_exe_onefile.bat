@echo off
setlocal

cd /d "%~dp0"

echo [1/2] Building single-file BurningLotusBot.exe with PyInstaller...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name BurningLotusBot ^
  --icon burning_lotus_icon.ico ^
  --add-data "images;images" ^
  --add-data "Buttons;Buttons" ^
  --add-data "cards.json;." ^
  --add-data "cards_metadata.json;." ^
  --add-data "missing_cards.json;." ^
  --add-data "scryfall_cache.json;." ^
  --add-data "scryfall_oracle_cache.json;." ^
  --add-data "scryfall_bulk_metadata.json;." ^
  --add-data "calibration_config.json;." ^
  --add-data "recorded_actions_records.json;." ^
  ui.py

if errorlevel 1 (
  echo Build failed.
  exit /b %errorlevel%
)

echo [2/2] Build complete.
echo Executable: dist\BurningLotusBot.exe
echo.
echo Copy just this file to the target Windows laptop:
echo dist\BurningLotusBot.exe

endlocal
