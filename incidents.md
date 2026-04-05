# Incidents

## Tracking Model

Use incident tracking as evidence, not binary truth.

- Status values:
  - `proposed`: Codex has a diagnosis / fix idea, but it is not merged yet.
  - `applied`: the change is merged, but there is not enough runtime evidence yet.
  - `survived_n_runs`: the bot survived `N` later comparable runs without the same signature.
  - `reproduced_and_passed`: a replay-like recurrence of the original situation happened and the bot got through it.
  - `regressed`: the same signature happened again after the fix.
- Confidence is heuristic:
  - Start around `0.3` for a log-derived guess with no direct replay.
  - Raise toward `0.6` after roughly `10+` comparable runs without the same signature.
  - Raise toward `0.9` when a replay-like recurrence is survived cleanly.
  - Drop toward `0.1` and/or mark `regressed` if the same signature returns.
- Signature clustering matters:
  - Prefer a stable signature key built from trigger + control-flow step + prompt type + dominant log pattern, instead of a raw timestamp.
  - Do not treat two different hangs as the same incident just because both ended in `own_inactivity_timer_stalled`.
- Verification evidence should prefer:
  - later recurrence or absence of the same signature
  - survival through similar UI/log states
  - longer time until the next same-signature failure
  - lower frequency of that signature across many runs

## Entry Template

Use this template for new incidents:

- `YYYY-MM-DD HH:MM:SS`, `incident-<timestamp>`
  Signature: `<stable_signature_key>`
  Trigger: `<supervisor_trigger>`
  Supervisor: `<recovery summary>`, Codex notify `<summary>`
  Root cause: `<concrete gameplay/control-flow diagnosis>`
  Fix: `<applied fix or next targeted debug step>`
  Tracking:
  - Status: `proposed|applied|survived_n_runs|reproduced_and_passed|regressed`
  - Confidence: `0.x`
  - Evidence: `<runs survived / replay-like reproduction / repeated regression / still unverified>`

Machine-readable workflow:

- The supervisor now creates `<incident-dir>/tracking.json` automatically for every new incident bundle.
- `tracking.json` now also stores `suggested_signature` and `signature_basis`, inferred from trigger + wait reason + phase/step + dominant log patterns.
- Use `python tools/incident_tracking.py set-status ...` to write signature/status/confidence/evidence into that bundle and into `incident_registry.json`.
- Use `python tools/incident_tracking.py suggest ...` to inspect the current auto-suggested cluster key before confirming or overriding it.
- Use `python tools/incident_tracking.py record-survival ...` to increase `runs_since_applied` for a signature without overstating certainty.

## Legacy Notes

Entries below this point predate the tracking model and remain narrative-only unless they are later revisited and upgraded.

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

- `2026-04-03 17:50:12`, `incident-20260403-175012`
  Trigger: `repeated_own_timer_critical`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify failed because the old `codex_window.png` template did not match
  Root cause: this was a real botlogic stall in `DeclareBlockersReq`. The controller armed its normal 4-second `decision_delay_wait` as soon as blocking priority opened, but the local `TimerType_Inactivity` rope was already down to `4.0s`. A later timer update noticed the rope critical state, yet the controller kept the already-armed timer instead of canceling it and deciding immediately, so the bot never submitted blockers before the supervisor had to concede.
  Fix: delayed own-priority decisions are now rope-aware. If the local inactivity timer is already low, the controller bypasses or clamps the artificial delay and cancels any existing delayed callback for that same priority window so blocker/combat decisions execute immediately instead of timing out.

- `2026-04-03 18:34:19`, `incident-20260403-183419`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real botlogic stall in `SelectNReq` during resolution. The local `GameState` merged Arena diffs by blindly replacing list fields, so partial updates dropped previously known `zones` and `gameObjects`. When Arena later asked for a battlefield `SelectN` with ids like `669,702,708`, the controller no longer had a valid battlefield snapshot, misclassified the request as “ids not in hand and no prompt candidates found”, and stopped making gameplay decisions while the inactivity timer kept running.
  Fix: `GameState.update()` now merges keyed list payloads (`zones`, `gameObjects`, `players`, `timers`, annotations) cumulatively and honors deleted-instance ids, so partial diffs no longer erase battlefield context needed for `SelectN`/combat decisions.

- `2026-04-03 18:40:39`, `incident-20260403-184039`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was primarily a supervisor false-positive. Arena had already auto-resolved a cleanup discard through an `informationalUseOnly=true` `SelectNReq` and emitted `ClientMessageType_SelectNResp`, then the game advanced to the opponent turn (`decisionPlayer=1`, local seat `=2`). The controller still treated that SelectN as actionable, and the supervisor's inactivity-stall trigger ignored whose priority it actually was, so the supervisor conceded while the bot had no legal local decision window.
  Fix: the controller now clears informational-only `SelectNReq` payloads immediately instead of scheduling delayed clicks, and the supervisor now requires the local seat to own current priority/decision before firing `own_inactivity_timer_stalled`.

- `2026-04-03 18:50:15`, `incident-20260403-185015`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall during our own `Phase_Main1`. Arena exposed a legal `ActionType_Pass` alongside cast/play actions while one object remained on the stack, but the controller checked `action.get("actionType")` directly on the action wrapper instead of the nested `action` payload. That made it miss the available pass action, log `Deferring decision: stack has 1 object(s)` forever, and burn the inactivity rope even though the game could legally progress.
  Fix: stack-resolution pass detection now unwraps the Arena action entries correctly and recognizes nested `ActionType_Pass`, so the controller no longer misclassifies those states as mandatory stack waits.

- `2026-04-03 19:01:47`, `incident-20260403-190147`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real `SelectNReq` stall on a triggered stack-choice prompt. Arena asked for `ids=[314,315]`, but those ids were `GameObjectType_Ability` entries in `ZoneType_Pending/Stack`, while the UI hover stream only exposed the underlying parent objects (`parentId=280` and `parentId=311`). The controller tried to hover-select `314/315` directly, never found them, logged `SelectN failed to select any cards`, and then stayed stuck on the unresolved trigger with our own inactivity rope running.
  Fix: stack/pending `SelectN` selection now remaps ability-instance prompt ids to their `parentId` hover targets before scanning, while still tracking the original prompt ids for stale-prompt verification.

- `2026-04-03 19:08:20`, `incident-20260403-190820`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall caused by a stale stack-mode `SelectNReq` worker surviving into combat. Arena first exposed a triggered-choice prompt with `ids=[342,341]`, and the controller correctly remapped those ability ids to `337/333`; but while that stack-selection retry thread was still sleeping/scanning, Arena advanced to our own `DeclareAttackersReq` (`requestId=85`, `canSubmitAttackers=True`). The controller kept `__select_n_in_progress` set, so every decision tick logged `SelectN in progress: pausing other decisions.` and combat recovery expired before attackers were ever submitted.
  Fix: stack-mode `SelectN` state is now centrally clearable, and our own `DeclareAttackersReq` preempts any stale stack-selection retry immediately. As a second guard, `__should_pause_for_select_n()` now also auto-clears stack-mode `SelectN` when combat recovery is already armed, so combat submit is no longer blocked behind an obsolete trigger-choice thread.

- `2026-04-03 19:29:46`, `incident-20260403-192946`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall caused by stale target-selection state. Arena was already back in our `Phase_Main1` with legal `ActionType_Cast`, `ActionType_Play`, and `ActionType_Pass`; the controller even logged `Stack present but safe to resolve: ... pass available.`. But `__should_pause_for_targets()` still saw `AnnotationType_PlayerSelectingTargets` in the merged local annotations and kept publishing `target_selection_wait`, so every decision tick aborted with `Pausing decision while target selection is pending` even though no real target prompt remained.
  Fix: target-selection state is now centrally clearable, `SubmitTargetsResp` and inactive target prompts clear it through one helper, and stale `PlayerSelectingTargets` annotations are auto-cleared when the target-selection signal is old and Arena has no pending target message anymore. That prevents dead target-selection annotations from blocking later main-phase/combat decisions.

- `2026-04-03 19:38:23`, `incident-20260403-193823`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall caused by a missing mulligan-state restore on an attach/supervisor start. The raw log for the new match contained `ClientMessageType_MulliganResp` with `MulliganOption_AcceptHand`, but the controller never converted that into `__has_mulled_keep=True`. It therefore stayed in a fake pre-mulligan state even while Arena was already offering normal `ActionType_Cast` / `ActionType_Play` / `ActionType_Pass` windows, and all real decisions were skipped behind `has_mulled_keep=False`.
  Fix: the controller now marks keep immediately when it sees `ClientMessageType_MulliganResp` accept-hand for the local seat, cancels any pending mulligan timer, and also infers keep from an already-live gameplay state as a fallback for late attaches. That lets fresh matches proceed even when the classic mulligan callback path was bypassed.

- `2026-04-04 15:10:51`, `incident-20260404-151051`
  Signature: `own_mulligan_wait_callback_false_negative`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall at the start of a new match. The controller correctly armed a delayed mulligan response after seeing the local `GREMessageType_MulliganReq`, but the timer callback re-ran `__has_pending_mulligan_state()` against the merged live state instead of trusting the armed prompt. Because that live snapshot already contained normal turn/action data, the heuristic returned false and the callback logged `Skipping delayed mulligan (decisionPlayer=1, my_seat=1)` instead of sending the keep response. The mulligan prompt never got answered, so the inactivity watchdog eventually classified the match as stuck.
  Fix: the delayed mulligan callback now trusts the armed prompt once it is already scheduled for the local seat. It no longer re-checks the merged live action list before resolving the timer, so a real local mulligan prompt cannot be dropped just because the current snapshot already contains gameplay actions.
  Tracking:
  - Status: `applied`
  - Confidence: `0.3`
  - Evidence: the latest incident bundle shows `Arming mulligan decision timer.` followed by `Skipping delayed mulligan (decisionPlayer=1, my_seat=1)` and then the supervisor `own_inactivity_timer_stalled` recovery path; the callback was suppressing a real mulligan response rather than the supervisor failing to recover.

- `2026-04-03 19:48:04`, `incident-20260403-194804`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall in a resolution `SelectNReq` with concrete battlefield ids `340` and `355`. Arena had already exposed those exact permanents as selectable targets, and the hover stream confirmed both ids repeatedly; but inside the delayed `SelectN` worker the controller still treated `resolution_context && stack_count > 0` as blocking, retried twice, then aborted the prompt with `SelectN aborted: decisionPlayer=2, pendingMessages=0.` instead of clicking the available battlefield targets.
  Fix: the `SelectN` retry gate now distinguishes real concrete resolution selections from generic unresolved stack waits. If Arena already provided concrete hand/battlefield/stack ids, `stack_count > 0` no longer blocks the retry worker, so the controller proceeds with the selection instead of looping on `SelectN delayed` and aborting.

- `2026-04-03 20:01:32`, `incident-20260403-200132`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real state-reset bug across matches. After the previous game ended and queue spam restarted, the next match's first `GameStateType_Full` snapshot was merged into stale cached state from the old game. That left the controller with a hybrid impossible state like `turn=18 / Phase_Combat / Step_CombatDamage` plus new-match mulligan data, so later decision ownership/timer logic was working against the wrong board state before the supervisor eventually had to concede.
  Fix: `GameState.update()` now treats `GameStateType_Full` as a hard reset and replaces the local cached game state outright instead of merging it like a diff. That prevents stale turn/zones/timers/annotations from bleeding into the next match after restart.

- `2026-04-03 20:11:12`, `incident-20260403-201112`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall caused by a stale stack-mode `SelectN` worker. After our cast, Arena had already returned to a normal own `Phase_Main1` decision window with `pendingMessageCount=0` and legal `ActionType_Pass`, and the controller even logged `Stack present but safe to resolve`. But `__should_pause_for_select_n()` still treated the old in-progress stack selection as blocking, so it kept scanning dead stack ids like `372` and `313` and suppressed all real decisions until the supervisor conceded.
  Fix: the controller now auto-clears stack-mode `SelectN` state when Arena exposes a normal own main-phase pass window on a live stack (`ActionType_Pass`, local `decisionPlayer`, `pendingMessageCount=0`). That lets the stale selection worker terminate immediately and restores normal cast/pass decisions.

- `2026-04-03 20:19:37`, `incident-20260403-201937`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real matchstart mulligan-state bug. Very early pregame diffs already exposed `turnInfo`, legal-looking `Cast/Play` actions, and a stack object, so the controller's live-state fallback inferred `has_mulled_keep=True` before the actual local `GREMessageType_MulliganReq` arrived. Once that real mulligan prompt did arrive, the controller still behaved as if the hand had already been kept and let the earlier stack/defer path win instead of arming mulligan handling.
  Fix: mulligan timing is now tracked separately from accepted keep. Live-state keep inference is blocked while any mulligan prompt is still pending, an actual local `MulliganReq` clears any premature keep state, and mulligan handling now runs before stack/pending defers so matchstart prompts cannot be shadowed by fake early board state.

- `2026-04-03 20:27:02`, `incident-20260403-202702`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a second mulligan-state stall caused by stale merged player metadata. A very early diff had set `players[].pendingMessageType=ClientMessageType_MulliganResp`, and later keyed player merges never cleared that field. By turn 4 the game was already in our real `Phase_Main1` with `Play/Cast/Pass`, but `__has_pending_mulligan_state()` still trusted the stale player field, kept `mulligan_wait` armed, suppressed all gameplay decisions, and eventually even tried to click `KEEP_HAND` in the middle of the live game.
  Fix: mulligan-pending detection now yields to real gameplay once Arena exposes a numbered turn plus `phase/step` and normal `Play/Cast/Pass` actions. That lets live keep inference cancel the stale mulligan timer instead of preserving an old `pendingMessageType` forever.

- `2026-04-03 21:13:12`, `incident-20260403-211312`
  Trigger: `supervisor_stuck_timeout`
  Supervisor: recovery succeeded (`HOME` already visible), Codex notify succeeded
  Root cause: this was not a real in-game stall. After match end, the built-in account-switch logout never reached the login screen, but Arena was already back on `HOME` / `MainNav`. Runtime status still carried stale `bot_state=IN_GAME` and old combat `turn_info`, while `main_nav_loaded` had switched mode to `home_ready`; the child then idled on home after `logout did not reach the login screen`, and the supervisor later misclassified that stale home state as a gameplay timeout incident.
  Fix: home/queue-ready markers now explicitly reset runtime bot state to `HOME` and clear stale turn/timer telemetry, the supervisor's generic stale-timeout fallback now only applies in real in-game modes, and account-switch logout failures that land on `HOME` now abort that switch attempt and resume queueing on the current account instead of idling forever.

- `2026-04-03 21:35:08`, `incident-20260403-213508`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall caused by stale target-selection state. Arena was already in our own `Phase_Main1` with `decisionPlayer=2`, `pendingMessageCount=0`, a live stack object, and legal `ActionType_Pass`; the controller even logged `Stack present but safe to resolve`. But an old target-selection marker still made `__should_pause_for_targets()` return true, so the controller kept sitting in `target_selection_wait` instead of yielding to the real pass window until the inactivity watchdog fired.
  Fix: target-selection blocking now auto-clears when Arena exposes a normal own safe stack pass window (`pendingMessageCount=0`, local `ActionType_Pass`). In that state stale `target_selection_wait` is treated as phantom state and no longer blocks main-phase decisions.

- `2026-04-03 21:44:53`, `incident-20260403-214453`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real fresh-game state-reset bug. Arena had already entered a new mulligan/start-of-game baseline, but the controller still merged that early low-`gameStateId` diff into cached combat state from the previous match. Because the incoming `turnInfo` was sparse (`activePlayer` / `decisionPlayer` only), the old `phase`, `step`, `turnNumber`, stack objects, and actions survived locally, producing an impossible hybrid like fresh mulligan plus stale `turn=10 / Phase_Combat / Step_CombatDamage`. The controller then reasoned against that stale live state until the inactivity watchdog fired.
  Fix: the controller now hard-resets local live game state when the raw match id changes or when an early fresh-game baseline is detected (`GameState_Start`, low/regressed `gameStateId`, mulligan signals, sparse `turnInfo`) before merging the new payload. That clears stale turn/stack/action/timer state from the prior match before the new game is processed.

- `2026-04-03 22:01:21`, `incident-20260403-220121`
  Signature: `own_main_phase_decision_delay_wait_dropped`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was not a real gameplay stall. Arena had already exposed a normal own `Phase_Main1` pass/cast window on turn 2, and the controller correctly armed its standard 4-second decision delay. But on the next `GameState` update the same delay timer was still alive, while `runtime/status.json` no longer carried `decision_delay_wait`. The controller logged `Decision delay already armed for current priority window; keeping existing timer.`, returned without republishing the intentional wait, and the supervisor misread the live inactivity rope plus old action idle as `own_inactivity_timer_stalled` before the delayed callback could fire.
  Fix: when the controller keeps an already-armed decision-delay timer for the same priority window, it now refreshes `decision_delay_wait` in runtime status using the remaining delay. That preserves the supervisor skip condition until the delayed decision actually fires or is cancelled.
  Tracking:
  - Status: `applied`
  - Confidence: `0.3`
  - Evidence: patch is merged and a targeted local repro confirmed that a reused delay timer now republishes `decision_delay_wait`, but no later live replay of the same signature has been observed yet.

- `2026-04-04 10:39:17`, `incident-20260404-103917`
  Signature: `own_combat_select_n_worker_survived_preemption`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was a real controller stall caused by a stale stack-mode `SelectN` worker surviving combat preemption. Arena first opened a stack/pending `SelectNReq` with prompt ids `405,406`, which the controller remapped to hover targets `335,331`; then our own `DeclareAttackersReq` arrived and correctly cleared the stale `SelectN` state. But the already-running selection worker was still asleep inside its built-in 3-second delay, woke up after the clear, never re-validated its token, and continued clicking old stack targets plus `SUBMIT_SELECTION` into the combat window. That collided with `all_attack` / combat recovery and left the bot with no further progress until the inactivity watchdog fired.
  Fix: delayed `SelectN` workers now re-check prompt-token validity immediately after their sleep and again before each later click/submit step. If combat preemption or any other clear invalidated that prompt in the meantime, the worker exits without touching the UI.
  Tracking:
  - Status: `applied`
  - Confidence: `0.3`
  - Evidence: patch is merged after matching the latest incident timeline to the delayed worker logs (`SelectN delay`, `DeclareAttackersReq: preempted stale stack SelectN prompt.`, then stale `STACK_ITEM_*` clicks), but no later live replay of the same signature has been observed yet.

- `2026-04-04 10:55:29`, `incident-20260404-105529`
  Signature: `own_main_phase_active_turn_delay_untracked`
  Trigger: `own_inactivity_timer_stalled`
  Supervisor: recovery succeeded (`Concede -> HOME`), Codex notify succeeded
  Root cause: this was another supervisor false-positive, but in a different delay layer. The controller-side own-priority delay behaved correctly and even kept `decision_delay_wait` alive; after it fired, `Game.decision_method()` entered its own normal `Active turn delay: waiting 2 seconds before actions` on our turn-1 `Phase_Main1 / Step_Upkeep`. That AI-side sleep did not publish any intentional wait in `runtime/status.json`, so the supervisor saw our local inactivity timer plus stale action timestamps and classified the normal 2-second think delay as `own_inactivity_timer_stalled`.
  Fix: the AI-side active-turn sleep now publishes `active_turn_delay` as an intentional wait for the duration of that pause and clears it afterwards, so the supervisor skip logic covers both controller delays and the later per-turn delay inside `Game.decision_method()`.
  Tracking:
  - Status: `applied`
  - Confidence: `0.3`
  - Evidence: patch is merged after the incident timeline showed `Decision delay fired`, then `Active turn delay: waiting 2 seconds before actions`, with the incident status already carrying an empty `intentional_wait_reason`; no later live replay of the same signature has been observed yet.

- `2026-04-04 16:37:32`, manual stop (no supervisor bundle)
  Signature: `queue_click_outside_window__arena_region_none__absolute_fallback`
  Trigger: manual user stop (bot clicked outside MTGA window, minimizing it)
  Supervisor: not running at the time
  Root cause: Bot started with `state=BotState.IN_GAME` but `arena_region` was `None` (no cached region available). The queue-button click path in `Controller.py:1848` fell back to absolute desktop coordinates `(1699, 996)` instead of window-relative coordinates. Because MTGA was not positioned where those absolute coords assume, the click landed outside the Arena window, causing Windows to minimize it. The key failure is that the queue-button fallback uses raw absolute coordinates without verifying they actually fall within the MTGA window bounds.
  Fix: proposed — the queue-button absolute fallback should either (a) refuse to click when `arena_region` is `None` (like `ATTACK_ALL` already does at Controller.py:2112), or (b) call `focus_mtga_window()` and re-acquire the arena region before clicking. The safest approach is to skip the click and retry arena detection on the next loop iteration.
  Tracking:
  - Status: `proposed`
  - Confidence: `0.4`
  - Evidence: single occurrence; bot.log shows `arena_region unavailable, using absolute coordinates` immediately followed by `CLICK (1699, 996) - QUEUE_BUTTON`; user confirmed click landed outside MTGA and window minimized.

- `2026-04-04 17:01:00`, manual stop (no supervisor bundle)
  Signature: `declare_attackers_paycosts_loop__timer_expired__no_concede`
  Trigger: manual user stop (timer expired, bot kept playing after auto-pass)
  Supervisor: not running at the time
  Root cause: Two linked issues. (1) The bot entered an infinite loop on turn 9, `Phase_Combat / Step_DeclareAttack`: it received `DeclareAttackersReq`, chose `all_attack`, but a `PayCostsReq` immediately followed, which triggered `auto-pay`, which produced another `DeclareAttackersReq` — repeating endlessly. The attack likely required a cost the bot's auto-pay couldn't resolve, so the declare-attack never completed. This consumed the entire timer (CRITICAL at 5s remaining at 17:01:24 and 17:01:54). (2) After the timer expired and Arena auto-passed, the bot resumed playing on turn 11 with no concede. The bot has no built-in logic to concede after a timer expiration event — it only logs `MY_TIMER_CRITICAL` as informational. Without the supervisor running, nothing triggered a concede.
  Fix: proposed — (a) break the DeclareAttackers → PayCostsReq loop by detecting repeated PayCostsReq cycles for the same DeclareAttackersReq and submitting without further retry after N attempts; (b) add bot-level timer-expiration handling: when `MY_TIMER_CRITICAL` fires with remaining <= 5s and the same request is looping, the bot should abort the current action and pass priority instead of retrying.
  Tracking:
  - Status: `proposed`
  - Confidence: `0.4`
  - Evidence: single occurrence; bot.log shows DeclareAttackersReq/PayCostsReq cycling from 17:01:00 to 17:01:56 on turn 9 with two CRITICAL timer events at 5s remaining; bot continued to turn 11 after timer expiry without conceding.

- `2026-04-04 17:20:34`, manual stop (no supervisor bundle)
  Signature: `declare_attackers_on_opponent_turn__combat_recovery_wrong_player__ui_desync`
  Trigger: manual user stop (bot stuck, timer expired at 0.0s)
  Supervisor: running but did not trigger (child process still alive)
  Root cause: The `__handle_declare_attackers_req` handler did not check whether the bot was the active player. On turn 10 at 17:20:34, the opponent (activePlayer=1) entered DeclareAttack phase. The bot (seat 2) received the DeclareAttackersReq, armed COMBAT_RECOVERY (key=req:96), and clicked ATTACK_ALL at (3263, 1088) — on the opponent's turn. This likely caused a UI desync: no further game state updates arrived for ~2 minutes (17:20:35 to 17:22:48), and by the time the game state resumed, the bot's timer was at 0.0s. The bot processed the new state but could not act because the timer had already expired.
  Fix: applied — added an `activePlayer` check in `__handle_declare_attackers_req`: if `activePlayer != self.__system_seat_id`, the handler logs a message and skips combat recovery entirely. This prevents the bot from clicking ATTACK_ALL on the opponent's combat step.
  Tracking:
  - Status: `applied`
  - Confidence: `0.5`
  - Evidence: single occurrence; bot.log shows COMBAT_RECOVERY_ARMED key=req:96 cycle=1/5 and CLICK ATTACK_ALL at 17:20:35 while activePlayer=1 (opponent), followed by 2-min gap with no game state updates, then MY_TIMER_CRITICAL remaining=0.0s at 17:22:48.

- `2026-04-04 18:26:45`, `incident-20260404-182645`
  Signature: `stack_defer_no_pass__inactivity_timer_expired__concede_failed`
  Trigger: `repeated_own_timer_critical` (TimerType_Inactivity 150s → 0s)
  Supervisor: recovery attempted but FAILED — concede template scored 0.739, game stayed IN_GAME through 30+ retry cycles
  Root cause: Bot was decisionPlayer on turn 8, Phase_Main1 with 7 Cast actions available and stack=1. The stack-deferral logic at Controller.py:5559 checked for `ActionType_Pass` — which wasn't in the action list. Without pass, the bot deferred with "stack has 1 object(s)" and set a 2s intentional wait, but every new game state re-triggered the same defer. This loop continued for the full 150s inactivity timer. The bot never made a decision despite having 7 castable cards.
  Fix: applied — when decisionPlayer==us and no pass action but other actions are available, proceed with the decision instead of deferring. Only defer when there are truly zero actions to take.
  Tracking:
  - Status: `applied`
  - Confidence: `0.5`
  - Evidence: incident bundle shows MY_TIMER_TIMEOUT_OBSERVED timerId=12 TimerType_Inactivity elapsed=150s, bot_tail.txt shows repeated "Deferring decision: stack has 1 object(s)" with 7 ActionType_Cast actions available; recovery failed with concede_template_click score=0.739.
