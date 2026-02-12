# AGENTS.md

## Access Restrictions

The Codex agent must **not open, read, or analyze** the following files:

- credentials.txt

This file contains sensitive information and is explicitly excluded from access.

## Documentation

After every change to code or UI, the README.md must be updated to reflect the current state.

## Logs

When the user asks to compare `bot.log` and `Player.log`, the default location for `Player.log` is:
`/home/barrylim/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`

## User Corrections

If the user states something factually incorrect, the agent should correct it clearly and directly, with brief reasoning when needed.
