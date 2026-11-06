"""Today's wins per account, read from Player.log before the bot plays.

The bot used to trust only the games it played itself: an account whose four
wins were won by hand before Start looked like it had none, so it was farmed
for four more. Every finished match is in the log though -- the
`MatchGameRoomStateType_MatchCompleted` record carries the reserved players
(name and team), the winning team and a millisecond timestamp -- so the day's
wins can be counted on the way in and seeded into daily_progress.

Counting is per Arena day (the reset hour from daily_progress), so a match from
before tonight's reset is yesterday's win and does not count.

Pure functions over log lines, plus a small file reader: nothing here touches
the screen, a Controller or the network.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

import daily_progress

MATCH_COMPLETED = "MatchGameRoomStateType_MatchCompleted"
MATCH_SCOPE = "MatchScope_Match"
WIN_RESULT = "ResultType_WinLoss"


def _name_key(name: str | None) -> str:
    """An account name without its `#digits`, case-folded (Arena's own rule)."""
    return str(name or "").split("#", 1)[0].strip().casefold()


def _record(line: str) -> dict | None:
    start = line.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(line[start:])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def match_outcome(line: str, account: str) -> tuple[int, bool] | None:
    """(timestamp_ms, won) for a completed match of `account`, else None.

    Reads one log line. The account is matched on the reserved players' names,
    so it works for matches the bot never saw: the log names who was playing.
    """
    if MATCH_COMPLETED not in line:
        return None
    payload = _record(line)
    if payload is None:
        return None
    info = (payload.get("matchGameRoomStateChangedEvent") or {}).get("gameRoomInfo") or {}
    config = info.get("gameRoomConfig") or {}
    final = info.get("finalMatchResult") or {}
    wanted = _name_key(account)
    if not wanted:
        return None
    my_team = None
    for player in config.get("reservedPlayers") or []:
        if _name_key((player or {}).get("playerName")) == wanted:
            my_team = (player or {}).get("teamId")
            break
    if my_team is None:
        return None
    winning_team = None
    for result in final.get("resultList") or []:
        result = result or {}
        if result.get("result") != WIN_RESULT:
            continue
        # One win/loss per game and one per match; the match one is the answer.
        if result.get("scope") == MATCH_SCOPE:
            winning_team = result.get("winningTeamId")
            break
        if winning_team is None:
            winning_team = result.get("winningTeamId")
    if winning_team is None:
        return None
    try:
        stamp = int(payload.get("timestamp") or 0)
    except (TypeError, ValueError):
        stamp = 0
    return stamp, int(winning_team) == int(my_team)


def wins_from_lines(lines: Iterable[str], account: str, since_epoch_ms: int) -> int:
    """Wins for `account` among `lines` whose match finished at/after the cutoff."""
    wins = 0
    seen_matches: set[str] = set()
    seen_wanted = _name_key(account)
    for line in lines:
        if MATCH_COMPLETED not in line or (seen_wanted and seen_wanted not in line.casefold()):
            continue
        outcome = match_outcome(line, account)
        if outcome is None:
            continue
        stamp, won = outcome
        if stamp and stamp < since_epoch_ms:
            continue
        if not won:
            continue
        # A match can be written more than once (state changes re-log it); the
        # transaction id is the same for those, so count each once.
        payload = _record(line) or {}
        key = str(payload.get("transactionId") or "") or f"{stamp}:{len(seen_matches)}"
        if key in seen_matches:
            continue
        seen_matches.add(key)
        wins += 1
    return wins


def _paths(log_path: str) -> list[str]:
    """The current log plus the rotated one, oldest first (a client restart)."""
    path = str(log_path or "").strip()
    if not path:
        return []
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    previous = os.path.join(folder, f"{stem}-prev{ext}")
    return [candidate for candidate in (previous, path) if os.path.isfile(candidate)]


def wins_today_by_account(log_path: str, account_names: Iterable[str]) -> dict[str, int]:
    """{account: wins today} for the accounts named, from the log files.

    One streaming pass collects the completed matches, then each account is
    counted from those lines; the cutoff is the last Arena reset, so matches
    from before it belong to the previous day. Accounts with no win are omitted.
    """
    since = int(daily_progress.day_start_epoch() * 1000)
    wanted = [name for name in dict.fromkeys(account_names) if _name_key(name)]
    if not wanted:
        return {}
    lines: list[str] = []
    for path in _paths(log_path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if MATCH_COMPLETED in line:
                        lines.append(line)
        except OSError:
            continue
    result: dict[str, int] = {}
    for name in wanted:
        wins = wins_from_lines(lines, name, since)
        if wins:
            result[name] = wins
    return result
