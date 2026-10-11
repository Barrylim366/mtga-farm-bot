"""Downloads and unpacks the Forge engine package into ``runtime/forge``.

The package (a Java runtime, Forge's jars and card scripts, the compiled
advisor) is built by ``tools/build_forge_package.py`` and published as a
GitHub release asset. Its SHA-256 is pinned here, so a download is only ever
unpacked when it is exactly the file that was built and published.

What Forge keeps between runs (``run/``: its preferences and the decision
log; ``cache/``: the export of Arena's card database) is carried over when a
newer package replaces an older one.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from ForgeEngine import paths

RELEASE_TAG = f"forge-engine-v{paths.PACKAGE_VERSION}"
ASSET_NAME = f"forge-engine-v{paths.PACKAGE_VERSION}-windows-x64.zip"
PACKAGE_URL = f"https://github.com/Barrylim366/mtga-farm-bot/releases/download/{RELEASE_TAG}/{ASSET_NAME}"
# Filled in by tools/build_forge_package.py --pin after the asset is built.
PACKAGE_SHA256 = ""
PACKAGE_SIZE_MB = 0

_KEEP = ("run", "cache")


class InstallError(Exception):
    pass


def _long(path: Path) -> Path:
    """Windows' extended-length form: some card script paths pass 260 characters
    once the bot sits in a deep folder."""
    if not sys.platform.startswith("win"):
        return path
    prefix = "\\\\?\\"
    text = str(path.resolve())
    return Path(text if text.startswith(prefix) else prefix + text)


def supported_platform() -> bool:
    return sys.platform.startswith("win")


def install(progress: Callable[[str, float], None] | None = None,
            url: str = PACKAGE_URL, sha256: str = PACKAGE_SHA256) -> None:
    """Download, verify and unpack the engine. ``progress(text, fraction)``."""
    say = progress or (lambda _text, _fraction: None)
    if not supported_platform():
        raise InstallError("The Forge engine package is only built for Windows.")
    if not sha256:
        raise InstallError("This bot version has no Forge engine package pinned yet.")
    target = _long(paths.install_dir())
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    download = parent / (ASSET_NAME + ".part")
    staging = parent / "forge.new"

    say("Downloading the Forge engine...", 0.0)
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, download.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    say(f"Downloading the Forge engine... {done >> 20} / {total >> 20} MB", 0.9 * done / total)
    except OSError as exc:
        download.unlink(missing_ok=True)
        raise InstallError(f"Download failed: {exc}") from exc
    if digest.hexdigest().lower() != sha256.lower():
        download.unlink(missing_ok=True)
        raise InstallError("The downloaded file does not match the published package (checksum mismatch).")

    say("Unpacking...", 0.92)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(download) as archive:
            root = staging.resolve()
            for member in archive.infolist():
                destination = (staging / member.filename).resolve()
                if root != destination and root not in destination.parents:
                    raise InstallError(f"Unsafe path in package: {member.filename}")
            archive.extractall(staging)
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallError(f"Unpacking failed: {exc}") from exc
    finally:
        download.unlink(missing_ok=True)

    say("Installing...", 0.97)
    if target.exists():
        for keep in _KEEP:
            old = target / keep
            if old.exists() and not (staging / keep).exists():
                shutil.move(str(old), str(staging / keep))
        shutil.rmtree(target, ignore_errors=True)
        if target.exists():
            raise InstallError(f"Could not replace {target}; is the bot's Forge engine still running?")
    staging.rename(target)
    if not paths.installed():
        raise InstallError("The package was unpacked but is incomplete.")
    say("Forge engine installed.", 1.0)


def remove() -> None:
    shutil.rmtree(_long(paths.install_dir()), ignore_errors=True)
