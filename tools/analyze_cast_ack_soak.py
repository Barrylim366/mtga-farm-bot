"""Summarize temporary [SOAK_CAST_ACK_V2] cast acknowledgement telemetry.

Read-only. It groups each attempted hand-card cast by its attempt id, reports
which acknowledgement signal arrived, and lists genuine/ambiguous failures.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import runtime_file


MARKER = "[SOAK_CAST_ACK_V2] "
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
                at = line.find(MARKER)
                if at < 0:
                    continue
                raw = line[at + len(MARKER):].strip()
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
        })
        row["last_timestamp"] = event.get("log_timestamp") or row["last_timestamp"]
        row["events"].append(event_name)
        if event_name == "acknowledged":
            row["outcome"] = "acknowledged"
            row["signals"] = event.get("signals", []) or []
            for signal in row["signals"]:
                signal_counts[str(signal)] += 1
        elif event_name in {"click_ineffective", "state_changed_elsewhere", "ambiguous", "stale_decision_context"}:
            row["outcome"] = event_name
            row["reason"] = event.get("reason") or ",".join(
                str(item) for item in (event.get("mismatch_reasons") or [])
            )
            row["signals"] = event.get("signals", []) or []
            outcome_reasons[str(row["reason"] or "unknown")] += 1
        elif event_name == "cast_not_clicked":
            row["outcome"] = "not_clicked"
            row["reason"] = event.get("reason")
        elif event_name == "ack_cancelled" and "outcome" not in row:
            row["outcome"] = "cancelled"
            row["reason"] = event.get("reason")
    rows = sorted(attempts.values(), key=lambda row: (str(row["first_timestamp"]), row["attempt_id"]))
    outcome_counts = Counter(row.get("outcome", "unfinished") for row in rows)
    return {
        "event_count": len(events),
        "attempt_count": len(rows),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "ack_signal_counts": dict(sorted(signal_counts.items())),
        "outcome_reasons": dict(sorted(outcome_reasons.items())),
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
        "attempts": rows,
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
