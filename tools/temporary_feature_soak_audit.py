"""Temporary, read-only feature evidence check for an overnight bot soak.

Run after the normal audit_logs summarizer. It reports observed event counts;
zero means no matching retained log line, not proof that the feature is broken.
It never clicks, changes config, writes audit CSVs, or edits runtime artefacts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


SIGNALS = {
    "long_game_concede": ("[MATCH_TIMEOUT] Concede requested:",),
    "reconnect_click": ("POPUP_RECONNECT: confirmed popup persisted",),
    "ability_activation_selected": ("ABILITY_ACTIVATION_SELECTED:",),
    "ability_activation_clicked": ("ABILITY_ACTIVATION_CLICKED:",),
    "ability_activation_acknowledged": ("ABILITY_ACTIVATION_ACKNOWLEDGED:",),
    "ability_activation_timeout": ("ABILITY_ACTIVATION_TIMEOUT:",),
    "ability_activation_cancel_recovery": ("ABILITY_ACTIVATION_RECOVERY:",),
    "750_only_quest_policy": ("750-only= True", "750-only=True", "(750-only)."),
    "quest_target_met_win_deck": (
        "configured quest target met; selecting WB win-farming deck",
        "quest target met; using fallback deck for wins",
    ),
    "play_time_shutdown": ("Play time limit reached: PC shutdown scheduled",),
    "round_complete_shutdown": ("Round complete: PC shutdown scheduled",),
    "shutdown_schedule_failed": (
        "PLAY_TIME_LIMIT_SHUTDOWN_FAILED", "ROUND_COMPLETE_SHUTDOWN_FAILED",
    ),
}


def history_files(runtime: Path) -> list[Path]:
    analysis = runtime / "analysis"
    return [p for p in (analysis / "history.log.1", analysis / "history.log") if p.is_file()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-dir", type=Path,
        default=Path(os.environ.get("MTGA_RUNTIME_DIR", Path(__file__).resolve().parents[1] / "runtime")),
    )
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve()
    files = history_files(runtime)
    counts = {name: 0 for name in SIGNALS}
    examples: dict[str, list[dict[str, object]]] = {name: [] for name in SIGNALS}
    for path in files:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, 1):
                for name, needles in SIGNALS.items():
                    if any(needle in line for needle in needles):
                        counts[name] += 1
                        if len(examples[name]) < 3:
                            examples[name].append({
                                "path": str(path), "line": line_number, "text": line.strip()[:300],
                            })
    result = {
        "runtime_dir": str(runtime),
        "history_files": [str(path) for path in files],
        "coverage": "available" if files else "unavailable",
        "signals": {
            name: {
                "count": count,
                "status": "observed" if count else "not observed in retained history",
                "examples": examples[name],
            }
            for name, count in counts.items()
        },
        "limitations": [
            "Reconnect and ability log events show attempts or acknowledgements, not successful match outcomes.",
            "The win-farming deck log confirms a selection decision, not deck strength or match win rate.",
            "Static UI appearance and an uneventful shutdown configuration cannot be verified from runtime logs.",
            "History rotation can remove evidence; zero counts are not proof that an event never occurred.",
        ],
    }
    print(json.dumps(result, indent=2))
    return 0 if files else 2


if __name__ == "__main__":
    raise SystemExit(main())
