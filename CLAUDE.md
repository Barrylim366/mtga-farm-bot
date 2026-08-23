# CLAUDE.md

## Access Restrictions

AI agents must **not open, read, or analyze** the following files:

- credentials.txt

This file contains sensitive information and is explicitly excluded from access.


## Git Workflow

- Committing and pushing straight to `main` is fine. The branch protection
  ("Changes must be made through a pull request") may be bypassed — we own the
  repo and the bypass is intended. No feature branch or PR is needed unless
  explicitly asked for.
- Close GitHub issues with `Closes #<nr>` in the commit message when the commit
  fixes one.


## Versioning / Auto-Update

The bot has two auto-update channels (see `update_checker.py`): git installs
pull every new commit via `git pull`, but ZIP/website installs (no `.git`) only
get an update **when `version.py` on `main` goes up** (the `__version__` number
is compared, then `main.zip` is downloaded).

**Do NOT bump `version.py` on every push.** A bump raises an update dialog for
*all* ZIP users, so it should only happen for larger, user-visible changes (real
new features, important fixes to bot behaviour) — not for internal rework,
refactors, docs, tooling or updater internals.

Procedure: when a commit *might justify* a bump, **ask the user before** doing
it ("Should this trigger a user update? Bump the version?"). Only bump
`version.py` after explicit confirmation. When in doubt, **don't** — ask. Bump
semantically: patch for fixes, minor for features.

## Documentation

Before a commit, README.md must be updated to reflect the current state.

## Testing

Before commits that change controller/AI logic, run
`python -m unittest discover tests`. The suite checks code correctness
(state-machine guards, AI activation logic, cast dialogs) through mocked unit
tests — it is no substitute for the debug artefacts below, which cover game
behaviour and decisions.

The suite is isolated from `runtime/` so tests do not write into the running
bot's artefacts. That isolation does **not** hang off `tests/__init__.py`:
`discover tests` sets `top_level_dir` to `tests/`, so the `tests` package is
never imported and its `__init__` never runs. Instead,
`runtime_paths._running_under_test_runner()` detects the test run from the
process's `__main__` and redirects `runtime/` to a temp directory (the line
`[runtime_paths] test runner detected …` says where, on every run).

Two traps that have already sprung here:

- **Never freeze a runtime path into a module constant at import time** — that
  is exactly what `bot_logger.BOT_LOG_FILE` did, and it defeated the redirect.
  Resolve paths at call time (`bot_logger.get_bot_log_path()`).
- **Do not rely on a package `__init__` running.** While the isolation depended
  on it, it worked by accident: `tests/test_combat_blocks.py` is the first to
  pull the package in, via `from tests.test_combat_shadow import …`, halfway
  through the run. Everything alphabetically before it wrote into the real
  `runtime/` — on 2026-08-21 that put 273 lines of fixture output (card 999)
  into the `bot.log` of a bot that was *playing*, and `test_runtime_isolation`
  (which runs later) still saw a correct environment.

`tests/test_runtime_isolation.py` checks both, and also fails if the test-runner
guard does not recognise the runner.

**Unit tests must not see the screen.** Build a `Controller` and trigger a path
that does template matching, and it really searches the monitor. On 2026-08-21
`BlindSweepTest` ran into the overlay rescue, found a Done button in the
*running* MTGA, clicked for real and died with
`AttributeError: '_FakeInput' object has no attribute 'left_down'`. It looked
exactly like a flaky test — it only fell over while the bot was running, and was
green in five repeats afterwards — and cost a round of hunting a timing problem
that never existed. Side effect: 13 of the module's 15 seconds of runtime were
real image searches (now 1.6 s). So in `make_controller` always stub
`_locate_image_center_in_scaled_arena_region` and
`_click_image_in_scaled_arena_region`.

## PR Reviews

When asked to review a PR, always also fetch and consider that PR's CodeRabbit
review comments on GitHub (both the summary comment and any inline review
comments), not just a manual read of the diff.

## Debugging / Post-mortem Artefacts

**When the user asks for the bot to be watched, babysat, supervised or kept
running while it plays — or for several accounts to be farmed unattended — use
the `babysit-bot` skill** (`.claude/skills/babysit-bot/SKILL.md`). It is the
procedure for a **long unattended run**: a Monitor on `bot.log` as the primary
signal plus a self-paced `/loop` as the fallback — because a *freeze writes no
log line*, so silence and health look identical to the Monitor. Snapshot tool:
`python tools/health_check.py` (read-only, safe even mid-match).

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
- **Ineffective target click** (`runtime/debug/target-click-<stamp>/`) —
  screenshot plus `target_click_state.json` with the click point
  **arena-relative** (directly measurable against the card in the image),
  written when a target selection is still open 2.5 s after a target click
  (log: `TARGET_CLICK_INEFFECTIVE`). Important because MTGA delivers **no hand
  hovers** while a target selection is open — every following cast sweep is then
  blind and looks like a completely different fault.
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
So the "there is no overlay" finding below holds for most, but not all, cases:
roughly one in six is a genuine, dismissible overlay and the rescue works.

Grep for the lines that actually reach `bot.log`:
`the sweep was blind with MTGA active, but no Done button` and
`dismissed an overlay covering the board`. **Not** for "Scanned entire hand area
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
does not know those modes, so it considers the sweep legitimate. The rescue then
inevitably reaches for the wrong thing (reactivate window, hunt a Done button).
A fix would have to address *when a sweep is allowed*, not the rescue.

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
in `Step_DeclareAttack`) — fix still open, deliberately not done unattended.**
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

The bot reads "still in `Step_DeclareAttack`" as "my move did not arrive" and
clicks again immediately — i.e. straight into the animation. The
`COMBAT_RECOVERY_ATTEMPT` fires 0.24 s after the first click and is therefore
guaranteed to be ineffective. Measured cost per incident **~6 s** (not the ~20 s
previously claimed here), 3× in 90 minutes. A fix would have to wait for
`attackState=AttackState_Declared` after declaring and then submit *once*,
instead of re-clicking after a fixed timeout. This sits in the path of every
attack — only touch it with supervision and with a test.
