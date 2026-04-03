# Incidents

- `2026-04-03 12:02:30`, `incident-20260403-120230`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: the bot was asked to sacrifice permanents through a `SelectNReq`, but the controller only supported `hand` and `stack/pending` SelectN targets. It aborted on battlefield ids `699,682`, never made the sacrifice selection, and then roped until the supervisor legitimately conceded.
  Fix: added battlefield-aware `SelectN` selection in the controller, narrowed the old stack-wait fallback so it only applies when there are no concrete selectable ids yet, and added battlefield selection debug bundles via `debug/hand-select-<timestamp>/`.

- `2026-04-03 12:55:02`, `incident-20260403-125502`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this incident happened during `DeclareBlockersReq` while the controller still had an active `decision_delay_wait` and the local inactivity timer still showed `29s` remaining. The supervisor fired on stale action timestamps before the delayed blocker decision had a chance to run, so this was a supervisor false-positive rather than a gameplay click miss.
  Fix: the supervisor now suppresses the rope-stall trigger while a controller `intentional_wait` is still active, unless the rope is already down to the final `<= 8s`.

- `2026-04-03 13:16:07`, `incident-20260403-131607`
  Trigger: `repeated_own_timer_critical`
  Supervisor: recovery failed, Codex notify did not run because `HOME` was never reached
  Root cause: the recovery screenshots showed the browser/UI window in the foreground instead of MTGA, so `ESC`/`Concede` clicks were sent while Arena was not the active frontmost window. The fallback click `(2794,755)` was still inside the detected MTGA client region, but not applied to the visible foreground app state the user was actually seeing.
  Fix: the supervisor now explicitly restores/focuses the MTGA window before in-game recovery steps and match-end dismiss, and the controller also focuses MTGA before `dismiss_end_screen()`.

- `2026-04-03 13:36:32`, `incident-20260403-133632`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real botlogic stall, not a supervisor mistake. The UI-path child attached into an already-running match, armed a delayed decision, and then dropped the callback with `decision_method called but game not started yet, ignoring` because `game_started` was only ever set by the mulligan callback. Since no mulligan happens on a mid-match attach, the bot never acted and roped until the supervisor legitimately conceded.
  Fix: `Game.decision_method()` now infers `game_started` from a live gameplay state (`turn_info`/actions/priority) on direct attach paths and suppresses starting-hand logging for midgame attaches.

- `2026-04-03 15:06:33`, `incident-20260403-150633`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery failed, Codex notify did not run because `HOME` was never reached
  Root cause: this run is not a clean baseline for the cast path because the stuck state was manually induced during testing. The trustworthy part of the evidence is the failed recovery: supervisor recovery pressed `ESC`, but the options/concede menu never became visible; the old recovery then clicked a blind fallback concede coordinate and stayed in-game.
  Fix: the supervisor side stays fixed: `ESC` is now retried with per-attempt debug captures and recovery clicks only a real `Buttons/concede.png` match instead of a blind fallback coordinate. The temporary multi-band cast hand-scan experiment was reverted because this incident was not a reliable reproduction of a real cast bug.

- `2026-04-03 15:20:03`, `incident-20260403-152003`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: incident capture succeeded, but the bundle stopped at `incident.json`/screenshots without `recovery.json` or `codex_notify.json`
  Root cause: the bot stalled first on a normal `cast`/`play` decision for hand card `170`; the hand-select debug bundle from the same run shows the Arena `Options` overlay was still open over the battlefield, so the hover-based hand scan never had a chance to find the card. Separately, the supervisor bundle was misleading because recovery/notify placeholder files were not written until after recovery had already run.
  Fix: the controller now dismisses an open `options_anchor.png` overlay with `ESC` before hover-based hand scans and writes a dedicated overlay debug bundle if the menu still blocks the board; the supervisor now writes placeholder `recovery.json` / `codex_notify.json` immediately after creating the incident directory so incomplete incidents are still self-describing.

- `2026-04-03 15:47:02`, `incident-20260403-154702`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: the bot got stuck in a discard `SelectNReq` loop. The controller kept calling `__handle_select_n_req(line)` on the same request line, but each call rebuilt `__pending_select_n` as if it were a brand-new prompt. That reset `discard_retry` back to `0` every time, so the bot spammed `SelectN ids not in hand; ... hand zone missing, retrying once` for minutes instead of ever exhausting the retry budget and clearing the stale selection.
  Fix: repeated processing of the same `SelectNReq` now reuses the existing pending token/state for the same `ids`, so discard retries keep their counter instead of restarting forever.

- `2026-04-03 16:10:31`, `incident-20260403-161031`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: the prior discard `SelectN` had already cleared, but the controller left `runtime/status.json` on `target_selection_wait`. The incident screenshot showed no active target prompt, only a normal in-game board state, and the log had already emitted `SelectN cleared: decisions may resume.`. The remaining pause was a normal stack-resolution defer, but it inherited the stale target-selection label.
  Fix: clearing a `SelectN` prompt now also clears stale target-selection waits when no real target/selection annotation is still active, and stack-based decision defers now publish `stack_resolution_wait` explicitly instead of leaving the old wait reason behind.

- `2026-04-03 16:39:06`, `incident-20260403-163906`
  Trigger: `repeated_own_timer_critical`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a timer-classification false-positive. The bot had just successfully attacked and submitted, then Arena emitted a critical `TimerType_ActivePlayer` for the long turn clock while the real inactivity timer had already reset to `elapsedMs=344`. The controller still incremented `my_timer_critical_count` for that active-player timer, so the supervisor treated it like a rope emergency and conceded even though no real inactivity timeout was happening.
  Fix: only `TimerType_Inactivity` now increments the watchdog critical counter, and the supervisor only honors `repeated_own_timer_critical` when the current timer type is also `TimerType_Inactivity`.
