#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$REPO_DIR/.venv-macos/bin/python"

if [ ! -x "$PYTHON_BIN" ]; then
  osascript -e 'display alert "Burning Lotus Bot" message "Python venv fehlt: .venv-macos/bin/python\nBitte Setup zuerst ausführen." as warning'
  exit 1
fi

cd "$REPO_DIR"
exec "$PYTHON_BIN" ui.py
