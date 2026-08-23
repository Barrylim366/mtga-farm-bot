"""Read-only session watchdog: black-box recorder for farming sessions.

This process observes the bot without ever touching the mouse or keyboard, so
it is safe to run alongside the UI-launched bot (which owns input). It exists to
turn an unattended farming session into debuggable evidence:

  A. Persistent history  -> runtime/analysis/history.log
     Accumulates a copy of bot.log that survives the many bot restarts between
     matches (bot.log is truncated on every restart, so without this the earlier
     matches of a session are lost).

  A. Alert stream        -> runtime/analysis/alerts.log
     The subset of log lines that indicate a problem (exceptions, combat forced
     via recovery, image-match failures, skipped/unsupported casts, critical
     timers), throttled per signature so it stays scannable.

  D. Stall black boxes   -> runtime/debug/incident-<stamp>/
     When runtime/status.json shows a genuine stall, a text-only incident bundle
     is written and registered in the shared signature registry
     (tools/incident_tracking). No recovery is attempted here.

  B. Per-match records   -> runtime/records/<session>/match-NNNN.json
     One record per game (result / turns / duration from the [MATCH_END] marker
     Game emits, plus the alert counts seen during that game and quest context).

Nothing here is sent anywhere. Everything lands under runtime/ on this machine
for a developer to inspect (or feed to tools/session_report.py) after a session.

Usage:
    python -m tools.session_watchdog                # run continuously
    python -m tools.session_watchdog --once         # single pass then exit
    python -m tools.session_watchdog --poll-sec 3   # custom poll interval
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot_logger import ensure_debug_dir
from runtime_paths import ensure_runtime_subdir, runtime_file
from runtime_status import read_status
from state.state_machine import BotState, get_state_from_playerlog
from tools.incident_tracking import (
    build_related_incidents_payload,
    build_signature_knowledge_payload,
    ensure_tracking_file,
)

# --- Alert signatures ------------------------------------------------------
# (label, needles, cooldown_sec). Checked in order; the first matching needle
# wins so specific errors are classified before the generic buckets. The
# cooldown throttles alerts.log writes only -- per-match counting still counts
# every occurrence.
ALERT_SIGNATURES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("combat_force", ("COMBAT_RECOVERY_ATTEMPT",), 20.0),
    ("submit_img_fail", ("SUBMIT_SELECTION_IMG: image not found",), 20.0),
    ("unsupported_cast", ("not implemented yet", "chooser not implemented"), 30.0),
    # Game.py's stuck-move breaker (_STUCK_MOVE_RETRY_LIMIT): the same active
    # move (cast/attack/select_target/...) was returned unchanged 3x in a row
    # with no game-state progress and got force-replaced with resolve(). The
    # bot's own DECISION_HEARTBEAT resets the inactivity timer on every retry,
    # so the timer-based stall detector never sees this as a stall -- this
    # is the only signal that surfaces it.
    ("stuck_action", ("STUCK_ACTION_RETRY_LIMIT",), 15.0),
    # A Player.log line that arrived without its terminator. Either MTGA died
    # mid-write, or a tear stalled past the buffer window -- both were
    # completely invisible before, and a tear is what silently froze the bot
    # on 2026-08-23 (see LogReader.__follow).
    ("log_line_torn", ("LOG_LINE_UNTERMINATED", "LOG_LINE_TAIL_DISCARDED"), 30.0),
    ("no_scryfall_land", ("No Scryfall data for land",), 60.0),
    ("no_card_info", ("No card info for grpId",), 60.0),
    ("timer_critical", ("MY_TIMER_CRITICAL", "TimerType_Inactivity remaining=0"), 20.0),
    ("exception", ("Traceback (most recent call last)", "Unhandled exception"), 5.0),
    # NOTE: no generic "[ERROR]" bucket on purpose -- most [ERROR] lines are benign
    # retries (image-not-found, arena reacquire) and would drown the alert stream.
    # Genuine crashes are caught by "exception"; the raw lines stay in history.log.
)

MATCH_END_RE = re.compile(
    r"\[MATCH_END\]\s+result=(?P<result>\S+)\s+turns=(?P<turns>-?\d+)\s+"
    r"duration_sec=(?P<duration>[\d.]+)"
)

# Per-reason cooldown before a repeated stall writes another black box.
INCIDENT_COOLDOWN_SEC = 120.0
# No-progress window (any activity timestamp) before we treat the bot as hung
# OUTSIDE a game -- e.g. frozen at home / post-account-switch, which the in-game
# stall detector never sees. Set well above a normal account switch or queue
# search (both keep emitting input/playerlog events), so only a genuine hang trips
# it. The overnight milo freeze idled ~3.5h, so there is enormous margin.
HOME_IDLE_THRESHOLD_SEC = 360.0
# Soft cap for history.log; rotated to history.log.1 when exceeded.
HISTORY_MAX_BYTES = 50 * 1024 * 1024


def _has_local_priority(status: dict) -> bool:
    turn_info = status.get("turn_info")
    if not isinstance(turn_info, dict) or not turn_info:
        return False
    try:
        local_seat = int(status.get("local_system_seat_id") or 0)
    except Exception:
        local_seat = 0
    if local_seat <= 0:
        return False
    try:
        decision_player = int(turn_info.get("decisionPlayer") or 0)
    except Exception:
        decision_player = 0
    try:
        priority_player = int(turn_info.get("priorityPlayer") or 0)
    except Exception:
        priority_player = 0
    return decision_player == local_seat or priority_player == local_seat


def _detect_stuck_reason(status: dict, thresholds: "_Thresholds") -> str | None:
    bot_state = str(status.get("bot_state") or "")
    timer_type = str(status.get("my_timer_type") or "")
    try:
        critical_count = int(status.get("my_timer_critical_count") or 0)
    except Exception:
        critical_count = 0
    if (
        bot_state != str(BotState.HOME)
        and timer_type == "TimerType_Inactivity"
        and critical_count >= max(1, int(thresholds.my_timer_critical_threshold))
    ):
        return "repeated_own_timer_critical"
    if bot_state != str(BotState.HOME) and bool(status.get("my_timer_timeout_seen")):
        return "own_timeout_observed"
    wait_active = float(status.get("intentional_wait_until_epoch") or 0.0) > time.time()
    if (
        bot_state == str(BotState.IN_GAME)
        and str(status.get("mode") or "") == "in_game"
        and bool(status.get("my_timer_running"))
        and timer_type == "TimerType_Inactivity"
    ):
        try:
            stall_threshold = max(5.0, float(thresholds.my_timer_stall_sec))
        except Exception:
            stall_threshold = 20.0
        try:
            timer_elapsed = float(status.get("my_timer_elapsed_sec") or 0.0)
        except Exception:
            timer_elapsed = 0.0
        try:
            timer_remaining = float(status.get("my_timer_remaining_sec") or 0.0)
        except Exception:
            timer_remaining = 0.0
        activity = []
        for key in ("last_input_at_epoch", "last_decision_at_epoch", "last_playerlog_event_at_epoch"):
            try:
                activity.append(float(status.get(key) or 0.0))
            except Exception:
                activity.append(0.0)
        latest = max(activity)
        idle = max(0.0, time.time() - latest) if latest > 0.0 else 0.0
        if wait_active and timer_remaining > 8.0:
            return None
        if not _has_local_priority(status):
            return None
        if timer_elapsed >= stall_threshold and idle >= stall_threshold:
            return "own_inactivity_timer_stalled"
    return None


def _resolve_playerlog_path(status: dict) -> str:
    candidate = str(status.get("log_path") or "").strip()
    if candidate and os.path.isfile(candidate):
        return candidate
    env_path = str(os.environ.get("MTGA_BOT_LOG_PATH", "")).strip()
    if env_path and os.path.isfile(env_path):
        return env_path
    return str(Path.home() / "AppData" / "LocalLow" / "Wizards Of The Coast" / "MTGA" / "Player.log")


def _resolve_bot_log_path() -> str:
    return str(runtime_file("logs", "bot.log"))


def _read_tail(path: str, *, max_bytes: int) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _write_text(path: Path, content: str) -> None:
    try:
        with path.open("w", encoding="utf-8") as handle:
            handle.write(content or "")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform liveness check for the bot/UI process."""
    if not pid:
        return True  # unknown -> assume alive, don't self-terminate prematurely
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == STILL_ACTIVE
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


class _Thresholds:
    """Thresholds used by the read-only stall detector."""

    my_timer_critical_threshold = 1
    my_timer_stall_sec = 45.0


def _analysis_dir() -> Path:
    return ensure_runtime_subdir("analysis")


def _state_path() -> Path:
    return _analysis_dir() / "watchdog_state.json"


def _stop_request_path() -> Path:
    # The UI drops this file on Stop Bot to ask for a graceful final flush + exit.
    return _analysis_dir() / "watchdog.stop"


def _history_path() -> Path:
    return _analysis_dir() / "history.log"


def _alerts_path() -> Path:
    return _analysis_dir() / "alerts.log"


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        tmp.replace(path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _default_match_state() -> dict:
    return {"count": 0, "alerts": {}, "stalled": False, "session_dir": ""}


def _append_text(path: Path, text: str) -> None:
    if not text:
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        pass


def _rotate_history_if_needed() -> None:
    path = _history_path()
    try:
        if path.is_file() and path.stat().st_size > HISTORY_MAX_BYTES:
            backup = path.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            path.replace(backup)
    except Exception:
        pass


def _classify_line(line: str) -> str | None:
    for label, needles, _cooldown in ALERT_SIGNATURES:
        if any(needle in line for needle in needles):
            return label
    return None


def _cooldown_for(label: str) -> float:
    for candidate, _needles, cooldown in ALERT_SIGNATURES:
        if candidate == label:
            return cooldown
    return 30.0


def _records_session_dir(state: dict, session_id: str) -> Path:
    match_state = state.setdefault("match", _default_match_state())
    session_dir = str(match_state.get("session_dir") or "")
    if not session_dir:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        sid = (session_id or "nosid")[:8]
        session_dir = f"session-{stamp}-{sid}"
        match_state["session_dir"] = session_dir
    path = ensure_runtime_subdir("records", session_dir)
    return path


def _write_match_record(state: dict, session_id: str, match: dict, status: dict) -> None:
    match_state = state.setdefault("match", _default_match_state())
    index = int(match_state.get("count") or 0) + 1
    match_state["count"] = index

    quest = {
        "id": str(status.get("active_quest_id") or ""),
        "colors": str(status.get("active_quest_colors") or ""),
    }
    record = {
        "schema_version": 1,
        "session_id": session_id,
        "match_index": index,
        "ended_at": _timestamp(),
        "ended_at_epoch": time.time(),
        "result": str(match.get("result") or "unknown"),
        "turns": int(match.get("turns", -1)),
        "duration_sec": float(match.get("duration", 0.0)),
        "stalled": bool(match_state.get("stalled")),
        "quest": quest,
        "game_mode": str(status.get("mode") or ""),
        "alerts": dict(match_state.get("alerts") or {}),
    }
    session_dir = _records_session_dir(state, session_id)
    out = session_dir / f"match-{index:04d}.json"
    try:
        with out.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
    except Exception:
        pass

    # Also drop a compact one-liner into alerts.log so a human scanning the alert
    # stream sees the match boundary and its problem tally in context.
    alert_summary = ", ".join(f"{k}={v}" for k, v in sorted(record["alerts"].items())) or "clean"
    _append_text(
        _alerts_path(),
        f"[{_timestamp()}] [MATCH_SUMMARY] #{index} result={record['result']} "
        f"turns={record['turns']} duration={record['duration_sec']:.0f}s "
        f"stalled={record['stalled']} ({alert_summary})\n",
    )

    # Reset per-match accumulators for the next game.
    match_state["alerts"] = {}
    match_state["stalled"] = False


def _process_new_lines(state: dict, session_id: str, text: str, status: dict) -> None:
    match_state = state.setdefault("match", _default_match_state())
    last_alert = state.setdefault("last_alert", {})
    now = time.time()
    for line in text.splitlines():
        label = _classify_line(line)
        if label:
            # Count every occurrence for the per-match tally.
            alerts = match_state.setdefault("alerts", {})
            alerts[label] = int(alerts.get(label, 0)) + 1
            # Throttle the alerts.log write per signature.
            if (now - float(last_alert.get(label, 0.0))) >= _cooldown_for(label):
                last_alert[label] = now
                _append_text(_alerts_path(), f"[{_timestamp()}] [{label.upper()}] {line.strip()}\n")
        match = MATCH_END_RE.search(line)
        if match:
            parsed = {
                "result": match.group("result"),
                "turns": int(match.group("turns")),
                "duration": float(match.group("duration")),
            }
            _write_match_record(state, session_id, parsed, status)


def _consume_bot_log(state: dict, session_id: str, status: dict) -> None:
    bot_log = _resolve_bot_log_path()
    if not os.path.isfile(bot_log):
        return
    offset = int(state.get("offset") or 0)
    try:
        size = os.path.getsize(bot_log)
    except Exception:
        return
    if size < offset:
        # bot.log was truncated (bot restarted): start over and mark the seam.
        # Persist the reset immediately so an early return below (no complete line
        # yet) does not re-detect the truncation and re-append the seam each poll.
        offset = 0
        state["offset"] = 0
        _append_text(
            _history_path(),
            f"\n[{_timestamp()}] === session_watchdog: bot.log restart detected ===\n",
        )
    if size <= offset:
        return
    try:
        with open(bot_log, "rb") as handle:
            handle.seek(offset)
            raw = handle.read(size - offset)
    except Exception:
        return
    # Only process up to the last complete line; keep the remainder for next poll
    # so a line being written right now is never split.
    last_nl = raw.rfind(b"\n")
    if last_nl == -1:
        return
    chunk = raw[: last_nl + 1]
    text = chunk.decode("utf-8", errors="replace")
    _rotate_history_if_needed()
    _append_text(_history_path(), text)
    _process_new_lines(state, session_id, text, status)
    state["offset"] = offset + len(chunk)


def _write_incident_bundle(state: dict, status: dict, reason: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    incident_dir = Path(ensure_debug_dir(f"incident-{stamp}"))
    playerlog = _resolve_playerlog_path(status)
    player_tail = _read_tail(playerlog, max_bytes=160000)
    try:
        derived_state = get_state_from_playerlog(player_tail)
    except Exception:
        derived_state = None
    payload = {
        "reason": reason,
        "created_at": stamp,
        "source": "session_watchdog",
        "derived_playerlog_state": str(derived_state),
        "status": status,
    }
    try:
        with (incident_dir / "incident.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except Exception:
        pass
    # Prefer the persistent history (survives restarts) for the bot-side tail.
    history = _history_path()
    bot_tail = _read_tail(str(history), max_bytes=160000) or _read_tail(
        _resolve_bot_log_path(), max_bytes=160000
    )
    _write_text(incident_dir / "bot_tail.txt", bot_tail)
    _write_text(incident_dir / "player_tail.txt", player_tail)
    # Register in the shared signature machinery so recurring stalls dedupe and
    # accumulate known guidance across sessions.
    try:
        ensure_tracking_file(incident_dir, created_at=stamp, trigger=reason)
        with (incident_dir / "related_incidents.json").open("w", encoding="utf-8") as handle:
            json.dump(
                build_related_incidents_payload(
                    incident_dir=incident_dir, created_at=stamp, trigger=reason
                ),
                handle,
                indent=2,
            )
        with (incident_dir / "signature_knowledge.json").open("w", encoding="utf-8") as handle:
            json.dump(
                build_signature_knowledge_payload(
                    incident_dir=incident_dir, created_at=stamp, trigger=reason
                ),
                handle,
                indent=2,
            )
    except Exception:
        pass

    _append_text(
        _alerts_path(),
        f"[{_timestamp()}] [STALL] reason={reason} -> {incident_dir.name}\n",
    )
    # Flag the current match so its record notes it stalled.
    state.setdefault("match", _default_match_state())["stalled"] = True
    print(f"[session_watchdog] stall captured: reason={reason} dir={incident_dir}")


def _check_stall(state: dict, status: dict, thresholds: _Thresholds) -> str | None:
    """Detect and record an in-game stall. Returns the detected reason (even if a
    cooldown suppressed the bundle write) so the caller can skip the home-idle
    check when an in-game stall already explains the freeze."""
    try:
        reason = _detect_stuck_reason(status, thresholds)
    except Exception:
        reason = None
    if not reason:
        return None
    last_incident = state.setdefault("last_incident", {})
    now = time.time()
    if (now - float(last_incident.get(reason, 0.0))) >= INCIDENT_COOLDOWN_SEC:
        last_incident[reason] = now
        _write_incident_bundle(state, status, reason)
    return reason


def _check_home_idle(state: dict, status: dict) -> None:
    """Catch a hang OUTSIDE a game: no activity of any kind for too long.

    The in-game detector only fires while IN_GAME, so a
    freeze at home / post-account-switch (like the overnight milo hang that idled
    3.5h until disconnection) left no trace. Here we track the newest of the
    bot's activity timestamps; if none advances for HOME_IDLE_THRESHOLD_SEC we
    write one black box for the episode (reset once progress resumes)."""
    now = time.time()
    progress = max(
        float(status.get("last_input_at_epoch") or 0.0),
        float(status.get("last_decision_at_epoch") or 0.0),
        float(status.get("last_playerlog_event_at_epoch") or 0.0),
    )
    tracker = state.setdefault("progress", {})
    if progress <= 0.0:
        return  # no activity observed yet (bot still booting); nothing to compare
    if progress > float(tracker.get("value") or 0.0):
        tracker["value"] = progress
        tracker["seen_at"] = now
        tracker["alerted"] = False
        return
    seen_at = float(tracker.get("seen_at") or 0.0)
    if seen_at <= 0.0:
        tracker["seen_at"] = now
        return
    if tracker.get("alerted"):
        return  # already flagged this idle episode
    if (now - seen_at) < HOME_IDLE_THRESHOLD_SEC:
        return
    tracker["alerted"] = True
    idle_min = (now - seen_at) / 60.0
    _write_incident_bundle(state, status, "home_idle_no_progress")
    print(f"[session_watchdog] home-idle hang: no progress for {idle_min:.1f} min.")


def run_once(state: dict, thresholds: _Thresholds, parent_pid: int = 0) -> bool:
    """Process one poll. Returns False when the watchdog should exit (the process
    it was observing has gone away)."""
    status = read_status()
    session_id = str(status.get("session_id") or "")
    stored_session = str(state.get("session_id") or "")
    if session_id and session_id != stored_session:
        # New bot process: fresh per-match accumulators and a new records folder.
        # bot.log is truncated on restart, so re-read it from the start too --
        # a stale offset from the previous session would skip into a new file.
        state["session_id"] = session_id
        state["match"] = _default_match_state()
        state["offset"] = 0
    _consume_bot_log(state, session_id, status)
    # In-game stall first; only look for a home/post-switch idle hang when no
    # in-game stall already explains the freeze.
    if not _check_stall(state, status, thresholds):
        _check_home_idle(state, status)

    # Self-terminate safety net. Prefer the launching UI pid passed on the command
    # line: it is stable and immune to a stale status.json left by a previous
    # session (which could otherwise make us adopt a dead pid and exit at once).
    # Only fall back to the bot pid in status.json when we weren't told a parent.
    watch_pid = int(parent_pid or 0)
    if not watch_pid:
        try:
            pid = int(status.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid:
            state["seen_pid"] = pid
        watch_pid = int(state.get("seen_pid") or 0)
    if watch_pid and not _pid_alive(watch_pid):
        print(f"[session_watchdog] watched process {watch_pid} gone; exiting.")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only MTGA bot session watchdog.")
    parser.add_argument("--poll-sec", type=float, default=2.0, help="Polling interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Run a single pass then exit.")
    parser.add_argument(
        "--parent-pid", type=int, default=0,
        help="PID of the launching UI; the watchdog exits when that process dies.",
    )
    args = parser.parse_args()

    thresholds = _Thresholds()
    state = _load_state()
    parent_pid = int(args.parent_pid or 0)
    # Ensure the analysis dir and files exist so first-run reads never fail.
    _analysis_dir()
    # Drop any stale stop request from a previous session so we don't exit at once.
    try:
        _stop_request_path().unlink()
    except OSError:
        pass
    print(f"[session_watchdog] observing bot.log; artifacts under {runtime_file('analysis')}")

    if args.once:
        run_once(state, thresholds, parent_pid)
        _save_state(state)
        return 0

    poll = max(0.5, float(args.poll_sec))
    try:
        while True:
            # One bad poll must never end an unattended session's monitoring.
            try:
                keep_going = run_once(state, thresholds, parent_pid)
            except Exception as exc:
                print(f"[session_watchdog] poll error: {exc}")
                keep_going = True
            _save_state(state)
            if not keep_going:
                break
            # Graceful stop: the UI drops a sentinel on Stop Bot. Do one final pass
            # to flush the last match's log lines, then exit cleanly.
            if _stop_request_path().is_file():
                try:
                    run_once(state, thresholds, parent_pid)
                except Exception:
                    pass
                _save_state(state)
                try:
                    _stop_request_path().unlink()
                except OSError:
                    pass
                print("[session_watchdog] stop requested; final flush done.")
                break
            time.sleep(poll)
    except KeyboardInterrupt:
        _save_state(state)
        print("[session_watchdog] stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
