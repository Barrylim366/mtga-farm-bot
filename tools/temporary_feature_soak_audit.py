"""Temporary, read-only feature evidence check for an overnight bot soak.

Run after the normal audit_logs summarizer. It reports observed event counts;
zero means no matching retained log line, not proof that the feature is broken.
Use --arm before starting the bot to save a local start boundary and branch
revision. Ordinary audit invocation is read-only and uses that boundary.
It never clicks, changes bot config, or writes audit CSVs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


SIGNALS = {
    "block_warning": ("BLOCK_RECOVERY_WARNING:",),
    "block_extra_assignment_attempt": ("BLOCK_RECOVERY: add_blocker ",),
    "block_cancel_attempt": ("BLOCK_RECOVERY: cancel_blocks ",),
    "block_recovery_failed": ("BLOCK_RECOVERY_FAILED:",),
    "pile_choice_armed": ("PILE_CHOICE_ARMED:",),
    "pile_choice_click": ("PILE_CHOICE_CLICK:",),
    "pile_choice_closed": ("PILE_CHOICE_CLOSED:",),
    "pile_choice_unsupported": ("PILE_CHOICE_UNSUPPORTED:",),
    "group_recovery_unconfirmed": ("GROUP_RECOVERY_UNCONFIRMED:",),
    "group_recovery_failed": ("GROUP_RECOVERY_FAILED:",),
    "stack_order_cancelled_for_target": ("SelectN stack cancelled: token=",),
    "stack_order_timeout": ("SelectN stack wait timeout: aborting selection to avoid stall.",),
    "stuck_action_retry_limit": ("STUCK_ACTION_RETRY_LIMIT",),
    "inactivity_timer_critical": ("MY_TIMER_CRITICAL",),
    "long_game_concede": ("[MATCH_TIMEOUT] Concede requested:",),
    "reconnect_click": ("POPUP_RECONNECT: confirmed popup persisted",),
    "ability_activation_selected": ("ABILITY_ACTIVATION_SELECTED:",),
    "ability_activation_clicked": ("ABILITY_ACTIVATION_CLICKED:",),
    "ability_activation_acknowledged": ("ABILITY_ACTIVATION_ACKNOWLEDGED:",),
    "ability_activation_timeout": ("ABILITY_ACTIVATION_TIMEOUT:",),
    "ability_activation_cancel_recovery": ("ABILITY_ACTIVATION_RECOVERY:",),
    "750_only_quest_policy": ("750-only= True", "750-only=True", "(750-only)."),
    "quest_target_met_win_deck": (
        " win-farming deck.",
        "quest target met; using fallback deck for wins",
    ),
    "play_time_shutdown": ("Play time limit reached: PC shutdown scheduled",),
    "round_complete_shutdown": ("Round complete: PC shutdown scheduled",),
    "shutdown_schedule_failed": (
        "PLAY_TIME_LIMIT_SHUTDOWN_FAILED", "ROUND_COMPLETE_SHUTDOWN_FAILED",
    ),
    "preferred_deck_decision": (
        "only-grind preferred deck active;", "only-grind preferred historic deck active",
        "selecting preferred ", "using preferred historic deck",
    ),
    "starter_deck_submitted": ("submitted; event page ready to queue.",),
    "starter_deck_reused": ("Starter: reusing remembered deck ",),
    "historic_deck_selected": ("Historic: deck selected (", "Post-login: deck selected ("),
    "deck_selection_problem": (
        "could not select any deck on the My Decks grid.",
        "deck/format selection not verified; skipping this queue attempt.",
    ),
    "mulligan_keep_observed": ("Mulligan keep observed from ClientMessageType_MulliganResp.",),
    "mulligan_tuck_done_clicked": ("London Mulligan tuck complete: clicked done button.",),
    "mulligan_tuck_problem": (
        "MULLIGAN_TUCK incomplete:", "aborting tuck to prevent dragging wrong cards.",
        "Error during execute_london_mulligan_tuck:",
    ),
    "premature_mulligan_keep_recovery": ("Local MulliganReq observed: clearing premature keep state.",),
    "end_screen_dismissal_confirmed": (
        "Match completed - dismissed end screen via ",
        "Match completed - dismissed end screen (confirmed after attempt ",
    ),
    "end_screen_dismissal_unconfirmed": ("Match completed - dismissed end screen (unconfirmed;",),
    "post_match_reward_click_attempt": ("Post-match reward popup detected (",),
    "forge_ready": ("FORGE advisor ready:",),
    "forge_start_failed": ("FORGE advisor failed to start (",),
    "free_forge_approval": ("approved by Forge (free forge;",),
    "free_forge_safety_exception": ("Free Forge safety check exception for grp_id=",),
    "battlefield_multi_fallback_click": ("SELECT_BATTLEFIELD_MULTI_FALLBACK (",),
    "battlefield_fallback_selection_complete": ("select_battlefield_permanents complete with fallbacks:",),
    "sacrifice_option_click": ("CASTING_TIME_OPTION_SACRIFICE",),
    "stall_watchdog_triggered": ("STALL_WATCHDOG_TRIGGERED:",),
    "stall_watchdog_cleared": ("STALL_WATCHDOG_CLEARED:",),
    "concede_incident_created": ("CONCEDE_INCIDENT_CREATED",),
    "concede_incident_finished": ("CONCEDE_INCIDENT_FINISHED",),
    "account_switch_attempt": ("Switching account to '",),
    "quest_count_fresh": ("Quest count confirmed fresh for '",),
    "home_navigation_recovery_attempt": (
        "Starter: on unrecognized screen; clicking Home tab to recover.",
        "Quest prime: no block after the Home click; re-entering Home via Profile.",
    ),
    "home_navigation_recovery_problem": (
        "Quest prime: Home not verified after the Profile re-entry.",
        "Quest prime: Home re-entry failed (",
    ),
    "notification_delivery_error": ("Discord webhook failed:", "Telegram alert failed:",),
}

STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\]")
ARM_FILE = "temporary-feature-soak.json"


def changed_feature_checks(counts: dict[str, int]) -> dict:
    """Triage signals without treating unrelated events as matched outcomes."""
    features = {
        "block_recovery": (
            ("block_warning", "block_extra_assignment_attempt", "block_cancel_attempt"),
            ("block_recovery_failed",),
            "Inspect blockers snapshots and fresh DeclareBlockersReq/SubmitBlockersReq: extra assignments and Cancel clicks are attempts, not acknowledged recovery.",
        ),
        "pile_choice": (
            ("pile_choice_armed", "pile_choice_click", "pile_choice_closed"),
            ("pile_choice_unsupported", "group_recovery_unconfirmed", "group_recovery_failed"),
            "Correlate each armed choice with server progress or settled overlay closure. Shared group recovery errors may belong to Scry/Surveil; a closure does not prove the chosen pile resolved.",
        ),
        "stack_order_cancellation": (
            ("stack_order_cancelled_for_target",), ("stack_order_timeout",),
            "Inspect clicks.jsonl and target snapshots for input from obsolete ordering workers after cancellation; a cancellation log alone cannot prove absence of stale input.",
        ),
    }
    return {
        name: {
            "status": ("needs review" if any(counts[key] for key in problems)
                       else "evidence observed; verify outcomes" if any(counts[key] for key in events)
                       else "not exercised in retained evidence"),
            "events": {key: counts[key] for key in events},
            "review_signals": {key: counts[key] for key in problems},
            "verification": verification,
        }
        for name, (events, problems, verification) in features.items()
    }


def timestamp(line: str) -> float | None:
    match = STAMP.match(line)
    if match:
        try:
            # bot_logger uses local, naive wall-clock time.
            return datetime.fromisoformat(match[1]).timestamp()
        except ValueError:
            pass
    return None


def revision() -> dict[str, str]:
    result = {}
    for key, args in (("branch", ["branch", "--show-current"]),
                      ("commit", ["rev-parse", "HEAD"]),
                      ("working_tree", ["status", "--short"])):
        try:
            result[key] = subprocess.check_output(
                ["git", *args], cwd=Path(__file__).resolve().parents[1],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            result[key] = "unavailable"
    return result


def scan(runtime: Path, since: float | None = None) -> dict:
    files = history_files(runtime)
    counts = {name: 0 for name in SIGNALS}
    examples: dict[str, list[dict[str, object]]] = {name: [] for name in SIGNALS}
    first = last = None
    selected_lines = 0
    for path in files:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, 1):
                at = timestamp(line)
                if at is None:
                    continue
                first = at if first is None else min(first, at)
                last = at if last is None else max(last, at)
                if since is not None and at < since:
                    continue
                selected_lines += 1
                # Never match raw GRE payloads or untagged continuation lines.
                if not re.match(r"^\[[^\]]+\] \[(?:INFO|ERROR|CLICK)\] ", line):
                    continue
                for name, needles in SIGNALS.items():
                    if any(needle in line for needle in needles):
                        counts[name] += 1
                        if len(examples[name]) < 3:
                            examples[name].append({
                                "path": str(path), "line": line_number, "text": line.strip()[:500],
                            })
    return {
        "runtime_dir": str(runtime),
        "history_files": [str(path) for path in files],
        "coverage": "available" if selected_lines else "unavailable",
        "since_epoch": since,
        "scope": "since boundary (may include multiple bot sessions)" if since is not None else "all retained history (no soak boundary)",
        "first_retained_epoch": first,
        "last_retained_epoch": last,
        "history_starts_after_boundary": first is not None and since is not None and first > since,
        "changed_feature_checks": changed_feature_checks(counts),
        "signals": {
            name: {
                "count": count,
                "status": "observed" if count else "not observed in retained history",
                "examples": examples[name],
            }
            for name, count in counts.items()
        },
        "manual_checks": [
            "UI layout, DPI/window snapping, mouse movement, and mouse-wheel Stop across repeated starts.",
            "Notification receipt; no delivery error does not prove an alert was delivered.",
            "Shutdown cancellation, actual power-off, and max-play-time finishing the current game without requeueing.",
            "Correct deck contents, spell/ability resolution, sacrifice ordering, combat choices, and mulligan tuck acknowledgment require match snapshots/GRE evidence.",
        ],
        "limitations": [
            "Counts are log events, not unique games, passes, or confirmed failures. Use the normal audit for match results and concede confirmation.",
            "A feature with no event may be disabled or never exercised; zero is not a failed test.",
            "History rotation can remove evidence. A late first timestamp can also mean the bot started after arming; it does not prove rotation.",
            "The marker identifies the branch at arming; it does not prove a running bot loaded that revision. Restart the app after changes.",
            "Local log timestamps assume the same machine timezone as the run. All sessions since the boundary are included.",
        ],
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--arm", action="store_true", help="Mark the start of a new soak; does not start the bot")
    mode.add_argument("--since", type=float, help="Audit from this Unix epoch instead of the saved marker")
    mode.add_argument("--all-history", action="store_true", help="Ignore the marker and inspect all retained history")
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve()
    marker = runtime / "analysis" / ARM_FILE
    if args.arm:
        now = time.time()
        result = {"since_epoch": now, "armed_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                  **revision()}
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"armed": True, "marker": str(marker), **result}, indent=2))
        return 0
    saved = None
    since = args.since
    if since is None and not args.all_history and marker.exists():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
            since = float(saved["since_epoch"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            parser.error(f"Invalid soak marker {marker}: {exc}; use --since or --all-history")
    if since is not None and not (0 <= since < float("inf")):
        parser.error("--since/marker since_epoch must be a finite, nonnegative Unix epoch")
    result = scan(runtime, since)
    result["soak_marker"] = saved
    print(json.dumps(result, indent=2))
    return 0 if result["coverage"] == "available" else 2


if __name__ == "__main__":
    raise SystemExit(main())
