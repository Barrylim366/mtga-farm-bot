#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[INFO] Creating virtual environment at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[ERROR] Venv python not found: $VENV_PYTHON"
  exit 1
fi

if ! "$VENV_PYTHON" -c "import pynput" >/dev/null 2>&1; then
  echo "[INFO] Installing required packages into .venv"
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install pynput pyautogui opencv-python pillow
fi

exec "$VENV_PYTHON" "$ROOT_DIR/ui.py"
