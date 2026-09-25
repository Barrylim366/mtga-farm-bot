"""Summarize cast acknowledgement and Escape recovery diagnostics.

Read-only. It groups each attempted hand-card cast by its attempt id, reports
which acknowledgement signal arrived, and lists genuine/ambiguous failures.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import runtime_file


MARKERS = ("[CAST_ACK_V3] ", "[SOAK_CAST_ACK_V3] ", "[SOAK_CAST_ACK_V2] ")
TIMESTAMP_RE = re.compile(r"^\[(?P<timestamp>[^]]+)]")


def parse_events(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    events, errors, seen = [], [], set()
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                marker = next((item for item in MARKERS if item in line), None)
                if marker is None:
                    continue
                at = line.find(marker)
                raw = line[at + len(marker):].strip()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError as exc:
                    errors.append({"path": str(path), "line": line_number, "error": str(exc)})
                    continue
                match = TIMESTAMP_RE.match(line)
                event["log_timestamp"] = match.group("timestamp") if match else None
                key = (event.get("attempt_id"), event.get("event"), event["log_timestamp"], raw)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
    return events, errors


def summarize(events: list[dict], parse_errors: list[dict] | None = None) -> dict:
    attempts: dict[str, dict] = {}
    event_counts, signal_counts, outcome_reasons = Counter(), Counter(), Counter()
    retry_policy_counts, press_counts, blocker_counts = Counter(), Counter(), Counter()
    for event in events:
        event_name = str(event.get("event") or "unknown")
        event_counts[event_name] += 1
        attempt_id = event.get("attempt_id")
        if not attempt_id:
            continue
        row = attempts.setdefault(attempt_id, {
            "attempt_id": attempt_id,
            "card_id": event.get("card_id"),
            "match_id": event.get("match_id"),
            "first_timestamp": event.get("log_timestamp"),
            "last_timestamp": event.get("log_timestamp"),
            "events": [],
            "_event_objects": [],
        })
        row["last_timestamp"] = event.get("log_timestamp") or row["last_timestamp"]
        row["events"].append(event_name)
        row["_event_objects"].append(event)
        if event.get("blocker"):
            blocker_counts[str(event["blocker"])] += 1
        click_dispatched = event.get("click_dispatched", event.get("dispatched", True))
        if event_name == "PRESS_1" and click_dispatched:
            row["press_count"] = max(int(row.get("press_count", 0)), 1)
            press_counts["press_1"] += 1
        elif event_name == "PRESS_2" and click_dispatched:
            row["press_count"] = max(int(row.get("press_count", 0)), 2)
            press_counts["press_2"] += 1
        elif event_name == "cast_partial_press":
            row["press_count"] = max(
                int(row.get("press_count", 0)),
                int(event.get("press_count", 1) or 1),
            )
            row["partial_press_reason"] = event.get("partial_reason")
        elif event_name == "cast_retry_policy":
            action = str(event.get("action") or "unknown")
            retry_policy_counts[action] += 1
            row.setdefault("retry_policy", []).append({
                "action": action,
                "reason": event.get("reason"),
                "failure_count": event.get("failure_count"),
                "blocker": event.get("blocker"),
            })
        if event_name == "acknowledged":
            row["outcome"] = "acknowledged"
            row["signals"] = event.get("signals", []) or []
            for signal in row["signals"]:
                signal_counts[str(signal)] += 1
        elif event_name in {"click_ineffective", "state_changed_elsewhere", "ambiguous", "stale_decision_context", "cast_safety_abort"}:
            row["outcome"] = "safety_abort" if event_name == "cast_safety_abort" else event_name
            row["reason"] = event.get("reason") or ",".join(
                str(item) for item in (event.get("mismatch_reasons") or [])
            )
            row["signals"] = event.get("signals", []) or []
            if event_name != "cast_safety_abort":
                outcome_reasons[str(row["reason"] or "unknown")] += 1
        elif event_name == "cast_not_clicked":
            reason = event.get("reason")
            row["outcome"] = "safety_abort" if str(reason).startswith("cast_") else "not_clicked"
            row["reason"] = event.get("reason")
            if row["outcome"] == "safety_abort":
                outcome_reasons[str(row["reason"])] += 1
        elif event_name == "ack_cancelled" and "outcome" not in row:
            row["outcome"] = "cancelled"
            row["reason"] = event.get("reason")
    rows = sorted(attempts.values(), key=lambda row: (str(row["first_timestamp"]), row["attempt_id"]))
    linked_followups = {
        str(event.get("linked_attempt_id")): str(event.get("attempt_id"))
        for event in events if event.get("event") == "cast_escape_followup"
        and event.get("linked_attempt_id") and event.get("attempt_id")
    }
    # Older V3 runs may lack the explicit cast_escape_followup event when the
    # AI decision omitted game_state_id. Recover that link only when the same
    # card is selected in the exact state recorded on the ineffective attempt.
    event_by_attempt: dict[str, list[dict]] = {}
    for event in events:
        if event.get("attempt_id"):
            event_by_attempt.setdefault(str(event["attempt_id"]), []).append(event)
    for event in events:
        if (event.get("event") != "cast_escape_recovery"
                or event.get("phase") != "after_escape"):
            continue
        origin = str(event.get("attempt_id") or "")
        if not origin or origin in linked_followups:
            continue
        origin_events = event_by_attempt.get(origin, [])
        origin_failure = next((item for item in origin_events
                               if item.get("event") == "click_ineffective"), {})
        failed_state = ((origin_failure.get("current_snapshot") or {}).get("game_state_id"))
        failed_match = origin_failure.get("match_id")
        failed_card = origin_failure.get("card_id")
        recovery_time = event.get("log_timestamp") or ""
        try:
            recovery_epoch = datetime.strptime(recovery_time, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        except (TypeError, ValueError):
            recovery_epoch = None
        candidates = []
        for attempt_id, candidate_events in event_by_attempt.items():
            selected = next((item for item in candidate_events
                             if item.get("event") == "cast_selected"), None)
            if selected is None or selected.get("card_id") != failed_card:
                continue
            snapshot = selected.get("snapshot") or {}
            if (selected.get("match_id") != failed_match
                    or snapshot.get("game_state_id") != failed_state
                    or not recovery_time <= (selected.get("log_timestamp") or "")
                    or (failed_match and selected.get("match_id") != failed_match)):
                continue
            if recovery_epoch is not None:
                try:
                    selected_epoch = datetime.strptime(
                        selected.get("log_timestamp"), "%Y-%m-%d %H:%M:%S.%f"
                    ).timestamp()
                except (TypeError, ValueError):
                    continue
                if selected_epoch - recovery_epoch > 30:
                    continue
            candidates.append((selected.get("log_timestamp") or "", attempt_id))
        if candidates:
            linked_followups[origin] = min(candidates)[1]
    escape_recovery_counts = Counter()
    for origin, followup in linked_followups.items():
        result = attempts.get(followup, {}).get("outcome")
        category = ("worked" if result == "acknowledged" else
                    "did_not_work" if result == "click_ineffective" else "inconclusive")
        escape_recovery_counts[category] += 1
        if origin in attempts:
            attempts[origin]["escape_followup_attempt_id"] = followup
            attempts[origin]["escape_followup_result"] = category
    recovery_origins = {
        str(event.get("attempt_id")) for event in events
        if event.get("event") == "cast_escape_recovery"
        and event.get("phase") in {"after_escape", "aborted"}
        and event.get("attempt_id")
    }
    recoveries_without_followup = sum(
        1 for origin in recovery_origins if origin not in linked_followups
    )
    escape_recovery_counts["inconclusive"] += recoveries_without_followup
    # V3 diagnostic comparisons. Missing fields (V2 or unavailable platforms)
    # are excluded rather than treated as failures.
    diagnostic_fields = (
        "hover_to_click_delay_sec", "cursor_displacement_px", "foreground_owned",
        "click_dispatched", "click_duration_sec", "prompt_flags", "bundle_images",
        "input_transaction_owner", "hover_revalidated", "hover_source",
        "hover_age_sec", "cursor_position", "blocker", "press_count",
    )
    diagnostic_comparisons = {}
    for field in diagnostic_fields:
        groups = {"acknowledged": [], "failed_or_ambiguous": []}
        for row in rows:
            outcome = row.get("outcome")
            vals = [e.get(field) for e in row.get("_event_objects", []) if e.get(field) is not None]
            if not vals:
                continue
            key = "acknowledged" if outcome == "acknowledged" else "failed_or_ambiguous"
            groups[key].extend(vals)
        diagnostic_comparisons[field] = {
            key: {"count": len(vals), "mean": (sum(vals) / len(vals) if vals and all(isinstance(v, (int, float)) for v in vals) else None)}
            for key, vals in groups.items()
        }
    for row in rows:
        row.pop("_event_objects", None)
    outcome_counts = Counter(row.get("outcome", "unfinished") for row in rows)
    return {
        "event_count": len(events),
        "attempt_count": len(rows),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "ack_signal_counts": dict(sorted(signal_counts.items())),
        "outcome_reasons": dict(sorted(outcome_reasons.items())),
        "press_counts": dict(sorted(press_counts.items())),
        "partial_press_attempt_count": sum(
            1 for row in rows if int(row.get("press_count", 0)) == 1
        ),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "retry_policy_counts": dict(sorted(retry_policy_counts.items())),
        "escape_recovery_counts": {
            key: int(escape_recovery_counts.get(key, 0))
            for key in ("worked", "did_not_work", "inconclusive")
        },
        "event_counts": dict(sorted(event_counts.items())),
        "parse_errors": parse_errors or [],
        "investigation_cases": [
            row for row in rows
            if row.get("outcome") in {"click_ineffective", "ambiguous"}
        ],
        "stale_decision_cases": [
            row for row in rows
            if row.get("outcome") == "stale_decision_context"
        ],
        "unfinished_attempts": [
            row for row in rows if row.get("outcome", "unfinished") == "unfinished"
        ],
        "attempts": rows,
        "diagnostic_comparisons": diagnostic_comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    paths = args.paths or [
        Path(runtime_file("logs", "bot.log")),
        Path(runtime_file("analysis", "history.log")),
    ]
    events, errors = parse_events(paths)
    print(json.dumps(summarize(events, errors), indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
