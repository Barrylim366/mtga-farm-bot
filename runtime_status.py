from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from runtime_paths import runtime_file

_LOCK = threading.RLock()
_SESSION_ID = uuid.uuid4().hex
# Mirrors status.json's "startup_phase" so set_startup_phase can drop repeats
# without a file round-trip. Kept in sync by reset_status.
_last_startup_phase: str = ""


def get_runtime_dir() -> str:
    path = runtime_file().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(path)


def get_status_path() -> str:
    return str(Path(get_runtime_dir()) / "status.json")


def reset_status(*, log_path: str | None = None) -> dict[str, Any]:
    global _last_startup_phase
    # The dedupe cache in set_startup_phase mirrors what is in the file, so a
    # reset (one per bot start, from the Controller ctor) has to clear it too --
    # otherwise the first phase of a new session is dropped as a "repeat" of the
    # last phase of the previous one.
    _last_startup_phase = ""
    now = time.time()
    payload = {
        "session_id": _SESSION_ID,
        "pid": os.getpid(),
        "started_at_epoch": now,
        "updated_at_epoch": now,
        "mode": "starting",
        "bot_state": "UNKNOWN",
        "log_path": log_path or "",
        "last_playerlog_event_at_epoch": 0.0,
        "last_decision_at_epoch": 0.0,
        "last_input_at_epoch": 0.0,
        "intentional_wait_until_epoch": 0.0,
        "intentional_wait_reason": "",
        "last_input_tag": "",
        "last_input_target": None,
        "last_move_name": "",
        "turn_info": {},
        "local_system_seat_id": None,
        "last_recovery_reason": "",
        "my_timer_running": False,
        "my_timer_type": "",
        "my_timer_remaining_sec": None,
        "my_timer_elapsed_sec": None,
        "my_timer_duration_sec": None,
        "my_timer_critical_count": 0,
        "my_timer_last_critical_at_epoch": 0.0,
        "my_timer_timeout_seen": False,
        "my_timer_timeout_at_epoch": 0.0,
        # Human-readable label for the long startup phase between "card data
        # loaded" and "bot is actually playing" (Home dip, quest refresh, Play
        # navigation). The UI shows it on the startup progress bar so that phase
        # is not a silent gap. "" whenever there is nothing to report.
        "startup_phase": "",
        "quests": [],
        "active_quest_id": "",
        "active_quest_colors": "",
        # Per-account gold farmed this session, {screenName: gold}. Reset here
        # so the "Current Session" window starts every account at 0 on bot open.
        "gold_farmed": {},
        # Map screenName -> configured alias, filled in as accounts are switched.
        "account_aliases": {},
        # Friendly alias of the account playing right now, and the one we would
        # switch INTO next when the switch criteria are met (both "" until known).
        "current_account": "",
        "next_account": "",
    }
    return _write_payload(payload)


def read_status() -> dict[str, Any]:
    path = Path(get_status_path())
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def update_status(**fields: Any) -> dict[str, Any]:
    with _LOCK:
        payload = read_status()
        if not payload:
            payload = reset_status()
        payload.update(fields)
        payload["updated_at_epoch"] = time.time()
        return _write_payload(payload, already_locked=True)


def set_startup_phase(phase: str) -> None:
    """Publish what the bot is busy with during startup (see reset_status).

    Called from the queue loop, which re-runs every few seconds, so repeats are
    dropped without touching the file -- an unchanged phase must not turn this
    into a periodic status.json write. Never raises: a cosmetic label must not
    be able to break the navigation it is reporting on.
    """
    global _last_startup_phase
    phase = str(phase or "")
    if phase == _last_startup_phase:
        return
    _last_startup_phase = phase
    try:
        update_status(startup_phase=phase)
    except Exception:
        pass


def set_mode(mode: str, **extra: Any) -> dict[str, Any]:
    return update_status(mode=str(mode or "unknown"), **extra)


def set_bot_state(state: str, **extra: Any) -> dict[str, Any]:
    return update_status(bot_state=str(state or "UNKNOWN"), **extra)


def set_turn_info(turn_info: dict[str, Any] | None) -> dict[str, Any]:
    payload = {}
    if isinstance(turn_info, dict):
        payload = {
            "turnNumber": turn_info.get("turnNumber"),
            "phase": turn_info.get("phase"),
            "step": turn_info.get("step"),
            "activePlayer": turn_info.get("activePlayer"),
            "priorityPlayer": turn_info.get("priorityPlayer"),
            "decisionPlayer": turn_info.get("decisionPlayer"),
        }
    return update_status(turn_info=payload)


def touch_playerlog_event(*, state: str | None = None, turn_info: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    fields: dict[str, Any] = {"last_playerlog_event_at_epoch": now}
    if state is not None:
        fields["bot_state"] = str(state)
    if turn_info is not None:
        fields["turn_info"] = {
            "turnNumber": turn_info.get("turnNumber"),
            "phase": turn_info.get("phase"),
            "step": turn_info.get("step"),
            "activePlayer": turn_info.get("activePlayer"),
            "priorityPlayer": turn_info.get("priorityPlayer"),
            "decisionPlayer": turn_info.get("decisionPlayer"),
        }
    return update_status(**fields)


def touch_decision(*, move_name: str | None = None, turn_info: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    fields: dict[str, Any] = {"last_decision_at_epoch": now}
    if move_name is not None:
        fields["last_move_name"] = str(move_name)
    if turn_info is not None:
        fields["turn_info"] = {
            "turnNumber": turn_info.get("turnNumber"),
            "phase": turn_info.get("phase"),
            "step": turn_info.get("step"),
            "activePlayer": turn_info.get("activePlayer"),
            "priorityPlayer": turn_info.get("priorityPlayer"),
            "decisionPlayer": turn_info.get("decisionPlayer"),
        }
    return update_status(**fields)


def touch_input(tag: str, target: tuple[int, int] | None = None) -> dict[str, Any]:
    now = time.time()
    payload: dict[str, Any] = {
        "last_input_at_epoch": now,
        "last_input_tag": str(tag or ""),
    }
    if target is not None:
        payload["last_input_target"] = [int(target[0]), int(target[1])]
    return update_status(**payload)


def set_intentional_wait(seconds: float, reason: str) -> dict[str, Any]:
    wait_seconds = max(0.0, float(seconds or 0.0))
    return update_status(
        intentional_wait_until_epoch=(time.time() + wait_seconds) if wait_seconds > 0.0 else 0.0,
        intentional_wait_reason=str(reason or ""),
    )


def clear_intentional_wait() -> dict[str, Any]:
    return update_status(intentional_wait_until_epoch=0.0, intentional_wait_reason="")


def set_recovery_reason(reason: str) -> dict[str, Any]:
    return update_status(last_recovery_reason=str(reason or ""))


def bump_counter(field: str, amount: int = 1, **extra: Any) -> dict[str, Any]:
    with _LOCK:
        payload = read_status()
        if not payload:
            payload = reset_status()
        current = payload.get(field, 0)
        try:
            current_value = int(current or 0)
        except Exception:
            current_value = 0
        payload[field] = current_value + int(amount)
        payload.update(extra)
        payload["updated_at_epoch"] = time.time()
        _write_payload_unlocked(payload)
        return payload


def _write_payload(payload: dict[str, Any], *, already_locked: bool = False) -> dict[str, Any]:
    if already_locked:
        _write_payload_unlocked(payload)
        return payload
    with _LOCK:
        _write_payload_unlocked(payload)
    return payload


def _write_payload_unlocked(payload: dict[str, Any]) -> None:
    path = Path(get_status_path())
    temp = path.with_suffix(".tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        temp.replace(path)
    except Exception:
        try:
            if temp.exists():
                temp.unlink()
        except Exception:
            pass
