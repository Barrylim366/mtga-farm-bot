#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"
MODE="${1:-onedir}"
ONEDIR_SPEC="$ROOT_DIR/burning_lotus_bot.spec"
ONEFILE_SPEC="$ROOT_DIR/burning_lotus_bot_onefile.spec"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[INFO] Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[ERROR] Venv python not found: $VENV_PYTHON"
  exit 1
fi

if ! "$VENV_PYTHON" -m pip show pyinstaller >/dev/null 2>&1; then
  echo "[ERROR] PyInstaller is not installed in .venv."
  echo "[HINT] Install it with:"
  echo "       $VENV_PYTHON -m pip install pyinstaller"
  exit 1
fi

echo "[INFO] Building PyInstaller bundle..."
cd "$ROOT_DIR"
case "$MODE" in
  onedir)
    "$VENV_PYTHON" -m PyInstaller --noconfirm "$ONEDIR_SPEC"
    echo "[INFO] Output: $ROOT_DIR/dist/BurningLotusBot/"
    ;;
  onefile)
    "$VENV_PYTHON" -m PyInstaller --noconfirm "$ONEFILE_SPEC"
    echo "[INFO] Output: $ROOT_DIR/dist/BurningLotusBot"
    ;;
  *)
    echo "[ERROR] Unknown mode: $MODE"
    echo "[USAGE] ./build_pyinstaller.sh [onedir|onefile]"
    exit 1
    ;;
esac

echo "[INFO] Build finished."
