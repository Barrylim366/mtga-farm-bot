import json
import os
import sys
from pathlib import Path
from typing import Any

APP_DIR_NAME_WINDOWS = "BurningLotusBot"
APP_DIR_NAME_UNIX = "burninglotusbot"
LICENSE_FILE_NAME = "license.bllic"
STATUS_CACHE_FILE_NAME = "license_status.json"


def get_license_dir_path() -> Path:
    home = Path.home()
    if os.name == "nt" or sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / APP_DIR_NAME_WINDOWS
        return home / "AppData" / "Roaming" / APP_DIR_NAME_WINDOWS
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME_WINDOWS
    return home / ".config" / APP_DIR_NAME_UNIX


def get_license_file_path() -> Path:
    return get_license_dir_path() / LICENSE_FILE_NAME


def get_status_cache_path() -> Path:
    return get_license_dir_path() / STATUS_CACHE_FILE_NAME


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_license_text() -> str | None:
    path = get_license_file_path()
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def save_license_text(license_text: str) -> Path:
    path = get_license_file_path()
    _ensure_dir(path.parent)
    path.write_text((license_text or "").strip() + "\n", encoding="utf-8")
    return path


def load_status_cache() -> dict[str, Any]:
    path = get_status_cache_path()
    if not path.is_file():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def save_status_cache(data: dict[str, Any]) -> Path:
    path = get_status_cache_path()
    _ensure_dir(path.parent)
    payload = data if isinstance(data, dict) else {}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
