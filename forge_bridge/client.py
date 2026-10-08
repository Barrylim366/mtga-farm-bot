"""Runs the Forge advisor (a Java process) and talks to it over stdin/stdout, one JSON object per line."""
from __future__ import annotations

import glob
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from forge_bridge.paths import ADVISOR_DIR, forge_dir, tools_dir

FORGE_ASSETS = forge_dir() / "forge-gui"


class AdvisorError(RuntimeError):
    pass


def _find_java() -> str:
    env = os.environ.get("BURNING_FORGE_JAVA_HOME")
    homes = [env] if env else sorted(glob.glob(str(tools_dir() / "jdk-*")), reverse=True)
    for home in homes:
        candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    return "java"


def _classpath() -> str:
    deps = ADVISOR_DIR / "build" / "forge-deps.classpath"
    classes = ADVISOR_DIR / "build" / "classes"
    jars = glob.glob(str(FORGE_ASSETS / "target" / "forge-gui-*.jar"))
    if not deps.exists() or not classes.exists() or not jars:
        raise AdvisorError("advisor is not built; run: python scripts/build_advisor.py --forge")
    return os.pathsep.join([str(classes), jars[0], deps.read_text(encoding="utf-8").strip()])


class AdvisorClient:
    """One advisor process. Not safe for concurrent requests; callers serialise through `request`."""

    def __init__(self, startup_timeout: float = 180.0, heap: str = "2g"):
        self._startup_timeout = startup_timeout
        self._heap = heap
        self._proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self._log = None
        self.ready_info: dict = {}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> dict:
        with self._lock:
            return self._start_locked()

    def _start_locked(self) -> dict:
        if self._proc is not None and self._proc.poll() is None:
            return self.ready_info
        run_dir = ADVISOR_DIR / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        args_file = ADVISOR_DIR / "build" / "java.args"
        # an argument file keeps the long classpath off the command line
        args_file.write_text(
            '-Xmx{}\n-Dfile.encoding=UTF-8\n-Djava.awt.headless=true\n-Dforge.assets="{}/"\n-cp "{}"\n'.format(
                self._heap,
                FORGE_ASSETS.as_posix(),
                _classpath().replace("\\", "/"),
            ),
            encoding="utf-8",
        )
        self._log = open(run_dir / "advisor.log", "ab")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            [_find_java(), f"@{args_file}", "burningforge.advisor.AdvisorMain"],
            cwd=str(run_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            creationflags=creationflags,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc, self._lines), daemon=True).start()
        line = self._read_line(self._startup_timeout)
        if line is None:
            self._kill_locked()
            raise AdvisorError("advisor did not start; see forge_advisor/run/advisor.log")
        self.ready_info = json.loads(line)
        return self.ready_info

    @staticmethod
    def _pump(proc: subprocess.Popen, lines: "queue.Queue[Optional[str]]") -> None:
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                lines.put(raw.decode("utf-8", errors="replace"))
        finally:
            lines.put(None)

    def _read_line(self, timeout: float) -> Optional[str]:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def _kill_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            for pipe in (proc.stdin, proc.stdout):
                try:
                    if pipe is not None:
                        pipe.close()
                except Exception:
                    pass
        if self._log is not None:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None

    def close(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(b'{"op":"quit"}\n')  # type: ignore[union-attr]
                    self._proc.stdin.flush()  # type: ignore[union-attr]
                    self._proc.wait(timeout=3)
                except Exception:
                    pass
            self._kill_locked()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------ requests
    def request(self, payload: dict, timeout: float = 10.0) -> dict:
        """Send one request and wait for its reply.

        The advisor enforces `timeoutMs` itself and answers with an error; the extra second here only
        covers a process that has stopped answering at all, which is then killed so the next request
        starts a fresh one.
        """
        with self._lock:
            if not self.running:
                raise AdvisorError("advisor is not running")
            self._next_id += 1
            request_id = self._next_id
            body = dict(payload)
            body["id"] = request_id
            body.setdefault("timeoutMs", int(timeout * 1000))
            try:
                self._proc.stdin.write((json.dumps(body) + "\n").encode("utf-8"))  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
            except Exception as exc:
                self._kill_locked()
                raise AdvisorError(f"advisor pipe closed: {exc}") from exc
            deadline = time.time() + timeout + 1.0
            while True:
                line = self._read_line(max(0.05, deadline - time.time()))
                if line is None:
                    self._kill_locked()
                    raise AdvisorError("advisor stopped answering")
                try:
                    reply: dict[str, Any] = json.loads(line)
                except ValueError:
                    continue
                if reply.get("id") == request_id:
                    return reply
                if time.time() > deadline:
                    self._kill_locked()
                    raise AdvisorError("advisor stopped answering")

    def decide(self, kind: str, state: dict, timeout: float = 10.0, **extra: Any) -> dict:
        payload = {"op": "decide", "kind": kind, "state": state}
        payload.update(extra)
        reply = self.request(payload, timeout=timeout)
        if not reply.get("ok"):
            raise AdvisorError(str(reply.get("error")))
        return reply
