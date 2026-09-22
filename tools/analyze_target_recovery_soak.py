"""Summarize temporary [SOAK_TARGET_RECOVERY_V1] telemetry.

Read-only.  Groups target-prompt lifecycle events by match and prompt token so
an overnight run can distinguish recovered click misses from unresolved modal
transactions and casts that were correctly deferred.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MARKER = "[SOAK_TARGET_RECOVERY_V1] "


def parse_events(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    events, errors = [], []
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
                try:
                    event = json.loads(line[at + len(MARKER):].strip())
                except json.JSONDecodeError as exc:
                    errors.append({"path": str(path), "line": line_number, "error": str(exc)})
                    continue
                event["log_timestamp"] = line.split("]", 1)[0].lstrip("[")
                events.append(event)
    return events, errors


def summarize(events: list[dict], errors: list[dict] | None = None) -> dict:
    grouped: dict[tuple[object, object], dict] = {}
    counts = Counter(str(event.get("event") or "unknown") for event in events)
    for event in events:
        key = (event.get("match_id"), event.get("token"))
        row = grouped.setdefault(key, {
            "match_id": event.get("match_id"),
            "token": event.get("token"),
            "source_id": event.get("source_id"),
            "target_id": event.get("target_id"),
            "events": [],
            "retry_count": 0,
            "cast_deferred_count": 0,
        })
        row["events"].append(event.get("event"))
        row["source_id"] = event.get("source_id") or row["source_id"]
        row["target_id"] = event.get("target_id") or row["target_id"]
        if event.get("event") == "retry_scheduled":
            row["retry_count"] += 1
        if event.get("event") == "cast_deferred":
            row["cast_deferred_count"] += 1
        if event.get("event") in {"resolved", "retry_exhausted"}:
            row["outcome"] = "resolved" if event.get("event") == "resolved" else "retry_exhausted"
    rows = sorted(grouped.values(), key=lambda row: (str(row.get("match_id")), str(row.get("token"))))
    outcome_counts = Counter(row.get("outcome", "unfinished") for row in rows)
    return {
        "event_count": len(events),
        "prompt_count": len(rows),
        "event_counts": dict(sorted(counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "unresolved_prompts": [row for row in rows if row.get("outcome", "unfinished") == "unfinished"],
        "retry_exhaustions": [row for row in rows if row.get("outcome") == "retry_exhausted"],
        "prompts": rows,
        "parse_errors": errors or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    if not args.paths:
        from runtime_paths import runtime_file
        args.paths = [Path(runtime_file("logs", "bot.log")), Path(runtime_file("analysis", "history.log"))]
    events, errors = parse_events(args.paths)
    print(json.dumps(summarize(events, errors), indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
