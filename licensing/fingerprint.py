import base64
import hashlib
import os
import re
import subprocess
import sys

PRODUCT_NAME = "BurningLotusBot"
DEVICE_PREFIX = f"{PRODUCT_NAME}|"
DEFAULT_DEVICE_ID_LENGTH = 32


def _read_windows_machine_guid() -> str:
    try:
        import winreg
    except Exception as exc:
        raise RuntimeError("Windows registry access unavailable.") from exc

    key_path = r"SOFTWARE\Microsoft\Cryptography"
    access_modes = [0]
    wow64_64 = getattr(winreg, "KEY_WOW64_64KEY", 0)
    wow64_32 = getattr(winreg, "KEY_WOW64_32KEY", 0)
    if wow64_64:
        access_modes.append(wow64_64)
    if wow64_32:
        access_modes.append(wow64_32)

    for extra_access in access_modes:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                key_path,
                0,
                winreg.KEY_READ | extra_access,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            machine_guid = str(value).strip()
            if machine_guid:
                return machine_guid
        except OSError:
            continue

    raise RuntimeError("MachineGuid not found in Windows registry.")


def _read_linux_machine_id() -> str:
    candidates = [
        "/etc/machine-id",
        "/var/lib/dbus/machine-id",
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = f.read().strip()
            if value:
                return value
        except OSError:
            continue
    raise RuntimeError("Linux machine-id not found.")


def _read_macos_platform_uuid() -> str:
    cmd = ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]
    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except Exception as exc:
        raise RuntimeError("Unable to query IOPlatformUUID via ioreg.") from exc

    match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', output)
    if not match:
        raise RuntimeError("IOPlatformUUID not found in ioreg output.")
    value = match.group(1).strip()
    if not value:
        raise RuntimeError("IOPlatformUUID is empty.")
    return value


def get_device_fingerprint_raw() -> str:
    if os.name == "nt" or sys.platform.startswith("win"):
        return _read_windows_machine_guid()
    if sys.platform == "darwin":
        return _read_macos_platform_uuid()
    return _read_linux_machine_id()


def get_device_id(length: int = DEFAULT_DEVICE_ID_LENGTH) -> str:
    if length < 8 or length > 52:
        raise ValueError("length must be between 8 and 52 characters.")

    raw = get_device_fingerprint_raw()
    digest = hashlib.sha256((DEVICE_PREFIX + raw).encode("utf-8")).digest()
    token = base64.b32encode(digest).decode("ascii").rstrip("=")
    return token[:length]


def get_device_id_hash(device_id: str | None = None) -> str:
    resolved = device_id or get_device_id()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()
