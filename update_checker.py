from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from runtime_paths import get_app_root, get_repo_root

_GIT_TIMEOUT_SECONDS = 6
# `git fetch` talks to GitHub; 6s is not enough for a cold TLS handshake or a
# credential helper that has to unlock a keychain (seen on macOS).
_GIT_NETWORK_TIMEOUT_SECONDS = 30

# --- Release channel (used when the install is NOT a git checkout) ----------
# Non-git installs (e.g. users who downloaded a ZIP from GitHub/a website)
# cannot `git pull`, so they follow `main` via the raw version.py + branch ZIP.
_GITHUB_OWNER = "Barrylim366"
_GITHUB_REPO = "mtga-farm-bot"
_GITHUB_BRANCH = "main"
_RAW_VERSION_URL = (
    f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}/{_GITHUB_BRANCH}/version.py"
)
_ARCHIVE_URL = (
    f"https://codeload.github.com/{_GITHUB_OWNER}/{_GITHUB_REPO}/zip/refs/heads/{_GITHUB_BRANCH}"
)
_HTTP_TIMEOUT_SECONDS = 15
_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")

# Belt-and-suspenders: GitHub's archive already omits git-ignored user data,
# but never let a stray archive entry clobber these local files/dirs.
_OVERLAY_SKIP = {".git", ".venv", ".venv-macos", "runtime", "Accounts", "credentials.txt", ".claude"}


def _run_git(args: list[str], timeout: int = _GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        ["git", *args],
        cwd=str(get_repo_root()),
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=creationflags,
    )


@dataclass
class UpdateCheckResult:
    update_available: bool
    # "git" for git checkouts (git pull), "release" for ZIP installs (branch archive).
    kind: str = "git"
    branch: str = ""
    local_sha: str = ""
    remote_sha: str = ""
    # Populated for the "release" (non-git) path.
    current_version: str = ""
    latest_version: str = ""
    download_url: str = ""
    error: str = ""


def is_git_checkout() -> bool:
    return (get_repo_root() / ".git").exists()


def check_for_update() -> UpdateCheckResult:
    """Compares local HEAD against the remote branch tip.

    Only updates the local `.git` metadata (fetches the remote-tracking ref
    for `branch`) - it never touches the working tree, so it is safe to call
    from a background thread at startup.
    """
    if not is_git_checkout():
        return UpdateCheckResult(update_available=False, error="not a git checkout")

    try:
        branch_proc = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if branch_proc.returncode != 0:
            return UpdateCheckResult(update_available=False, error=branch_proc.stderr.strip())
        branch = branch_proc.stdout.strip()
        if not branch or branch == "HEAD":
            return UpdateCheckResult(update_available=False, error="detached HEAD")

        local_proc = _run_git(["rev-parse", "HEAD"])
        if local_proc.returncode != 0:
            return UpdateCheckResult(update_available=False, error=local_proc.stderr.strip())
        local_sha = local_proc.stdout.strip()

        fetch_proc = _run_git(["fetch", "--quiet", "origin", branch], timeout=_GIT_NETWORK_TIMEOUT_SECONDS)
        if fetch_proc.returncode != 0:
            return UpdateCheckResult(update_available=False, error=fetch_proc.stderr.strip())

        remote_proc = _run_git(["rev-parse", "FETCH_HEAD"])
        if remote_proc.returncode != 0:
            return UpdateCheckResult(update_available=False, error=remote_proc.stderr.strip())
        remote_sha = remote_proc.stdout.strip()

        if remote_sha == local_sha:
            return UpdateCheckResult(update_available=False, branch=branch, local_sha=local_sha, remote_sha=remote_sha)

        # Only offer an update when the remote tip is actually ahead of local
        # (a fast-forward pull would apply). If local has unpushed commits or
        # the two have diverged, remote_sha != local_sha but there is nothing
        # useful to pull, so don't show a misleading "update available" dialog.
        ancestor_proc = _run_git(["merge-base", "--is-ancestor", local_sha, remote_sha])
        update_available = ancestor_proc.returncode == 0

        return UpdateCheckResult(
            update_available=update_available,
            branch=branch,
            local_sha=local_sha,
            remote_sha=remote_sha,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return UpdateCheckResult(update_available=False, error=str(exc))


def check_for_updates() -> UpdateCheckResult:
    """Single entry point for the UI.

    Picks the right update channel automatically: git checkouts pull from
    their tracked branch; everything else (ZIP downloads) follows `main` via
    the raw version.py + branch archive.
    """
    if is_git_checkout():
        return check_for_update()
    if getattr(sys, "frozen", False):
        # A frozen build can't be updated by overlaying .py files next to the
        # exe; it needs its own installer. Skip rather than nag every startup.
        return UpdateCheckResult(update_available=False, kind="release", error="frozen build not updatable via archive")
    return check_for_release_update()


# --- Version helpers --------------------------------------------------------


def _parse_version(text: str) -> str:
    match = _VERSION_RE.search(text or "")
    return match.group(1).strip() if match else ""


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in (version or "").split("."):
        digits = re.match(r"\d+", chunk.strip())
        parts.append(int(digits.group(0)) if digits else 0)
    return tuple(parts) or (0,)


def _is_newer(candidate: str, current: str) -> bool:
    a, b = _version_tuple(candidate), _version_tuple(current)
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    return a > b


def _local_version() -> str:
    # Prefer the version the running process actually loaded (also works in a
    # frozen build where version.py isn't a loose file on disk).
    try:
        import version  # noqa: PLC0415

        loaded = getattr(version, "__version__", "")
        if loaded:
            return str(loaded).strip()
    except Exception:
        pass
    version_path = get_app_root() / "version.py"
    try:
        return _parse_version(version_path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def _http_get(url: str, timeout: int = _HTTP_TIMEOUT_SECONDS) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "burning-lotus-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (trusted GitHub host)
        return response.read()


def check_for_release_update() -> UpdateCheckResult:
    """Compares the local version.py against the one on `main` at GitHub.

    Network-only and read-only: it never touches the working tree, so it is
    safe to call from a background thread at startup.
    """
    current = _local_version()
    if not current:
        # Can't determine what we're running — don't offer a blind "update"
        # (an empty version would compare as older than everything).
        return UpdateCheckResult(update_available=False, kind="release", error="could not determine local version")
    try:
        remote_text = _http_get(_RAW_VERSION_URL).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return UpdateCheckResult(update_available=False, kind="release", current_version=current, error=str(exc))

    latest = _parse_version(remote_text)
    if not latest:
        return UpdateCheckResult(
            update_available=False,
            kind="release",
            current_version=current,
            error="could not read remote version",
        )

    return UpdateCheckResult(
        update_available=_is_newer(latest, current),
        kind="release",
        branch=_GITHUB_BRANCH,
        current_version=current,
        latest_version=latest,
        download_url=_ARCHIVE_URL,
    )


@dataclass
class UpdateResult:
    success: bool
    message: str
    requirements_changed: bool = False


def apply_update(branch: str) -> UpdateResult:
    """Pulls the latest changes and reinstalls requirements if they changed."""
    repo_root = get_repo_root()
    req_path = repo_root / "requirements.txt"

    try:
        req_before = str(req_path.read_text(encoding="utf-8")) if req_path.exists() else None

        # Only *tracked* changes can be clobbered by a pull, and only those
        # should block the update. Untracked files must not: the macOS launcher
        # creates `.venv-macos/` inside the repo folder, which older clones do
        # not have in .gitignore - counting it as "dirty" made the update abort
        # on every single macOS git install.
        status_proc = _run_git(["status", "--porcelain", "--untracked-files=no"])
        if status_proc.returncode == 0 and status_proc.stdout.strip():
            dirty_files = [line[3:].strip() for line in status_proc.stdout.splitlines() if line.strip()]
            file_list = "\n".join(dirty_files[:10])
            return UpdateResult(
                success=False,
                message=(
                    "Local changes detected in the bot folder. Update aborted to avoid overwriting them.\n\n"
                    f"Affected file(s):\n{file_list}"
                ),
            )

        pull_proc = _run_git(["pull", "--ff-only", "origin", branch], timeout=60)
        if pull_proc.returncode != 0:
            return UpdateResult(success=False, message=pull_proc.stderr.strip() or pull_proc.stdout.strip())

        req_after = str(req_path.read_text(encoding="utf-8")) if req_path.exists() else None
        requirements_changed = req_before != req_after

        if requirements_changed and req_path.exists():
            pip_proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if pip_proc.returncode != 0:
                return UpdateResult(
                    success=False,
                    message="Update pulled, but installing new dependencies failed:\n"
                    + (pip_proc.stderr.strip() or pip_proc.stdout.strip()),
                    requirements_changed=True,
                )

        return UpdateResult(success=True, message="Update installed successfully.", requirements_changed=requirements_changed)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return UpdateResult(success=False, message=str(exc))


def _pip_install_requirements(req_path: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _archive_top_dir(extract_root: Path) -> Path | None:
    """GitHub branch archives wrap everything in a single top-level folder."""
    entries = [p for p in extract_root.iterdir() if p.is_dir()]
    return entries[0] if len(entries) == 1 else None


def _zip_executables(zip_path: Path) -> set[str]:
    """Rel paths (below the archive's top folder) that GitHub stored as +x.

    `zipfile.extractall` drops Unix permission bits, so the extracted copies of
    `start_macos.command` / `*.sh` come out non-executable and the launcher a
    macOS/Linux user double-clicks would stop working after an update. The bits
    are still in the archive, so read them back and re-apply them below.
    """
    executables: set[str] = set()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not (info.external_attr >> 16) & 0o111:
                    continue
                parts = info.filename.split("/")[1:]  # strip the top folder
                if parts:
                    executables.add("/".join(parts))
    except (zipfile.BadZipFile, OSError):
        return set()
    return executables


def _atomic_overlay(src: Path, dest: Path) -> None:
    """Copies src onto dest atomically so a crash can't leave a half-written file.

    Writes to a temp sibling first, then os.replace()s it into place (atomic on
    the same volume). Clears a read-only bit on an existing dest so the replace
    can't fail on files copy2 previously marked read-only.
    """
    tmp = dest.with_name(dest.name + ".new-update")
    shutil.copy2(src, tmp)
    try:
        os.replace(tmp, dest)
    except PermissionError:
        if dest.exists():
            os.chmod(dest, stat.S_IWRITE)
        os.replace(tmp, dest)


def apply_zip_update(download_url: str = _ARCHIVE_URL) -> UpdateResult:
    """Downloads the `main` branch archive and overlays it onto the install.

    Used by non-git (ZIP) installs. The archive only contains tracked files,
    so overlaying it never touches user data (runtime/, Accounts/, .venv,
    credentials.txt, ...). Files removed upstream are left in place.
    """
    app_root = get_app_root()
    req_path = app_root / "requirements.txt"

    try:
        req_before = str(req_path.read_text(encoding="utf-8")) if req_path.exists() else None

        with tempfile.TemporaryDirectory(prefix="burning-lotus-update-") as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "update.zip"
            try:
                zip_path.write_bytes(_http_get(download_url, timeout=120))
            except (urllib.error.URLError, OSError) as exc:
                return UpdateResult(success=False, message=f"Download failed: {exc}")

            extract_root = tmp_path / "extracted"
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_root)
            except (zipfile.BadZipFile, OSError) as exc:
                return UpdateResult(success=False, message=f"Downloaded archive is invalid: {exc}")

            source_root = _archive_top_dir(extract_root)
            if source_root is None:
                # Not the expected single-top-folder layout: bail out instead of
                # silently copying the wrapper folder and reporting success.
                return UpdateResult(success=False, message="Unexpected archive layout; update aborted.")

            executables = _zip_executables(zip_path)

            # Overlay every file from the archive onto the install directory.
            for src in source_root.rglob("*"):
                rel = src.relative_to(source_root)
                if rel.parts and rel.parts[0] in _OVERLAY_SKIP:
                    continue
                dest = app_root / rel
                if src.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_overlay(src, dest)
                    if os.name != "nt" and rel.as_posix() in executables:
                        mode = dest.stat().st_mode
                        dest.chmod(mode | ((mode & 0o444) >> 2))

        req_after = str(req_path.read_text(encoding="utf-8")) if req_path.exists() else None
        requirements_changed = req_before != req_after

        if requirements_changed and req_path.exists():
            pip_proc = _pip_install_requirements(req_path, app_root)
            if pip_proc.returncode != 0:
                return UpdateResult(
                    success=False,
                    message="Update installed, but installing new dependencies failed:\n"
                    + (pip_proc.stderr.strip() or pip_proc.stdout.strip()),
                    requirements_changed=True,
                )

        return UpdateResult(success=True, message="Update installed successfully.", requirements_changed=requirements_changed)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return UpdateResult(success=False, message=str(exc))


def apply_update_result(result: UpdateCheckResult) -> UpdateResult:
    """Applies an update using the channel recorded in the check result."""
    if result.kind == "release":
        return apply_zip_update(result.download_url or _ARCHIVE_URL)
    return apply_update(result.branch)


def restart_application() -> None:
    python = sys.executable
    os.execv(python, [python] + sys.argv)
