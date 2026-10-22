---
name: audit-logs
description: Audit a Burning Lotus bot session from local runtime records, Gold balances, alerts, and debug artefacts; report performance and likely bugs with evidence.
---

# Audit bot logs

Use this skill when the user explicitly invokes `$audit-logs`. Report findings in chat and maintain the local, gitignored `audit-history.csv` and `audit-matches.csv` as authorized by the user. Do not modify bot code, records, or temporary soak diagnostics as part of the audit.

1. Read repository `CLAUDE.md` and `.claude/skills/debug-artefacts/SKILL.md`. Never read `credentials.json`. Start with `runtime/records/`, relevant `runtime/debug/` bundles, and `runtime/status.json`; then inspect focused lines in `runtime/analysis/history.log*` and `alerts.log`. Respect `MTGA_RUNTIME_DIR`.
2. Run `.venv\Scripts\python.exe .agents\skills\audit-logs\scripts\summarize.py` on Windows (or `.venv/bin/python` elsewhere). It returns a JSON index for the latest session and previous session and upserts the selected session into `audit-history.csv` in the project root. Use `--session <session-id-prefix>` when the user names another run. Treat its account labels and concede candidates as leads to verify in source artefacts. Check the output's `csv.updated` and report any CSV error. Use `--no-csv` only when a read-only audit is explicitly requested; `--csv <path>` selects an alternate destination.
3. Report the actual session window and evidence coverage. Use completed matches as the denominator for win rate and confirmed bot-concede rate. A watchdog trigger or concede click is an attempt, not proof of a completed concede; confirm against a lost match and game-result reason before counting it. If that evidence is missing, state the attempt count and leave the confirmed rate unavailable or explicitly bounded.
4. Report measured Gold balance gain by account and in total. This is the bot's observed increase from each account's first Gold balance, not gross rewards. For the current session, prefer `status.json` when its session ID matches; otherwise use baseline/farmed events in history. Merge aliases only when their identity is unambiguous, and never add two aliases of one account. Flag late first balances, missing log coverage, and uncertain attribution. Compute overall Gold/hour only when both Gold and full start/end times are known: total measured Gold / whole-session wall-clock hours, including queues and switches. A running session's rate is provisional. Do not estimate Gold from wins or use summed match time.
5. Show overall and best-effort per-account match results, win rate, and concede evidence. Compare the previous session with counts and denominators; show a Gold or Gold/hour change only when both sessions have sound data. Prioritize all critical incidents and the most repeated failures, including concede priority context, quest reroll freshness/outcome, and opponent creature target scans. Link to exact records, log lines, screenshots, or cursor/hover bundles. Label each cause as confirmed, likely, or unresolved; do not infer a fix from an alert alone.

Keep the final audit concise with evidence links. If an artefact has been rotated away, say what is unavailable instead of reporting a clean zero. Temporary soak signals may disappear after their bugs are fixed; the audit must continue to work from persistent match records and ordinary logs.

### Stall-only concede statistics

The label **Confirmed bot concedes** and the corresponding counts, attempts,
rates, per-account tables, and previous-session comparisons refer only to
concedes caused by stalls (`STALL_CONCEDE`, supported by watchdog/incident
evidence). Confirm each against a lost match and a concede game-result reason.
Count each confirmed game once, even when its concede sequence was retried.

Exclude configured game-duration limits (`TIMEOUT_CONCEDE` / `MATCH_TIMEOUT`)
from these statistics. Report them separately as **Duration-limit concedes**,
distinguishing attempts from confirmed losses. A long game or a generic concede
result alone does not establish a stall. Keep unknown causes unresolved rather
than assigning them to stalls. Continue using all completed matches as the
denominator for win rate and the stall-only concede rate; duration-limit losses
remain losses in overall match results.

### Game-length distribution

Include a table of completed games where the bot did not concede, with columns
`Length`, `Games`, `Share`, and `Win rate`, plus a Total row. Use these duration
groups: under 3 minutes, 3-5, 5-7.5, 7.5-10, 10-15, 15-20, and 20+ minutes.
Intervals include the lower bound and exclude the upper bound. Keep empty groups
with zero games/share and an unavailable win rate. Share is the group's count
divided by all included games; win rate is wins divided by games in that group.

Use match-record `duration_sec`, excluding queues and account switches. Exclude
unknown/incomplete results and missing, nonpositive, or invalid durations.
For this distribution, exclude bot-concedes of every cause, including
duration-limit concedes, and, conservatively, games with unresolved bot concede
attempts or incident bundles. Report exclusions separately for confirmed stall
concedes, confirmed duration-limit concedes, and unresolved attempts/incidents.
Correlate incidents to games rather than counting repeated attempts as games.
Opponent concessions remain included. When history is partial, label the table
as best effort and note that unrecognised bot-concedes may remain. Do not treat
these conservative exclusions as additional confirmed concedes in session stats.

Report average, median, minimum, and maximum duration for the same included games.
If none qualify, state that the distribution and duration statistics are
unavailable.

Include the deck played for each game using `match_details`, and summarize the
session's deck counts. `logged_selection` means the bot logged a completed deck
selection before that game; it does not independently verify the actual deck
contents. `inferred` means a prior selection was carried into another game on
the same uninterrupted account visit, Historic selection memory was used, or a
reliably owned saved hand uniquely matched a local Starter deck list. Saved-hand
inference requires logged Starter event context and at least three distinct
known nonland cards, all known hand cards fitting exactly one local deck list.
`unknown` means the logs do not establish a named deck. Link each available
`deck_evidence` path/line. Never treat quest colors, a requested template, or a
deck-pick click alone as proof of the deck played. Account switches and bot
restarts clear carried selections. Do not infer a deck name from the unnamed
first-deck fallback. Card/screenshot cross-checks may corroborate a selection.
Treat saved-hand matches as inference, never confirmation of full deck contents.

The per-game `audit-matches.csv` stores session/match index, account, UTC game
times, result, deck label, evidence status, and source log path/line. Repeat
audits upsert each game; missing logs preserve earlier known deck evidence when
the game identity is unchanged. New evidence of an unconfirmed pick supersedes
old attribution. Check and report errors in both `csv` and `matches_csv`.
`--no-csv` disables both writes; `--matches-csv <path>` overrides the game CSV.

The CSV stores one row per session, with UTC date/timestamps, completed-match
counts, wins/losses, win rate, and stall-only confirmed concede counts/attempts
and concede rate. The fields `confirmed_concedes`, `concede_attempts`, and
`concede_rate` exclude duration-limit concedes. An unresolved duration-limit
outcome alone does not make the stall-only rate unavailable.
Rates are fractions from 0 to 1; use completed matches as the denominator.
Running sessions are provisional. Missing or partial history and unresolved
concede evidence leave the rate blank, never an assumed zero. Repeat audits
update the same session instead of appending duplicates. A previously available
stall-only concede rate is preserved after log rotation when the denominator is
unchanged. Recompute previously mixed-cause CSV values from retained evidence;
if causes cannot be separated, leave the rate blank rather than preserving a
mixed-cause value.
Historical start/end timestamps fall back to first/last recorded match times.
Do not substitute screenshot completion outcomes for confirmed concede evidence.

Inspect the summarizer's `optional_target_recovery` events from ordinary logs.
Report attempts separately from acknowledged prompt advancement; neither proves
that the Equipment was exiled or that the spell resolved.

Inspect `ability_activation` even for games with no concede. Report selected
sources, confirmation-button clicks, acknowledged activations, timeouts, and
Cancel recovery separately. `ABILITY_ACTIVATION_CLICKED` means a recognized
Activate Ability control received a click; only `ABILITY_ACTIVATION_ACKNOWLEDGED`
means a fresh local payment/target request or new source ability was observed.
Neither proves payment completed or the ability resolved. Link event lines and
any retained `runtime/debug/ability-activation-*` bundles. Timeouts are also
counted as `ability_activation_timeout` in match records, independent of concede
or match result. A timeout can be followed by a late acknowledgment; do not count
these observations as separate failed abilities. Log event counts cover retained
history only; report rotated evidence as missing, never as zero successes.

For the temporary overnight feature soak, also run
`.venv/Scripts/python.exe tools/temporary_feature_soak_audit.py` on Windows
(or `.venv/bin/python tools/temporary_feature_soak_audit.py` elsewhere). It is
read-only and reports retained log evidence for long-game concede attempts,
Reconnect clicks, ability activation, the 750-gold quest policy, win-deck
selection, and shutdown scheduling. Link its example lines in the audit. Treat
zero as "not observed in retained history," not as a failure or proof the
feature never ran. It cannot verify static UI appearance or deck win rate.

The checker also covers preferred deck decisions, confirmed deck submissions,
Starter deck reuse, mulligan keep/tuck events and recovery, confirmed versus
unconfirmed end-screen dismissal, reward-click attempts, Free Forge approvals
and safety exceptions, battlefield fallback selection, sacrifice-option clicks,
watchdog/concede incidents, account switching, and notification delivery errors.
These are event counts, not feature pass/fail results or unique match counts.
Corroborate behavior against snapshots, GRE evidence, and the normal summary.
Use the checker's `manual_checks` for features ordinary logs cannot establish.

Report `changed_feature_checks` for the pending blocker recovery, pile choice,
and stack ordering cancellation changes. Keep attempts separate from outcomes;
review blocker snapshots and GRE acknowledgments, pile closure evidence, and
click records around ordering cancellation. Shared group recovery errors may
belong to Scry/Surveil. Include rope/stuck-action counts as context, not proof
that these changes caused them. Unexercised paths remain untested.

Before an overnight run, the user or setup task may run the checker with
`--arm`. This saves `runtime/analysis/temporary-feature-soak.json` with the start
epoch, branch, commit, and working-tree status; it does not launch the bot.
Normal checker invocation automatically reads that marker and includes all
sessions after its boundary. Report the saved revision and actual coverage;
restart the app after code changes to load them. Do not re-arm during an audit.
The marker survives restarts but does not preserve rotated logs. A first retained
timestamp after the marker can mean delayed startup or missing history; do not
claim complete coverage from it. `coverage: unavailable` (exit 2) means no dated
history in the selected window, even if older logs exist. `--since <Unix epoch>`
or `--all-history` explicitly overrides the marker when auditing another run.
Do not use an old marker to claim that a different selected session was tested.

### Permanent concede incidents

Inspect the summary's `concede_incidents` and matching `runtime/concedes/` bundles
for stall, inactivity-timer, and game-duration-limit concede sequences. Classify
the cause from the incident reason and matching log events before aggregating;
only stall concedes enter the Confirmed bot concedes statistics. Images are masked,
window-only JPEGs; `incident.json` remains useful when capture failed. Only the
newest 30 incidents are retained. Correlate session/match IDs, attempts, capture
status and final outcome with match records and game-result evidence. A
`match_completion_observed` outcome alone does not establish a successful bot
concede. Inspect `CONCEDE_INCIDENT_CREATED`, `CONCEDE_INCIDENT_FINISHED` and
`CONCEDE_INCIDENT_FAILED` log events if a bundle is unavailable.
