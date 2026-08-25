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

**When debugging bot behaviour, read the artefacts the bot writes under
`runtime/` before you read the log** — per-decision snapshots, the per-click
log, the unanswered-cast-dialog bundles, the watchdog's session records. The full catalogue, the log phrases that are safe
to grep for (and the one that silently reports zero), `MTGA_RUNTIME_DIR`, the
debug toggles, and the open, measured blind-sweep gap all live in the
`debug-artefacts` skill (`.claude/skills/debug-artefacts/SKILL.md`) — invoke
it instead of re-deriving any of it.


**The declare-attack path is the most sensitive code in the bot**: an attack
needs two clicks on the same button, and re-clicking into MTGA's animation gets
the second one swallowed. Since `06bbe3f` the press waits for the submit
acknowledgement instead of a stopwatch, and a lock keeps a second sequence from
pressing at all. It sits in the path of every attack — only touch it with
supervision and with a test. The measurements are in the skill.
