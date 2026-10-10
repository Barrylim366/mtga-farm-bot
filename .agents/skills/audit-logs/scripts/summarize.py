"""Index local bot sessions and maintain an optional local audit-history CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)?\]")
GOLD_BASELINE = re.compile(r"Gold baseline for '([^']+)': (\d+)")
GOLD_FARMED = re.compile(r"Gold farmed \(real\): '([^']+)' .*?farmed=(\d+)")
ACCOUNT_EVENT = re.compile(
    r"(?:SWITCH CHECK \(account='([^']+)'|Quest count confirmed fresh for '([^']+)')"
)
OPTIONAL_TARGET_EVENTS = (
    "FIERY_EQUIPMENT_ATTEMPT", "FIERY_EQUIPMENT_SCAN_FAILED",
    "SUBMIT_ZERO_ATTEMPT", "SUBMIT_ZERO_FAILED", "SUBMIT_ZERO_CANCELLED",
    "SUBMIT_ZERO_ACKNOWLEDGED", "SUBMIT_ZERO_UNCONFIRMED",
    "TARGET_RECOVERY_EXHAUSTED", "OPTIONAL_TARGET_SOAK_FAILED", "OPTIONAL_TARGET_SOAK",
)
CONCEDE_INCIDENT_EVENTS = (
    "CONCEDE_INCIDENT_CREATED", "CONCEDE_INCIDENT_FINISHED", "CONCEDE_INCIDENT_FAILED",
)
RELEVANT = (
    "Gold baseline for '", "Gold farmed (real): '", "SWITCH CHECK (account='",
    "Quest count confirmed fresh for '", "STALL_WATCHDOG_TRIGGERED",
    "STALL_CONCEDE", "ResultReason_Concede", "OPP_BATTLEFIELD_ITEM_TIMEOUT",
    "Quest reroll: stale data", "Quest reroll: skipped", "Quest reroll: failed",
    "QUEST_REROLL_CONFIRM", "GOLD_BALANCE_BELOW_BASELINE",
    *OPTIONAL_TARGET_EVENTS,
    *CONCEDE_INCIDENT_EVENTS,
)
CRITICAL = {
    "stall_concede", "target_scan_timeout", "target_click_missed",
    "quest_no_fresh_data", "quest_tile_missed", "quest_reroll_failed",
    "stuck_action", "timer_critical", "exception", "log_line_torn",
    "account_attribution_missing",
}
CSV_FIELDS = (
    "session_id", "date_utc", "started_at_utc", "ended_at_utc", "completed_matches",
    "wins", "losses", "win_rate", "confirmed_concedes", "concede_attempts",
    "concede_rate", "concede_rate_status", "provisional", "audited_at_utc",
)
MATCH_CSV_FIELDS = (
    "session_id", "match_index", "account", "started_at_utc", "ended_at_utc",
    "result", "deck", "deck_status", "deck_evidence_path", "deck_evidence_line",
)
DECK_SELECTED = re.compile(
    r"Starter: deck (.+?) submitted; event page ready to queue\."
    r"|(?:Historic|Post-login): deck selected \(([^)]+)\)"
    r"|(?:Historic|Post-login): deck (.+?) is already the selected deck for account '([^']+)'"
    r"|(?:Historic|Post-login): deck (.+?) is not on the grid and was the last tile "
    r"this session selected for account '([^']+)'"
)
SWITCH_ACCOUNT = re.compile(r"Switching account to '([^']+)'")


def utc_iso(epoch: float | None) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds") if epoch else ""


def update_match_csv(path: Path, summary: dict) -> None:
    """Persist per-game deck evidence; rotation must not erase a known selection."""
    rows = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(MATCH_CSV_FIELDS):
                raise ValueError(f"Unexpected match CSV columns in {path}; existing file left unchanged")
            rows = {(r["session_id"], r["match_index"]): r for r in reader}
    for match in summary["match_details"]:
        evidence = match.get("deck_evidence") or {}
        row = {
            "session_id": summary["session_id"], "match_index": str(match["match_index"]),
            "account": match["account"], "started_at_utc": utc_iso(match["started_at_epoch"]),
            "ended_at_utc": utc_iso(match["ended_at_epoch"]), "result": match["result"],
            "deck": match["deck"] or "", "deck_status": match["deck_status"],
            "deck_evidence_path": evidence.get("path", ""),
            "deck_evidence_line": evidence.get("line", ""),
        }
        key = (row["session_id"], row["match_index"])
        existing = rows.get(key)
        if (existing and row["deck_status"] == "unknown" and not evidence and existing["deck"]
                and all(row[field] == existing[field] for field in
                        ("account", "started_at_utc", "ended_at_utc", "result"))):
            for field in ("deck", "deck_status", "deck_evidence_path", "deck_evidence_line"):
                row[field] = existing[field]
        rows[key] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MATCH_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows.values(), key=lambda r: (r["session_id"], int(r["match_index"]))))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def match_decks(runtime: Path, records: list[dict], accounts: dict, configured: set[str],
                session_start: float | None) -> list[dict]:
    """Join logged selections to game starts, never guesses from quest colors.

    Account switches and bot restarts invalidate a carried selection. A pick
    click without a completed selection also makes the deck uncertain. Later
    games inherit a selection only within the same uninterrupted account visit.
    """
    events = []
    last_end = max(float(r.get("ended_at_epoch") or 0) for r in records)
    for path in (runtime / "analysis" / "history.log.1", runtime / "analysis" / "history.log"):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                # Bot-authored messages only; RAW payloads are not deck evidence.
                if "[INFO]" not in line and "[CLICK]" not in line and "=== MTGA Bot Session Started ===" not in line:
                    continue
                if not any(token in line for token in (
                    "deck", "Starter Deck Duel selected.", "STARTER_DECK_PICK_", "Switching account to '",
                    "Gold baseline for '", "SWITCH CHECK (account='",
                    "Quest count confirmed fresh for '", "=== MTGA Bot Session Started ===",
                )):
                    continue
                at = timestamp(line)
                if at is not None and at <= last_end:
                    events.append({"at": at, "path": str(path.resolve()),
                                   "line": number, "text": line.strip()})
    # Stable sort retains file/line order within one timestamp across rotation.
    events.sort(key=lambda e: e["at"])
    first_start = min(float(r.get("ended_at_epoch") or 0) - float(r.get("duration_sec") or 0)
                      for r in records)
    headers = [e["at"] for e in events if e["at"] <= first_start
               and "=== MTGA Bot Session Started ===" in e["text"]]
    # Without a session boundary, do not borrow a deck from an earlier run.
    lower = session_start if session_start is not None else (max(headers) if headers else first_start)
    events = [e for e in events if e["at"] >= lower]
    position = 0
    active_account = None
    selected = None
    uncertain_pick = None
    starter_context = False
    previous_end = lower
    details = []
    for record in sorted(records, key=lambda r: float(r.get("ended_at_epoch") or 0)):
        ended = float(record.get("ended_at_epoch") or 0)
        started = ended - float(record.get("duration_sec") or 0)
        account = accounts[record["_path"]]
        while position < len(events) and events[position]["at"] <= started:
            event = events[position]
            position += 1
            text = event["text"]
            switch = SWITCH_ACCOUNT.search(text)
            identity = ACCOUNT_EVENT.search(text)
            baseline = GOLD_BASELINE.search(text)
            if "=== MTGA Bot Session Started ===" in text or switch:
                selected = None
                uncertain_pick = None
                starter_context = False
                active_account = canonical(switch.group(1), configured).casefold() if switch else None
            elif identity or baseline:
                raw = next(v for v in (identity.groups() if identity else (baseline.group(1),)) if v)
                new_account = canonical(raw, configured).casefold()
                if active_account != new_account:
                    selected = None
                    uncertain_pick = None
                active_account = new_account
            if ("[CLICK]" in text and "STARTER_DECK_PICK_" in text
                    or "selected the first deck in the list" in text):
                selected = None
                uncertain_pick = event
            if "Starter: Starter Deck Duel selected." in text:
                starter_context = True
            elif "Historic:" in text or "Post-login: deck selected" in text:
                starter_context = False
            match = DECK_SELECTED.search(text)
            if match:
                starter, image, visible, visible_account, remembered, remembered_account = match.groups()
                if visible_account or remembered_account:
                    owner = canonical(visible_account or remembered_account, configured).casefold()
                    if owner != active_account:
                        selected = None
                    active_account = owner
                name = starter or image or visible or remembered
                # Keep Historic filenames as labels: arbitrary names need not be color codes.
                if name != "<first deck in list>":
                    selected = (name, active_account, event, bool(remembered))
                    uncertain_pick = None
                else:
                    selected = None
                    uncertain_pick = event
        deck, status, evidence = None, "unknown", None
        if uncertain_pick:
            evidence = {"path": uncertain_pick["path"], "line": uncertain_pick["line"],
                        "at": uncertain_pick["at"]}
        if selected and account != "unattributed" and selected[1] == account.casefold():
            deck, _, event, remembered = selected
            status = "inferred" if remembered or event["at"] < previous_end else "logged_selection"
            evidence = {"path": event["path"], "line": event["line"], "at": event["at"]}
        details.append({
            "match_index": record.get("match_index", len(details) + 1), "record": record["_path"],
            "account": account, "started_at_epoch": started, "ended_at_epoch": ended,
            "result": str(record.get("result") or "unknown").lower(),
            "deck": deck, "deck_status": status, "deck_evidence": evidence,
            "deck_format": "starter" if starter_context else None,
        })
        previous_end = ended
    infer_starter_decks(runtime, details)
    return details


def infer_starter_decks(runtime: Path, details: list[dict]) -> None:
    """Infer only uniquely matching Starter lists from a reliably owned saved hand."""
    if not any(m["deck_format"] == "starter" and not m["deck"] for m in details):
        return
    root = Path(__file__).resolve().parents[4]
    lists = read_json(root / "data" / "starter_decks.json")
    cards = read_json(root / "data" / "starter_deck_cards.json")
    by_name = {info.get("name"): info for info in cards.values() if isinstance(info, dict)}
    codes = {}
    for name, deck in lists.items():
        colors = {color for card in deck for color in by_name.get(card, {}).get("colors", [])}
        code = "".join(color for color in "WUBRG" if color in colors)
        if len(code) == 2:
            codes[name] = code
    candidates = defaultdict(list)
    for path in sorted((runtime / "debug" / "matches").glob("*/snapshots.jsonl")):
        try:
            with path.open(encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    try:
                        snap = json.loads(line)
                        at = datetime.fromisoformat(snap["ts"]).timestamp()
                    except (ValueError, KeyError, TypeError):
                        continue
                    matching = [m for m in details if m["deck_format"] == "starter" and not m["deck"]
                                and m["started_at_epoch"] <= at <= m["ended_at_epoch"]]
                    if len(matching) != 1 or snap.get("seat_unknown") or snap.get("my_seat") not in (1, 2):
                        continue
                    names = {card.get("name") for card in snap.get("hand", [])
                             if isinstance(card, dict) and card.get("name") in by_name}
                    nonlands = {name for name in names if "Land" not in by_name[name].get("types", [])}
                    if len(nonlands) < 3:
                        continue
                    fits = [name for name, deck in lists.items() if name in codes and names <= set(deck)]
                    if len(fits) == 1:
                        candidates[matching[0]["record"]].append((codes[fits[0]], fits[0], path, number, sorted(names)))
        except OSError:
            continue
    for match in details:
        observations = candidates.get(match["record"], [])
        if observations and len({item[0] for item in observations}) == 1:
            code, name, path, number, cards_seen = observations[0]
            match.update(deck=code, deck_status="inferred", deck_name=name,
                         deck_evidence={"path": str(path.resolve()), "line": number,
                                        "source": "starter_hand_match", "cards": cards_seen})


def update_audit_csv(path: Path, summary: dict) -> None:
    """Upsert one session, retaining earlier rows when their artefacts rotate away."""
    window, matches, concede = summary["window"], summary["matches"], summary["concede"]
    start = window.get("started_at_epoch") or window.get("first_match_start_estimate_epoch")
    end = window.get("ended_at_epoch") or window.get("last_match_end_epoch")
    # Missing history must not become a clean zero. Keep the summarizer's
    # conservative unavailable rate whenever known attempts are unresolved.
    coverage = list(summary.get("history_coverage", {}).values())
    first = min((item.get("first_at") or float("inf") for item in coverage), default=float("inf"))
    last = max((item.get("last_at") or 0 for item in coverage), default=0)
    complete_history = (first <= window["first_match_start_estimate_epoch"]
                        and last >= window["last_match_end_epoch"])
    rate = concede.get("confirmed_rate") if complete_history else None
    row = {
        "session_id": summary["session_id"], "date_utc": utc_iso(start)[:10],
        "started_at_utc": utc_iso(start), "ended_at_utc": utc_iso(end),
        "completed_matches": matches["completed"], "wins": matches["won"],
        "losses": matches["lost"], "win_rate": matches["win_rate"],
        "confirmed_concedes": concede["confirmed_matches"], "concede_attempts": concede["attempts"],
        "concede_rate": rate, "concede_rate_status": "available" if rate is not None else "unavailable",
        "provisional": str(bool(window["running"])).lower(), "audited_at_utc": utc_iso(time.time()),
    }
    rows = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(CSV_FIELDS):
                raise ValueError(f"Unexpected audit CSV columns in {path}; existing file left unchanged")
            rows = {existing["session_id"]: existing for existing in reader}
    existing = rows.get(row["session_id"])
    # A repeated audit after log rotation must not erase an already established
    # rate for the same completed-match sample.
    if (existing and rate is None and existing["concede_rate_status"] == "available"
            and int(existing["completed_matches"]) == matches["completed"]):
        for field in ("concede_rate", "concede_rate_status", "confirmed_concedes", "concede_attempts"):
            row[field] = existing[field]
    rows[row["session_id"]] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows.values(), key=lambda item: (item["started_at_utc"], item["session_id"])))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def concede_incidents(runtime: Path, session_id: str) -> list[dict]:
    """Index retained bundles even when capture failed or history has rotated."""
    incidents = []
    for path in sorted((runtime / "concedes").glob("*/incident.json")):
        data = read_json(path)
        if data.get("session_id") == session_id:
            incidents.append(data | {"_path": str(path.resolve())})
    return incidents


def timestamp(line: str) -> float | None:
    match = STAMP.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def session_dirs(runtime: Path) -> list[tuple[Path, list[dict]]]:
    result = []
    for folder in (runtime / "records").glob("session-*"):
        records = [read_json(path) | {"_path": str(path.resolve())}
                   for path in sorted(folder.glob("match-*.json"))]
        records = [record for record in records if record.get("session_id")]
        if records:
            result.append((folder, records))
    result.sort(key=lambda pair: min(float(r.get("ended_at_epoch") or 0) for r in pair[1]))
    return result


def canonical(name: str, configured: set[str]) -> str:
    if name in configured:
        return name
    matches = [item for item in configured if item.split("#", 1)[0].casefold() == name.casefold()]
    if len(matches) == 1:
        return matches[0]
    # Arena sometimes logs a shortened display name ("Lass") before the full
    # name ("Lass E"). Merge only when exactly one configured name extends it
    # across a word boundary; ambiguous prefixes remain unattributed.
    if not matches:
        prefix_matches = [
            item for item in configured
            if item.split("#", 1)[0].casefold().startswith(name.casefold() + " ")
        ]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
    return name


def history_events(runtime: Path, start: float, end: float) -> tuple[list[dict], dict]:
    events = []
    coverage = {}
    for path in (runtime / "analysis" / "history.log.1", runtime / "analysis" / "history.log"):
        if not path.is_file():
            continue
        first = last = None
        first_line = last_line = None
        with path.open(encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                if STAMP.match(line):
                    if first_line is None:
                        first_line = line
                    last_line = line
                if not any(token in line for token in RELEVANT):
                    continue
                at = timestamp(line)
                if at is None:
                    continue
                if first is None:
                    first = at
                last = at
                if start <= at <= end:
                    kind = next((token for token in RELEVANT if token in line), "")
                    events.append({"at": at, "kind": kind, "path": str(path.resolve()),
                                   "line": number, "text": line.strip()[:650],
                                   "reason_concede": "ResultReason_Concede" in line,
                                   "stall_concede_click": "STALL_CONCEDE" in line and "[CLICK]" in line})
        coverage[str(path.resolve())] = {"first_relevant_at": first, "last_relevant_at": last,
                                       "first_at": timestamp(first_line) if first_line else None,
                                       "last_at": timestamp(last_line) if last_line else None}
    events.sort(key=lambda event: (event["at"], event["path"], event["line"]))
    return events, coverage


def gold_summary(events: list[dict], status: dict, current: bool, configured: set[str],
                 start: float | None, end: float | None, records: list[dict]) -> dict:
    baselines = {}
    farmed = {}
    evidence = {}
    for event in events:
        baseline = GOLD_BASELINE.search(event["text"])
        gain = GOLD_FARMED.search(event["text"])
        if baseline:
            key = canonical(baseline.group(1), configured)
            baselines.setdefault(key, event["at"])
            evidence.setdefault(key, event)
        if gain:
            key = canonical(gain.group(1), configured)
            farmed[key] = max(farmed.get(key, 0), int(gain.group(2)))
            evidence[key] = event
    source = "history"
    if current and isinstance(status.get("gold_farmed"), dict):
        source = "status.json"
        for alias, raw_value in status["gold_farmed"].items():
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                continue
            key = canonical(str(alias), configured)
            farmed[key] = max(farmed.get(key, 0), value)
    known = bool(current and isinstance(status.get("gold_farmed"), dict)) or bool(baselines)
    if not known:
        return {"available": False, "reason": "No matching Gold snapshot or baseline history"}
    if not current:
        return {"available": False, "reason": "Historical session has no persisted start time or final Gold snapshot; log-derived totals may double-count aliases"}
    by_account = {key: value for key, value in sorted(farmed.items()) if value}
    if isinstance(status.get("gold_farmed"), dict):
        for alias, raw_value in status["gold_farmed"].items():
            key = canonical(str(alias), configured)
            if key in configured:
                try:
                    value = int(raw_value)
                except (TypeError, ValueError):
                    continue
                by_account.setdefault(key, value)
    late = []
    for key, at in baselines.items():
        if key not in by_account:
            continue
        if start is not None and (at - start > 60 or any(
            float(record.get("ended_at_epoch") or 0) < at for record in records
        )):
            late.append(key)
    total = sum(by_account.values())
    duration = end - start if start is not None and end is not None else None
    return {
        "available": True, "source": source, "by_account": by_account, "total": total,
        "gold_per_hour": round(total * 3600 / duration, 1) if duration and duration > 0 else None,
        "late_first_balance_accounts": sorted(late),
        "evidence": {key: {"path": value["path"], "line": value["line"]}
                     for key, value in evidence.items()},
    }


def summarize(folder: Path, records: list[dict], status: dict, runtime: Path) -> dict:
    sid = str(records[0]["session_id"])
    current = sid == status.get("session_id")
    first_match_start = min(float(r.get("ended_at_epoch") or 0) - float(r.get("duration_sec") or 0)
                            for r in records)
    last_match_end = max(float(r.get("ended_at_epoch") or 0) for r in records)
    start = float(status.get("started_at_epoch") or 0) if current else None
    if start == 0:
        start = None
    running = current and str(status.get("mode")) != "stopped"
    end = (time.time() if running else float(status.get("updated_at_epoch") or 0)) if current else None
    if end == 0:
        end = None
    events, coverage = history_events(runtime, start or first_match_start, end or last_match_end)
    configured = set(status.get("account_aliases", {}).values()) if current else set()
    configured.update(str(r.get("account")) for r in records if r.get("account"))
    account_events = []
    for event in events:
        account_match = ACCOUNT_EVENT.search(event["text"])
        baseline_match = GOLD_BASELINE.search(event["text"])
        if account_match or baseline_match:
            raw = next((value for value in (account_match.groups() if account_match else
                                             (baseline_match.group(1),)) if value), "")
            account_events.append((event["at"], canonical(raw, configured)))
    account_events.sort()
    by_account = defaultdict(lambda: Counter())
    record_accounts = {}
    totals = Counter()
    alerts = Counter()
    examples = {}
    for record in records:
        result = str(record.get("result") or "unknown").lower()
        totals[result] += 1
        match_start = float(record.get("ended_at_epoch") or 0) - float(record.get("duration_sec") or 0)
        # New records carry the account captured by the watchdog at match end.
        # Older records fall back to nearby account events for compatibility.
        explicit_account = str(record.get("account") or "").strip()
        prior = [name for at, name in account_events if at <= match_start]
        account = canonical(explicit_account, configured) if explicit_account else (
            prior[-1] if prior else "unattributed"
        )
        if configured and account not in configured:
            account = "unattributed"
        record_accounts[record["_path"]] = account
        by_account[account][result] += 1
        for label, count in (record.get("alerts") or {}).items():
            try:
                alerts[label] += int(count)
            except (TypeError, ValueError):
                continue
            examples.setdefault(label, record["_path"])
    completed = totals["won"] + totals["lost"]
    attempts = []
    for event in events:
        if event["kind"] != "STALL_WATCHDOG_TRIGGERED":
            continue
        containing = next((record for record in records if
                           float(record.get("ended_at_epoch") or 0) - float(record.get("duration_sec") or 0) - 10
                           <= event["at"] <= float(record.get("ended_at_epoch") or 0) + 10), None)
        if containing is None:
            attempts.append({"status": "unmatched", "evidence": event})
            continue
        match_start = float(containing.get("ended_at_epoch") or 0) - float(containing.get("duration_sec") or 0) - 10
        match_end = float(containing.get("ended_at_epoch") or 0) + 10
        related = [item for item in events if match_start <= item["at"] <= match_end]
        clicked = any(item["stall_concede_click"] for item in related)
        reason = any(item["reason_concede"] for item in related)
        confirmed = clicked and reason and containing.get("result") == "lost"
        attempts.append({"status": "confirmed" if confirmed else "unconfirmed",
                         "match": containing["_path"], "click_seen": clicked,
                         "concede_result_reason_seen": reason, "evidence": event})
    confirmed_paths = {item.get("match") for item in attempts if item["status"] == "confirmed"}
    confirmed_matches = len(confirmed_paths)
    recorded_attempts = alerts["stall_concede"]
    unresolved = (any(item["status"] != "confirmed" for item in attempts)
                  or recorded_attempts > len(attempts))
    concede_by_account = Counter(record_accounts[path] for path in confirmed_paths if path in record_accounts)
    deck_details = match_decks(runtime, records, record_accounts, configured, start)
    return {
        "session_id": sid, "records_dir": str(folder.resolve()),
        "window": {"started_at_epoch": start, "ended_at_epoch": end,
                   "first_match_start_estimate_epoch": first_match_start,
                   "last_match_end_epoch": last_match_end, "running": running},
        "matches": {"total": len(records), "completed": completed, "won": totals["won"],
                    "lost": totals["lost"], "other": len(records) - completed,
                    "win_rate": round(totals["won"] / completed, 4) if completed else None},
        "match_details": deck_details,
        "deck_counts": dict(Counter(match["deck"] or "unknown" for match in deck_details)),
        "by_account": {key: {"won": value["won"], "lost": value["lost"],
                             "other": sum(value.values()) - value["won"] - value["lost"],
                             "confirmed_concedes": concede_by_account[key],
                             "confirmed_concede_rate": round(
                                 concede_by_account[key] / (value["won"] + value["lost"]), 4)
                             if value["won"] + value["lost"] and not unresolved else None}
                       for key, value in sorted(by_account.items())},
        "concede": {"attempts": max(len(attempts), recorded_attempts),
                    "confirmed_matches": confirmed_matches,
                    "confirmed_rate": round(confirmed_matches / completed, 4)
                    if completed and not unresolved else None,
                    "rate_upper_bound": round(max(len(attempts), recorded_attempts) / completed, 4)
                    if completed else None,
                    "confirmed_by_account": dict(concede_by_account),
                    "candidates": attempts},
        "alerts": [{"label": label, "count": count, "critical": label in CRITICAL,
                    "example_record": examples[label]} for label, count in alerts.most_common()],
        "gold": gold_summary(events, status, current, configured, start, end, records),
        "history_coverage": coverage,
        "concede_incidents": {
            "retained": concede_incidents(runtime, sid),
            "events": [event for event in events if event["kind"] in CONCEDE_INCIDENT_EVENTS],
        },
        "optional_target_recovery": {
            "history_available": bool(coverage),
            "observed_event_counts": dict(Counter(event["kind"] for event in events
                                                   if event["kind"] in OPTIONAL_TARGET_EVENTS)),
            "events": [event for event in events if event["kind"] in OPTIONAL_TARGET_EVENTS],
        },
        "focused_events": [event for event in events if event["kind"] in (
            "OPP_BATTLEFIELD_ITEM_TIMEOUT", "Quest reroll: stale data", "Quest reroll: skipped",
            "Quest reroll: failed", "GOLD_BALANCE_BELOW_BASELINE")][:35],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="Session ID prefix or records directory suffix")
    parser.add_argument("--csv", type=Path, default=Path(__file__).resolve().parents[4] / "audit-history.csv",
                        help="Session history CSV (default: gitignored audit-history.csv in project root)")
    parser.add_argument("--no-csv", action="store_true", help="Read-only summary; do not update CSV")
    parser.add_argument("--matches-csv", type=Path, help="Per-game deck CSV (default: audit-matches.csv beside the session CSV)")
    parser.add_argument("--runtime-dir", type=Path, default=Path(os.environ.get(
        "MTGA_RUNTIME_DIR", Path(__file__).resolve().parents[4] / "runtime")))
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve()
    groups = session_dirs(runtime)
    if args.session:
        matches = [i for i, (folder, records) in enumerate(groups)
                   if records[0]["session_id"].startswith(args.session) or folder.name.endswith(args.session)]
        if len(matches) != 1:
            parser.error(f"session selector matched {len(matches)} sessions")
        index = matches[0]
    else:
        index = len(groups) - 1
    if index < 0:
        parser.error(f"no session records in {runtime / 'records'}")
    status = read_json(runtime / "status.json")
    selected = summarize(*groups[index], status, runtime)
    previous = summarize(*groups[index - 1], status, runtime) if index > 0 else None
    csv_result = {"path": str(args.csv.resolve()), "updated": False}
    match_path = args.matches_csv or args.csv.with_name(
        "audit-matches.csv" if args.csv.name == "audit-history.csv" else args.csv.stem + "-matches.csv")
    match_csv_result = {"path": str(match_path.resolve()), "updated": False}
    if not args.no_csv:
        try:
            update_audit_csv(args.csv, selected)
            csv_result["updated"] = True
        except (OSError, ValueError) as exc:
            csv_result["error"] = str(exc)
        try:
            update_match_csv(match_path, selected)
            match_csv_result["updated"] = True
        except (OSError, ValueError) as exc:
            match_csv_result["error"] = str(exc)
    print(json.dumps({"runtime_dir": str(runtime), "selected": selected,
                      "previous": previous, "csv": csv_result, "matches_csv": match_csv_result}, indent=2))


if __name__ == "__main__":
    main()
