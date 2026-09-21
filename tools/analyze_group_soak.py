"""Summarize temporary [SOAK_GROUP_V1] scry/surveil telemetry.

Read-only: accepts bot.log/history.log files, groups events by run/prompt, and
prints compact JSON suitable for attaching to an overnight-run handoff.
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


MARKER = "[SOAK_GROUP_V1] "
TIMESTAMP_RE = re.compile(r"^\[(?P<timestamp>[^]]+)]")


def parse_events(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    errors: list[dict] = []
    seen: set[tuple] = set()
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                marker_at = line.find(MARKER)
                if marker_at < 0:
                    continue
                raw = line[marker_at + len(MARKER):].strip()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError as exc:
                    errors.append({
                        "path": str(path), "line": line_number,
                        "error": f"invalid JSON: {exc}",
                    })
                    continue
                timestamp_match = TIMESTAMP_RE.match(line)
                event["log_timestamp"] = (
                    timestamp_match.group("timestamp") if timestamp_match else None
                )
                event["source_path"] = str(path)
                event["source_line"] = line_number
                # history.log includes bot.log, so deduplicate copied markers.
                key = (
                    event.get("run_id"), event.get("prompt_seq"),
                    event.get("event"), event.get("log_timestamp"), raw,
                )
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)
    return events, errors


def summarize(events: list[dict], parse_errors: list[dict] | None = None) -> dict:
    prompts: dict[tuple, dict] = {}
    event_counts = Counter()
    anomaly_counts = Counter()
    decision_origin_counts = Counter()
    shadow_recommendation_counts = Counter()
    unattributed_event_counts = Counter()
    for event in events:
        event_name = str(event.get("event") or "unknown")
        event_counts[event_name] += 1
        decision_origin = event.get("decision_origin")
        if decision_origin:
            decision_origin_counts[str(decision_origin)] += 1
        if event_name == "resume_shadow_guard":
            shadow_recommendation_counts[str(event.get("recommendation") or "unknown")] += 1
        key = (event.get("run_id"), event.get("prompt_seq"))
        # A recovery event without a prompt is intentionally outside this report.
        # Keeping it out prevents old/generic recovery telemetry from becoming a
        # fictional prompt row and inflating the scry anomaly count.
        if key[1] is None:
            unattributed_event_counts[event_name] += 1
            continue
        prompt = prompts.setdefault(key, {
            "run_id": key[0], "prompt_seq": key[1], "context": event.get("context"),
            "match_id": event.get("match_id"), "first_timestamp": event.get("log_timestamp"),
            "last_timestamp": event.get("log_timestamp"), "events": [], "anomalies": [],
        })
        prompt["last_timestamp"] = event.get("log_timestamp") or prompt["last_timestamp"]
        prompt["events"].append(event_name)
        if event_name == "anomaly":
            anomaly = str(event.get("anomaly") or "unknown")
            anomaly_counts[anomaly] += 1
            if anomaly not in prompt["anomalies"]:
                prompt["anomalies"].append(anomaly)

    prompt_rows = []
    for prompt in prompts.values():
        events_seen = set(prompt["events"])
        prompt["received"] = "prompt_received" in events_seen
        prompt["done_attempted"] = "done_attempt" in events_seen
        prompt["state_progress_seen"] = "state_progress" in events_seen
        prompt["resume_invoked"] = "resume_invoked" in events_seen
        prompt["post_group_decisions"] = prompt["events"].count("post_group_decision")
        prompt["post_group_casts"] = prompt["events"].count("post_group_cast_selected")
        prompt["incomplete"] = bool(
            prompt["received"]
            and (not prompt["done_attempted"] or not prompt["state_progress_seen"])
        )
        prompt_rows.append(prompt)
    prompt_rows.sort(key=lambda row: (str(row["run_id"]), int(row["prompt_seq"] or -1)))
    return {
        "event_count": len(events),
        "prompt_count": sum(bool(row["received"]) for row in prompt_rows),
        "anomalous_prompt_count": sum(bool(row["anomalies"]) for row in prompt_rows),
        "incomplete_prompt_count": sum(bool(row["incomplete"]) for row in prompt_rows),
        "event_counts": dict(sorted(event_counts.items())),
        "anomaly_counts": dict(sorted(anomaly_counts.items())),
        "decision_origin_counts": dict(sorted(decision_origin_counts.items())),
        "shadow_recommendation_counts": dict(sorted(shadow_recommendation_counts.items())),
        "unattributed_event_counts": dict(sorted(unattributed_event_counts.items())),
        "parse_errors": parse_errors or [],
        "prompts": prompt_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="Logs to parse (default: runtime bot.log and watchdog history.log).",
    )
    parser.add_argument("--compact", action="store_true", help="Emit one-line JSON.")
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
