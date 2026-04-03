# AGENTS.md

## Access Restrictions

The Codex agent must **not open, read, or analyze** the following files:

- credentials.txt

This file contains sensitive information and is explicitly excluded from access.

## Documentation

After every change to code or UI, the README.md must be updated to reflect the current state.

## Logs

When the user asks to compare `bot.log` and `Player.log`, the default location for `Player.log` is:
`C:/Users/giaco/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`

## User Corrections

If the user states something factually incorrect, the agent should correct it clearly and directly, with brief reasoning when needed.

## Session Learnings

Whenever the agent learns something relevant during a session (e.g., root cause, stable workaround, configuration caveat), it must append a short entry to `learning.md`.

## Incident Notes

Whenever a real supervisor incident occurs, the agent must append a short entry to `incidents.md` describing:

- timestamp / incident folder
- trigger reason
- whether supervisor recovery / Codex notify succeeded
- the concrete gameplay/control-flow root cause
- the fix that was applied (or the next targeted debug artifact if still unresolved)

## Supervisor Debug Workflow

If the user asks to run the "supervisor workflow", the Codex agent should:

1. Start the bot through the supervisor in stop-after-incident mode, not as an endless auto-requeue loop.
2. Let the supervisor recover to `HOME`, send the Codex `stuck` notification, and then stop itself.
3. Treat the supervisor behavior itself as correct unless there is direct evidence that recovery/notification failed; the default response after `stuck` is to debug and fix the bot logic that got stuck.
4. Inspect the newest incident bundle, compare `bot.log` and `Player.log`, and identify the concrete root cause in gameplay/control flow.
5. If the same failure repeats or the available evidence is insufficient, add targeted debug artifacts in code at the failing step (for example extra screenshots, state dumps, click-context bundles, or post-action verification images) before rerunning.
6. Record the analyzed incident in `incidents.md` before rerunning.
7. After applying bot-logic fixes, restart the supervisor workflow again only when the previous incident has been analyzed.
