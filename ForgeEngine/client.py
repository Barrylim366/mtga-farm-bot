"""Runs the Forge advisor (a Java process) and talks to it over stdin/stdout.

One JSON object per line in each direction. The advisor answers requests one at
a time and enforces each request's ``timeoutMs`` itself; the extra second here
only covers a process that has stopped answering altogether, which is then
killed so the next request starts a fresh one.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from ForgeEngine import paths


class AdvisorError(Exception):
    pass


def _forward(path) -> str:
    return str(path).replace("\\", "/")


def profile_text() -> str:
    """Tells Forge to keep its preferences and caches under run/, not AppData."""
    run = paths.run_dir()
    return f"userDir={_forward(run / 'user')}\ncacheDir={_forward(run / 'cache')}\n"


def classpath() -> str:
    """The advisor's classes and every jar in lib/, as Java wants them."""
    lib = paths.lib_dir()
    jars = sorted(p for p in lib.glob("*.jar")) if lib.is_dir() else []
    if not jars:
        raise AdvisorError(f"Forge engine is not installed ({lib} has no jars)")
    separator = ";" if sys.platform.startswith("win") else ":"
    return separator.join(_forward(jar) for jar in jars)


class AdvisorClient:
    """One advisor process. Thread-safe; requests are answered one at a time."""

    def __init__(self, startup_timeout: float = 180.0, heap: str | None = None):
        self.startup_timeout = startup_timeout
        self.heap = heap or os.environ.get("MTGA_FORGE_HEAP", "1g")
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._lines: queue.Queue | None = None
        self._next_id = 0
        self.ready_info: dict = {}

    # -- lifecycle ------------------------------------------------------------

    def running(self) -> bool:
        with self._lock:
            return self._alive()

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _kill(self) -> None:
        process, self._process = self._process, None
        self._lines = None
        if process is None:
            return
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except Exception:
            pass

    def start(self) -> dict:
        """Start the advisor if it is not running; returns its ready line."""
        with self._lock:
            if self._alive():
                return self.ready_info
            return self._start_locked()

    def _start_locked(self) -> dict:
        run = paths.run_dir()
        for folder in (run, run / "user", run / "cache"):
            folder.mkdir(parents=True, exist_ok=True)
        # Forge reads where to keep its files from a file beside its card
        # scripts. The engine folder can have been moved (or packaged
        # elsewhere), so it is put right before every start.
        profile = paths.assets_dir() / "forge.profile.properties"
        wanted = profile_text()
        try:
            current = profile.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != wanted:
            profile.write_text(wanted, encoding="utf-8")
        # An argument file keeps the long classpath off the command line.
        args_file = run / "java.args"
        args_file.write_text(
            f"-Xmx{self.heap}\n"
            "-XX:+UseG1GC\n"
            "-XX:+UseStringDeduplication\n"
            "-Dfile.encoding=UTF-8\n"
            "-Djava.awt.headless=true\n"
            f'-Dforge.assets="{_forward(paths.assets_dir())}/"\n'
            f'-cp "{classpath()}"\n',
            encoding="utf-8",
        )
        java = paths.java_executable()
        if not java.exists():
            raise AdvisorError(f"Java runtime missing: {java}")
        log = open(run / "advisor.log", "ab")
        flags = 0
        if sys.platform.startswith("win"):
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                [str(java), f"@{args_file}", "burningforge.advisor.AdvisorMain"],
                cwd=str(run),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                creationflags=flags,
            )
        finally:
            log.close()
        lines: queue.Queue = queue.Queue()

        def pump() -> None:
            try:
                if process.stdout is not None:
                    for raw in process.stdout:
                        lines.put(raw.decode("utf-8", errors="replace"))
            except Exception:
                pass
            lines.put(None)

        threading.Thread(target=pump, name="forge-advisor-output", daemon=True).start()
        self._process, self._lines = process, lines
        line = self._read_line(self.startup_timeout)
        if line is None:
            self._kill()
            raise AdvisorError(f"advisor did not start; see {run / 'advisor.log'}")
        try:
            self.ready_info = json.loads(line)
        except ValueError as exc:
            self._kill()
            raise AdvisorError(f"advisor sent no ready line: {line[:200]!r}") from exc
        return self.ready_info

    def _read_line(self, timeout: float) -> str | None:
        lines = self._lines
        if lines is None:
            return None
        try:
            return lines.get(timeout=max(0.05, timeout))
        except queue.Empty:
            return None

    def close(self) -> None:
        with self._lock:
            if self._alive():
                proc = self._process
                if proc is not None and proc.stdin is not None:
                    try:
                        proc.stdin.write(b'{"op":"quit"}\n')
                        proc.stdin.flush()
                        proc.wait(timeout=3)
                    except Exception:
                        pass
            self._kill()

    # -- requests -------------------------------------------------------------

    def request(self, payload: dict, timeout_sec: float) -> dict:
        with self._lock:
            if not self._alive():
                raise AdvisorError("advisor is not running")
            self._next_id += 1
            request_id = self._next_id
            body = dict(payload)
            body["id"] = request_id
            body.setdefault("timeoutMs", int(timeout_sec * 1000))
            proc = self._process
            if proc is None or proc.stdin is None:
                self._kill()
                raise AdvisorError("advisor process or stdin is unavailable")
            try:
                proc.stdin.write((json.dumps(body) + "\n").encode("utf-8"))
                proc.stdin.flush()
            except OSError as exc:
                self._kill()
                raise AdvisorError(f"advisor pipe closed: {exc}") from exc
            deadline = time.monotonic() + max(0.0, timeout_sec) + 1.0
            while True:
                line = self._read_line(deadline - time.monotonic())
                if line is None:
                    self._kill()
                    raise AdvisorError("advisor stopped answering")
                try:
                    reply = json.loads(line)
                except ValueError:
                    reply = None
                if isinstance(reply, dict) and reply.get("id") == request_id:
                    return reply
                if time.monotonic() > deadline:
                    self._kill()
                    raise AdvisorError("advisor stopped answering")

    def decide(self, kind: str, state: dict, timeout_sec: float, extra: dict | None = None) -> dict:
        payload: dict[str, Any] = {"op": "decide", "kind": kind, "state": state}
        payload.update(extra or {})
        reply = self.request(payload, timeout_sec)
        if not reply.get("ok"):
            raise AdvisorError(str(reply.get("error")))
        return reply


def environment_ok() -> bool:
    """Cheap check used by the UI: is there anything to start at all?"""
    return paths.installed() and os.access(str(paths.java_executable()), os.X_OK)
