#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv-macos"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"
REQ_FILE="$REPO_DIR/requirements.txt"
MARKER="$VENV_DIR/.requirements.installed"

alert_warning() {
  local msg="$1"
  osascript -e "display alert \"Burning Lotus Bot\" message \"$msg\" as warning" >/dev/null 2>&1 || true
  echo "$msg" >&2
}

tk_install_hint() {
  local py_ver=""
  py_ver="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  if command -v brew >/dev/null 2>&1 && [ -n "$py_ver" ]; then
    printf "Homebrew Python ohne Tk erkannt.\nInstalliere Tk passend zu Python %s:\n  brew install python-tk@%s\n\nAlternativ Python direkt von python.org verwenden.\nWenn du die Python-Version wechselst, loesche danach .venv-macos und starte den Launcher erneut." "$py_ver" "$py_ver"
    return
  fi
  printf "Python ohne Tk erkannt.\nInstalliere eine Python-Version mit Tk-Unterstuetzung (empfohlen: python.org Installer).\nWenn du die Python-Version wechselst, loesche danach .venv-macos und starte den Launcher erneut."
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  alert_warning "Python 3 nicht gefunden. Bitte Python 3.10 oder neuer installieren: https://www.python.org/downloads/"
  exit 1
fi

if ! "$PYTHON_BIN" -c "import tkinter" >/dev/null 2>&1; then
  alert_warning "$(tk_install_hint)"
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR" || {
    alert_warning "Konnte virtuelle Umgebung nicht erstellen: $VENV_DIR"
    exit 1
  }
fi

if [ ! -x "$VENV_PYTHON" ]; then
  alert_warning "Python venv fehlt: .venv-macos/bin/python"
  exit 1
fi

if [ ! -f "$MARKER" ] || [ "$REQ_FILE" -nt "$MARKER" ]; then
  "$VENV_PYTHON" -m pip install --upgrade pip || {
    alert_warning "Konnte pip in .venv-macos nicht aktualisieren."
    exit 1
  }
  "$VENV_PYTHON" -m pip install -r "$REQ_FILE" || {
    alert_warning "Konnte erforderliche Pakete nicht installieren."
    exit 1
  }
  touch "$MARKER"
fi

cd "$REPO_DIR"
exec "$VENV_PYTHON" ui.py
