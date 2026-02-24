import json
import os
import sys
from pathlib import Path
from typing import Any

APP_DIR_NAME = "BurningLotus"
LICENSE_FILE_NAME = "license.json"


def get_license_dir_path() -> Path:
    home = Path.home()
    if os.name == "nt" or sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / APP_DIR_NAME
        return home / "AppData" / "Roaming" / APP_DIR_NAME
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    return home / ".config" / APP_DIR_NAME.lower()


def get_license_file_path() -> Path:
    return get_license_dir_path() / LICENSE_FILE_NAME


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_license_state() -> dict[str, Any]:
    path = get_license_file_path()
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_license_state(data: dict[str, Any]) -> Path:
    path = get_license_file_path()
    _ensure_dir(path.parent)
    payload = data if isinstance(data, dict) else {}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
