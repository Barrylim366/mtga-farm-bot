#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER_FILE="$REPO_DIR/tools/macos_python_helper.sh"
VENV_PYTHON="$REPO_DIR/.venv-macos/bin/python"
PLAYER_LOG="$HOME/Library/Logs/Wizards Of The Coast/MTGA/Player.log"

if [ ! -f "$HELPER_FILE" ]; then
  echo "[FAIL] Missing helper file: tools/macos_python_helper.sh"
  exit 1
fi

# shellcheck source=/dev/null
. "$HELPER_FILE"

echo "Burning Lotus Bot macOS doctor"
echo ""

SELECTED_PYTHON="$(select_macos_python || true)"
if [ -n "$SELECTED_PYTHON" ]; then
  echo "[OK] Python: $SELECTED_PYTHON ($(python_version "$SELECTED_PYTHON"))"
else
  echo "[FAIL] No supported Python with tkinter found."
  macos_python_install_hint
  exit 1
fi

if [ -x "$VENV_PYTHON" ] && "$VENV_PYTHON" -c "import tkinter" >/dev/null 2>&1; then
  echo "[OK] Existing .venv-macos has tkinter"
else
  echo "[WARN] .venv-macos missing or not Tk-capable yet"
fi

if [ -f "$PLAYER_LOG" ]; then
  echo "[OK] Player.log found: $PLAYER_LOG"
else
  echo "[WARN] Player.log not found yet: $PLAYER_LOG"
fi

echo "[INFO] macOS permissions still need to be granted manually:"
echo "       Accessibility and Screen Recording for Terminal and the .venv-macos Python binary."
echo "[INFO] MTGA still needs: English, Windowed, exact 16:9 resolution, Detailed Logs ON."
