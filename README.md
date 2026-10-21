# Burning Lotus Bot

Startup reward recovery requires Claim text, preventing orange Play and Submit Deck buttons from being mistaken for rewards.
Pending startup/login rerolls are retried before an account switch, including accounts that already met their farming target.
Account login and repeated Home transitions preserve the queue worker; leaving a match still cancels that match's pending input.
Post-match resets preserve delayed queueing and account switching. Cancelled input transactions release their reservation without unlocking newer work.
Preferred decks also apply after the configured quest target is met. Starter deck reuse remains tied to the current account and a verified event page. Repeated mulligans preserve the configured match deadline.
<img width="429" height="823" alt="githubscreen" src="https://github.com/user-attachments/assets/ac3ec57b-45de-4a22-aebe-0bcb3db90ae0" />

Free, open-source Magic the Gathering Arena (MTGA) bot for automating daily quests, daily wins, and account switching. Burning Lotus runs on Windows, macOS, and Linux without code injection or subscriptions. Built in Python with a graphical UI, no command-line knowledge required.

Feel free to inspect the code, request a feature, or report a bug via GitHub Issues or open a pull request. Discord: https://discord.gg/5v6V6HvCRn

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
| `pynput` | Global input listener (hotkeys, calibration) |
| `mss` | Fast screen capture |
| `opencv-python` | Template matching |
| `Pillow` | UI rendering |
| `numpy` | Numerical arrays (shared data format between mss and OpenCV) |

### Required MTGA settings (all platforms)

- `Options -> View Account -> Detailed Logs (Plugin Support)`: **ON** *(required — the bot reads `Player.log` as its primary state source)*
- `Options -> Video -> Language`: **English**
- `Options -> Video -> Display Mode`: **Windowed**
- `Options -> Video -> Resolution`: **any exact 16:9 windowed size**
- OS display scaling: **any (the bot converts coordinates for scaled displays)**

Keep the entire MTGA window visible while the bot is running. If the game is moved or resized to an unsupported/off-screen geometry mid-match, the bot now stops visual input instead of reusing obsolete coordinates and sweeping empty space for cards; restore a visible 16:9 window and let the next game-state update retry.

Seasonal rewards and other overlays showing **Claim Rewards** or **Click to Continue** are recovered after five seconds without meaningful progress, including during startup and account login. Recovery checks the text even if the log still says matchmaking or gameplay, claims rewards first, and searches again before another click. When an overlay hides the normal navigation anchors, recovery searches within the freshly detected Arena window. A verified disconnect dialog takes priority over rewards behind it: after 15 seconds without progress, the bot clicks **Reconnect** and checks the screen again before any later action. Continue text uses a 0.70 matching threshold to tolerate its changing background; recovery respects Stop and existing input ownership.

Login notifications are also recognised from Player.log rather than the screen alone. MTGA wraps them in `BEGIN home page notification flow` / `END home page notification flow`; a BEGIN with no END, scene change or reload after it means one is still on screen. When that is the case and no text template matches, recovery (after the same five-second persistence wait) clicks the bottom-right button position, where all of them put their button: **Claim**, the **Continue** of "New Season Placement", and a set promo's **Get Started!**. The "[Click to Continue]" pages also accept that click. This was added after a season rollover stranded the bot on "New Season Placement": its "[Click to Continue]" sits on a darker backdrop that scored 0.64 against the text template, below the 0.70 threshold. Verified live on 2026-10-10 across an account switch: Claim, Placement and the Reality Fracture promo were cleared in turn, MTGA logged the END, and the bot went on to its quest reroll on Home. In that run **Get Started!** closed the notification flow without opening the Store; if it ever navigates instead, the resulting scene change ends the flow and normal Home navigation takes over.

Reward recovery runs before quest reroll and Home navigation. While a verified popup is waiting to be handled, reroll and deck navigation pause. Text checks include buttons that say only **Claim**. If text recognition misses, recovery waits for a later observation; it never falls back to the generic orange Claim-button shape. After three consecutive unrecognized-screen checks, Starter navigation uses a Continue-position click, followed by a centre click only if a fresh check still reports an unrecognized screen in the same Arena window. A recognized page, a popup, changed or invalid geometry, or a failed check prevents the centre click. Claim, Claim Rewards, Click to Continue and Reconnect text take priority over generic clicks. The fallback rechecks that matchmaking/gameplay has not started before sending input, rejects unsupported or off-screen window geometry, and sends no clicks if window focusing fails on Windows.

During cost payment, the bot looks for **Auto Pay** if Submit is unavailable. If another selection is still using the controller, payment retries for up to 16 seconds while the same payment prompt remains active. Normal play stays paused until the game acknowledges advancement after payment.

For Forge-selected battlefield abilities, the bot clicks the visible **Activate Ability** control when Arena requires confirmation after selecting the permanent. It waits for a fresh payment/target request or a new ability on the stack, rather than treating the click as success. An unacknowledged activation saves an `ability-activation-*` debug bundle and attempts a visually verified Cancel before suppressing that permanent's ability for the rest of the turn. Overnight audits distinguish confirmation clicks from acknowledged activations, timeouts, and Cancel recovery, including games that never concede; timeouts are retained in per-match watchdog counts. This recovery still needs supervised in-game validation.

Quest reroll searches for the 500 Gold label in one continuous daily-quest area (x=50–910, y=750–960 in the normalized 1920×1080 Arena window). This avoids clipping labels at internal search boundaries while leaving win rewards farther to the right outside the search. The existing matching threshold and brightness checks still apply.

Scry and Surveil keep normal gameplay paused until their central overlay is confirmed closed in two settled observations. Done is located again for each of up to three attempts, with input ownership held across recognition and the press. Surveil's fixed-position fallback requires its visible heading. Missing captures never count as completion. Recovery requires successful window focusing on Windows; Linux and macOS verify the visible overlay without requiring the Windows focus helper. Recovery attempts do not reset the stall countdown; auto-concede starts at 30 seconds without confirmed progress.

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

- **Git installs** (started from a `git clone` of this repository): the check works off git commit history (not the version number below), only fetches when the remote is actually ahead of local, and installs via a fast-forward `git pull`. If you have local, uncommitted changes to *tracked* files in the bot folder, the update is aborted rather than overwriting them, and the dialog lists which file(s) are affected. Untracked files (your venv, notes, …) never block an update.
- **ZIP / website installs** (no `.git` folder): the bot compares its local `version.py` against the one on the `main` branch at GitHub. If `main` has a newer version, it downloads the branch archive and overlays it onto the install folder. The archive only contains tracked source files, so user data (`runtime/`, `Accounts/`, `.venv/`, `.venv-macos/`, …) is never touched. Executable bits on the launcher scripts are preserved, so `start_macos.command` / `start_linux.sh` stay double-clickable after an update.

Either check is skipped when there's no network access. Every check writes its outcome (up to date, update available, or why it was skipped) to `bot.log`, so a missing update dialog can be diagnosed afterwards. Dependencies from `requirements.txt` are reinstalled automatically if they changed as part of the update.

The app's current version (`1.9.1`, sourced from `version.py`) is shown in **Settings**, above the Manage Accounts button.

### Version 1.9.1

- Fixed: Arena detection on the primary monitor when another display is positioned left of or above it.
- Fixed: Windows startup now attempts to bring Arena forward before checking its screen.
- New: Clear setup messages for unsupported monitor placement or unsafe mixed display scaling.
- Fixed: Recovery avoids old click positions after Arena moves to an unsupported display.

### Version 1.9.0

- New: smarter **mulligans**. The bot checks lands, early plays and colors in the opening hand and, after a mulligan, puts the weakest cards on the bottom.
- New: **selective attacks** (on by default). The bot only attacks with creatures that trade well or push for lethal instead of sending everything every turn.
- Smarter play: counterspells go after the biggest threat, removal prefers creatures that hit hard, burn goes to the face when it wins the game, and landfall creatures come down before the land drop.
- New: **Enhancements & Alerts** window with optional Discord/Telegram alerts for wins, losses, quests and account switches, human-like mouse movement, a low-power overnight mode and 16:9 window snapping.
- New: a match that runs longer than 20 minutes is conceded so the bot never hangs in one game.
- Fixed: closing the bot window could freeze the Windows taskbar.

### Version 1.8.0

- New: a **Session Info** panel over the top-left corner of the Arena window shows what the bot is doing: turn, phase and last move during a match, queue, account switch, current and next account, games and wins, gold farmed, and the daily quests. The bot never sees or clicks it (see *Overlay over the Arena window*). It stays up while the bot runs, also with the bot's window minimized, and goes away when the bot stops.
- New: **Settings → Game Overlay** sets how see-through the panel's background is; the text always stays readable. It replaces **Record Action**, which is gone together with its recording/replay (account switching now always uses the built-in logout).
- The main window is tidier: below the buttons only the bot's status, in the title's gold lettering, with a play or stop symbol. **Queue** and **Account Switch** show their setting in the button; right-click **Account Switch** to tell the bot which account is open in Arena.
- New: everything the bot writes to `bot.log` also scrolls by live in the terminal window it was started from, including what the Forge engine decided.
- Faster start: reading the daily quests at Start took about 50 seconds of Home clicks and waiting when Arena was already on Home; it now takes a few seconds (see *Faster start: reading the quests*).
- Fixed: the progress bar at Start kept saying *Loading card data* while the bot was already reading quests and clicking through Arena; it now names each step.

### Version 1.7.0

- New, opt-in: **Forge Engine (experimental)** under Settings → Bot Behavior. With it ticked, the open-source Forge rules engine and AI choose lands, spells, abilities, attacks, blocks, targets and scry, and the bot carries them out; where Forge has no answer the bot decides as before. A one-time download of about 85 MB (Windows). See *Forge Engine (experimental)* under Configuration.
- The Bot Behavior window now has the same look as Manage Accounts.
- The bot is now licensed under the GPL-3.0 (see *License*).

### Version 1.6.3

- Fixed: since 1.6.0 the bot could sit on Home forever without queueing, clicking Home every few seconds and logging `Quest reroll: stale data`. Seen with a **1600×900** Arena window: the reroll's Home check was rejected one pixel off the edge of the screen there, so the Profile → Home refresh never ran, and the queue loop kept waiting for a reroll that could never happen. The Home check now works at that size, and a reroll that cannot run is given up after three attempts so the bot always goes on to queue.

### Version 1.6.2

- Removed the `UNCONFIGURED_ACCOUNT` warning that 1.6.1 logged when the logged-in account was not one of the accounts configured in Manage Accounts.

### Version 1.6.1

- Fixed: the bot could enter or play **whatever event was last open on the account** — for example a Draft left in progress — instead of Starter Deck Duel. Every Play press now has to prove which screen it is on (see *Never another event's Play button* under Quest-Based Deck Selection), and the bot stops itself if Arena is ever told to join or queue an event it must not enter.

### Version 1.5.1

- Fixed a gameplay stall after MTGA is moved or resized during a match: the bot now refuses obsolete screen coordinates instead of slowly sweeping empty space while trying to play cards. Restore a visible 16:9 game window and it will safely retry on the next game-state update.

Cast retry protection now counts repeated moves within the same match and Arena game state. When Arena reports a newer state, the bot can reconsider a cast that was cancelled because its decision became stale; three identical attempts in one unchanged state still trigger a priority pass.

## Configuration

### Game-duration limit

In **Settings > Bot Behavior**, set **Concede after (minutes)**.
Press Enter or leave the field to save.
The default is **10 minutes**; **0** disables the duration limit. This works
independently of **Auto-concede stalled matches** and replaces the hidden
20-minute timeout. `MTGA_MATCH_TIMEOUT_SEC` no longer overrides the limit.

Time starts at the first mulligan decision (or when the bot recognizes a live
game after attaching mid-game), excludes queueing, and includes opponent turns.
Changes apply immediately using time already elapsed: shortening the limit below
the game's current duration requests a concede, while 0 cancels the timeout.
A concede already claimed by the controller may finish. Each new game starts a
fresh timer. Command-line launches use the same saved setting.

### Auto-concede stalled matches

When enabled in Settings, the bot starts conceding after **30 seconds without
confirmed progress on a decision Arena is waiting for it to complete**. This
covers normal play, mulligans, attacks, blocks, targets, costs, and other choices.
Requests addressed to the bot and Arena's local decision timers establish whose
decision it is, even when the priority fields disagree. Waiting for the opponent
does not count as a bot stall.

Accepted partial progress, such as assigning another blocker or selecting another
required target, restarts the countdown. Repeated clicks, duplicate requests,
rejected submissions, and toggling previously selected choices do not. The
countdown continues if Arena's log becomes silent. Before conceding, the bot
checks the match identity and Arena's log state to confirm the same match remains
active. Conceding does not require the settings gear (`assets/assert/settings_gear.png`).
The gear appears throughout the client, in matches and
menus. Its background changes between screens and can affect image matching;
its presence does not establish that a match is being played.

At 30 seconds, gameplay recovery stops and concession begins; focusing Arena and
confirming the dialog take additional time. If two concede attempts fail, normal
play resumes with a fresh 30-second countdown. If the mouse or keyboard backend
itself hangs, the bot stops and displays an error.

The 30-second deadline is checked and the concession is claimed as one
indivisible step, so a prompt that changes (or the setting being switched off)
while that check runs cancels the concede instead of being overtaken by it.
The concede itself then runs outside that lock — it clicks and waits for
Arena, which must never block the log thread.

Every click of the concede sequence is authorised at the moment it happens,
not when the sequence started. Searching the screen for the Concede and OK
buttons takes up to 1.5 seconds each, and focusing the window, the Escape
settle and re-acquiring the game area add more; the match can end or be
replaced by the next one in that time. If it does, the remaining clicks are
dropped rather than landing on whatever Arena is showing by then. The
unconditional concede that follows an expired Arena turn timer is unaffected —
it deliberately does not depend on a known match id.

While a concede is running it holds sole ownership of mouse and keyboard. Old
gameplay workers lose permission to click and remain cancelled after normal play
resumes. An actual input operation already in flight gets up to five seconds to
finish before the bot reports an input failure. Match changes and Stop cancel
old decisions and pending clicks.

### Input backend

The bot auto-selects the best available input backend per platform:

| Platform | Default | Fallback |
|---|---|---|
| Linux | `pyautogui` | `ydotool` (if installed), then `pynput` |
| macOS | `pyautogui` | `pynput` |
| Windows | `pynput` | — |

Override via environment variable: `MTGA_BOT_INPUT_BACKEND=auto|pyautogui|pynput|ydotool|null`

`ydotool` requires `ydotoold` daemon to be running and is recommended for Linux Wayland sessions.

Typing (account e-mail and password during an account switch) always goes through `pynput`, even on the `pyautogui` backend: `pyautogui` types by posting hardcoded **US**-layout key codes, which the OS re-interprets against the layout that is actually active. On a German QWERTZ keyboard that turned `@` into `"`, swapped y/z and mangled `-`/`_`, so logins failed with no visible reason. `pynput` inserts the literal character and is therefore layout-independent.

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

The Manage Accounts dialog scales its text, fields and buttons together with the UI scale setting. Long account names and emails stay inside their table columns, and a scrollbar keeps all controls accessible on shorter screens.

Each account has exactly one name: its **Arena Name**, the one Arena shows top-left (`Name#12345`). The `#digits` may be omitted when that visible name is unique among the configured accounts. If two accounts share the same visible name, every account in that group must use its complete `Name#digits`; otherwise Arena's log cannot identify which row is active and Manage Accounts rejects the configuration. It is not a label you choose — the Arena log identifies an account *only* by that name, never by email, so rotation order, per-account gold tracking and the Current/Next display all key off it. Arena Names must be unique; a row without one is rejected on save. Older versions had a second, free-text `Name` field beside it. Manage Accounts now shows such a row under its Arena Name, and pressing **Save Accounts** renames the account for good — the play order and a manual pin are carried across the rename, and the account keeps its folder. A row that never had an Arena Name shows the old label instead, which may not be the Arena one; Manage Accounts points those out when you open it.

#### Current / Next account

While account switching is enabled, the [overlay over the Arena window](#overlay-over-the-arena-window) shows **Current ACC** (playing now) and **Next ACC** (the account the next switch will log into). **Right-click Account Switch** in the main window to tell the bot which account is open in Arena right now. Use it when you changed account by hand: Arena writes the login only once, so after a while the bot can no longer read it from the log, and a manual pick sets rotation straight again. The pin is dropped automatically on the next switch the bot performs itself, and when the pinned account is renamed or deleted.

Arena records a login in two ways — the match-server handshake and an `[Accounts - Login] Logged in successfully. Display Name: …` line — and the bot reads both, taking whichever is later in the log. Only the handshake used to count, and a re-login (the path taken after a rejected password) can be written without one at all, which left the bot naming the account it had just left. That name decides which account folder the Historic deck thumbnails come from, so it was not a cosmetic slip.

A pin is also dropped when Arena logs a login for a *different* account after the pin was set — you changed account by hand again, so the log is newer than your pick. This only happens when that login can be matched to one of your configured accounts by its Arena Name; an unrecognised login never overrides your choice. A pin restored from the previous session additionally yields to a login that is *older* than it, since a restored pin says nothing about who is logged in now — so give every account its Arena Name if you want the bot to correct a stale pin on its own.

The queue and account-switch toggles are main-menu buttons that carry their current value in the label (**Queue: Historic** / **Queue: Starter Deck**, **Account Switch: On** / **Off**). Queue mode is locked while the bot runs (changing it mid-run would desync navigation).

#### Overlay over the Arena window

Below its buttons the main window shows only the bot's **Status**, in the title's molten-gold lettering, with a play triangle under it while the bot runs and a square while it is stopped. Everything else sits in a small **Session Info** panel over the top-left corner of the Arena window: queue, account switch and the time or thresholds until the next switch, Current/Next account, the session's games and wins, the gold farmed, during a match the turn, phase, whose turn it is and the bot's last move, and the **Daily Quests** (heading in the same gold lettering, the active quest marked). It is up for as long as the bot runs and Arena's window is open and not minimized — also with the bot's own window minimized or while you work in another app (it is topmost, so it then lies over that app too) — and goes away when the bot is stopped or closed. While stopped it is shown only while **Settings → Game Overlay** is open, as a preview.

While the bot starts, a progress bar above the status names the step in progress: *Checking Arena setup*, *Loading card data*, then what the bot itself reports (*Reading daily quests*, *Rerolling 500-gold daily quest*, *Refreshing quests*, *Opening the Play menu*, ... *Pressing Play*). Fixed in this version: the bar only started following the bot's own steps once the first ~40 seconds of startup were over, so it said *Loading card data* while the bot was already reading quests and clicking through Arena's menus.

The bot must never notice that panel, so it is only put on the screen once Windows confirms all three of:

- **Clicks pass through it** (`WS_EX_LAYERED | WS_EX_TRANSPARENT`): a click aimed at Home under the panel lands on Arena.
- **It never takes the focus** (`WS_EX_NOACTIVATE`): Arena answers hovers only while it is the foreground window.
- **Screen captures do not contain it** (`WDA_EXCLUDEFROMCAPTURE`): template matching and the debug captures see the game behind it. Screenshots and screen recordings taken on this PC do not show it either (a camera pointed at the monitor does, of course).

Keeping a window out of captures needs Windows 10 version 2004 or newer. Where it cannot be confirmed — and on macOS/Linux — the panel stays off and `bot.log` says `Overlay not shown` with the reason. Its title never contains "MTGA" or "Arena", so the window locator cannot take it for the game. Code: `overlay.py`.

**Settings → Game Overlay** sets how see-through the panel's background is (0-100 %, default 25 %); the text always stays fully opaque. The change applies immediately. The panel is two windows on top of each other for this — the background with that transparency, and the text above it on a fully transparent color-keyed background — and both are dressed and checked as above.

The old **Record Action** button and its recording/replay are gone. Account switching only used a recording named *Account Switch* as an optional logout replay, falling back to the built-in logout sequence when there was none; it now always uses the built-in sequence. An existing `runtime/records/recorded_actions_records.json` is simply ignored.

Account switching can be toggled on/off live from the main window without restarting the bot, and runs in one of two modes:
- **Time**: switch every N minutes (configurable).
- **Quests**: switch once the configured number of daily quests (measured *absolutely* — completed by the bot or by hand, whichever comes first) and/or daily wins seen this session are reached on that account. With **both** thresholds set the round runs as two passes over the accounts: first every account's daily quests are cleared, then the bot goes round again for the wins. Quests expire at the daily reset and wins do not, so the quests get banked everywhere before the open-ended win grinding starts — under the old rule (both criteria demanded before leaving an account) the last account's quests were only reached after hours of grinding on the first, and a session cut short in between never banked them at all. Wins earned during the quest pass count towards the win pass, so nothing is farmed twice. With only one threshold set there is a single pass and nothing changes. The current pass is in the `SWITCH CHECK` log line as `mode=quests/quests` or `mode=quests/wins`. Once every configured account has completed a round, the bot stops itself instead of cycling back to the first account, and says so in a pop-up so a normal finish isn't mistaken for a freeze. A round covers the accounts in your play order — not every folder under `Accounts/` — and an account whose switch failed is not counted as finished.

**750-gold quests only (free a slot if full)** is an optional checkbox in Manage Accounts, off by default. It saves immediately and applies on the next Start, in **quests** mode with **Main** above zero. The bot still rerolls first and keeps the existing quest counting: Main 2 means leaving at most one unfinished quest, including any empty slots already present. With this option, it also finishes the quest pass early when only 500-gold quests remain and there is a free slot for tomorrow. If all three quests are 500 gold, it completes one to free a slot, even with Main 3. It prioritizes 750-gold quests across supported quest types.

For **Main 2**, three 500s means completing one; three 750s means completing two; one 750 plus two 500s means completing the 750. Two 750s plus an empty slot means completing one, while two 500s plus an empty slot means skipping the quest pass. The configured win target still applies in the normal second pass. Matches can complete several quests together, and win farming may incidentally complete saved 500-gold quests; the bot checks its stopping target between matches.

Rotation always continues from the account actually logged in, so it never wastes a switch logging back into the same account, and the order stays intact even after a failed switch.

> Fixed in this version: in quests mode the bot could log straight back out of every account without playing a single match. MTGA only writes the quest list on Home, and if no fresh block arrives within the 30-second prime window the read falls back to the newest block already in the log — which is the *previous* session's, written after that session had cleared its quests, so it parses as "0 quests left". With the threshold at 3 ("clear them all") that reads as "this account is finished", and the bot switched again immediately. Measured on 2026-08-22: five accounts, ~30 seconds each, zero matches, heading straight for the end-of-round stop — which now also powers the PC off. The count is still read for deck colours and the UI, but the switch decision now needs proof that the block was logged past this session's (or this switch's) boundary; without it the bot keeps playing and keeps dipping to Home until MTGA logs a real one. `SWITCH CHECK` says `STALE - not this account's own read` while that is the case.

#### Shut down the PC when the round is done

**Max play time (hours)** in Manage Accounts limits the entire run across all accounts, including startup and account switching. It defaults to **3 hours**; **0 disables it**. Both `0,1` and `0.1` mean six minutes. Click **Save** or press Enter to save; changes apply on the next Start, which resets the timer.

Once the limit is reached, the bot finishes its current match (including one already matchmaking), stops, and schedules PC shutdown **regardless of the completion checkbox below**. It does not queue another match or switch accounts. Finishing the match can extend play beyond the limit. You then have **two minutes to cancel**: choose **Yes** in the “Cancel the shutdown?” popup, or run `shutdown /a` on Windows (`shutdown -c` on Linux). Cancelling leaves the bot stopped and the PC on. Manual Stop, errors, and closing the app cancel the run timer without scheduling shutdown. Normal round completion before the limit follows the existing checkbox.

**Manage Accounts** has an opt-in checkbox, **Shut down PC when all accounts are done**, right below the switch settings it depends on. With it ticked, the end-of-round stop described above also powers the machine off — so an overnight run of every account ends with the PC off instead of idling at Home until morning.

It is off by default and saved the moment you click it, not on **Save**. The guard rails matter more than the feature:

- It fires on exactly one event — the controller stopping itself with *all accounts completed this round*, i.e. the end of the last pass (quests only, wins only, or quests-then-wins, depending on which thresholds are set). A manual **Stop**, a crash, a failed switch or a time-mode rotation never reaches it (time mode has no end of round at all, which is why the checkbox says so).
- The shutdown is always **delayed by two minutes** and announced in a pop-up with a **Cancel** button; a zero delay is clamped up rather than honoured, so there is always a window to abort. Cancelling by hand works too: `shutdown /a` on Windows, `shutdown -c` on Linux.
- If arming the shutdown fails, you get a warning telling you to power off yourself, rather than silence — the failure mode of a silent miss is finding the machine still running hours later.
- Works on **Windows and Linux**. Windows gets `shutdown /s /t <seconds>`; Linux hands systemd `shutdown -h +<minutes>` (rounded up, so the delay is never shorter than announced) with `--no-wall`, because the wall broadcast is a separate permission that can be denied where powering off is allowed. An unprivileged Linux call goes through logind/polkit, which grants power-off to the active desktop session without a password — on a headless or locked-down system it can be refused, and then you get the same warning as any other failure to arm. On macOS the setting saves but warns that nothing will power off: its `shutdown` needs root, which a background run cannot get.

A switch that becomes due while a match is running or while matchmaking is in progress is **deferred**, not skipped: it is carried out on the first moment between matches. This also covers the end-of-round stop, which previously could end the session in the middle of a game the bot had just started.

If the logout never reaches the login screen three times in a row — usually a mis-calibrated **Log Out** button or a changed Arena layout — the bot stops attempting to switch for the rest of the session and keeps playing the current account instead of looping through failed logouts. It says so once in `bot.log`, pointing at the calibration.

> Fixed in this version: the **Log Out** click missed the button on every attempt but one, in the whole recorded history. "Log Out" is a centered text link on the Options overlay, and it is found by template match with a coordinate fallback behind it. Both halves were broken. The match passed no scale tolerance, and because the search region is the arena itself — normalized to 1920×1080 — nothing gets rescaled on a 1920-wide client, so only scale 1.0 was ever tried: measured against a live overlay the template scores 0.549 at 1.0 and 0.950 at 1.10, i.e. it could not match at all. The fallback coordinate then pointed at (1716, 851), the legacy bottom-right layout, which is empty background — the link sits at (959, 667). Across the recorded history the image match hit **once** (on a 2048×1152 client) and produced the only successful logout, while the coordinate fallback ran **11 times and produced none**; when the Arena window changed to 1920×1080 the last working path disappeared and switching went from unreliable to impossible, disabling itself after three failures. The match is now scale-tolerant (0.70–1.50) and the fallback coordinate corrected, so both paths agree on the same point.

> Fixed in this version (Linux only): the switch typed a **broken e-mail address** on every login. On X11/XWayland pynput resolves a character to its AltGr level — `@` sits on AltGr+Q on a German layout — and then presses that key *without* AltGr. Measured on 2026-08-23: `a@b` arrived as `aqb`, the address was typed as `…qmail.de`, Arena answered "Invalid email address or password", and the bot — believing it had logged in — span for an hour in `GO_HOME refused` on a login screen, 163 seconds at a time without a single click. Linux now **pastes** instead: the text goes to the clipboard and Ctrl+V puts it in the field, and since Ctrl+V carries no character, no keyboard layout can distort it. Ctrl+A precedes it because `tap_delete()` only deletes forward from the caret and leaves a pre-filled field alone. The clipboard is held by a short-lived helper process (an X selection is served by a live process, and a Tk root that is merely updated once hands out stale content), the text reaches it length-prefixed on **stdin** so no password appears in the process list, and keys go out through `ydotool` — uinput, so neither the layout nor the X server is in the way. If the paste fails, nothing is typed and the switch aborts with an error in the log: a silently wrong password is what cost that hour. **Windows and macOS keep the keystroke path unchanged** — it works there.

> Fixed in this version: after a successful switch, MTGA's post-login announcements could strand the bot. Observed live: "Banned Standard Cards" (an Okay button) followed by a set promo ("The Hobbit — Available Now!"), both covering Home completely — so navigation found no anchor at all and the queue loop spun for two and a half minutes until they were cleared by hand. The bot now clears them, but only once every navigation anchor has already been ruled out, so a dismissal can never steal a click from a real screen. A plain **Okay** is clicked; anything else gets **ESC**, deliberately *not* the promo's own call-to-action, because "Get Started!" opens the Store instead of dismissing. If that ESC merely opened the Options overlay then nothing was covering the screen after all, so it is closed again and the attempt reports no progress — otherwise the overlay would hide Home from the next pass and the dead-end would repeat forever. The Okay search runs at confidence 0.90, not the 0.80 used elsewhere: measured against real screens it scores 0.970 on an actual popup but 0.817 on the event page's orange Play pill and 0.808 on plain Home, so a looser threshold would press Play and start a match with the wrong deck.

> **Known issue:** in quests mode, if a switch becomes due while the bot is on the Starter Deck Duel event page (`game_mode = starter`), the logout sequence can fail to open the Options menu and misclick into the event page instead of logging out. Time-mode switching from Home is unaffected. A fix (navigate to Home before attempting logout) is planned as a follow-up. This is separate from the calibration fix above and still open.

### Gold Tracking

The bot reads each account's real Gold balance from MTGA's own logs and tracks the delta (current balance minus the balance first seen this session) per account — no estimate. Open **Current Session** from the main window to see gold farmed per account (labeled with your account names), alongside games/wins for the session.

Accounts the bot switches into get their baseline read on arrival at Home, before they play, so their earnings are complete.

A balance in MTGA's log does not say which account it belongs to, so only balances written after the session started — and after the most recent account switch — are used. Anything older is ignored rather than guessed at.

> **Known limitation:** the **first** account of a session can show `0` farmed gold. MTGA only reports that account's balance after its first match, at which point the win reward is already included, so that match's earnings can't be measured.

After a switch the bot takes the account's identity from the credentials it just typed — it knows which account it logged in. It used to read that back out of the Arena log instead, but the log has no login event to read: the only thing there is the match-server handshake, written when a match connects. Right after a switch the newest one still belongs to the account just left, so the old name was re-adopted and stuck, and every balance read afterwards — correct in itself, but nameless in the log — was booked against the wrong account. One row could grow implausibly large while its partner sat at `0`. A handshake written *after* the switch still wins, so changing account in Arena by hand is still picked up. A `GOLD_BALANCE_BELOW_BASELINE` line in the log flags a balance below its baseline, which is what a misattribution looks like from the other side.

### Quest-Based Deck Selection

Before its first match at **Start**, and after each account login, the bot automatically rerolls one incomplete **500-gold daily quest** if Arena's fresh quest response says a swap is available. It picks the leftmost visually recognized 500-gold quest, confirms once, accepts either a 500- or 750-gold replacement, and refreshes the quest list and deck colors before continuing. Existing 750-gold quests are left alone. There is no setting to enable, and ordinary between-match refreshes do not spend rerolls; starting during a match defers the check until the first safe return to Home.

The bot verifies Home, the daily-quest tile, and Arena's **Confirm Swap** dialog using captured UI templates, then uses the dialog's fixed, scaled button positions for **OK** and **Cancel**. If clicking an already-active Home tab produces no new quest response, it makes one bounded **Profile → Home** refresh. The same re-entry can obtain the post-swap response, which Arena does not always log immediately. Missing/stale data or an unrecognized dialog skip the attempt. If a 500-gold tile cannot be recognized, the bot skips the reroll only after it confirms Home is still visible; an uncertain or overlaid screen remains blocked for a later safe retry. After submission it waits up to 10 seconds for verification and never repeats an uncertain confirmation; an unresolved dialog prevents the queue loop from starting. A reroll that cannot run for a recoverable reason still lets the post-login routine pick this account's deck — only an unresolved swap dialog, a stop, or a running match/account switch skips deck selection. No error from the check can escape into the queue loop. Quest replacement is not counted as quest completion or gold earned. Outcomes are recorded as `Quest reroll:` in `bot.log`.

If Home navigation succeeds but the reroll's stricter Home recognition fails twice, startup and post-login checks try a verified **Profile → Home** re-entry to redraw the screen. Recovery makes at most three return attempts, with an 8-second cooldown between re-entries. It requires a visible, enabled Profile tab and pauses for a match, swap dialog, stop request, or another thread's account switch. Home must still pass recognition before reroll or deck selection proceeds. Recovery is logged as `QUEST_HOME_REENTRY`.

**Faster start: reading the quests.** At Start the bot reads the account's daily quests from scratch, and Arena only logs them when Home is *entered*. Clicking the Home tab while Arena already shows Home — the normal state when you press Start — logs nothing, and the bot used to wait out its whole 30-second window for a list that never came (a second Home click halfway through did not help either), then fell back to the list already in the log. It now waits about 4 seconds after the Home click and, if nothing arrived, leaves Home for Profile and comes back (the same verified re-entry as above: Profile is found by its template, never a blind click), which makes Arena send the list at once. When that read succeeds, the quest reroll check needs no Home trip of its own, and in quests switch mode the check before the first queue (*Refreshing quests*, another Home trip plus up to 8 seconds of waiting) is skipped too — it existed only to obtain such a fresh read. If the read still fails, both behave as before. In one `bot.log` from before the change, about 50 of the 55 seconds between Start and the first *Play* went to these three Home trips. `bot.log` says `Quest prime: no block after the Home click; re-entering Home via Profile.` and `Startup/home quest check: quests already read fresh for this account; no Home dip.`

A landing that keeps the reroll for later without clicking anything (no fresh quest response, Home not verified, 500-gold tile not recognized, a screen-check error) counts as one attempt; after three, the reroll is dropped for this login (`Quest reroll: skipped (… giving up after 3 attempts)`) and the bot queues normally. Each login re-arms it. Before 1.6.3 there was no limit, and a check that could never succeed kept the queue loop waiting on it forever. The brightness check that rejects dimmed controls also tolerates a crop up to 4 px past the frame edge: Home's anchor sits flush against the top edge, and through a 1600×900 client the matched point rounded to one pixel above it, which rejected every Home check there.

Validated against the live English Arena UI at 1280×720 with Windows display scaling at 175%: one 500-gold quest was replaced by a 750-gold quest, the other two quests were preserved, and the refreshed log/cache still showed three incomplete quests. Arena omits `canSwap` when false; only an explicit `true` enables a reroll. The standalone `tools/validate_quest_reroll.py --log-path <Player.log>` inspects controls without input; `--cancel` closes a recognized swap dialog, and `--reroll` runs one real startup check. Use it with the bot stopped; it never queues a match.

On **Start**, and after each account switch, the bot picks a deck based on active quests. Quests are read fresh at start: everything already in Arena's log is ignored and the bot briefly returns to Home to make Arena log the current list (up to 30 seconds; it falls back to the newest entry it has if none arrives). Without this, a quest you re-rolled or finished by hand before pressing Start was read as still active, and the first matches were played on the wrong deck.

Place deck screenshot images in the account folder named by color letters:

- `RG.png`, `WU.png`, `B.png`, `R.png` etc. — matched to quest colors
- `C.png` — used for creature-type quests
- Fallback only when *no* quest target could be read at all. In Historic a quest that names its colors is never satisfied by a non-matching thumbnail — see below

In **Historic** the format and deck are now selected by the bot before it queues, instead of trusting whatever Arena had selected last. Before each queue the bot resolves the current quest's colors and, if they differ from the selection it last verified (or it has verified none yet — a fresh **Start**, or the first queue after an account switch), navigates **Play → Find Match → Play sub-tab → Historic Play → My Decks**, clicks the deck thumbnail matching the quest from the logged-in account's own folder, and confirms on screen that it is really on the Historic deck screen. Only then is the Play button pressed. A quest that completes mid-session therefore also swaps the Historic deck, and the between-matches dip to Home that keeps quest progress current (Arena only logs it on Home) now happens in Historic too, not just in Starter Deck Duel. A verified selection is kept across matches, so the usual re-queue stays a single Play click.

When the quest names a target — colors, or one of the forced deck files — only a thumbnail that actually matches it counts. A quest for **RW** is never satisfied by the first tile in the deck list or by the nearest other file in the folder: if `RW.png` (or a thumbnail sharing one of the colors) is not configured, or is configured but no longer matches on screen, the bot does **not** queue. Farming a color quest with an arbitrary deck makes no progress either — it is the same bug as the stale deck below, just quieter. The first-tile fallback remains only for the case where no quest target could be read at all, where there are no colors to miss. Thumbnails are matched from the logged-in account's folder alone once its Arena screen name is known; before it has been latched — the first moments after a login — the account the switch was aiming for is used instead. Other accounts' folders are only searched when the identity is entirely unknown, because two accounts' thumbnails for the same colors are usually the same precon artwork and would resolve to a deck the config never pointed at.

The same rule now governs the **first** queue of an account, not just the re-queues. The post-login routine (which runs once per login and per account switch) used to click whatever thumbnail came closest — or the first tile — and then press **Play**, so an account's opening matches could still be farmed on the wrong deck while the between-matches path was strict about exactly that. It now selects through the same check and, when nothing satisfies the target, does not press Play at all; the queue loop then re-checks before every queue.

So a Historic account needs a deck thumbnail for the colors its quests ask for. When one is missing the bot logs `Historic: no deck thumbnail matched quest target …` and idles instead of playing, backing off to one attempt a minute rather than retrying every queue-loop tick.

> Fixed in this version: in Historic the bot re-queued with a bare Play click, which re-enters *whatever Arena last had selected*. After a Starter Deck Duel session (or any manual play) the client was still pointing at that event and its deck, so an **RW** quest was farmed with a **Golgari Starter** deck and never progressed — visible in `bot.log` as the quest colors, a Home/Play click, and then a `DualColorPrecons` match. If any step of the new navigation, the deck pick, or the final screen verification fails, the bot **does not queue at all**, rather than falling back to the stale Starter mode and deck. The failure is logged as `Historic: …` in `bot.log`. Starter Deck Duel behaviour and account switching are unchanged.
>
> The final check is a live template match on the Historic / My Decks anchor. The state read from Arena's log is not accepted on its own: it is substring-matched over a 250 KB tail, so the words that made an *earlier* navigation report “My Decks” are still in that tail long after the client has moved on — precisely the stale read this gate exists to catch. The log state is only used where the anchor images are missing from the install.
>
> Fixed in this version: Historic navigation could never leave the Home screen. `POST_LOGIN_PLAY`, the first of the five navigation actions, checks that the Home anchor is on screen before it clicks Play — but it looked for it in a box starting 20 pixels below the top edge, and the anchor sits flush against that edge (client rect x=43–165, **y=0–79**). The top of the anchor was sliced off, so it scored **0.218** against a 0.84 threshold on a screen where it matches at **0.998** over the full client area. Every attempt failed the check, the bot logged `action_failed:POST_LOGIN_PLAY`, and the saved debug bundle showed an ordinary Home screen with Play plainly visible. All five actions' anchor boxes now start at the client origin.
>
> Two things made that failure much harder to read than it should have been, and both are fixed:
>
> - **The recovery pressed ESC on a healthy screen.** ESC is not a neutral "go back" in Arena — on Home it opens the exit/settings overlay, so the first attempt opened it and the second closed it again, flip-flopping an overlay over the very screen the action was trying to read. ESC is now sent **only when the Options overlay is actually verified on screen**; otherwise the recovery just re-acquires the client rectangle and, for the first action, re-clicks the Home tab. A failed overlay probe never falls back to pressing ESC.
> - **The debug bundle screenshotted the wrong moment.** It was written after the action gave up, by which time the recovery had already navigated back to Home — so every bundle showed a healthy Home screen no matter which step failed, which is why the first round of debugging could not tell "the Play blade opened but its anchor is stale" from "the click did nothing". A bundle is now written the moment a step fails, while the offending screen is still up, tagged `step_failed:<action>:<pre_assert|click|post_assert>`. Once per step per action, so a retrying loop cannot fill the disk.
> - **A too-tight anchor box was invisible in the log.** It reported as "the screen is not what I expected", which is indistinguishable from genuinely being on the wrong screen. When an anchor is not found in its box, the bot now looks once more across the whole client area; if it finds it there, it logs `ACTION_ROI_TOO_TIGHT` naming the action, the box and the position and score of the real match, and carries on. A misconfigured box costs a log line instead of the navigation. This widening applies to screen-identity anchors only, never to a button the bot is about to click — searching outside that box could put a real click on the wrong widget.
>
> Two further stops on the same road, both of which kept the bot navigating correctly and then refusing to play:
>
> - **The final pre-queue gate searched the wrong part of the screen.** It looked for the "My Decks" and "Historic Play" anchors in one hand-written top-left box, and neither is top-left: measured live, "My Decks" sits at x=25–380, y=309–402 and the selected "Historic Play" row at x=1558–1769, y=554–618, so the box scored **0.30** and **0.46** against a 0.80 threshold. The gate failed on the very screen the navigation had just reached and verified, the selection was never cached, and the queue loop re-navigated and refused again every few seconds without ever queueing. The gate now reuses the navigation's own boxes (`actions/navigation_flow.py`), so the two cannot drift apart again.
> - **The bot could not recognise its own selection.** MTGA lifts the selected deck tile, outlines it and fans the cards over the art, so the thumbnail captured from the normal grid scores ~0.44 against the deck the bot itself just picked — and "thumbnail not on screen" was treated as "this account has no deck for the quest", which refuses to queue. Besides the optional `<deck>.sel.png` selected-state capture, the bot now remembers the tile it last clicked for each account: MTGA keeps the deck selected between matches, so that record identifies the selection without a second image. It is written only after a confirmed click on a thumbnail matching the quest, and dropped on a new session, an account switch, and the no-quest first-tile fallback. The screen anchors are still checked afterwards, so this can shorten the selection step but never waves a wrong screen through to the queue click.

> Fixed in this version: Historic still could not leave Home, for a second and unrelated reason — and then, once it could, it still would not queue. Both were measured live on 2026-09-20 and both are fixed:
>
> - **One template stood between the bot and the whole flow.** `POST_LOGIN_PLAY` clicks Home's Play button by locating `play_btn.png`, and nothing else. An always-on-top window (a terminal bubble parked in the bottom-right corner) covered the button's lower half, so the template scored **0.727** against the 0.85 threshold — **0.895** on the rows it did not cover, i.e. the template was fine and the button was exactly where it belongs. The click step failed on every retry, the navigation failed at its first action, and Historic refused to queue every few seconds, indefinitely. A navigation step may now carry a **measured coordinate fallback**: when its click template does not match, it clicks the position the button was measured at instead. The fallback is bounded to the step's own search box (a point outside it is refused and logged), it is reported as `ACTION_CLICK_FALLBACK` rather than passing silently, and the step's post-assert still has to confirm the result — so a fallback that lands on nothing fails the step exactly as before. Only Home's Play button has one; it does not move, and it is the single point the entire flow depends on.
> - **The bot was looking in the wrong account's folder, and said so misleadingly.** Arena records a login in two different ways, and the bot read only one of them: `authenticateResponse.screenName`. A re-login — after two `401 | INVALID ACCOUNT CREDENTIALS`, which is how a mistyped or rate-limited attempt ends — was written **only** as `[Accounts - Login] Logged in successfully. Display Name: Name#12345`, 1.88 MB further into the log than the last `authenticateResponse`, which still named the *previous* account. So the bot kept calling itself by the old name, and since Historic matches deck thumbnails from the logged-in account's folder alone, it read an empty folder and logged `Historic: no deck thumbnail matched quest target colors=UB` on every queue tick, forever, while the decks sat in the folder of the account actually signed in. Both forms now count as a login, and the later one in the log wins. A login whose name carries a `#discriminator` also resolves to a configured account that has none, provided exactly one account shares that visible name and none of them specifies a discriminator — otherwise there is no spelling of that row such a login could ever match. Two accounts that both give a full `Name#digits` are still never confused for one another.
> - **An account folder with no thumbnails at all was skipped in silence.** The only thing logged was the summary — "no deck thumbnail matched quest target colors=UB" — which blames the quest for what is really an empty directory, and sent a debugging session after the color matching. The bot now says `Historic: account 'X' (folder 'Y') has no deck thumbnails at all` and points out that the folder is named after the account's Arena screen name.

> Fixed in this version: Historic navigation only worked when the MTGA client was exactly 1920×1080. Every box, point and template of the navigation steps is in the 1920×1080 reference frame, but the steps applied them to the client unscaled — so on a windowed client at, for example, **1366×768**, the search boxes pointed at the wrong pixels and every template was compared with a screen drawn at 0.71×. The Home anchor could not match anywhere, `POST_LOGIN_PLAY` failed at `step=pre_assert` on every attempt, and the bot logged `Historic: navigation to the deck screen failed; NOT queueing` indefinitely. Starter Deck Duel worked on the same machine because its navigation already rescales. The navigation steps now map every box and point into the client's real size and rescale each capture to the reference frame before matching, just as the rest of the bot does; on a 1920×1080 client nothing changes.

> The post-login routine marks its own deck selection as verified only when it confirmed every step: state-asserted navigation, a configured thumbnail matching the quest, and the planned account's own folder. After the legacy image-only navigation fallback, a first-tile pick, or a deck taken from a different account's folder, the selection stays unverified and the first re-queue navigates and checks it again — that routine has already pressed Play and cannot re-inspect the screen, so it must not vouch for what it did not confirm.

In Starter Deck Duel the quest target is re-checked before **every** queue, so when a quest completes mid-session the bot swaps to the colors of the next one instead of finishing the session on the deck it started with. After successfully submitting a deck and verifying the event Play page, the bot remembers that deck for the current account and session. If the target stays the same and the Starter event Play page is confirmed, it skips opening the chooser and submitting the same deck again. Normal match resets and Home quest refreshes keep this memory; a new bot session, account switch, or uncertain selection clears it. Stop and restart the bot after manually changing the deck while it runs. The colors are resolved *after* the quest check (previously the just-completed quest's deck was replayed for one more match), and the bot verifies the deck chooser actually closed after submitting — a missed swap is now reported in `bot.log` instead of silently farming the wrong colors.

After dismissing the match result screen, the bot waits **20 seconds** before resuming queue navigation or an account switch. The ten-second game-state reset runs within that pause. Home quest refreshes and existing reward-popup recovery remain in place.

In quest/win mode with a win target, Starter Deck Duel selects **WB (Vampiric Hunger)** as soon as the configured quest target is met. A **0 quests / 6 wins** setting uses WB immediately, even with unfinished quests. Nonzero quest targets count quests already completed by the player and require a fresh quest list for the current account, using the same completion rule as account switching. Until that target is met, unfinished color-targeted quests determine the deck. Outside this win-farming rule, a fresh list confirming all daily quests are complete also selects WB. WB is the fixed win-farming choice based on previously measured statistics (32% across 1,673 matches), rather than a live ranking of decks.

In the account-switch win pass, an account that already banked its configured wins during the quest pass skips post-login deck selection and queueing; the queue loop switches to the next account instead of starting an unnecessary WB match.

Finding the **Starter Deck Duel** banner no longer depends on it being near the top of the Events list. The bot applies the **In Progress** filter, which normally cuts the list down to the events actually in progress so the banner fits on one page; if it is still not visible, the bot drags the list's scrollbar and re-checks after each step, then rewinds to the top if it never appears.

> Fixed in this version: the **In Progress** filter was never actually applied. Its anchor image had been captured with the filter *selected*, so the lit orange marker dominated the match and it resolved to whichever row was selected — normally **All**. The bot clicked **All**, logged "In Progress filter selected", and then searched the full, unfiltered list. In that list Starter Deck Duel had drifted down far enough to be cut off by the bottom edge of the viewport, and since its image includes the title strip, a half-visible banner matches nothing. The filter is matched on its label text now, which reads the same selected or not.

> Fixed in this version: on brand new accounts (or accounts that have never entered Starter Deck Duel), the event is not listed under **In Progress**. The bot now automatically falls back to the **All** filter if the banner is missing under **In Progress** and waits for the page transition.

> Fixed in this version: on a brand new account the bot reached the deck selection and then opened the deck's **card list** instead of picking a deck, and never found its way out. Measured against a fresh account, the event has *four* screens that a template matcher cannot tell apart — each is a single rounded pill in the bottom-right corner, and it is the same widget every time:
>
> | Screen | Bottom-right button |
> | --- | --- |
> | first entry, event not joined | green **Start** |
> | joined, no deck chosen yet | **Choose Your Deck** (no Play button at all) |
> | the 10-deck chooser grid | **Submit Deck** (plus **View Deck** bottom-left) |
> | deck chosen, ready to queue | orange **Play** |
>
> At the 0.80 confidence the old code used, `event_play.png` matches *all four* of those pills and `submit_deck.PNG` matches all three landing pages, so the bot read the two first-time screens as the normal landing page. It then clicked the current-deck box coordinate — which on the first-time page is the **Inspect Event Decks** thumbnail — and landed in the read-only card list, a screen with no anchor it recognises. The arena-wide Submit-Deck search had the same failure mode from the other side: it matched **View Deck** in the opposite corner, which opens that same card list.
>
> Screens are now identified at 0.90 with two gates that do not rely on the shared pill: the chooser is anchored on the blue **View Deck** button, which exists on no other screen, and the three landing pages additionally require the top-left **Starter Deck Duel** header. That header matters because Home's Play button *is* the event page's Play button — the same widget, matching at 0.90 and even 0.95 — so without it the bot reads Home as the event page and starts clicking event coordinates there. From that, the bot walks the sequence explicitly (Start → Choose Your Deck → grid), never clicks grid coordinates on a screen it has not positively identified as the grid, restricts the Submit-Deck search to the bottom-right corner, and backs out of the card list via the top-left arrow if it ever ends up there.
>
> Also corrected while measuring the live grid: the fixed fallback coordinates for the 10 deck cards were the art's *bottom edge* rather than its center, so that fallback clicked the seam between a card and its name plate and selected nothing.

> Fixed in this version: on the event's Play page only the current-deck box's **art** opens the deck chooser — the name plate under it does nothing. On an account that has already won the event's three games (the page shows "Keep playing until the event ends"), the one fixed click point landed on that name plate, so every swap attempt ended in `Starter: could not reach the deck chooser in 5 steps; keeping the current deck.` and a whole session was farmed on the previous deck despite an open RG quest. The bot now clicks the box art first and, if the page does not change, falls back to the old point, which is where the box sat on an account still mid-event.

> Fixed in this version: running the test suite moved the real mouse and clicked. A Controller arms fire-and-forget timers (the card-prompt settle timer among them) that nothing cancels, so tests that built one produced real clicks seconds later, at absolute screen coordinates, on whatever was in front — 231 input events including 77 clicks per full run. Constructing a Controller without naming an input backend now gets an inert one; everything that actually drives Arena names its backend explicitly, so nothing changed for the bot itself. `MTGA_BOT_INPUT_BACKEND` still overrides.

> **Never another event's Play button** (fixed in 1.6.1, reported on Discord): with a Draft — or any other event — as the last thing played on an account, the bot could enter that event instead of Starter Deck Duel. Every Play press is found by image, and the orange Play pill is the *same* widget on Home and on every event page: measured on 53 captures, `play_btn.png` and `event_play.png` score 0.92–0.99 at the same spot on Home and on Starter Deck Duel's page alike. Navigation clicked that pill on its first step without checking that it was on Home, and after clicking the Starter banner (a 0.72 match, in an *In Progress* list that also shows an unfinished Draft) nothing re-checked the page before pressing Play. Now:
>
> - the Home Play click needs Home's own nav anchor (`home_anchor.png`) on screen;
> - every event Play press needs the **Starter Deck Duel** header on screen, and Player.log must not name a different event as the open event page — Arena logs the event's internal name with each page change (`Client.SceneChange … "toSceneName":"EventLanding","context":"DualColorPrecons"` for Starter Deck Duel);
> - parked on another event's page, the bot clicks the Home tab instead of anything on that page (Historic mode too);
> - as a last line of defence, if Player.log shows Arena being told to join or queue a forbidden event (`EventJoin` / `EventEnterPairing` — anything but `DualColorPrecons` in Starter mode, any Draft/Sealed/Cube event in Historic mode), the bot logs `FOREIGN_EVENT_ENTRY` and stops. It cannot undo an entry, but it does not play on in it.

> **Stuck on the event page between matches** (fixed, seen on two installs): after a match the bot could sit on the Starter Deck Duel page for good — 47 minutes in one session, about 100 clicks, and Player.log shows no page change and no queue join for any of them. Three faults stacked:
>
> - When the page's header was visible but its pill (Play/Start/Choose Your Deck) was not read, navigation treated that as "nothing recognizable" and pressed **ESC** to clear a possible popup. On an uncovered screen ESC opens the **Options** menu instead.
> - The check that closes Options again asked the window locator, which names only the *first* screen anchor that matches. Options is checked after the nav bar's Profile icon, which can still match behind the overlay, so Options went unseen and stayed open.
> - Nothing ever left the page: every pass retried the same deck-box and Play clicks.
>
> Now ESC is not pressed while the Starter Deck Duel header is visible (a popup that hides Home hides the header too), and Options is probed on its own anchor rather than through the first-match order. After two passes in a row that end stuck on the event page, the bot saves a screenshot bundle (`runtime/debug/`, at most one per 10 minutes), closes Options if it is open, and goes Home, so the next pass takes the full Home → Play → Events path. Both stuck cases log `STARTER_DEAD_END` / `could not reach the deck chooser`, which the session watchdog now records as an alert (`starter_dead_end`); the stall used to leave `alerts.log` silent.

> Note on scrolling: MTGA's event list ignores the mouse wheel entirely, so the bot drags the scrollbar. The step is deliberately small — the bar is about a quarter of its track, so the list moves roughly four times as far as the bar, and a step wider than one banner can jump straight over the one being looked for.

> Fixed in this version: the reward-popup handler mistook the event page's orange **Play** button for a **Claim** button (same corner, same shape). It pressed Play, which started the next match immediately and skipped the deck check — so the bot kept replaying its first deck even after the active quest changed colors. The handler now verifies it is not on the event page before clicking.

### Casting Logic

Combat blocking is enabled by default and can be disabled with `MTGA_COMBAT_BLOCKS=0`. The bot consumes
Arena's legal blocker graph, takes profitable blocks first, and only sacrifices
creatures when needed to survive lethal damage. Trample damage is included in
that survival calculation. Removal targeting also prices Ward before committing:
colored Ward symbols require matching untapped mana sources, and creatures used
for Convoke are not incorrectly counted as mana available for Ward.

The bot maximizes mana usage each turn:
- Prefers the highest-value spell(s) that spend the most mana
- Respects color requirements and discounted costs
- Treasure tokens count as one source that can pay any color. Unresolved mana sources remain explicitly marked unknown and emit `UNRESOLVED_MANA_DIAGNOSTIC` with their ability IDs and game-object details; the payment estimate conservatively counts them for generic costs only.
- Target submission keeps brief lock retries, but stops waiting after eight seconds from the first contention, saves a debug bundle, and leaves the prompt for existing recovery. It never releases another operation's lock.
- Type priority when CMC is tied: creature → instant → sorcery → enchantment
- Supports Convoke (untapped creatures as mana sources)
- Kicker: the "Cast with Kicker?" chooser is answered automatically (always the plain, non-kicked version for now) so the bot never stalls on it
- The mid-screen "Choose One" overlay — kicker plates, a modal spell's or creature's mode plates (e.g. Apothecary Stomper's enter-the-battlefield choice), and the "sacrifice or pay" buttons — is treated as blocking: while it is up, no other move is dispatched, and the chosen plate is clicked again if the game has not moved on. Previously a single click that failed to register left the dialog open and the bot immediately swept the hand row for its next play behind the overlay, which could not be reached — the match then idled until it was conceded. "Has the game moved on?" is answered by the newest `gameStateId` seen on **any** GRE message, including the timer messages that never reach the merged game state — reading only the merged state made an already-answered dialog look open for another two seconds, and the retry then clicked onto the battlefield behind it
- Client-side "Are You Sure?" confirmations are handled reactively after a failed cast attempt, avoiding speculative screen probes during normal casts
- Decision recovery is guarded against open payment/selection prompts and resumes safely after modal, stack, or scry interruptions
- If a target-selection prompt appears during a hand scan or before the first cast click, the cast is deferred without clicking, retrying the sweep, or marking the card unreachable. The target handler resumes decisions after the prompt; a completed scan that cannot find the card still uses the existing 20-second cast suppression.
- A creature target is clicked again only when a newer Arena target update confirms that no target was selected. A delayed or missing acknowledgement alone does not trigger another click, which could otherwise unselect the target.
- Once required targets are selected, a failed Submit search retries up to three total attempts for the same prompt. Retries stop when the prompt changes, the match ends, or the bot stops; a successful Submit click is not repeated by this recovery.
- Fiery Annihilation selects its required creature first, then tries one bounded scan for an opponent-controlled Equipment offered by Arena. If the Equipment cannot be selected, it uses **Submit 0** to skip that optional target. Target recovery also checks for Submit 0 when existing retries or submission attempts fail, provided every required target group is satisfied; it does not wait for the stall-concede deadline. Both own-player and opponent-player targets are checked against Arena's legal choices. Submission clicks must be followed by prompt advancement rather than being treated as proof of success.
- Submit 0 uses a dedicated color template at **0.85 confidence**, with no Okay or coordinate fallback. Ordinary logs record Equipment scan attempts and Submit 0 attempts, failures, and acknowledged prompt advancement; the audit index includes these events.
- After a two-click cast leaves the card, game state, and cast action unchanged, the bot waits for a known screen blocker to clear, then asks the AI for a fresh decision. This poll stops if the match or game state changes. On a clear screen, the bot checks that the same decision is still active and sends a guarded Escape. If that opens Options, a second Escape closes it. Only a completed Escape permits one fresh cast decision; busy input or lost focus before Escape leaves that retry available. If the follow-up also has no effect, it promptly asks for another decision; if the AI still chooses that card, the bot passes priority without a third click. Cast acknowledgement failures, Escape recovery, and detected cast blockers save text diagnostics only under `runtime/debug/`; frames used for live recovery checks are not saved as screenshots. `tools/analyze_cast_recovery.py` summarizes recovery outcomes from the log
- Ties between otherwise equal casting plans favor lifegain-payoff creatures, so decks built around gaining life develop toward their game plan sooner
- Removal only ever targets creatures still on the battlefield and never redirects a harmful spell at your own board when no valid enemy target exists
- Ward is priced before a target is chosen, not discovered afterwards. A warded creature makes Magic Arena raise a confirmation window that exists only in the client — it is announced nowhere in the game's own messages, so nothing tells the bot it is there. Declining it hands back the same board the bot just looked at, so it picks the same target, and the same window opens again; a single such loop burned six minutes across four turns. Now the ward's cost is read from the card and weighed against the mana left over once the spell is paid for. Affordable, and the bot pays it and kills the creature — refusing to ever pay would make removal useless against the cards it most needs to answer. Unaffordable, and it picks a different target, or holds the spell rather than feeding it to a counter. A target it does back out of is remembered for the rest of the match, so nothing can loop even if the pricing is wrong
- Damage marked on a creature is forgotten when the turn ends, as the rules say it should be. Magic Arena announces damage but never announces its removal — it sends the field once and then simply stops mentioning it — and since the game state is merged from diffs, nothing would otherwise unset it. Left alone the damage accumulated for the rest of the match and every "how much toughness is left" reading drifted below the truth, until creatures that were healthy and attacking looked to the bot like they were already dead. That number decides which creatures removal can kill, which fights are winnable, and which blocks are worth making
- When a card lets you choose which creature to return from the graveyard or exile, the bot ranks candidates by their role in the deck's strategy instead of taking whatever the game offers first
- Resolution prompts requiring exactly one card from your own graveyard also use the central chooser, including Inspiration from Beyond. The bot ranks creatures using the existing strategy and other cards by mana value, clicks once, submits, and waits for game-state advancement. Retries are bounded; an unconfirmed choice keeps normal plays paused and saves a debug bundle. Multi-card and opponent-graveyard choices remain outside this path.
- A card in hand is located by sweeping the mouse across the hand row until Magic Arena logs a hover naming it — the client never says where a card is drawn, so this is the only way to find one. When that sweep failed, all three retries used to re-run the *identical* 1000 px/s pass, each taking exactly 2.0s. Retrying at the same speed cannot find what the first pass missed: a hover is only logged after a client → server → log round trip, so it is not synchronous with the mouse, and a fast sweep can cross a card without one ever being emitted. Observed live: the bot decided to play a land on nine consecutive decisions, executed none of them, passed priority every time, kept an empty board from turn 2 to turn 6 and sat there until Arena's 150-second inactivity timer expired twice. Each retry now sweeps slower and in finer steps (10px/10ms → 8px/18ms → 7px/24ms). Only a failing sweep pays for it — the loop stops the moment the target card is hovered, so a healthy hand still resolves at the original speed (measured live: 0.45–1.40s per card). Note that a sweep can also fail for a reason no pacing fixes: see "Arena's Unity 6 update" below
- Hover lines are read only from log entries that actually describe a hover. A game-state message is packed with object ids and describes no hover at all, yet the old parser fell through to a generic nested search and then a regex over the whole line, so the scan could adopt an unrelated object as "the card under the cursor" and clear its cast suppression. Note that seat filtering is deliberately *not* attempted: the incoming hover shape carries two complementary seat fields and which one identifies the player doing the hovering could not be established from the logs, while a filter with the polarity inverted would discard the bot's own hovers and keep only the opponent's — turning an intermittent failure into a permanent one. It is safe to leave open because instance ids are unique per game, so a foreign id can never match the card being looked for; the sweep simply continues. Measured over a 21 MB `Player.log`, every one of the 1382 bare `"objectId"` hover fragments — the shape behind roughly 97% of identifications — originates in an *outgoing* client message, i.e. the local player's own hover
- A second copy of a legendary permanent already on our board is never cast. The action is legal, so Magic Arena offers it and the AI used to pick it — and then the client raises its own "Are You Sure?" confirm, which no game message announces. Observed live: the cast never lands, the hand sweep retries three times, `_dismiss_are_you_sure_if_present` answers No (the right default for a question the bot did not understand, so the loop cannot resolve itself), and the card is finally written off as unreachable although it was in hand the whole time — around 20 seconds of the inactivity timer per decision. Confirming would be no better: the copy resolves and the legend rule immediately bins one of the two, leaving the same board and a card fewer. The check needs no card database — the game state carries `superTypes` on the objects themselves (on hand objects too, not just battlefield ones) and matches the copies on `name`, which is a title id shared by every printing of a card, so an older reprint of a legend is still recognised where a grpId comparison would miss it. It fails open in both directions that matter: an unknown battlefield zone or a card with no supertype information means "cast it", because refusing a second Llanowar Elves, or refusing every cast on the turns Arena has not re-declared its battlefield zone, would cost far more than the loop being avoided. Known gap: land plays go through a separate path that never sees the board, so a duplicate legendary *land* is still cast — no starter or precon deck the bot farms contains one
- Modal card windows are handled: the library browser from "search your library for …" (e.g. Circuitous Route) and the card-ordering window that follows it. The bot reads how many cards it may take and which ones are legal straight from the game's own request — Magic Arena pre-filters the browser to valid choices, so it takes the required number and confirms. Everything behind such a window is unreachable, so while one is open no other move is attempted; previously the bot hunted the hand row for a card it could not click and idled until the game was conceded. The result is verified against the client's own response, and a partial answer is logged as an error rather than passing silently.
- Arena's "Report a Player" dialog is detected and **cancelled** whenever the queue stalls. It is not a game prompt and no game message announces it, so the bot is blind to it: while it is up every screen probe fails in silence. It happened three times over 2026-08-21/22; on the last one it opened on a match-end screen and the queue loop clicked Play into it for 28 minutes, with a won match sitting unclaimed behind it. That time there was no logged click for six minutes beforehand, so the trigger is unknown and guarding a click path would not have helped — which is why this net is keyed on the symptom instead: when the queue loop has ticked for 90 seconds without a match starting, it probes once per interval. The stall alone would be survivable; what makes it worth a dedicated check is the **Submit Report** button next to Cancel, pointed at a real player. Only Cancel is ever clicked: the dialog is confirmed by its title template before anything is clicked at all, the button is searched for in a band that ends at x=1000 while Submit starts near x=988 and is 285px wide (so no match for it can fit), and the fallback is the measured Cancel position (797, 875), never an absolute desktop click. Nothing but that title-gated dialog is probed on this path, because it runs on Home where a false-positive match on any other button template would click into the live UI. Both templates cross-validate against a second incident's capture at 0.99 (title) and 0.96 (button), well above the 0.80 threshold. A finished match counts as progress and restarts the stall clock — without that the probe fired once after *every* match, measured live on 2026-08-25 three times running and each time exactly 30.02s after the match ended, because the post-match flow owns those 30 seconds and the queue loop does not tick through them. It clicked nothing on any of the three (the title gate held, which is the property the net is built around), but it cost a screen search in the middle of the reward claim, and a net that cries wolf once per match is a net nobody reads

> **Rolled back on 2026-08-23 to the 1.2.1 behaviour of the click path.** Between 1.2.1 and 1.3.0 the hand-sweep, blind-sweep recovery, window-activation, Home-tab guard, target-click diagnostic and Report-a-Player detection landed as roughly 1700 changed lines in one file, and play got measurably worse: matches lost to the rope, Arena's library viewer opening by itself, and combats where the bot stood still. None of it was isolated enough to bisect against a live game, so that whole group is back at its 1.2.1 state, while the changes outside the click path are kept — the legend rule, the verified combat submit, the two-pass account rotation, the serialised screen capture, the Linux credential paste, the PC shutdown and the health check. The problems those reverted fixes described are therefore open again; each one has to come back on its own, with a session of watching behind it, not as a block.
>
> **Back since 2026-08-25:** the Report-a-Player detection, as the first of the group to return on its own. It was the only purely additive one of the six — it changed no existing click behaviour, so it cannot be what made play worse — and it comes back with only its queue-stall hook, outside the click path entirely. Its second, in-match hook hung off the blind-sweep recovery, which is still reverted; if that path ever returns, the in-match hook returns with it.

### Combat

`AI/Utilities/CombatLogic.py` decides both halves of combat, but only one half is allowed to move the mouse. Every choice is made from Magic Arena's own request rather than from our own reading of the rules: `DeclareAttackersReq` lists exactly which creatures may attack, and `DeclareBlockersReq` hands over the complete legal blocker-to-attacker graph — so flying, menace, "can't block" and summoning sickness never have to be worked out here, and a block that is offered is a block that is legal.

**Attacking: the bot swings with everything, deliberately.** This is not a gap waiting to be filled. The bot exists to farm gold, and the measured numbers say attacking selectively would cost gold rather than earn it. Replaying real `Player.log` files through the selective logic showed it would decline to attack at all on 31% of combats (20% even in the narrowest version, which only holds back a creature that dies to an available block for nothing). Meanwhile the match records say a *lost* match takes longer than a won one — 308s against 285s median — so the thing that hurts throughput is a match that will not end, and holding creatures back is exactly what stops matches ending. The logic also assumes the opponent always blocks optimally, which real opponents frequently do not, so it declines attacks that would in fact have connected. `choose_attackers` is kept, and still logs what it would have done, so the decision can be revisited if selection ever becomes cheap enough to be worth it.

**Blocking: the bot blocks.** Two passes. First the blocks that are good on their own terms — kill the attacker and survive, soak damage for free, or trade evenly or up. Then, only if the damage still coming through would be lethal, it keeps adding blocks until it is not, chumping if that is all that is left. It never spends more creatures than survival actually needs. On the same replay, 72% of block prompts would have produced a block and two otherwise-lethal attacks would have been survived, at a cost of 0.78 assignments per match. Live, across 41 matches, the win rate rose from 17% to 27% — but matches also got longer, and wins became slower than losses rather than faster, which reverses the throughput assumption the attacking decision above was built on. Blocking buys survival, and survival is what makes a match run long. Any future change that makes the bot block *more* has to be measured in gold per hour before it is believed.

Blocking is **on**; switch it off with `MTGA_COMBAT_BLOCKS=0` if a session misbehaves. The first live sessions found the weak point, and it was not the one expected. Magic Arena uses a single bottom-right button for "pass priority" and for "No Blocks", and the ordinary decision loop presses it whenever it has nothing to do — so while a block was still being clicked out, that loop would reach past it and submit an empty block step, and combat damage resolved before a single blocker had been assigned. On ordinary turns every block still landed; on turns where the incoming attack was lethal almost none did, because that is when the prompts come thick enough to lose the race, and three matches the blocking logic had already solved were lost that way. Deciding to block now mutes that button until the blocks are submitted. The mute expires on its own, so a block that goes wrong can never leave the bot unable to pass priority for the rest of the match. Finding the creatures needs no new machinery: the attackers were expected to slide off the opponent's row into the middle of the board during the declare-blockers step, and measuring twelve real captures showed they simply do not. They edge forward but stay inside the opponent's scan region, and our blockers ride up but stay inside ours, so blocking reuses both. Every block prompt still writes a `runtime/debug/declare-block-*` bundle (board capture plus the decision and the regions in play, capped at 12 per session) whether or not blocking is enabled; set `MTGA_COMBAT_BLOCK_CAPTURE=0` to stop it.

When blocking runs, each block is clicked out as blocker-then-attacker and the bottom-right button submits afterwards. The whole sequence is on a 12-second budget and every failure path — a creature that cannot be found, a scan that runs long, an exception — still presses that button, because an unassigned blocker only costs a block whereas a combat that never submits costs the rope and then the match.

> Fixed in this version: that submit press is now confirmed instead of repeated on a stopwatch. Magic Arena needs **two** presses of the same button — the first declares, the second submits — and it animates the creatures in between, swallowing anything clicked during the animation. The old code slept a fixed 0.6–1.0s and pressed again blind, which fails invisibly: the click is in our log, the game never saw it. Measured across one session on 2026-08-23: **26 logged `DeclareBlockersReq` against 2 `SubmitBlockersReq`**, the blocker timer at 0.0s combat after combat, three matches lost — one of them while ahead on life. Two separate causes. First, by the time the bot presses, Arena has usually *already* asked with `canSubmitAttackers=true` (measured: 3.4s before the click), so waiting for a *new* declaration timed out on the most common case there is; the submit acknowledgement is now awaited first and verified, and a "No Blocks" that only needs one press is recognised rather than pressed on into the next step. Second, `all_attack()` ran **twice concurrently** — the second press landing 0.46–0.48s behind the first, straight into the animation. The earlier post-mortem read that spacing as a designed retry; it was a parallel invocation, and on one decision three separate threads pressed. A lock now makes the second one a no-op. The whole sequence stays under the 8-second decision heartbeat on purpose, because overrunning it makes the heartbeat re-enter this path and rebuild exactly that concurrency.

> Also fixed: the click log used to lie about this. It was written once by the caller *before* the sequence ran, so it recorded a click for a sequence the lock then skipped, and said nothing about the extra presses that really happened. Every press now logs itself — retries and the blind fallback with their own label — and the last clicks are copied into the `runtime/debug/hand-select-*` bundle, so "what was clicked just before the board got covered" travels with the evidence instead of having to be reconstructed afterwards.

Both decisions are also written to `runtime/logs/bot.log` as `COMBAT_SHADOW` lines and attached to the matching decision snapshot under `extra.combat_shadow`, with the numbers behind them (life totals, incoming damage, what would have been left unblocked). Card data is read from the local database only — a combat decision never waits on a network call — and the computation is wrapped so that a failure inside it cannot stop the bot from declaring no blocks and moving on. Disable the logging with `MTGA_COMBAT_SHADOW=0`.

### Stopping the bot

**Scroll the mouse wheel up or down** to
stop the bot, even while Magic Arena has focus. This works across repeated
Stop/Start sessions; restart the bot app after updating to load this fix.

### Forge Engine (experimental)

**Settings → Bot Behavior → "Enable Forge Engine (experimental)"** hands the
choice of plays to [Forge](https://github.com/Card-Forge/forge), the open-source
MTG rules engine and AI. Enabled by default when Forge is installed in `runtime/forge/`.

At each decision, the current Arena position is rebuilt inside Forge and Forge's
AI is asked what it would do. The bot then carries that out with its own clicks.
Forge decides:

- **Main phase:** which land to play, which spell to cast, which ability of a
  permanent to use. Forge can only choose from the actions Arena is offering.
  A card is cast only if the bot's own rules agree that it can pay for it and
  click through its prompts.
- **Instant speed:** instants, flash cards and abilities in the opponent's turn
  and in combat, while the stack is empty.
- **Attacks:** which creatures attack. Forge's pick, "everything" and "nobody"
  are each played through the opponent's next turn, and the best result wins.
- **Blocks:** Forge's blocks, plus damage control. If an attack would take
  more than 35% of our life, cheap creatures are put in front of it.
- **Targets:** for the abilities and the artifact or enchantment spells Forge
  chose.
- **Scry and surveil:** which cards stay on top. The others are dragged away
  before Done.

The mulligan, and everything Forge has no answer for, stays with the bot exactly
as before. That includes the time while the engine is still starting (about 10
to 20 seconds), a timeout (4 s per decision), a crash, or a choice the bot
cannot carry out. With the box unticked, none of this code runs.

Ticking the box the first time downloads the engine, about 85 MB, Windows only,
into `runtime/forge/`. The package holds a trimmed Java 17 runtime, Forge's
jars and card scripts, and the compiled advisor. The download is checked
against a pinned SHA-256 and survives bot updates. The engine needs about 2 GB
of RAM.

Every decision is logged in `bot.log` as a `FORGE …` line and in full in
`runtime/forge/run/decisions.jsonl`. The Java side's own log is
`runtime/forge/run/advisor.log`.

Finer switches for testing, as environment variables (the first value is the
default):

| Variable | Values |
| --- | --- |
| `MTGA_FORGE` | `on`, `shadow` (log Forge's choice, play the bot's), `off` |
| `MTGA_FORGE_ATTACK` | `select`, `shadow` (always attack with everything) |
| `MTGA_FORGE_BLOCKS` / `_INSTANTS` / `_ABILITIES` / `_LANDS` | `on`, `off` |
| `MTGA_FORGE_MITIGATE` | `35` (percent of life), `off` |
| `MTGA_FORGE_TARGETS` | `abilities`, `all`, `off` |
| `MTGA_FORGE_SCRY` | `on`, `advise` (log only), `off` |
| `MTGA_FORGE_TIMEOUT` | `4` (seconds per decision) |
| `MTGA_FORGE_HOME` | another engine folder (a local build) |

**Building the package** (maintainers): `python tools/build_forge_package.py
--jdk <JDK 17> --maven <Maven 3.9> --pin`. It builds Forge from source at a
pinned commit, compiles `forge_advisor/`, cuts a runtime with jlink, zips it,
and writes the checksum into `ForgeEngine/installer.py`. The zip is published as
the GitHub release asset `forge-engine-v<N>`.

The advisor (`forge_advisor/`) is adapted from
[mtga-play-bot](https://github.com/Funny-Bones/mtga-play-bot).

### Intelligent London Mulligan & Gameplay Enhancements

- **London Mulligan Engine (`AI/Utilities/MulliganLogic.py`)**: Evaluates opening hands based on land-to-spell ratios, mana color alignment (ensures lands produce colors matching spells in hand), early curve castability (Turns 1-3 plays), and automatically selects optimal cards (excess lands or high-CMC spells) to tuck under the London Mulligan rule.
- **Free Forge Autonomous Casting (experimental) (`ForgeEngine/bridge.py`)**: Allows Forge AI to cast legal spells autonomously without requiring base AI double-approval, while safely validating against legend rules and unsupported actions. Configurable via `MTGA_FORGE_FREE` / **Settings → Enhancements**.
- **Deck Selection & Preferred Deck Grinding (`Controller/MTGAController/Controller.py`)**: Select preferred Starter and Historic decks, or exclusively grind a single deck across all queues via **Settings → Deck Selection**.
- **Auto Match-End & Rewards Dismissal (`Controller/MTGAController/Controller.py`)**: Automatically dismisses Victory/Defeat end screens and claims match, quest, event, and daily win rewards in Starter Deck Duels and Historic queues.
- **Threat-Prioritized Counterspells (`AI/Utilities/CounterLogic.py`)**: Scores opponent spells on the stack by threat level (Planeswalkers > Creature bombs > Spells) and CMC, ensuring counterspells target high-impact threats rather than low-value cantrips.
- **Lethal Face-Burn Priority & Landfall Sequencing (`AI/DummyAI.py`)**: Checks for lethal direct damage before creature removal to immediately close games when face lethal is reached; sequences landfall creatures before land drops to capture triggers.
- **Anti-Detection Bezier Mouse (`Controller/Utilities/input_controller.py`)**: Generates natural, human-like cubic Bezier mouse curves with smooth acceleration, micro-jitter, and randomized pre-click pauses to prevent behavioral detection.
- **Per-Monitor DPI v2 & Window Snapping (`vision/window_locator.py`)**: Enables Windows Per-Monitor DPI Awareness v2 to eliminate coordinate drift across scaled monitors, with 16:9 window snapping support.
- **Discord & Telegram Notifications (`notifications.py`)**: Non-blocking background dispatcher delivering live alerts for match results (victory/defeat with duration), rolling win-rate summaries every 10 games, completed daily quests, and account switches. Configurable in **Settings → Enhancements**.
- **Starter Deck Duel Matcher (`AI/Utilities/StarterDeckMatcher.py`)**: Parses active daily quest color requirements and selects the optimal two-color Starter Deck to maximize quest progress, with optional JSON configuration override.

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
   │         (ForgeAI)      ← optional: asks Forge first (ForgeEngine/, a Java
   │                          process over stdin/stdout); controller.decision_hooks
   │                          lets it choose attackers, blocks, targets, scry
   │
   ├── LogReader            ← Reads Player.log continuously, parses GRE messages
   ├── state_machine        ← Tracks bot state: HOME / IN_GAME / PLAY_MENU / ...
   ├── actions              ← Declarative action specs (navigate, click, verify)
   ├── vision               ← Screen capture (mss) + template matching (OpenCV)
   │    └── window_locator  ← Finds the MTGA window (Win32 / xwininfo / anchor search)
   └── input_controller     ← Sends mouse/keyboard input (pyautogui / pynput / ydotool)
```

**Key design principle:** `Player.log` is the primary state source — the bot reads what MTGA reports rather than inferring state from screenshots. Vision is used only to verify that clicks landed and to locate buttons when coordinates are uncertain.

> Fixed in this version: a line of `Player.log` is only read once MTGA has finished writing it. One line carries *several* GRE messages and can be tens of kilobytes — 27804 characters and nine messages in the case that cost a match on 2026-08-23, 56 KB elsewhere — so the write is not atomic and `readline()` returned the first 3996 characters of it. That fragment matched the game-state pattern, became the newest line for it and then failed to parse, taking every message behind the tear with it: here the `ActionsAvailableReq` that passed the turn back to the bot. MTGA had nothing further to write, because it was waiting for the bot, so nothing arrived to correct the picture: for 16 seconds the bot believed it was still turn 6 with the opponent to act and a spell on the stack, while the real game sat in the bot's turn 7 main phase with the bot's own clock running. From the outside that is indistinguishable from a freeze. An unterminated line is now buffered until its terminator arrives, and only released unparsed after five seconds of complete silence — measured from the last data, not from the start of the line, so a slow write that is still making progress is never cut open, while a genuinely last line (MTGA exiting mid-write) is still not swallowed. The same tear had a second entrance: seeking to the end of the log can land *inside* a line being written, and that tail arrives newline-terminated, looking whole while starting mid-JSON — the first line after the seek is therefore discarded unless it starts like a real one. Both cases say so in the log (`LOG_LINE_UNTERMINATED`, `LOG_LINE_TAIL_DISCARDED`) and the watchdog counts them as `log_line_torn`; before this they were completely invisible.

**Card data** (`AI/Utilities/CardInfo.py`) is loaded from a local export of MTGA's own card database and delta-synced with the Scryfall API for missing entries. Cards Scryfall does not have at all — Arena-only tokens, Alchemy rebalances — are remembered as such and not requested again for 30 days, and the whole startup sync is capped at 10 seconds, so an unreachable Scryfall cannot stall the start. A card that a later Arena update ships locally is dropped from the retry list without a request.

`Controller` and `AI` each carry an informal interface file (`ControllerInterface.py` / `AIInterface.py`) documenting the methods `Game.py` relies on. Only one Controller (`Controller/MTGAController/Controller.py`, the real MTGA controller) and one AI (`AI/DummyAI.py`) are actually wired up today, and `Game.py` is typed directly against those two concrete classes — the interface files are documentation of the expected contract, not a runtime abstraction that makes swapping either one a drop-in change.

### Static type checking

A [Pyright](https://microsoft.github.io/pyright/) pass (developer-only, not run by the bot or its launchers) covers the AI decision layer -- `AI/`, `Game.py` and `Controller/Utilities/GameState.py` -- in `basic` (non-strict) mode, scoped via `pyrightconfig.json`. Run it from an activated project `.venv`:

```
python -m pip install -r requirements-dev.txt   # once, installs pyright
python -m pyright                               # uses pyrightconfig.json
```

(Activate first: `source .venv/bin/activate` on Linux/macOS, `.venv\Scripts\activate` on Windows.)

The rest of the codebase (`Controller/MTGAController/Controller.py`, `ui.py`, …) is out of scope for now.

GitHub Actions runs the same Pyright check automatically for every push and pull request (`.github/workflows/pyright.yml`).

### Change notes on the website

A push to `main` that changes `version.py` runs `.github/workflows/notify-website.yml`. It sends a `bot-version-bump` dispatch to the website repo, which adds the new version to its change notes page. The text comes from the "Change notes:" section of the bump commit message (format in `CLAUDE.md`). Needs the secret `WEBSITE_DISPATCH_TOKEN`: a fine-grained token with Contents read and write on `Barrylim366/red-lotus-bot-website`.

### Prompt-injection screen

PR and issue text is screened for prompt injection aimed at AI agents (`.github/workflows/injection-screen.yml`, using the [Jev](https://docs.typesafe.ai/api) classification model). Flagged items get the label `possible-injection`; `injection-unscreened` means the screen could not run; `injection-screened` means everything screened so far came back clean. CodeRabbit's comments and reviews (`coderabbitai[bot]`) are not screened: they quote diffs and address AI agents by design, so they were flagged as false positives. Setup and details: `tools/injection_screen.py` and `CLAUDE.md`; agent PR reviews start with `tools/screen_pr.py <nr>`, which rescreens the PR at review time.

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

### Live log in the terminal

Everything written to `bot.log` also scrolls by in the terminal window the bot was started from (`start_windows.bat` keeps it open, minimized, in the taskbar) — including the `FORGE ...` lines that say what the Forge engine decided. Lines longer than 400 characters are shortened there (the raw Player.log dumps); `bot.log` keeps them whole. The console is written from its own thread, so a slow or paused console window (selecting text in it pauses output) never holds up the bot; if it falls far behind, it skips lines and says how many. Started without a console (e.g. `pythonw`), nothing is shown. Turn it off with `MTGA_CONSOLE_LOG=0`.

### Is it still working? (`tools/health_check.py`)

```
.venv/Scripts/python.exe tools/health_check.py
```

One screenful, strictly read-only, safe to run mid-match. Written for watching a
long unattended run, where the newest log line is a bad witness: a bot stuck in a
retry loop writes plenty of lines while achieving nothing, and a frozen one writes
none at all — both read as "running". So it prints the three things that actually
decide whether to intervene: **how long ago** it last decided and last clicked,
what it has **achieved** (matches, wins, gold per account, account rotation), and
a **count per failure signature** — because the count over time is what separates
known flakiness that self-heals from something new and escalating.

When something goes wrong the bot saves debug bundles under `runtime/debug/<timestamp>/` containing screenshots, the log tail, and a state dump. The entire `runtime/` tree is gitignored.

`MTGA_RUNTIME_DIR` moves that whole tree — logs, debug bundles, records, status, card cache — somewhere else, which is how the test suite stops writing into the live bot's artefacts. It used to: one suite run put around a thousand lines of fixture output ("CAST_FAILED: card 999 …") into `runtime/analysis/history.log` while a real match was being played, and truncated `runtime/logs/bot.log`, because the tests build real Controllers and a Controller's constructor resets the status file. Those artefacts are the first thing you read when debugging live behaviour, so polluting them costs exactly when it matters. The catch found while fixing it: `bot_logger` resolved its log path once, into a module constant at import time, and is pulled in transitively before any test code runs — so the override could not reach it and the redirect silently did nothing for the biggest offender. Runtime paths are therefore resolved per call, never frozen at import, and `tests/test_runtime_isolation.py` asserts it by writing a real log line and checking the repo's `bot.log` was not touched.

### Decision snapshots

For post-mortem debugging of *play* decisions (why did the bot pass, attack, or pick that target?), the bot records one structured snapshot per decision under `runtime/debug/matches/<utc>_<matchId>/`:

- `snapshots.jsonl` — one JSON record per decision: turn/phase, both life totals, your and the opponent's permanents (name + power/toughness + tapped/attacking), your hand, the stack, the available actions, and the move the bot chose (`"exception"` if the decision crashed). Covered decision points include the main play decision plus mulligans, target selection, declare-blockers, pay-costs, casting-time (kicker/modal) choices, scry/surveil, and modal "choose one" prompts (each tagged with a `decision_kind`).
- `board.txt` — the same records rendered human-readable, one block per decision.
- `match.json` — per-match header/footer with the result and any card IDs that couldn't be resolved to names offline.

Card names are resolved from the local card database only (no network calls on the decision path), so this never delays in-game actions. Recording is on by default and writes only per decision (a few dozen small records per match); disable it with `MTGA_DEBUG_SNAPSHOTS=0`, or add the full raw game-state dump to each record with `MTGA_DEBUG_FULL_STATE=1`. Old match directories are pruned automatically (newest 30 kept).

### Click verification log

For debugging the *visual* layer (bot clicked the wrong screen position, or the right position at the wrong time), every mouse click is logged as one line in `runtime/debug/clicks.jsonl`: the purpose, the coordinates, the arena-mapping `source`, how old the arena-window fix was (`region_age_sec`), a `risky` flag (set when the arena window was lost and the click fell back to a blind absolute desktop coordinate), and a `decision_seq` that ties the click back to the game-state snapshot that caused it. The same context is appended to the `[CLICK]` lines in `bot.log`. On by default; disable with `MTGA_DEBUG_CLICKS=0`. The file rotates at ~5 MB (one `.1` backup kept).

The per-failure debug bundles under `runtime/debug/` (screenshots + state for mulligan/hand-select/navigation/etc.) are now capped automatically (oldest pruned, newest 60 kept) and their full-screen captures are saved as JPEG, so the debug folder no longer grows unbounded.

### A monitor left of or above the primary: every capture showed the wrong screen

Fixed: on a desk where a display sits **left of or above the primary** in the OS display arrangement, every screen capture the bot took showed *that* display instead of the Arena. **Check Arena Setup** and **Start** then fail with *no known UI anchors matched* on a plain Home screen — measured on Windows 11 with a three-monitor desk (one display at x = -1920): every attempt ended `anchor_not_found` with a best score of 0.58 against the 0.78 threshold, while a capture of the Arena window itself (PrintWindow) matched the Home anchor at 0.98 the whole time. The debug bundle gives it away: `runtime/debug/arena-setup-<stamp>/arena_region.png` holds the neighbouring monitor (a browser, the bot's own windows) at the Arena's coordinates.

The cause is a mismatch between the capture and its readers. The bot grabs the whole virtual screen (`mss.monitors[0]`), whose first column is the leftmost monitor — a *negative* screen coordinate on such a desk — while everything that reads those frames (`capture()`, `pixel_check()`, the window locator's full-frame readers) indexes them from the primary monitor's origin (0, 0). With no monitor left of or above the primary the two coincide, which is why most desks never see it. The grab is now cut at the primary origin before anything reads it (`VisionEngine._cut_at_primary_origin`), in the grab's own pixels so a macOS Retina capture (twice the logical size) is cut by the same amount, and then scaled back to logical size by that same ratio, read off the grab itself, so every monitor the frame still holds keeps its size (the old yardstick, `pyautogui.size()`, is the primary monitor alone and could squeeze a frame of several monitors into it). Monitors right of and below the primary are unaffected, and so is the Linux screenshot-tool fallback. The Arena still has to sit on the primary monitor or one right of / below it: a window on the monitor *left of* the primary has negative screen coordinates and is not covered by this. Since that is easy to hit by accident, Arena Setup now says so (`window_left_of_primary`: *MTGA window is ... on a monitor left of or above the primary monitor*, with what to do) instead of failing the anchor match, which looked like a wrong screen or a wrong language. The Controller treats that result like the other rejected geometries (wrong size, off-screen): no cached coordinates are reused and no recovery click is made while the window sits there.

Also fixed: the Arena setup check reads the *screen* at the window's rectangle, not the window, so an Arena lying behind another window — the bot's own always-on-top window, a browser — failed every anchor with a capture of whatever covered it. The check now raises the Arena window first (`focus_mtga_window()`, 0.3 s to let it repaint), as the Controller already did before every cast, so pressing Start with the Arena covered no longer reports a wrong screen. On macOS and Linux the focus step does nothing, as before.

### Different scaling on a secondary monitor (Windows)

Arena Setup blocks starting the bot when Arena is on a secondary monitor with different display scaling and the bot's calling thread is not per-monitor DPI aware. This combination can pass screen detection while mouse clicks land in the wrong place. The error reports both scaling percentages and asks you to move Arena to the primary monitor and press Start again. Secondary monitors with the same scaling, and per-monitor-aware operation, remain supported. Unsupported scaling also prevents recovery paths from reusing cached click coordinates. If Windows cannot report the monitor DPI, this check cannot establish a mismatch and keeps the existing detection behavior.

### Window focus during a hand sweep

To play a card the bot sweeps the mouse across the hand and waits for MTGA to log a hover; when that never arrives it gives up with `SCAN_STOPPED: No hover update before bounds` and the card is not played. Unity delivers no hover events to an *unfocused* window, so a lost foreground looks exactly like a hand that cannot be found.

`focus_mtga_window()` calls `SetForegroundWindow`, but Windows silently refuses a foreground steal from a background process (it returns 0, no exception) — and the function reports success either way, so the bot cannot tell the two cases apart. That is measured, not fixed:

- `bot.log` gets `FOCUS_MTGA_NOT_FOREGROUND: SetForegroundWindow returned …, foreground is hwnd=… title='…'` whenever the focus did not actually land on MTGA. Only the failure is logged (this runs on every cast attempt), so any occurrence is worth reading.
- each `hand-select-*` debug bundle now carries `mtga_foreground` (`hwnd`, `title`, `is_mtga`) captured at the moment the sweep gave up.

A `SCAN_STOPPED` together with `is_mtga: false` means the sweep never reached the game at all. Note that clicking into another window while the bot plays is enough to cause this.

Measured across 31 such failures: focus was **never** the cause (`is_mtga: true` in all 31). The mean brightness of the hand zone in each bundle's `arena_region.png` splits them into three groups instead, and two have a known cause that the bot now clears:

The cast scanner also avoids reactivating MTGA when its verified `MTGA.exe` window is already in the foreground. Redundant Windows `ShowWindow`/`BringWindowToTop`/`SetActiveWindow` calls are the leading suspect in a captured long-session failure where cast sweeps received no events, but the select-N scanner reported every card seconds later across the same hand row. The bot still requests focus when another application owns the foreground or the foreground process cannot be verified.

Starter-event navigation does not start while matchmaking or gameplay owns the screen. Its announcement-recovery path also rechecks after the slow image probe and immediately before ESC, covering a match transition during that probe or its focus-settle window. This prevents ESC from opening Options underneath the mulligan handler, where the fixed Keep Hand coordinate would otherwise click the `Report Player` link and strand the match behind that dialog.

| brightness | share | cause |
|---|---|---|
| 2.8–4.4 | 5/31 | Arena's "Report a Player" dialog, open mid-match |
| 16–31 | 5/31 | a card-selection overlay (e.g. a graveyard browser opened by a "sacrifice a creature" cost) left open, Done unanswered |
| 68–104 | 21/31 | board fully visible, cards on the scan line — still unexplained |

After the *first* failed sweep of a cast, the bot probes for both and clears them: `REPORT_DIALOG_DETECTED` (Cancel is clicked, never Submit) and `STRAY_DONE_DISMISSED`. Both fire only when their own template matches on screen, so a clear board costs one scan and no click. The selection overlay is otherwise invisible — it reports `casting_time_options_open: False` and logs no `CASTING_TIME_OPTION_UNANSWERED`.

A repeated `SCAN_STOPPED` does not resolve itself: after three attempts `CAST_FAILED` gives up and the turn moves on without the card, or the match dies to the 150s timer (`ResultReason_Timeout`).

The 21 unexplained failures above turned out to be the beginning of the next item.

### Arena's Unity 6 update: a warped cursor hovers nothing

On 2026-08-26 Magic Arena moved to Unity 6 (`Initialize engine version: 6000.3.14f1` in `Player.log`, up from `2022.3.62f2`; Direct3D 11 → 12) and every hover-based scan in the bot stopped identifying anything: `SCAN_STOPPED`, `HAND_SELECT_STOPPED`, `CAST_UNAVAILABLE`, and a bot that runs its mouse along the hand row all game without ever playing a card. That session's log held **0** of the bot's own hovers against 1507 in the previous session on the old client.

pynput moves the cursor on Windows with `SetCursorPos`, which teleports the pointer without producing any device input. Unity reads the mouse through Raw Input, and the new client no longer counts a teleported pointer as hovering. Counting hover lines over 12 stops on the hand row:

| | dwell 0.02s | 0.10s | 0.25s | 0.40s |
|---|---|---|---|---|
| warp (`SetCursorPos`) | 0 | 0 | 0 | **0** |
| injected motion | 0 | — | — | **8** |

`_Win32MouseMotion` in `Controller/Utilities/input_controller.py` now moves the cursor with `SendInput` instead — absolute coordinates over the virtual desktop, so pointer speed and acceleration cannot make a sweep drift, followed by a `SetCursorPos` correction so clicks still land on the exact pixel they were aimed at. It is used for movement only (clicks and keystrokes already went through `SendInput`), on Windows only, and falls back to the old warp if it cannot be set up. With it, the original 10px/0.01s sweep reports every card in the hand again, so the sweep pacing was left alone.

Two things this was *not*, both of which cost a round of investigation:

- **not the sweep speed.** An early measurement nudged the cursor with small relative `mouse_event` calls and only saw hovers above a ~0.30s dwell, which made the pacing look like the cause. Pacing the sweep down to 90px/0.35s changed nothing at all; proper absolute motion reports every card at the original speed.
- **not the log format.** The new client writes our own hovers as *outgoing* `ClientToGREUIMessage` blocks, pretty-printed across several lines, and the existing parser already reads that shape. Incoming `onHover` messages carrying an opponent's `seatIds` keep arriving throughout, which is why the log never looks empty.

Diagnosing this class of failure: check `Initialize engine version` at the top of `Player.log` before blaming bot code, and count `"objectId"` occurrences in it — a session with none of them is an input problem, not a scan problem.

### Session diagnostics

The session watchdog records `combat_recovery_failed`, `submit_failed`,
`pay_costs_unresolved`, `chooser_unconfirmed`, and `unsupported_cast` in
`runtime/analysis/alerts.log` and per-match records. Recovery attempts and
individual image-search misses are kept in history but are not counted as
failures unless recovery is exhausted or no submit control is recognized.
After a session, inspect those alerts, the matching lines in
`runtime/analysis/history.log`, and any
`runtime/debug/hand-select-*` bundle with reason
`opponent_battlefield_select_failed`. A stall concede line includes the local
seat, both priority fields, phase, step, and prompt. The target timeout line
includes the cursor position and number of hover events seen before stopping.

Unsupported casts are counted once per card name per match even if the decision
loop considers the same card repeatedly. Stall concessions, target scans, and
quest reroll outcomes remain available in ordinary history logs. Temporary soak
recorders and their dedicated tests have been removed; regression tests for the
recovery behavior remain.

### Concede screenshots

Bot-triggered stall and inactivity-timer concedes automatically create an incident
under `runtime/concedes/` (or `MTGA_RUNTIME_DIR/concedes/`). Each incident contains
up to two `attempt-N.jpg` images and `incident.json` with the reason, session/match
IDs, attempt and capture status, and final sequence outcome. The newest 30
incidents are retained independently of other debug bundles.

Images contain only the verified Arena client area, with solid masks over both
player-name areas. They fit within 1280×720, preserve aspect ratio, and use JPEG
quality 80. No original desktop image, player names or raw log excerpts are saved
in these bundles. Masking supports the normal 16:9 in-match layout; arbitrary
overlays and other diagnostic files are outside this feature's masking scope.
Missing windows, unsupported layouts and capture/write failures are recorded
without blocking concession. Existing capture-backend latency still applies.
`match_completion_observed` records completion after an attempt, not proof that
the bot's concede caused the result. Log entries beginning `CONCEDE_INCIDENT_`
link attempts to their folders; audit summaries also index the retained metadata.

### Audit history CSV

The `audit-logs` summary updates the gitignored `audit-history.csv` in the project
root, with one row per audited session. It stores UTC dates/timestamps, completed
match counts, wins/losses, win rate, confirmed concede counts/attempts and concede
rate for later plotting. Rates are fractions (0–1) using completed matches as the
denominator. Repeat audits update the session's row; running sessions are marked
provisional. Unknown concede rates are blank. Previous established rates survive
log rotation when the completed-match count is unchanged. Historical sessions
use first/last match times when full session times are unavailable.
Pass `--no-csv` for a read-only summary or `--csv <path>` for another destination.
The audit also reports the deck for each game and writes gitignored
`audit-matches.csv`, with account, result, UTC game times, deck label and log
evidence. Completed selections are marked `logged_selection`; selections carried
into later games are `inferred`. A saved hand with at least three distinct known
nonland cards fitting exactly one local Starter list can also supply an inferred
deck, with snapshot evidence, when Starter event context is logged. Missing or
unconfirmed deck choices without supporting snapshots are `unknown`.
Account switches and bot restarts clear carried selections. Repeat audits retain
previously established deck evidence after log rotation when the game is unchanged.
Use `--matches-csv <path>` for another destination; `--no-csv` disables both CSVs.
Invalid saved Gold values are skipped so they do not prevent the audit report.

## License

GNU General Public License v3.0, see [LICENSE](LICENSE). Forge and the advisor
built on it are GPL-3.0 too.

## See also on

[elitepvpers](https://www.elitepvpers.com/)
