# Burning Lotus Bot
<img width="429" height="823" alt="githubscreen" src="https://github.com/user-attachments/assets/ac3ec57b-45de-4a22-aebe-0bcb3db90ae0" />

Free, open-source Magic the Gathering Arena (MTGA) bot for automating daily quests, daily wins, and account switching. Burning Lotus runs on Windows, macOS, and Linux without code injection or subscriptions. Built in Python with a graphical UI, no command-line knowledge required.

Feel free to inspect the code, request a feature, or report a bug via GitHub Issues or open a pull request. Discord: https://discord.gg/49j93Jz6v

## Requirements

- **OS**: Windows 10/11, macOS 12+, or Linux (X11 or Wayland; tested on Debian and CachyOS)
- **Python**: 3.10+
- **MTG Arena**: installed and running
  - Windows: Steam or Wizards installer
  - macOS: Crossover or compatible Wine layer
  - Linux: Wine/Proton via Steam or Lutris

Python dependencies are installed automatically by the launcher scripts:

| Package | Purpose |
|---|---|
| `pyautogui` | Mouse/keyboard input (default backend) |
| `pynput` | Global input listener (hotkeys, macro recording) |
| `mss` | Fast screen capture |
| `opencv-python` | Template matching |
| `Pillow` | UI rendering |
| `numpy` | Numerical arrays (shared data format between mss and OpenCV) |

### Required MTGA settings (all platforms)

- `Options -> View Account -> Detailed Logs (Plugin Support)`: **ON** *(required — the bot reads `Player.log` as its primary state source)*
- `Options -> Video -> Language`: **English**
- `Options -> Video -> Display Mode`: **Windowed**
- `Options -> Video -> Resolution`: **any exact 16:9 windowed size**
- OS display scaling: **100%**

## Quick Start

Each platform has its own launcher script — named after the platform — that creates a virtual environment, installs dependencies, and starts the UI:

| Platform | Launcher |
|---|---|
| Windows | `start_windows.bat` |
| macOS | `start_macos.command` |
| Linux | `start_linux.sh` |

### Windows

1. Install Python 3.10+ from python.org (tick "Add python.exe to PATH").
2. Double-click `start_windows.bat`.

### macOS

1. Install Python 3.13 (recommended):
   - python.org installer, **or**
   - `brew install python@3.13 python-tk@3.13`
2. Optional preflight check: `./doctor_macos.command`
3. Double-click `start_macos.command` (or run `./start_macos.command` in Terminal).
4. Grant permissions to the Terminal app **and** the Python binary inside `.venv-macos`:
   - `System Settings -> Privacy & Security -> Accessibility`
   - `System Settings -> Privacy & Security -> Screen Recording`

### Linux

1. Install Python 3.10+ and OS-level packages:

   | Purpose | Arch / CachyOS | Debian / Ubuntu | Fedora | openSUSE |
   |---|---|---|---|---|
   | tkinter UI | `tk` | `python3-tk` | `python3-tkinter` | `python3-tk` |
   | MTGA window detection | `xorg-xwininfo` | `x11-utils` | `xorg-x11-utils` | `xwininfo` |
   | Screenshot (KDE) | `spectacle` | `kde-spectacle` | `spectacle` | `spectacle` |
   | Screenshot (GNOME) | `gnome-screenshot` | `gnome-screenshot` | `gnome-screenshot` | `gnome-screenshot` |
   | Screenshot (wlroots/Sway/Hyprland) | `grim` | `grim` | `grim` | `grim` |
   | Screenshot (X11 fallback) | `scrot` | `scrot` | `scrot` | `scrot` |

   The launcher warns if any required package is missing and prints the exact install command for your distro.

2. Run `./start_linux.sh`.

3. MTGA must run through Wine/Proton (Steam or Lutris). Under Wayland it goes through XWayland automatically.

### Manual start (any platform)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
.venv/bin/python ui.py                       # Windows: .venv\Scripts\python ui.py
```

### Updates

The bot checks GitHub for a newer version on startup and, when one is found, a dialog offers to install it and restarts the bot automatically. There are two channels, picked automatically:

- **Git installs** (started from a `git clone` of this repository): the check works off git commit history (not the version number below), only fetches when the remote is actually ahead of local, and installs via a fast-forward `git pull`. If you have local, uncommitted changes in the bot folder, the update is aborted rather than overwriting them, and the dialog lists which file(s) are affected.
- **ZIP / website installs** (no `.git` folder): the bot compares its local `version.py` against the one on the `main` branch at GitHub. If `main` has a newer version, it downloads the branch archive and overlays it onto the install folder. The archive only contains tracked source files, so user data (`runtime/`, `Accounts/`, `.venv/`, `credentials.txt`, …) is never touched.

Either check is silently skipped when there's no network access. Dependencies from `requirements.txt` are reinstalled automatically if they changed as part of the update.

The app's current version (`1.1.3`, sourced from `version.py`) is shown in **Settings**, above the Manage Accounts button.

## Configuration

### Input backend

The bot auto-selects the best available input backend per platform:

| Platform | Default | Fallback |
|---|---|---|
| Linux | `ydotool` (if installed) | `pynput` |
| macOS | `pyautogui` | `pynput` |
| Windows | `pyautogui` | `pynput` |

Override via environment variable: `MTGA_BOT_INPUT_BACKEND=auto|pyautogui|pynput|ydotool`

`ydotool` requires `ydotoold` daemon to be running and is recommended for Linux Wayland sessions.

### Calibration (optional)

The bot locates the MTGA window automatically on startup — no manual calibration needed in most cases.

Use **Settings → Calibrate** only if the bot repeatedly fails to click the right spots. Calibration captures 1920x1080-relative coordinates and maps them to the actual window position at runtime.

## Features

### Account Switching

Accounts are stored as folders under `Accounts/` (gitignored by default):

```
Accounts/
  MyAccount/
    credentials.json   →   { "MyAccount": { "email": "...", "pw": "...", "screen_name": "MyName#12345" } }
  AltAccount/
    credentials.json
```

Manage accounts via **Settings → Manage Accounts**. Set a switch timer and play order. When the timer expires and the bot is at a safe screen, it logs out, switches account, and resumes.

Each account has exactly one name: its **Arena Name**, the one Arena shows top-left (`Name#12345`; the digits are optional). It is not a label you choose — the Arena log identifies an account *only* by that name, never by email, so rotation order, per-account gold tracking and the Current/Next display all key off it. Arena Names must be unique; a row without one is rejected on save. Older versions had a second, free-text `Name` field beside it. Manage Accounts now shows such a row under its Arena Name, and pressing **Save Accounts** renames the account for good — the play order and a manual pin are carried across the rename, and the account keeps its folder. A row that never had an Arena Name shows the old label instead, which may not be the Arena one; Manage Accounts points those out when you open it.

#### Current / Next account

While account switching is enabled, the main window shows **Current ACC** (playing now) and **Next ACC** (the account the next switch will log into). Click **Current ACC** to tell the bot which account is open in Arena right now. Use it when you changed account by hand: Arena writes the login only once, so after a while the bot can no longer read it from the log, and a manual pick sets rotation straight again. The pin is dropped automatically on the next switch the bot performs itself, and when the pinned account is renamed or deleted.

A pin is also dropped when Arena logs a login for a *different* account after the pin was set — you changed account by hand again, so the log is newer than your pick. This only happens when that login can be matched to one of your configured accounts by its Arena Name; an unrecognised login never overrides your choice. A pin restored from the previous session additionally yields to a login that is *older* than it, since a restored pin says nothing about who is logged in now — so give every account its Arena Name if you want the bot to correct a stale pin on its own.

**Change Queue** and **Account Switch** in the main menu replace the older click-on-text toggles. Queue mode is locked while the bot runs (changing it mid-run would desync navigation).

Account switching can be toggled on/off live from the main window without restarting the bot, and runs in one of two modes:
- **Time**: switch every N minutes (configurable).
- **Quests**: switch once the configured number of daily quests (measured *absolutely* — completed by the bot or by hand, whichever comes first) and/or daily wins seen this session are reached on that account. Once every configured account has completed a round, the bot stops itself instead of cycling back to the first account, and says so in a pop-up so a normal finish isn't mistaken for a freeze. A round covers the accounts in your play order — not every folder under `Accounts/` — and an account whose switch failed is not counted as finished.

Rotation always continues from the account actually logged in, so it never wastes a switch logging back into the same account, and the order stays intact even after a failed switch.

A switch that becomes due while a match is running or while matchmaking is in progress is **deferred**, not skipped: it is carried out on the first moment between matches. This also covers the end-of-round stop, which previously could end the session in the middle of a game the bot had just started.

If the logout never reaches the login screen three times in a row — usually a mis-calibrated **Log Out** button or a changed Arena layout — the bot stops attempting to switch for the rest of the session and keeps playing the current account instead of looping through failed logouts. It says so once in `bot.log`, pointing at the calibration.

> **Known issue:** in quests mode, if a switch becomes due while the bot is on the Starter Deck Duel event page (`game_mode = starter`), the logout sequence can fail to open the Options menu and misclick into the event page instead of logging out. Time-mode switching from Home is unaffected. A fix (navigate to Home before attempting logout) is planned as a follow-up.

### Gold Tracking

The bot reads each account's real Gold balance from MTGA's own logs and tracks the delta (current balance minus the balance first seen this session) per account — no estimate. Open **Current Session** from the main window to see gold farmed per account (labeled with your account names), alongside games/wins for the session.

Accounts the bot switches into get their baseline read on arrival at Home, before they play, so their earnings are complete.

A balance in MTGA's log does not say which account it belongs to, so only balances written after the session started — and after the most recent account switch — are used. Anything older is ignored rather than guessed at.

> **Known limitation:** the **first** account of a session can show `0` farmed gold. MTGA only reports that account's balance after its first match, at which point the win reward is already included, so that match's earnings can't be measured.

After a switch the bot takes the account's identity from the credentials it just typed — it knows which account it logged in. It used to read that back out of the Arena log instead, but the log has no login event to read: the only thing there is the match-server handshake, written when a match connects. Right after a switch the newest one still belongs to the account just left, so the old name was re-adopted and stuck, and every balance read afterwards — correct in itself, but nameless in the log — was booked against the wrong account. One row could grow implausibly large while its partner sat at `0`. A handshake written *after* the switch still wins, so changing account in Arena by hand is still picked up. A `GOLD_BALANCE_BELOW_BASELINE` line in the log flags a balance below its baseline, which is what a misattribution looks like from the other side.

### Quest-Based Deck Selection

On **Start**, and after each account switch, the bot picks a deck based on active quests. Quests are read fresh at start: everything already in Arena's log is ignored and the bot briefly returns to Home to make Arena log the current list (up to 30 seconds; it falls back to the newest entry it has if none arrives). Without this, a quest you re-rolled or finished by hand before pressing Start was read as still active, and the first matches were played on the wrong deck.

Place deck screenshot images in the account folder named by color letters:

- `RG.png`, `WU.png`, `B.png`, `R.png` etc. — matched to quest colors
- `C.png` — used for creature-type quests
- Random fallback if no quest matches

In Starter Deck Duel the deck is additionally re-checked before **every** queue, so when a quest completes mid-session the bot swaps to the colors of the next one instead of finishing the session on the deck it started with. The colors are resolved *after* that check (previously the just-completed quest's deck was replayed for one more match), and the bot verifies the deck chooser actually closed after submitting — a missed swap is now reported in `bot.log` instead of silently farming the wrong colors.

When MTGA runs a lot of events at once, the Events grid grows and the **Starter Deck Duel** banner can end up below the visible area, where the bot cannot click it. If the banner is not found, the bot clicks **In Progress** in the right-hand event menu — that filters the grid down to the events it is actually playing, bringing the banner back on screen — and looks again. If the banner is still missing afterwards the **All** view is restored, so a filter that hides the event (one you have never entered is not "in progress") does not stick for the rest of the session.

> Fixed in this version: the bot pressed **All** instead of **In Progress**, on every single queue cycle. The reference image for that menu entry had been captured while its row was *selected*, so what it really matched was "the highlighted row" — whichever one that was. It therefore clicked the currently selected category, clearing any filter, and reported success, which also suppressed the retry above. The two entries are now clicked by position, and the bot reads back which row ended up highlighted so a misaimed click is reported in `bot.log` instead of passing silently.

> Fixed in this version: the reward-popup handler mistook the event page's orange **Play** button for a **Claim** button (same corner, same shape). It pressed Play, which started the next match immediately and skipped the deck check — so the bot kept replaying its first deck even after the active quest changed colors. The handler now verifies it is not on the event page before clicking.

### Casting Logic

The bot maximizes mana usage each turn:
- Prefers the highest-value spell(s) that spend the most mana
- Respects color requirements and discounted costs
- Type priority when CMC is tied: creature → instant → sorcery → enchantment
- Supports Convoke (untapped creatures as mana sources)
- Kicker: the "Cast with Kicker?" chooser is answered automatically (always the plain, non-kicked version for now) so the bot never stalls on it
- The mid-screen "Choose One" overlay — kicker plates, a modal spell's or creature's mode plates (e.g. Apothecary Stomper's enter-the-battlefield choice), and the "sacrifice or pay" buttons — is treated as blocking: while it is up, no other move is dispatched, and the chosen plate is clicked again if the game has not moved on. Previously a single click that failed to register left the dialog open and the bot immediately swept the hand row for its next play behind the overlay, which could not be reached — the match then idled until it was conceded. "Has the game moved on?" is answered by the newest `gameStateId` seen on **any** GRE message, including the timer messages that never reach the merged game state — reading only the merged state made an already-answered dialog look open for another two seconds, and the retry then clicked onto the battlefield behind it
- Client-side "Are You Sure?" confirmations are handled reactively after a failed cast attempt, avoiding speculative screen probes during normal casts
- Decision recovery is guarded against open payment/selection prompts and resumes safely after modal, stack, or scry interruptions
- Ties between otherwise equal casting plans favor lifegain-payoff creatures, so decks built around gaining life develop toward their game plan sooner
- Removal only ever targets creatures still on the battlefield and never redirects a harmful spell at your own board when no valid enemy target exists
- When a card lets you choose which creature to return from the graveyard or exile, the bot ranks candidates by their role in the deck's strategy instead of taking whatever the game offers first
- Modal card windows are handled: the library browser from "search your library for …" (e.g. Circuitous Route) and the card-ordering window that follows it. The bot reads how many cards it may take and which ones are legal straight from the game's own request — Magic Arena pre-filters the browser to valid choices, so it takes the required number and confirms. Everything behind such a window is unreachable, so while one is open no other move is attempted; previously the bot hunted the hand row for a card it could not click and idled until the game was conceded. The result is verified against the client's own response, and a partial answer is logged as an error rather than passing silently.

### Stopping the bot

Scroll **Mouse Wheel down** at any time to stop the bot immediately.

## Architecture

The codebase is split into clearly separated layers:

```
ui.py / run_bot.py          ← Entry points (UI or CLI)
        │
        ▼
    Game.py                 ← Session manager: connects Controller and AI,
                              handles match lifecycle (start → end → restart)
        │
   ┌────┴────┐
   ▼         ▼
Controller   DummyAI        ← AI decides what to play (generate_move / generate_keep)
   │
   ├── LogReader            ← Reads Player.log continuously, parses GRE messages
   ├── state_machine        ← Tracks bot state: HOME / IN_GAME / PLAY_MENU / ...
   ├── actions              ← Declarative action specs (navigate, click, verify)
   ├── vision               ← Screen capture (mss) + template matching (OpenCV)
   │    └── window_locator  ← Finds the MTGA window (Win32 / xwininfo / anchor search)
   └── input_controller     ← Sends mouse/keyboard input (pyautogui / pynput / ydotool)
```

**Key design principle:** `Player.log` is the primary state source — the bot reads what MTGA reports rather than inferring state from screenshots. Vision is used only to verify that clicks landed and to locate buttons when coordinates are uncertain.

**Card data** (`AI/Utilities/CardInfo.py`) is loaded from a local export of MTGA's own card database and delta-synced with the Scryfall API for missing entries. Cards Scryfall does not have at all — Arena-only tokens, Alchemy rebalances — are remembered as such and not requested again for 30 days, and the whole startup sync is capped at 10 seconds, so an unreachable Scryfall cannot stall the start. A card that a later Arena update ships locally is dropped from the retry list without a request.

Both `Controller` and `AI` follow an interface pattern (`ControllerInterface.py` / `AIInterface.py`) that decouples `Game.py` from the concrete implementations — making it straightforward to swap in a different AI or add a non-MTGA controller.

## Logs & Troubleshooting

| File | Location | Purpose |
|---|---|---|
| `bot.log` | `runtime/logs/bot.log` | Main bot debug log |
| `snapshots.jsonl` / `board.txt` | `runtime/debug/matches/<utc>_<matchId>/` | Per-decision game-state snapshots (see below) |
| `clicks.jsonl` | `runtime/debug/clicks.jsonl` | Per-click verification log (see below) |
| `Player.log` | Auto-detected per OS (see below) | MTGA game log — primary state source |

`Player.log` default paths:
- **Windows**: `C:/Users/<YourUser>/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`
- **macOS**: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
- **Linux/Proton**: `~/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`

If auto-detection fails, the UI prompts for a manual file selection on startup.

When something goes wrong the bot saves debug bundles under `runtime/debug/<timestamp>/` containing screenshots, the log tail, and a state dump. The entire `runtime/` tree is gitignored.

### Decision snapshots

For post-mortem debugging of *play* decisions (why did the bot pass, attack, or pick that target?), the bot records one structured snapshot per decision under `runtime/debug/matches/<utc>_<matchId>/`:

- `snapshots.jsonl` — one JSON record per decision: turn/phase, both life totals, your and the opponent's permanents (name + power/toughness + tapped/attacking), your hand, the stack, the available actions, and the move the bot chose (`"exception"` if the decision crashed). Covered decision points include the main play decision plus mulligans, target selection, declare-blockers, pay-costs, casting-time (kicker/modal) choices, scry/surveil, and modal "choose one" prompts (each tagged with a `decision_kind`).
- `board.txt` — the same records rendered human-readable, one block per decision.
- `match.json` — per-match header/footer with the result and any card IDs that couldn't be resolved to names offline.

Card names are resolved from the local card database only (no network calls on the decision path), so this never delays in-game actions. Recording is on by default and writes only per decision (a few dozen small records per match); disable it with `MTGA_DEBUG_SNAPSHOTS=0`, or add the full raw game-state dump to each record with `MTGA_DEBUG_FULL_STATE=1`. Old match directories are pruned automatically (newest 30 kept).

### Click verification log

For debugging the *visual* layer (bot clicked the wrong screen position, or the right position at the wrong time), every mouse click is logged as one line in `runtime/debug/clicks.jsonl`: the purpose, the coordinates, the arena-mapping `source`, how old the arena-window fix was (`region_age_sec`), a `risky` flag (set when the arena window was lost and the click fell back to a blind absolute desktop coordinate), and a `decision_seq` that ties the click back to the game-state snapshot that caused it. The same context is appended to the `[CLICK]` lines in `bot.log`. On by default; disable with `MTGA_DEBUG_CLICKS=0`. The file rotates at ~5 MB (one `.1` backup kept).

The per-failure debug bundles under `runtime/debug/` (screenshots + state for mulligan/hand-select/navigation/etc.) are now capped automatically (oldest pruned, newest 60 kept) and their full-screen captures are saved as JPEG, so the debug folder no longer grows unbounded.

## See also on
[elitepvpers](https://www.elitepvpers.com/)
