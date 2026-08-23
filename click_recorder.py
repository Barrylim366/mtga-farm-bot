"""Always-on click-verification log for debugging the visual/click layer.

Writes one JSONL line per mouse click to ``runtime/debug/clicks.jsonl`` so a
human or an AI agent can reconstruct, after the fact, *what* was clicked, *where*
(raw vs. arena-mapped point), *when*, with which window reference, and whether
the click was risky (arena window lost / stale). This answers the two failure
modes that the per-decision game-state recorder cannot: the bot clicking the
wrong screen position, or the right position at the wrong time.

Fed from ``bot_logger.log_click`` (the one call that runs immediately before
every physical click on both click paths), so there is a single hook point and
no new chokepoint. I/O runs on a daemon writer thread; the click path never
waits on disk. Correlate a click back to the decision that caused it via the
``decision_seq`` field (the DecisionRecorder's last sequence number). This is a
best-effort join key: on the main decision path it is exact (the seq is assigned
before the move executes), but clicks fired later from threading.Timer callbacks
(inactivity-resolve, combat recovery) or the select_n path may carry a
neighbouring seq -- use it together with the timestamps, not as a hard link.

Env: ``MTGA_DEBUG_CLICKS=0`` disables it (default: on).
"""
from __future__ import annotations

import collections
import json
import os
import queue
import threading
from datetime import datetime, timezone

import bot_logger

try:
    import debug_recorder
except Exception:  # pragma: no cover
    debug_recorder = None

# Rotate the JSONL once it crosses this size; keep one .1 backup.
_MAX_BYTES = 5 * 1024 * 1024
_QUEUE_MAXSIZE = 1000


def _enabled() -> bool:
    return os.environ.get("MTGA_DEBUG_CLICKS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


class ClickRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._writer: threading.Thread | None = None
        self._started = False
        self._path = None
        self._drop_warned = False
        # The file is the record of truth, but a bundle written mid-incident
        # cannot read its own tail reliably (the writer thread may not have
        # flushed yet). Keep the last few in memory so a debug bundle can carry
        # "what was clicked just before this" with it.
        self._recent: "collections.deque" = collections.deque(maxlen=24)

    def start_session(self) -> None:
        if not _enabled():
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            self._writer = threading.Thread(
                target=self._run_writer, name="ClickRecorder", daemon=True
            )
            self._writer.start()

    def record(self, x, y, purpose, *, source=None, region_age=None, arena=None, risky=None) -> None:
        if not self._started:
            return
        try:
            decision_seq = None
            if debug_recorder is not None:
                try:
                    decision_seq = debug_recorder.current_seq()
                except Exception:
                    decision_seq = None
            if risky is None:
                risky = bot_logger.click_is_risky(source, region_age)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "purpose": purpose,
                "point": [int(x), int(y)],
                "source": source,
                "arena": list(arena) if arena else None,
                "region_age_sec": round(float(region_age), 1) if region_age is not None else None,
                "risky": bool(risky),
                "thread": threading.current_thread().name,
                "decision_seq": decision_seq,
            }
            self._recent.append(dict(record))
            self._queue.put_nowait(record)
        except queue.Full:
            if not self._drop_warned:
                self._drop_warned = True
                try:
                    bot_logger.log_error("ClickRecorder queue full; dropping click record(s).")
                except Exception:
                    pass
        except Exception:
            # Never let click logging disturb the click path.
            pass

    # -- writer thread -----------------------------------------------------
    def _run_writer(self) -> None:
        while True:
            try:
                item = self._queue.get()
            except Exception:
                continue
            if item is None:
                return
            try:
                self._write(item)
            except Exception as e:
                try:
                    bot_logger.log_error(f"ClickRecorder writer failed: {e}")
                except Exception:
                    pass

    def _resolve_path(self):
        if self._path is None:
            base = bot_logger.ensure_debug_dir()  # runtime/debug
            self._path = os.path.join(base, "clicks.jsonl")
        return self._path

    def _write(self, record: dict) -> None:
        path = self._resolve_path()
        self._rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _rotate_if_needed(self, path) -> None:
        try:
            if os.path.exists(path) and os.path.getsize(path) >= _MAX_BYTES:
                backup = path + ".1"
                try:
                    if os.path.exists(backup):
                        os.remove(backup)
                except Exception:
                    pass
                os.replace(path, backup)
        except Exception:
            pass


_recorder = ClickRecorder()


def start_session() -> None:
    _recorder.start_session()


def record(x, y, purpose, *, source=None, region_age=None, arena=None, risky=None) -> None:
    _recorder.record(x, y, purpose, source=source, region_age=region_age, arena=arena, risky=risky)


def recent(limit: int = 12) -> list:
    """The last few recorded clicks, newest last. Safe to call from anywhere."""
    try:
        return list(_recorder._recent)[-max(1, int(limit)):]
    except Exception:
        return []
