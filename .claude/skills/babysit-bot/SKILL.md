---
name: babysit-bot
description: Watch a long unattended Burning Lotus run event-driven — arm a log Monitor plus a self-paced loop, triage from runtime/ artefacts, and stop/fix/restart the bot when it hits a real bug. Use when asked to babysit, monitor, supervise or "keep the bot running" for hours, or to farm several accounts unattended.
---

# Babysitting the bot

The job is to notice a real problem within a minute or two of it happening,
across hours, without burning context while nothing is wrong. Tailing the log
into the conversation fails at both ends: it floods context when the bot is
healthy, and it says nothing at all when the bot is frozen.

So: **be woken by events, and verify with a snapshot.**

## The two watchers, and why both

Arm both before doing anything else. They cover opposite failure modes.

**1. Monitor on `bot.log`** — the primary signal. It fires when the bot says
something happened.

```
tail -F -n 0 "<repo>/runtime/logs/bot.log" 2>/dev/null | grep -E --line-buffered \
"Round complete|ROUND_COMPLETE|Quest pass complete|Account switching disabled|\
emergency concede|Traceback|Bot stop requested|Quest count confirmed fresh"
```

Use `persistent: true`. `tail -F` (not `-f`) so it survives the log being
recreated on restart.

Deliberately **not** in that list, though all three are tempting:
`STUCK_ACTION_RETRY_LIMIT`, `Account switch attempt failed`, `Switching account to`.
Each fires several times an hour on a healthy run and each resolves itself —
the stall breaks its own loop, the failed logout retries and its counter resets
on success. Waking for them buys nothing and costs the attention you need for
the real thing. What still catches them escalating: `Account switching disabled`
(the 3/3 give-up) is in the filter, and the snapshot **counts** all three every
heartbeat. Filter on the state you would act on; count the rest.

**2. A self-paced `/loop`** — the fallback. **A freeze writes no log line**, so
silence and health look identical to the Monitor. Every ~25 minutes, run the
snapshot below and decide. With the Monitor armed this is only a heartbeat;
don't shorten it to poll.

## The snapshot

```
.venv/Scripts/python.exe tools/health_check.py
```

Read-only, safe mid-match. It answers the three questions that decide whether to
intervene, which no log tail answers:

- **Is it moving?** `last decision` / `last input` ages. A retry loop writes
  plenty of lines while achieving nothing; a freeze writes none. Both look like
  "running" if you only read the newest line.
- **Is it achieving?** matches finished, wins, gold per account, rotation.
- **Is it suffering?** counts per failure signature — and a *count over time* is
  what separates "known flakiness that self-heals" from "new and escalating".

## Tuning the Monitor filter — two traps that have both fired here

- **Match the log tag, not the prose.** A filter on `critical` matched
  "no longer **critical**" — the *recovery* message — so the timer coming back
  from the rope paged as the emergency it had just left. An earlier one matched
  `Report a Player` against a *refusal* message whose explanatory text merely
  mentioned the dialog it had prevented. Grep the tag the code emits, never a
  phrase from the sentence around it.
- **Drop transient noise.** `Arena region unavailable during reacquire` and
  `REWARD_CLAIM_*: image not found` fire in bursts during match loads and mean
  nothing on their own. A filter that pages on every one of them trains you to
  ignore it, which is worse than no filter.

If events arrive faster than you can act on them, re-arm with a tighter pattern
rather than living with the noise.

## Triage: read the artefacts, not the log

`runtime/` already holds the evidence, written per event. Reading megabytes of
log by hand is the slow, wrong path. The `debug-artefacts` skill has the full
list; the ones that resolve most questions:

| Question | Artefact |
|---|---|
| Why did it make *that* play? | `runtime/debug/matches/<utc>_<id>/snapshots.jsonl`, `board.txt` |
| Did it click the wrong place? | `runtime/debug/clicks.jsonl` (raw + arena-mapped, `decision_seq` joins to the decision) |
| Why did a card just vanish? | `runtime/debug/casting-option-*/` (screenshot + every point tried) |
| What happened overnight? | `runtime/analysis/alerts.log`, `runtime/records/<session>/match-*.json` |

## Screenshot first, log second

For anything about what the client is *showing*, take a screenshot and look
before forming a theory. This is not a tiebreaker of last resort; on 2026-08-23
it answered five questions in one step each, while log-grepping produced two
wrong hypotheses on the way:

| Symptom | What the log suggested | What the screenshot showed |
|---|---|---|
| Bot idle in combat, `all_attack=[]` | "declare-attack bug" | the **Library overlay** covering the whole board |
| `Start Bot` did nothing | — | a **DEFEAT** screen waiting on `[Click to Continue]` |
| Same again after a restart | — | a **750 gold reward** popup |
| "No decision for 163s, frozen?" | freeze | the **login screen** — the switch had never logged in |
| Login failing | "wrong credentials?" | `giacomo...`**q**`mail.de` — a mistyped `@` |

How to take one (`mss` alone returns black on this Wayland session; the engine's
own fallback chain handles it):

```python
import sys; sys.path.insert(0, ".")
from vision.vision import VisionEngine
import cv2
f = VisionEngine().capture()          # None means every backend failed
arena = f[193:1273, 760:2680]         # the MTGA window inside a 3440x1440 screen
cv2.imwrite("/tmp/shot.png", cv2.resize(arena, (1300, 731)))
```

Two things learned the hard way:

- **Crop and zoom before measuring a click target.** A full-screen downscale
  hides an 80px error. Read coordinates off a 3-4x crop of the region, then map
  back: `screen = (760 + arena_x, 193 + arena_y)`.
- **Capture deliberately, not continuously.** Each capture shells out to
  `spectacle`, which is single-instance — firing two in quick succession makes
  one return nothing, and the bot's own vision competes for the same tool. One
  capture per question, and pull several crops out of that one frame.

## Intervening

Only stop for a **real** bug: no progress, or a loop that is not self-healing.
Many things look alarming and are not — a failed logout retries and the counter
resets on success; `STUCK_ACTION_RETRY_LIMIT` breaks its own loop via `resolve()`.
Count before acting.

When it is real:

1. **Wait for `[MATCH_END]`.** Stopping mid-match loses the match — MTGA ropes
   the abandoned game out. The only thing that outranks this is a bot about to
   do something irreversible (see below).
2. **Stop from the UI**, never by killing the process: press `Stop Bot`. Measure
   the button before clicking -- its position depends on the window, and the
   coordinates that were hardcoded here were wrong by ~80px on the Linux box
   (measured 2026-08-23: `Stop Bot` at (179, 385), `Start Bot` at (179, 328)).
   Capture the panel via `VisionEngine().capture()` and read them off.
   Verify `Status: Stopped` before touching anything.
3. Diagnose from the artefacts, fix, `python -m unittest discover tests`.
4. **Restart the UI process.** Pressing `Start Bot` does *not* reload changed
   code — the running `ui.py` must be restarted. Windows: `pythonw.exe ui.py`
   from the repo root. Linux: `setsid nohup .venv/bin/python ui.py &` so it
   outlives the shell that started it.
5. Clear whatever MTGA is showing (defeat screen, reward popup) — a leftover
   screen blocks the next start; a `DEFEAT` screen with `[Click to Continue]`
   really does sit there and wait — then press `Start Bot`.
6. Confirm it is really playing again before standing down: a fresh
   `[MATCH_END]`, or `last decision` staying fresh across two snapshots.

## The one thing worth breaking a match for

If **Shut down PC when all accounts are done** is enabled, a false
"round complete" powers the machine off with the user away. If the bot is
rotating through accounts without playing, stop it *now* and cancel the
shutdown: `shutdown /a` on Windows, `shutdown -c` on Linux (both are harmless
when nothing is pending).

That exact sequence happened on 2026-08-22: a stale quest read made all five
accounts look finished, ~30 seconds each, no matches played, heading straight
for the shutdown.

## Reporting

Report when something **changed** — a fix, a new failure class, a milestone.
Stay quiet through routine switches and self-healing retries; a status line
every few minutes is the same noise problem in a different channel.

Report measured, not impressed: "5 matches, 1 win, rope 0, one self-healed stuck
loop" beats "running well". If play quality did not improve, say so.
