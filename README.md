# Red Lotus Bot

Automated MTGA bot with UI, calibration, account switching, and quest-based deck selection.

## Requirements

- CachyOS (Linux) with Steam + Proton
- Python 3.10+
- MTG Arena installed (Steam)
- Python packages:
  - pyautogui
  - opencv-python (needed for image matching confidence)
  - pillow
  - pynput

Install packages:

```
pip install pyautogui opencv-python pillow pynput
```

## Quick Start

1) Start the UI:

```
python ui.py
```

Or use the executable launcher (creates/uses `.venv` and starts the UI there):

```
./start_ui.sh
```

For double-click start in KDE/Dolphin, use:

```
./MTGA_Bot_UI.desktop
```

This launcher always calls `start_ui.sh`, so the UI runs from the project `.venv`
with `pynput` from that environment.

UI logo asset: `ui_symbol.png` (project root).
The main window now uses a ttk-based dark theme with centralized design tokens in `MTGBotUI._build_ui_theme()`:
- Background/surface layering (`#0F1115` + `#151A21`) with a subtle rounded card shell
- Single accent color (`#C8141E`)
- System-first font stack (`Segoe UI Variable`/`Segoe UI`/`Inter`/`Arial`)
- Compact hierarchy: centered logo, title, and uniform button grid
- Unified button states and spacing grid (Start primary uses subtle green `#1F3A2D`, soft secondary buttons, muted disabled state)
- Stop button enabled only while the bot runs
- Status shown as plain text (no box background, no border frame)
- Outer dark card rim/shadow was removed for a cleaner edge (card surround now matches surface color)
- Stop button uses a subtle red background treatment
- Button focus outline is neutralized (no red focus ring on the last clicked button)
- All submenu/pop-up windows now inherit the same bluish-dark UI palette via centralized submenu theming
- Main menu now includes a dedicated **Current Session** button between **Calibrate** and **Settings**
- **Current Session** opens in the same submenu position logic as **Settings** (below main window, ~5 mm gap, aligned X)
- Current session stats (`X Min till Account Switch`, `Games played`, `Win`) were moved out of **Settings** into **Current Session**
- Settings window uses the same width (`460`) with a reduced height (`230`) focused on account/actions tools
- **Manage Accounts** now supports up to 10 accounts with editable `Name`, `Email`, `Password` rows and dynamic play-order slots
- Saving accounts creates/updates one folder per account under `Accounts/` and writes `credentials.json` inside that folder
- Password input fields in **Manage Accounts** are masked (`*`) while typing
- In **Manage Accounts**, the **Switch account (min)** save button is placed directly next to `0 = off` with a prominent blue style
- Manage Accounts window height was increased to avoid bottom content clipping
- Manage Accounts window height is set to `920x760` so bottom action buttons are fully visible
- Manage Accounts action buttons use compact, unified sizing; the switch-time `Save` button now matches the other button size
- Buttons were reverted to the classic UI styling and original color direction
- Main menu window size is fixed (width and height are both non-resizable)
- Main menu top-left corner is fixed at screen coordinates `x=18`, `y=24`; Settings follows the main window position
- During bot startup, the UI shows an indeterminate loading bar with the label `Loading Carddata` until initialization finishes
- Fixed a startup regression in `ui.py` caused by a mismatched theme token in the loading bar style
- `Status: Stopped` now uses a subtle red text tone

Standalone runnable UI example (single file): `red_lotus_ui_example.py`.


2) Calibrate buttons via **Calibrate**:
   - Calibration uses `pynput` (the optional `Capture (slurp)` button was removed).
   - Required: keep_hand, queue_button, next, concede, attack_all, opponent_avatar, hand_scan_p1, hand_scan_p2, assign_damage_done
   - Logout flow uses: log_out_btn, log_out_ok_btn

3) Current Session:
   - Opens a session window with live stats in green text:
     `X Min till Account Switch`, `Games played`, `Win`.

4) Settings:
   - **Manage Accounts** opens a window for:
     - **Switch account (min)**
     - Up to **10 accounts** (`Name`, `Email`, `Password`)
     - **Account Play Order** (up to 10 positions)
   - Use **Save Accounts** to create/update account folders and credentials JSONs.
   - Use **Save Order** to persist the play order.
   - **Record Action** opens a window for **Record** (uses F8 to stop) and **Show Records**.
     Saved records include per-action timestamps (`ts`) in `recorded_actions_records.json`.

5) Start Bot.

Stop bot any time with **Mouse Wheel Down**.


## Account Switching

- Accounts are managed via **Settings -> Manage Accounts**.
- Each account is saved in its own folder under `Accounts/` and includes `credentials.json` in this format:
  - `{ "<AccountName>": { "email": "...", "pw": "..." } }`
- `Accounts/` is gitignored by default so local account folders are not pushed to GitHub.
- Switch happens when the timer expires and the bot reaches a safe screen.
- Logout/login uses recorded action sequence + credentials injection.
- If the client is in the Store scene during fallback logout, the bot logs the last scene and presses ESC twice to reach the options menu.
- SelectN stack/trigger selections are delayed while `pendingMessageCount > 0` or the bot is not the decision player to avoid hover spam.
- SelectN stack/trigger selections map ability instance IDs to their parent card IDs for hover-based selection when needed.
- Login wait before typing credentials: 5 seconds.
- Post-login wait before running the recorded action: 20 seconds.
- Order follows **Account Play Order** in Settings; the first entry is used as the next switch target.
  After each switch, the bot advances to the next entry in the list.
  Changing the order resets the cycle to the first entry.
  The cycle index is treated as the next position to use (not the last used one).

## Quest-Based Deck Selection

After account switch the bot:
1) clicks Play -> Find Match -> Historic Play -> My Decks (image matching)
2) parses quests from `Player.log`
3) selects a deck image from the switched account's folder that best matches quest colors
   - If no guild/color quest exists but a creature quest is present, it selects `C.png`
   - If no guild/color quest exists and `Quests/Quest_Fatal_Push` is active, it selects `B.png`
   - If no guild/color quest exists and `Quests/Quest_Raiding_Party` is active, it selects `C.png`
   - If no quests are available, it selects a random deck image
   - If a forced file (`B.png`/`C.png`) is not present, it falls back to the existing selection logic

Deck images are matched by filename letters (e.g. `RG.png`, `WU.png`, `R.png`).
Creature quests use `C.png`.
If the planned account deck image is not found after login, the bot retries deck selection
across other configured account folders and logs account/deck mismatches in `bot.log`.
`Buttons` is kept in Git, but its contents are ignored (see `.gitignore`). Keep your local images there.

## Casting Logic

In main phases the bot tries to use as much available mana as possible across all castable spells:
- Cast feasibility is based on effective action mana costs (`availableActions[].manaCost`), so discounted costs are respected correctly.
- It chooses casts that maximize total paid mana this turn.
- If multiple options spend the same total, it prefers a single higher-cost spell
  over multiple cheaper spells.
- If CMC is tied, it prefers: creature -> instant -> sorcery -> enchantment -> other.
- Multi-spell plans are validated against color requirements, not just CMC.
 - Convoke is supported using untapped creatures as colored mana sources.
- Heavily discounted high-mana-value spells are prioritized when castable.

## Decision Safety

The bot defers decisions while the game reports pending messages to avoid acting
mid-resolution or while the UI is still busy.
It also auto-confirms mana payment prompts when MTGA requests pay costs.
PayCosts prompts with non-mana cost selection (for example discard-a-card while casting)
are now handled directly via `GREMessageType_PayCostsReq` cost selection, instead of relying
only on `SelectNReq`.
When `GREMessageType_DeclareAttackersReq` arrives, any temporary pay-cost pause window is
cleared immediately so combat prompts are not blocked by stale pay-cost timing.
DeclareAttack prompts now arm a bounded combat-recovery fallback (`COMBAT_RECOVERY_ARMED`),
which can force `all_attack + submit` up to two times if the bot is still stuck on
`Phase_Combat / Step_DeclareAttack`.
Combat-recovery events are logged with explicit markers:
`COMBAT_RECOVERY_ARMED`, `COMBAT_RECOVERY_ATTEMPT`, `COMBAT_RECOVERY_CLEAR`.
If the bot is paused by a PayCosts prompt and no new game-state message arrives, it now
automatically retries the decision loop shortly after the pause window so `Next/Pass`
does not get stuck.
In main phases, decisions are also deferred while the stack contains objects.
If the bot is the decision player, `pendingMessageCount` is zero, and a `Pass` action is available, it will still resolve priority even with stack objects present.
On its own turn, the bot waits 2 seconds once per turn before starting actions like hovering, casting, or clicking.
SelectN prompts wait 3 seconds before the bot starts selecting cards.
While a SelectN selection is in progress, other decisions are paused to avoid extra clicks.
SelectN pauses and clears are logged in `bot.log` to trace when decisions resume.
SelectN submission is always attempted with a forced click, with retries logged if needed.
SelectN prompts pause decisions for a short window after submit, and retries are rate-limited to avoid duplicate submits while discards resolve.
If the local seat ID is temporarily unknown, stack resolution can still proceed when a Pass action is available.
SelectN submissions are only clicked when a selection is active, and the bot will retry submit if the selected card(s) remain in hand. It also retries the submit click a few times if the prompt doesn't advance. Hand-selection fallback scans above the hand are enabled only for discard prompts.
Resolution-based SelectN prompts use a double-click on the first attempt and allow retries even when `pendingMessageCount > 0`.
If the selected card remains in hand, resolution SelectN will re-select and re-submit a few times before giving up.
If `Buttons/submit_btn.png` exists, Submit clicks use image matching before falling back to the calibrated coordinate.
Resolution SelectN waits for the stack to clear before starting selection.
Discard (SelectN) prompts allow a single delayed retry when hand zone data is missing and avoid aggressive reselect loops.
SelectN pending-state clear is now robustly initialized before early abort branches, avoiding handler crashes during discard/stack prompts.
SelectN resolution waits for the stack to clear, but has a timeout to avoid indefinite stalls and clears on match end/reset.
If SelectN IDs are not in hand, the bot can now fall back to pending/stack item scanning.

Own timer ("sand clock") status is parsed from `Player.log` game-state timer data (only for
the local player seat, not opponent timers).
Timer transitions are logged as:
`MY_TIMER_START`, `MY_TIMER_WARNING`, `MY_TIMER_CRITICAL`, `MY_TIMER_STOP`.

## Card Data Updates

On startup:
- MTGA card DB export refreshes `cards.json` if the local MTGA data changed.
- Scryfall bulk delta check fetches new Arena IDs and merges missing cards.
If `cards.json` is missing on first run, it will be generated automatically.

Fallback:
- `missing_cards.json` tracks cards encountered in matches but not in `cards.json`.

## Logs

- `bot.log` – main bot debug
- `human.log` – high-level actions
- `bot_gui_subprocess.log` – UI subprocess log (if used)
- `Player.log` default path: `/home/barrylim/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`
- Hover logs are suppressed by default and only enabled during selection scans.
- A one-line match summary is logged at match completion.
