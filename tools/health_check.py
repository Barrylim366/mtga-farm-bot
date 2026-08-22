"""One screenful of "is the bot still actually working?".

Strictly read-only: it opens files under runtime/ and prints. It never touches
the screen, the config or the bot process, so it is safe to run at any moment,
including mid-match.

Written for babysitting a long unattended run (see .claude/skills/babysit-bot).
The point is to answer, in one command, the three questions that decide whether
to intervene:

  1. Is it moving?      -- mode/state, and how long ago it last decided or clicked
  2. Is it achieving?   -- matches finished this session, wins, gold, quest reads
  3. Is it suffering?   -- counts per failure signature, plus the last log lines

A log tail alone answers none of these: a bot stuck in a retry loop writes
plenty of lines, and a frozen one writes none at all -- and both look like
"running" if you only glance at the newest line.

Usage:  .venv/Scripts/python.exe tools/health_check.py [--lines N]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime_paths import get_runtime_root  # noqa: E402

RT = get_runtime_root()

# Signatures worth counting. Kept as a table rather than one big regex so the
# output names the problem instead of just proving "something matched".
SIGNATURES = {
    "rope (turn timer ran out)": r"MY_TIMER_CRITICAL",
    "blind hand sweep": r"Scanned entire hand area but did not find",
    "stuck action loop": r"STUCK_ACTION_RETRY_LIMIT",
    "cast abandoned": r"CASTING_TIME_OPTION_UNANSWERED",
    "ineffective target click": r"TARGET_CLICK_INEFFECTIVE",
    "report dialog": r"REPORT_DIALOG_DETECTED",
    "switch attempt failed": r"Account switch attempt failed",
    "switching disabled": r"Account switching disabled",
    "stale quest read": r"STALE - not this account",
    "errors (all)": r"\[ERROR\]",
}


def _age(ts) -> str:
    if not ts:
        return "n/a"
    try:
        return f"{time.time() - float(ts):.0f}s ago"
    except (TypeError, ValueError):
        return "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", type=int, default=12, help="log lines to show")
    # Generous by default: bot.log is dominated by per-decision and RAW lines, so
    # a few thousand lines can be a single match. A window that short makes the
    # counts below look clean simply because nothing old enough is in view.
    ap.add_argument("--scan", type=int, default=25000, help="log lines to count over")
    args = ap.parse_args()

    status = {}
    status_path = os.path.join(RT, "status.json")
    try:
        with open(status_path, encoding="utf-8") as f:
            status = json.load(f)
    except Exception as exc:
        print(f"status.json unreadable ({exc}) -- is the bot running at all?")

    print("=== STATUS ===")
    for key in (
        "mode", "bot_state", "current_account", "next_account", "startup_phase",
        "last_move_name", "intentional_wait_reason", "last_recovery_reason",
        "my_timer_critical_count", "gold_farmed",
    ):
        if key in status:
            print(f"  {key}: {status[key]}")
    # Freshness is the part that distinguishes "working" from "frozen"; a frozen
    # bot keeps a perfectly plausible-looking status.json forever.
    print(f"  last decision:   {_age(status.get('last_decision_at_epoch'))}")
    print(f"  last input:      {_age(status.get('last_input_at_epoch'))}")
    print(f"  last log event:  {_age(status.get('last_playerlog_event_at_epoch'))}")
    print(f"  status written:  {_age(status.get('updated_at_epoch'))}")
    turn = status.get("turn_info") or {}
    if turn:
        print(f"  turn {turn.get('turnNumber')}: {turn.get('phase')} / {turn.get('step')}")

    log_path = os.path.join(RT, "logs", "bot.log")
    lines: list[str] = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-args.scan:]
    except Exception as exc:
        print(f"\nbot.log unreadable: {exc}")

    print("\n=== MATCHES (this session) ===")
    results = collections.Counter()
    for line in lines:
        m = re.search(r"\[MATCH_END\] result=(\w+)", line)
        if m:
            results[m.group(1)] += 1
    total = sum(results.values())
    print(f"  {total} finished: {dict(results) or 'none yet'}")

    print(f"\n=== FAILURE SIGNATURES (last {len(lines)} log lines) ===")
    counts = collections.Counter()
    for line in lines:
        for name, pattern in SIGNATURES.items():
            if re.search(pattern, line):
                counts[name] += 1
    if counts:
        for name, n in counts.most_common():
            print(f"  {n:5d}  {name}")
    else:
        print("  clean")

    print("\n=== ACCOUNT ROTATION (last 15) ===")
    rotation = [
        line for line in lines
        if re.search(r"Switching account to|Quest count confirmed fresh|"
                     r"Quest pass complete|Round complete", line)
    ]
    for line in rotation[-15:] or ["(no switch activity in view)"]:
        print("  " + line.rstrip()[:180])

    print(f"\n=== LAST {args.lines} LOG LINES ===")
    for line in lines[-args.lines:]:
        print("  " + line.rstrip()[:200])

    incidents = sorted(glob.glob(os.path.join(RT, "debug", "incident-*")))
    newest = os.path.basename(incidents[-1]) if incidents else "-"
    print(f"\n=== INCIDENT BUNDLES: {len(incidents)} (newest: {newest}) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
