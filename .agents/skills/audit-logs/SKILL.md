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
counts, wins/losses, win rate, confirmed concede counts/attempts and concede rate.
Rates are fractions from 0 to 1; use completed matches as the denominator.
Running sessions are provisional. Missing or partial history and unresolved
concede evidence leave the rate blank, never an assumed zero. Repeat audits
update the same session instead of appending duplicates. A previously available
concede rate is preserved after log rotation when the denominator is unchanged.
Historical start/end timestamps fall back to first/last recorded match times.
Do not substitute screenshot completion outcomes for confirmed concede evidence.

Inspect the summarizer's `optional_target_recovery` events from ordinary logs.
Report attempts separately from acknowledged prompt advancement; neither proves
that the Equipment was exiled or that the spell resolved.

### Permanent concede incidents

Inspect the summary's `concede_incidents` and matching `runtime/concedes/` bundles
for both stall and inactivity-timer concede sequences. Images are masked,
window-only JPEGs; `incident.json` remains useful when capture failed. Only the
newest 30 incidents are retained. Correlate session/match IDs, attempts, capture
status and final outcome with match records and game-result evidence. A
`match_completion_observed` outcome alone does not establish a successful bot
concede. Inspect `CONCEDE_INCIDENT_CREATED`, `CONCEDE_INCIDENT_FINISHED` and
`CONCEDE_INCIDENT_FAILED` log events if a bundle is unavailable.
