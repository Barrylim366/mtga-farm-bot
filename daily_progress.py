"""Quests completed and wins earned per account, per Arena DAY, kept on disk.

The bot's own accounting is per session: `_session_wins_by_account` lives in
memory, so restarting the bot mid-day made an account that had already banked
its configured wins earn them all over again. Four wins became eight, twelve,
as many as the day had restarts. This keeps today's numbers in
`runtime/daily_progress.json`, keyed by the account, and rolls every entry over
at the game's own daily reset.

That reset is a fixed instant, not local midnight: Arena rolls its quests and
daily wins over at 09:00 UTC, which is 10pm in Auckland during NZDT and 4am in
New York, and shifts by an hour for every player when DST changes. Keying the
day on this machine's midnight would give each user a different boundary and
would split one evening's play across two "days" twice a year, so `day_key()`
shifts UTC back by the reset hour instead: the same instant for everybody.

Quests are counted the same way, from the open-quest count: the count is
snapshotted per account and a drop is one completed quest -- whether the bot or
a person finished it. That survives a restart too, because the snapshot is on
disk rather than in the run's memory.

Nothing here raises into the caller: a missing or unreadable file simply means
"nothing counted yet today", and a failed write leaves the in-memory answer it
already gave the caller.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime_paths import runtime_file

FILENAME = "daily_progress.json"
# Arena's daily reset: 09:00 UTC. Quests and the daily-win track restart then,
# for every player at the same instant, whatever their local clock says.
RESET_UTC_HOUR = 9
_PLACEHOLDER_KEYS = {"", "(current account)"}
_LOCK = threading.Lock()
_reset_hour = RESET_UTC_HOUR


def configure(reset_utc_hour: int | None = None) -> int:
    """Set the reset hour used for the day key; returns the value in force.

    The app calls this from its saved setting at startup and whenever the user
    changes it. Out-of-range junk keeps the current value rather than moving the
    boundary somewhere nonsensical.
    """
    global _reset_hour
    if reset_utc_hour is not None:
        try:
            hour = int(reset_utc_hour)
        except (TypeError, ValueError):
            hour = None
        if hour is not None and 0 <= hour <= 23:
            _reset_hour = hour
    return _reset_hour


def reset_hour() -> int:
    return _reset_hour


def day_key(when: datetime | None = None) -> str:
    """The Arena day `when` belongs to, as YYYY-MM-DD.

    With the default 09:00 UTC reset, 08:59 UTC still belongs to the previous
    day and 09:00 UTC starts the new one. A naive datetime is taken as UTC.
    """
    moment = when if when is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    shifted = moment.astimezone(timezone.utc) - timedelta(hours=_reset_hour)
    return shifted.strftime("%Y-%m-%d")


def day_start_epoch(when: datetime | None = None) -> float:
    """UTC epoch of the most recent reset: the instant today's Arena day began."""
    moment = when if when is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    start = moment.replace(hour=_reset_hour, minute=0, second=0, microsecond=0)
    if start > moment:
        start -= timedelta(days=1)
    return start.timestamp()


def _today() -> str:
    return day_key()


def _read() -> dict[str, dict]:
    try:
        with open(runtime_file(FILENAME), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, dict]) -> None:
    try:
        path = runtime_file(FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        temp.replace(path)
    except Exception:
        pass


def _fresh() -> dict[str, Any]:
    return {"date": _today(), "wins": 0, "quests": 0, "open_quests": None}


def _entry(data: dict[str, dict], account: str) -> dict[str, Any]:
    """Today's entry for `account`, created (or rolled over) in place."""
    key = str(account or "").strip()
    if not key:
        return _fresh()
    entry = data.get(key)
    if not isinstance(entry, dict) or entry.get("date") != _today():
        entry = _fresh()
        data[key] = entry
    else:
        entry.setdefault("wins", 0)
        entry.setdefault("quests", 0)
        entry.setdefault("open_quests", None)
    return entry


def _usable(account: str) -> str:
    key = str(account or "").strip()
    return "" if key in _PLACEHOLDER_KEYS else key


def wins(account: str) -> int:
    """Wins banked today on `account` (0 when nothing is stored for today)."""
    key = _usable(account)
    if not key:
        return 0
    with _LOCK:
        return int(_entry(_read(), key).get("wins") or 0)


def record_win(account: str) -> int:
    """Count one more win today for `account`; returns today's total."""
    key = _usable(account)
    if not key:
        return 0
    with _LOCK:
        data = _read()
        entry = _entry(data, key)
        entry["wins"] = int(entry.get("wins") or 0) + 1
        _write(data)
        return entry["wins"]


def seed_wins(account: str, total: int) -> int:
    """Raise today's win count to at least `total`; returns the value kept.

    Used at startup with the day's wins read from Player.log. Only ever raises:
    a game the bot counted itself must not be wiped by a log read that missed
    it (an unreadable or freshly rotated log, say).
    """
    key = _usable(account)
    if not key:
        return 0
    try:
        wanted = max(0, int(total))
    except (TypeError, ValueError):
        return 0
    with _LOCK:
        data = _read()
        entry = _entry(data, key)
        current = int(entry.get("wins") or 0)
        if wanted > current:
            entry["wins"] = wanted
            _write(data)
            return wanted
        return current


def quests_completed(account: str) -> int:
    """Quests completed today on `account` (0 when nothing is stored)."""
    key = _usable(account)
    if not key:
        return 0
    with _LOCK:
        return int(_entry(_read(), key).get("quests") or 0)


def observe_open_quests(account: str, open_quests: int) -> int:
    """Feed today's open-quest count; returns quests completed today.

    A drop since the last observation is that many completions. The first
    observation of the day only records the baseline, and a rise (a fresh
    quest arriving) only moves the baseline: it never counts as a completion.
    """
    key = _usable(account)
    if not key:
        return 0
    try:
        remaining = max(0, int(open_quests))
    except (TypeError, ValueError):
        return 0
    with _LOCK:
        data = _read()
        entry = _entry(data, key)
        previous = entry.get("open_quests")
        if previous is not None:
            try:
                drop = int(previous) - remaining
            except (TypeError, ValueError):
                drop = 0
            if drop > 0:
                entry["quests"] = int(entry.get("quests") or 0) + drop
        entry["open_quests"] = remaining
        _write(data)
        return int(entry.get("quests") or 0)


def snapshot() -> dict[str, dict]:
    """Today's entries only, for status.json / the UI."""
    data = _read()
    today = _today()
    return {
        key: {
            "wins": int(value.get("wins") or 0),
            "quests": int(value.get("quests") or 0),
        }
        for key, value in data.items()
        if isinstance(value, dict) and value.get("date") == today
    }
