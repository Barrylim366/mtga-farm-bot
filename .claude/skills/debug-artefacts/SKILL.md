---
name: debug-artefacts
description: Find out why the Burning Lotus bot behaved the way it did, from the artefacts it writes itself under runtime/ (per-decision snapshots, per-click log, unanswered cast dialogs, watchdog records) plus the currently open, measured bugs. Use when debugging bot behaviour, a wrong play, a stall or freeze, a vanished card, or when triaging an unattended run after the fact.
---

# Debug artefacts: read the evidence, not the log

When debugging bot behaviour, check these automatically written artefacts under
`runtime/` **first** (gitignored, purely local) instead of reading megabytes of
log by hand:

- **Per-decision snapshots** (`debug_recorder.py`) —
  `runtime/debug/matches/<utc>_<matchId8>/`: `snapshots.jsonl` (one record per
  bot *decision*: the board state it saw plus the move it chose), `board.txt`
  (the same, human-readable), `match.json` (result + unresolved grpIds). Keeps
  the last 30 match folders. For "why did it make *that* play?".
- **Per-click log** (`click_recorder.py`) — `runtime/debug/clicks.jsonl`: one
  line per physical click (raw vs. arena-mapped point, `risky` flag,
  `decision_seq` as the join key). For "wrong place / wrong moment?".
- **Unanswered casting dialog** (`runtime/debug/casting-option-<stamp>/`) —
  screenshot of the arena plus `casting_option_state.json` (every point tried),
  written when a "Choose One"/additional-cost dialog is still open after all
  click candidates (log: `CASTING_TIME_OPTION_UNANSWERED`). The case is
  otherwise invisible — every click is in the log, the spell just never gets
  cast. For "why does the card simply disappear?".
- **Session watchdog** (`tools/session_watchdog.py`, a separate read-only
  process, started automatically by the UI on Start Bot) —
  `runtime/analysis/history.log` (a copy of bot.log that survives restarts),
  `runtime/analysis/alerts.log` (throttled problem lines plus one
  `[MATCH_SUMMARY]` per game), `runtime/records/<session>/match-NNNN.json`
  (result/turns/duration/alert counts per match), `runtime/debug/incident-<stamp>/`
  (stall "black box" with log tails + signature). For triaging long unattended
  runs and stalls/freezes (survives a hard bot crash, being its own process).

`MTGA_RUNTIME_DIR` moves the entire `runtime/` directory (logs, debug, records,
status, cache) elsewhere — for a separately recorded run or, as in the test
suite, to avoid touching the running bot's artefacts.

Toggles (all default on): `MTGA_DEBUG_SNAPSHOTS=0` / `MTGA_DEBUG_CLICKS=0`
disable the recorders; `MTGA_DEBUG_FULL_STATE=1` appends the raw full state to
every snapshot. The watchdog is a no-op in packed `.exe` builds (source only).
The granular recorders (decision/click) run in-process; the watchdog is the
crash-proof, cross-session index on top of them.

**Known gap (open, still real on 2026-08-22):** blind hand sweeps with the MTGA
window *active* recur across sessions and accounts and regularly cost the rope
(`MY_TIMER_CRITICAL`). Re-measured over a full day (2026-08-22, ~7 h, 47
matches): **24 blind sweeps** — 20 where nothing covering the hand could be
found, and 4 where there really was an overlay and `scry_done.png` cleared it.
So the "there is no overlay" finding holds for most, but not all, cases:
roughly one in six was a genuine, dismissible overlay.

**Those measurements describe a build that no longer exists.** On 2026-08-23 the
whole click-path group between 1.2.1 and 1.3.0 was rolled back (see README):
the focus-state-aware rescue, the clamped hand band, the Home-tab guard, the
`TARGET_CLICK_INEFFECTIVE` bundle and the Report-a-Player detection are all
gone, because play got measurably worse with them in. So the numbers below are
history to reason from, not the behaviour of the running bot — and the gap is
open again with no diagnostics on it. Anything brought back comes back alone,
with a session of watching behind it.

Grep for the lines that actually reach `bot.log`: **not** "Scanned entire hand area
but did not find" — that phrase only exists in a bare `print()`
(`Controller.py`), never reaches the log, and a pattern on it reports a clean
zero for the bot's single most common failure. `tools/health_check.py` did
exactly that for a day before it was noticed.

Measured against the bundles on 2026-08-21:

- There is **no** overlay over the hand (checked twice via screenshot).
- The hand band does **not** clip the cards — with a 7-card hand, x=1700 sits in
  the middle of the rightmost card (~1572..1830).
- In one case a **target selection** was open; in another a card was visibly
  **mid-cast** (raised, glowing).
- In every case the controller simultaneously reported
  `pending_card_prompt=None`, `casting_time_options_open=False`,
  `pending_select_n=None`.

Working hypothesis: MTGA delivers **no** hand hovers while the client is in a
special mode (target selection, cast in progress) — and the bot's state model
does not know those modes, so it considers the sweep legitimate. A rescue built
on the sweep's own verdict therefore reaches for the wrong thing — that is what
the reverted attempt did. A fix has to address *when a sweep is allowed*.

**Known gap:** `_detect_stuck_reason` (in `tools/session_watchdog.py`; the
standalone `tools/bot_supervisor.py` has been removed) only detects stalls via
the inactivity timer. When `Game.py`'s own `DECISION_HEARTBEAT` re-triggers a
hanging decision, that resets the same timer — so a loop of identical,
ineffective moves (e.g. a cast click that never reaches the game) looks like
normal activity to the watchdog. `Game._STUCK_MOVE_RETRY_LIMIT` breaks such a
loop itself after 3 identical repeats (falling back to `resolve()`) and logs
`STUCK_ACTION_RETRY_LIMIT`, which `session_watchdog.py` records as its own alert
signal (`stuck_action`) — that is the only channel through which this case
becomes visible at all.

**Root cause of the most common `stuck_action` found (2026-08-22, `all_attack=[]`
in `Step_DeclareAttack`) — fixed on 2026-08-23 in `06bbe3f`, and this is the one
click-path change from that day that was *kept* in the rollback.**
An attack needs **two** clicks on the *same* button in MTGA
(1755, 944 → mapped 3274, 1072): first *declare*, then *submit*. In between MTGA
animates the creatures moving in, and clicks in that window are swallowed.
Measured on one incident:

```
36.572  CLICK ATTACK_ALL   → creature 312: attackState=AttackState_Declared,
                             MTGA sends another DeclareAttackersReq
                             with canSubmitAttackers: true
37.030  CLICK ATTACK_ALL  (+0.46s) → nothing
38.586  CLICK ATTACK_ALL  (+1.5s)  → nothing
42.222  CLICK RESOLVE     (+3.6s)  → SubmitAttackersReq sent, AttackState_Attacking
```

The bot read "still in `Step_DeclareAttack`" as "my move did not arrive" and
clicked again immediately — i.e. straight into the animation. Measured cost per
incident **~6 s** (not the ~20 s previously claimed here), 3x in 90 minutes. A
second contributor was a *concurrent* `all_attack()` running 0.46-0.48 s behind
the first, which the post-mortem had read as a designed retry.

`__press_combat_button_verified()` now presses, waits for the submit
acknowledgement in `Player.log`, and only presses again on evidence — bounded
retries, every press logged with a `_RETRY`/`_BLIND` suffix, and a
non-blocking lock so a parallel sequence presses nothing at all
(`tests/test_combat_submit.py`). Worst case stays under the 8 s
`DECISION_HEARTBEAT`. Field-observed once each: a first press that already
submitted, and one skipped parallel sequence. This still sits in the path of
every attack — change it only with supervision and with a test.
