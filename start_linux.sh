#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"
REQ_FILE="$ROOT_DIR/requirements.txt"
MARKER="$VENV_DIR/.requirements.installed"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[ERROR] $PYTHON_BIN nicht gefunden. Bitte Python 3.10 oder neuer installieren." >&2
  exit 1
fi

detect_pkg_manager() {
  if command -v pacman >/dev/null 2>&1; then echo "pacman"; return; fi
  if command -v apt-get >/dev/null 2>&1; then echo "apt"; return; fi
  if command -v dnf >/dev/null 2>&1; then echo "dnf"; return; fi
  if command -v zypper >/dev/null 2>&1; then echo "zypper"; return; fi
  echo "unknown"
}

install_hint() {
  local pkg_pacman="$1" pkg_apt="$2" pkg_dnf="$3" pkg_zypper="$4"
  case "$(detect_pkg_manager)" in
    pacman) echo "  sudo pacman -S --needed $pkg_pacman" ;;
    apt)    echo "  sudo apt install $pkg_apt" ;;
    dnf)    echo "  sudo dnf install $pkg_dnf" ;;
    zypper) echo "  sudo zypper install $pkg_zypper" ;;
    *)      echo "  (Paket bitte ueber deinen Paketmanager installieren)" ;;
  esac
}

MISSING_SYSTEM_DEPS=0

if ! "$PYTHON_BIN" -c "import tkinter" >/dev/null 2>&1; then
  echo "[WARN] Python-Modul 'tkinter' fehlt — die UI kann ohne nicht starten."
  echo "        Installation:"
  install_hint "tk" "python3-tk" "python3-tkinter" "python3-tk"
  MISSING_SYSTEM_DEPS=1
fi

if ! command -v xwininfo >/dev/null 2>&1; then
  echo "[WARN] 'xwininfo' fehlt — ohne das wird das MTGA-Fenster auf Linux nicht erkannt."
  echo "        Installation:"
  install_hint "xorg-xwininfo" "x11-utils" "xorg-x11-utils" "xwininfo"
  MISSING_SYSTEM_DEPS=1
fi

SCREENSHOT_TOOL=""
for candidate in grim spectacle gnome-screenshot scrot; do
  if command -v "$candidate" >/dev/null 2>&1; then
    SCREENSHOT_TOOL="$candidate"
    break
  fi
done
if [[ -z "$SCREENSHOT_TOOL" ]]; then
  echo "[WARN] Kein Screenshot-Tool gefunden (grim / spectacle / gnome-screenshot / scrot)."
  echo "        Empfohlen je nach Desktop:"
  echo "          KDE:           $(install_hint "spectacle" "kde-spectacle" "spectacle" "spectacle" | sed 's/^  //')"
  echo "          GNOME:         $(install_hint "gnome-screenshot" "gnome-screenshot" "gnome-screenshot" "gnome-screenshot" | sed 's/^  //')"
  echo "          Wayland wlroots: $(install_hint "grim" "grim" "grim" "grim" | sed 's/^  //')"
  echo "          X11 fallback:  $(install_hint "scrot" "scrot" "scrot" "scrot" | sed 's/^  //')"
  MISSING_SYSTEM_DEPS=1
else
  echo "[INFO] Screenshot-Tool: $SCREENSHOT_TOOL"
fi

if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]] || [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
  if ! command -v ydotool >/dev/null 2>&1; then
    echo "[WARN] Wayland-Session erkannt, aber 'ydotool' fehlt."
    echo "        Unter Wayland kann der Bot ohne ydotool keine Mausklicks senden."
    echo "        Installation:"
    install_hint "ydotool" "ydotool" "ydotool" "ydotool"
    MISSING_SYSTEM_DEPS=1
  else
    if command -v systemctl >/dev/null 2>&1 && ! systemctl --user is-active --quiet ydotool; then
      echo "[INFO] Starte ydotool.service für Wayland..."
      systemctl --user start ydotool 2>/dev/null || true
    fi
  fi
fi

if [[ $MISSING_SYSTEM_DEPS -ne 0 ]]; then
  echo ""
  echo "[INFO] Bitte die oben genannten Pakete installieren und dann erneut starten."
  echo "       (Der Start wird in 5 Sekunden fortgesetzt — Abbruch mit Strg+C.)"
  sleep 5
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[INFO] Erstelle virtuelle Umgebung in $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[ERROR] Venv-Python nicht gefunden: $VENV_PYTHON" >&2
  exit 1
fi

# Marker is a copy of the last installed requirements.txt; content compare
# skips reinstalling when only the file timestamp changed (e.g. git pull).
if [[ ! -f "$MARKER" ]] || ! cmp -s "$REQ_FILE" "$MARKER"; then
  echo "[INFO] Installiere Abhaengigkeiten aus requirements.txt"
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r "$REQ_FILE"
  cp "$REQ_FILE" "$MARKER"
else
  echo "[INFO] Abhaengigkeiten unveraendert - starte direkt."
fi

exec "$VENV_PYTHON" "$ROOT_DIR/ui.py"
