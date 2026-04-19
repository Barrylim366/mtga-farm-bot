# Burning Lotus Bot
<img width="429" height="823" alt="githubscreen" src="https://github.com/user-attachments/assets/ac3ec57b-45de-4a22-aebe-0bcb3db90ae0" />

Automated MTGA bot with UI, calibration, account switching, and quest-based deck selection.

## Requirements

- macOS 12+, Windows 10/11 or Linux (tested on Debian and CachyOS so far)
- Python 3.10+
- MTG Arena installed (Steam)
- Python packages:
  - pyautogui
  - opencv-python (needed for image matching confidence)
  - pillow
  - pynput

Input backend:
- Default is `auto`.
- On macOS, `auto` prefers `pyautogui` (more stable than global `pynput` hooks).
- Optional override via env: `MTGA_BOT_INPUT_BACKEND=auto|pyautogui|pynput|ydotool`.
- On macOS you must allow input control:
  - `System Settings -> Privacy & Security -> Accessibility`:
    enable your terminal app (Terminal/iTerm) and the Python executable used for the bot.
  - If image matching is used, also enable `Screen Recording` for the same app(s).

Install packages:

```
pip install pyautogui opencv-python pillow pynput
```

## Quick Start

1) Start the UI:

```
python ui.py
```

macOS one-click start:

```
./start_ui.command
```

You can also double-click `start_ui.command` in Finder.
The script creates `.venv-macos` automatically (if missing) and installs required packages on first run.

Windows quick test for built-in account switch flow (without starting a full match loop manually):
- Double-click `test_logout_record.bat` in the repo root.
- It runs the current built-in full account-switch path from code (logout + login + post-login handling).
- For logout-only testing, run: `python tools/test_builtin_logout.py`
- The active controller flow in `Controller/MTGAController/Controller.py` again includes queue spam, post-match dismissal, and built-in account switching as one continuous runtime path.
- Account switching now follows the `macOS_Version` logout order again: recorded logout replay first, then `ESC -> LOG_OUT_BTN -> LOG_OUT_OK_BTN` fallback. On Windows, those fallback targets are still mapped through the detected `arena_region` so the sequence stays window-relative instead of clicking raw desktop coordinates.
- The account-switch flow now verifies logout via fresh `Player.log` login-screen markers before typing credentials. If logout does not actually reach the login screen, the switch aborts with a debug bundle instead of typing into the still-open home/options UI. The built-in fallback also retries visible `log_out_btn.png` / `okay_btn.png` templates before giving up.
- Post-match dismiss and other home/options UI actions now use the detected MTGA window center or the last good cached `arena_region` as fallback. This avoids raw desktop clicks like `(1280, 720)` when the Arena window is shifted on the monitor.
- Logout confirm (`OK`) no longer relies on full-screen `okay_btn.png` matching first. The mapped `log_out_ok_btn` click now has priority, and any image-based confirmation retry is limited to a small region around the expected dialog button to avoid false positives on the Home/Play button.
- The logout dialog buttons now use the same low-level click injection pattern as record playback (`move -> left_down -> left_up`) instead of the generic `left_click(1)` helper, because the confirm dialog was visibly hovered but sometimes did not register the click.

## UI Updates

The main window now uses a ttk-based dark theme with centralized design tokens in `MTGBotUI._build_ui_theme()`:
- Start page uses a full-canvas background image loaded from `images/background`
- Single accent color (`#C8141E`)
- System-first font stack (`Segoe UI Variable`/`Segoe UI`/`Inter`/`Arial`)
- UI scale is calculated automatically on each startup based on current screen resolution and applied to the main window and subwindows.
- Compact hierarchy: centered logo, title, and uniform button grid
- Main window/start title now reads `Burning Lotus`
- Main menu buttons are canvas-rendered (no ttk widget box) with rounded edges, subtle inner shadow, stronger visibility (rim/shadow/glow), and fixed body color `#3D130E` slightly more transparent
- Stop button enabled only while the bot runs
- Status shown as plain text (no box background, no border frame)
- No inner center card is rendered on the start page (logo/title/buttons are placed directly on the background)
- Stop button uses a subtle red background treatment
- Button focus outline is neutralized (no red focus ring on the last clicked button)
- Manage Accounts now uses the same background image source as the main UI (`images/background` / `images/background.png`)
- Manage Accounts panel colors now follow the main UI palette (`bg/surface/surface_2/border/text`) with a subtle pseudo-transparency blend against the background image
- Manage Accounts container borders were tuned from cool-blue to warm red tones; panel blend opacity was reduced for a lighter translucent look on the fire background
- Main menu keeps **Current Session** and **Settings** as the only submenu entries
- **Current Session** opens in the same submenu position logic as **Settings** (below main window, ~5 mm gap, aligned X)
- Current session stats (`X Min till Account Switch`, `Games played`, `Win`) were moved out of **Settings** into **Current Session**
- Settings window keeps the main menu visual language: same background image source, centered title, and canvas-rendered action buttons using the shared main button skins
- Settings window size/position follows submenu behavior (`460x430`, opens below main with ~5 mm gap, aligned to main window X)
- **Calibrate** now lives inside **Settings**
- Calibrate uses a background-scene layout like Settings/Record Actions (no large dark outer frame), keeps glow-style action buttons, and opens to the right of Settings with ~0.4 cm gap
- Calibrate follows a split `Capture` / `Verify` scene layout with a vertical divider, `Last Captured` coordinate card, dedicated `Status` card, and footer action row (`Saved Buttons`, `Back`)
- Calibrate remains a fallback path for manual coordinate capture when the out-of-the-box arena detection/verification path is not enough
- Current Session window was restyled to the same fire/main theme (background image, unified panel colors, styled Back button) and now opens aligned below the main window
- Current Session no longer uses a large dark container frame; stats and Back are rendered directly on the background scene, and the dark box-frame around Back was removed
- Current Session stats are now grouped inside a bordered feature-card (`#320a02` fill, `#ff9318` border) with title/body typography aligned to the provided feature-box style
- Current Session card was refined to only show the three session lines (title removed), with rounded corners and semi-transparent dark fill (~44% target opacity)
- Current Session now uses the non-rounded feature-card style again, with no card title and all three stat lines shown in yellow inside the bordered card
- Current Session stats-card background now uses RGBA alpha rendering (`(50,10,2,210)`) to match the Back button's translucent intensity more closely
- Fixed Current Session stats text anchoring so the first line is fully inside the card and no longer clipped at the top
- Record Actions window now matches Settings layout more closely: title/buttons are rendered directly on the background scene (no outer dark container), using the same canvas glow-button skins as the main/settings UI
- Record Actions background now refreshes on canvas resize to prevent bottom strip artifacts during first paint
- Record Actions now opens to the right of Settings with ~0.4 cm gap and the same window size (`460x430`), while still clamping to visible screen bounds
- `Show Records` window is now larger and resizable so per-record action buttons (`Test Action`, `Delete`) remain visible across UI scales.
- **Manage Accounts** was rebuilt to match the provided reference design (fire/red split layout)
- Manage Accounts now opens aligned below Settings and uses tuned action-button border styling consistent with the updated submenu look
- Manage Accounts outer shell frames were removed for the switch row, accounts wrapper, and play-order wrapper; only inner functional/table/widget frames remain
- Remaining dark outer blocks in Manage Accounts were removed by blending wrapper rows/labels with the background image so only intended interactive/table elements remain framed
- Manage Accounts was further flattened: table/details row borders and entry highlight frames were removed so the view reads mostly as text on background
- All six Manage Accounts action buttons now use the same shared submenu button style as the other windows (`Secondary.TButton`)
- Manage Accounts controls were flattened further: buttons are now text-only (no box), and combobox/input field borders were removed to eliminate remaining dark/red frame artifacts
- Manage Accounts buttons now use the same main UI glow-button styles (`Primary.TButton` for save actions, `Secondary.TButton` for neutral actions)
- Manage Accounts background patch blending for wrapper blocks/headings was removed to eliminate rectangular color artifacts on the fire background
- Manage Accounts rows now use subtle dark list cards with a clearer selected-row highlight instead of heavy section boxes
- Manage Accounts list rows/inputs were flattened further: row borders and input/dropdown frames are removed for a cleaner, less boxed appearance
- Manage Accounts account-row selection is now indicated by text emphasis/color instead of a framed row border
- Manage Accounts uses a clickable account table plus inline `Name` / `Email` / `Password` editor fields below the table instead of a separate side editor
- Manage Accounts now uses a canvas-first scene layout (like the main window) for titles/table text and button placement, removing large frame-based panel backgrounds
- Manage Accounts default window size was increased to `900x980` to avoid right/bottom clipping with the canvas layout
- Manage Accounts action buttons are now flat no-border buttons (no glow-frame outline) to remove remaining visible button borders
- Manage Accounts buttons are now canvas-rendered rounded dark-red translucent controls (matching the other windows' rounded behavior) while keeping the prior button size footprint
- Manage Accounts input fields (`Switch account`, `Name`, `Email`, `Password`) and `Remember password` checkbox now share the same dark-red control tone as the play-order dropdowns
- Fixed Manage Accounts entry rendering so those fields now actually use `entry_bg` (dark-red) instead of falling back to the window background
- Manage Accounts now includes two feature-style translucent group boxes (yellow border, dark-red RGBA fill) around the accounts list area and the account play-order area
- Manage Accounts no longer shows the `Active` marker and no longer includes the old right-side `Account Details` editor section
- Manage Accounts window width is now compact and sized to the left-side content area with a small margin
- Manage Accounts now has a dedicated top `Switch account` group box in the same yellow bordered translucent style as the other manage groups
- The top switch action button was renamed to `Save Time` and moved to the lower-left area inside that switch group box
- `Save Accounts` was moved up to sit fully inside the accounts group box, and the play-order group box was enlarged so `Save Order` and `Close` are fully inside
- Switch group height was increased and `Save Time` moved lower to avoid overlap with the switch label line
- Manage Accounts window was widened slightly and group boxes are now inset with more symmetric left/right spacing
- Saving accounts creates/updates one folder per account under `Accounts/` and writes `credentials.json` inside that folder
- Account credentials are no longer stored in `runtime/config/calibration_config.json`; `Manage Accounts` reloads them from the per-account `Accounts/<folder>/credentials.json` files
- Row selection is clickable; the selected row gets highlight and its values are editable in the inline detail fields below the table
- Password fields in **Manage Accounts** are masked (`*`) while typing
- `Save Row` updates the selected row in memory, `Save Accounts` persists all valid rows to config/folders
- Play order is now shown as a 5-slot priority list (dropdowns) under the table section
- Manage Accounts window width is auto-fitted to content with a small right margin
- The former global dark content wrapper around all Manage Accounts blocks was removed; sections are now laid directly on the background with only left padding
- The headings `Manage Accounts` and `Accounts (max 10)` are now plain text on background (their dark title containers were removed)
- Heading labels in Manage Accounts now use cropped background-image patches (no solid color fill) to avoid visible red/dark heading blocks
- Manage Accounts background refresh no longer runs continuously on `<Configure>`; heading/background patching is applied on startup to prevent layout drift
- Heading labels no longer use fixed character widths; a delayed final background patch refresh avoids clipped text and stale border artifacts
- Manage Accounts minimum window height was increased to `900` to keep the full content visible without bottom clipping
- Buttons were reverted to the classic UI styling and original color direction
- Main menu window size is fixed (width and height are both non-resizable)
- Main menu top-left corner is fixed at screen coordinates `x=18`, `y=24`; Settings follows the main window position
- Window icon (top-left title bar) now uses a small `images/ui_symbol.png` logo instead of a black placeholder box
- During bot startup, the UI shows an indeterminate loading bar with the label `Loading Carddata` until initialization finishes
- Fixed a startup regression in `ui.py` caused by a mismatched theme token in the loading bar style
- `Status: Stopped` now uses `#ffb02a`
- Main UI now includes a bottom footer bar with the `Keep Window on Top` checkbox; the bar is flush to the left/right/bottom edges, the checkbox is centered with a yellow `X` indicator, and it does not overlap with the startup `Loading Carddata` area
- Main UI top spacing was tightened by about 1 cm so the logo sits higher with less empty space above it

Standalone runnable UI example (single file): `burning_lotus_ui_example.py`.

### First Start Requirements (Out-of-the-Box Mode)

On first launch, the UI asks you to confirm these required settings:

- MTGA language: **English**
- MTGA display mode: **Windowed**
- MTGA resolution: **1920x1080**
- OS display scaling: **100%**

The bot now runs in an out-of-the-box mode using:

- **Player.log as primary state source** (`Log = State`)
- **Vision checks only as verification gates** (`Vision = Verify`)

The runtime tries to locate MTGA dynamically:

- First via OS window rectangle detection
- On Windows, the setup check now uses the MTGA client area from Win32 instead of border-offset heuristics
- Then verifies/fallbacks with visual anchor checks
- Stores a session `arena_region` and re-acquires it on repeated verification failures
- During combat, if live re-acquire fails briefly, the controller now reuses the last known good `arena_region` instead of sending blind desktop clicks
- During normal in-game hand interaction, the controller now also reuses the last known good `arena_region` if live re-acquire fails, so regular cast/play scans do not drop back to raw desktop coordinates mid-match
- During active `SelectN` / target-selection flows, the controller now also reuses the last known good `arena_region` if live re-acquire fails, so hand scans stay window-relative instead of falling back to raw desktop coordinates
- `AssignDamage Done` now first template-matches `Buttons/assign_damage_done.png` in the lower center of the detected arena and only falls back to the saved click target if the image click does not clear `Step_CombatDamage`; it still writes an `assign-damage-<timestamp>/` debug bundle with full-screen, arena crop, focus crop, and state JSON when damage assignment remains stuck
- Opponent avatar target selection uses the same direct 1920-relative mapping path as other calibrated points (`_map_abs_point_to_arena`), without avatar-specific fallback heuristics


2) Use **Settings -> Calibrate** only if manual coordinate fallback is needed:
   - **Optional** for normal usage.
   - Use this only as advanced/support fallback when repeated button verification fails.
   - Windows/Linux: calibration uses `pynput`.
   - macOS: calibration uses stable polling mode (no global `pynput` hook) with `Enter` to save and `Esc` to cancel.
   - Required: keep_hand, queue_button, next, concede, attack_all, opponent_avatar, hand_scan_p1, hand_scan_p2, assign_damage_done
   - Logout flow uses: log_out_btn, log_out_ok_btn

3) Current Session:
   - Opens a session window with live stats in green text:
     `X Min till Account Switch`, `Games played`, `Win`.

4) Settings:
   - **Manage Accounts** opens a window for:
     - **Switch account (min)**
     - Up to **10 accounts** (`Name`, `Email`, `Password`) with clickable row selection and inline detail fields below the table
     - **Account Play Order** (up to 10 positions)
   - Use **Save Row** to update the currently selected row in memory.
   - Use **Save Accounts** to create/update account folders and credentials JSONs.
   - Use **Save Order** to persist the play order.
   - **Record Action** opens a window for **Record** (uses F8 to stop) and **Show Records**.
     Saved records include per-action timestamps (`ts`) in `runtime/records/recorded_actions_records.json`.
  - Includes **Calibrate** and **User Interface** buttons.
  - Opening any Settings subwindow (**Manage Accounts**, **Record Action**, **Calibrate**, **User Interface**) temporarily replaces the Settings window at the same screen position.
  - **User Interface** opens a settings window with:
    - `UI Scale` slider (50%..120%)
    - `UI Scale` control is vertically centered/lower in the panel for clearer spacing
    - scale controls remain inside the highlighted yellow framed card (matching Current Session style)
  - On `Save`, UI scale is applied immediately in-app (no restart required).
  - Subwindow minimum sizes are now derived from actual visible content bounds to avoid clipping without forcing oversized windows.

5) Start Bot.
   - Before the bot starts, the app checks that MTGA is visible with an exact windowed `1920x1080` client area and that Windows display scaling is `100%`.
   - If that check fails, the app writes an Arena setup debug bundle with diagnostics and screenshots.

Stop bot any time with **Mouse Wheel**.

## Account Switching

- Accounts are managed via **Settings -> Manage Accounts**.
- Each account is saved in its own folder under `Accounts/` and includes `credentials.json` in this format:
  - `{ "<AccountName>": { "email": "...", "pw": "..." } }`
- `runtime/config/calibration_config.json` no longer stores managed account credentials; the UI reloads accounts by scanning the account folders.
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
Sacrifice-style SelectN prompts can now also select local battlefield permanents by hover-scanning the lower battlefield region instead of aborting when the requested IDs are not in hand.
Resolution SelectN only waits for stack-clear as a fallback now; if the request already points at concrete hand, prompt, or battlefield candidates, the controller proceeds immediately instead of burning the rope.
Failed battlefield SelectN scans write the same `debug/hand-select-<timestamp>/` bundle used for hand-scan failures so sacrifice prompts can be debugged from screenshots and state dumps.

Own timer ("sand clock") status is parsed from `Player.log` game-state timer data (only for
the local player seat, not opponent timers).
Timer transitions are logged as:
`MY_TIMER_START`, `MY_TIMER_WARNING`, `MY_TIMER_CRITICAL`, `MY_TIMER_STOP`.

## Card Data Updates

On startup:
- MTGA card DB export refreshes `runtime/cache/cards.json` if the local MTGA data changed.
- Raw card-data discovery supports common Linux Steam paths, macOS Steam install paths, and Windows Steam install paths.
- Scryfall bulk delta check fetches new Arena IDs and merges missing cards.
If `runtime/cache/cards.json` is missing on first run, it is seeded from the repo copy of `data/cards.json` or generated automatically.

Fallback:
- `runtime/cache/missing_cards.json` tracks cards encountered in matches but not in `runtime/cache/cards.json`.
- Other local cache files such as `scryfall_cache.json`, `scryfall_oracle_cache.json`, and `scryfall_bulk_metadata.json` also live under `runtime/cache/`.

## Logs

- `bot.log` - main bot debug
  - Stored at `runtime/logs/bot.log`
  - If writing there fails, logger falls back to local `./bot.log` without stopping the bot.
  - Full parsed game-state snapshots stay in `bot.log`; they are no longer echoed to process `stdout`, so supervisor runs do not spam the terminal/chat with live `Player.log` state dumps.
  - The whole local `runtime/` tree is ignored in Git, including logs, cache files, debug bundles, and supervisor artifacts.
- `bot_gui_subprocess.log` - UI subprocess log (if used)
- `Player.log` default path (auto-detected):
  - macOS: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
  - Windows: `C:/Users/<YourUser>/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`
  - Linux/Proton: `~/.local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`
- `runtime/status.json` - shared bot telemetry for the external stuck supervisor
  - Stored in the repo-local `runtime/` folder next to logs/debug/cache artifacts
  - Includes current `mode`, derived `bot_state`, `turn_info`, `last_playerlog_event_at_epoch`, `last_decision_at_epoch`, `last_input_at_epoch`, `intentional_wait_until_epoch`, plus local sand-clock telemetry such as `my_timer_type`, `my_timer_elapsed_sec`, `my_timer_remaining_sec`, `my_timer_critical_count`, and `my_timer_timeout_seen`
  - When the bot is launched under `tools/bot_supervisor.py`, the controller disables its old 3-minute blind `Resolve` spam and reports idle state through this file instead
- Startup validation now requires an existing `Player.log`:
  - UI startup prompts for manual file selection if auto-detection fails.
  - CLI startup (`run_bot.py`) exits early with a clear error if the file does not exist.
- Windows fork preset: `runtime/config/calibration_config.json` keeps `log_path` at `C:/Users/giaco/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log`.
- Hover logs are suppressed by default and only enabled during selection scans.
- A one-line match summary is logged at match completion.
- Startup diagnostics now include:
  - `UI start: init controller ...`
  - `UI start: game.start() begin/completed`
  - `Queue target details` including target source, detected `arena_region`, configured `screen_bounds`, and configured click target.
  - Queue click selection is now template-first (`Buttons/play_btn.png` in MTGA ROI), then coordinate fallback.
  - 1920-only mode: loaded click targets must be 1920x1080 coordinates; non-1920 values are ignored and replaced with defaults.
  - Coordinate mapping is now direct 1920-relative inside `arena_region` (no legacy `screen_bounds` scaling and no queue-offset translation).
  - Mulligan clicks (`KEEP_HAND` / `MULLIGAN`) now log raw vs mapped target and are mapped relative to detected `arena_region`.
  - Opponent avatar targeting (`select_target` + retry offsets) now always maps calibrated `opponent_avatar` relative to detected `arena_region` (no absolute desktop click).
  - Opponent avatar targeting now first rebases legacy absolute coordinates via the calibrated `queue_button` anchor (reconstruct old window origin, then map to current arena), which matches the same relative-conversion principle used for other controls.
  - Logout fallback clicks (`LOG_OUT_BTN`, `LOG_OUT_OK_BTN`) now prefer mapping from a runtime `Play`-button template origin (detected before `ESC`), then fall back to queue-anchor/arena mapping.
  - Account-switch logout now uses a built-in mapped sequence (independent from recorded-action replay): two short focus clicks, one `ESC`, mapped `LOG_OUT_BTN`, then mapped `LOG_OUT_OK_BTN` with tuned delays.
  - If a `Logout` record exists, its first click (`log_out_focus`) and last two clicks (`log_out_btn`, `log_out_ok_btn`) are seeded once as baseline logout coordinates (and written to `runtime/config/calibration_config.json`), then normal mapped clicking is used afterward.
  - Seeded logout clicks from recorded actions are converted into 1920-relative window coordinates (using legacy `queue_button` origin reconstruction when needed) before runtime mapping.
  - For logout targets, 1920-relative values are mapped directly to current `arena_region`; queue-anchor rebase is only used for true legacy absolute targets to avoid mixed-space drift.
  - Fixed init-order bug: loaded/seeded logout coordinates are no longer overwritten by hardcoded defaults later in `Controller` startup.
  - Logout click injection now mirrors Record Action playback style (explicit `left_down`/`left_up`) for better consistency with `Test Action`.
  - Hand scan points (`hand_scan_p1/p2`) are treated as direct 1920-space targets; if loaded values are outside 1920x1080 they are replaced with 1920 defaults before runtime mapping.
  - Bottom-right actions (`RESOLVE` / `SUBMIT_SELECTION` / `ATTACK_ALL`) and `ASSIGN_DAMAGE_DONE` now also use mapped coordinates instead of fixed desktop absolute points.
  - `KEEP_HAND` uses only the configured keep-hand coordinate and maps it relative to detected `arena_region` (no template matching).
  - After each mulligan click, a keep-click debug bundle is saved under `runtime/debug/keep-click-<timestamp>/` with:
    - `keep_click_state.json` (raw/mapped points, source, arena region, correction, state)
    - `full_screen_after_click.png`
    - `arena_region_after_click.png` (if arena window was detected)
    - `click_focus_after_click.png` (crop around clicked position)
  - During account-switch fallback logout, each logout click saves a debug bundle under `runtime/debug/logout-click-<timestamp>/` with:
    - `logout_click_state.json` (raw/mapped points, source, arena region, state)
    - `full_screen_after_click.png`
    - `arena_region_after_click.png` (if arena window was detected)
    - `logout_focus_after_click.png` (crop around clicked position)
  - When `SelectN` / discard hand scanning stalls, the controller saves a debug bundle under `runtime/debug/hand-select-<timestamp>/` with:
    - `hand_select_state.json` (card id, scan bounds, current hover/cursor, arena regions, pending SelectN state)
    - `log_tail.txt`
    - `full_screen.png`
    - `arena_region.png` (if arena window was detected)
    - `scan_focus.png` (crop around the current scan position)
  - The same `hand-select-<timestamp>` bundle is now also written for normal cast/play hand-scan failures (`SCAN_FAILED` / `SCAN_STOPPED`), not only `SelectN`

### Navigation Debug Artifacts

When post-login navigation verification repeatedly fails, the bot saves a debug bundle in:

- `runtime/debug/<timestamp>/`

Bundle contents:

- `state.json` (reason, parsed bot state, arena region)
- `log_tail.txt` (latest log lines)
- `arena_region.png` (captured MTGA window region)
- `full_screen.png` (full-screen capture)

### Supervisor Workflow

Use the external supervisor for unattended stuck detection and restart:

```bash
python tools/bot_supervisor.py
```

For the interactive Codex debug workflow, run:

```bash
python tools/bot_supervisor.py --stop-after-incident
```

Default behavior:

- Starts `python tools/run_bot_ui_path.py` as a child process with `MTGA_SUPERVISOR_ACTIVE=1`
- That child uses the same runtime inputs as the UI Start button: `ConfigManager`, configured click targets, configured screen bounds, configured input backend, account-switch settings, and the Arena setup preflight
- If the bot is attached into an already-running match or priority window, `Game` now infers `game_started` from the live gameplay state instead of waiting forever for a mulligan callback that will never come on that attach path
- Ignores stale `runtime/status.json` for a short startup grace window and waits until the status belongs to the newly spawned child PID before it starts classifying incidents
- Watches `runtime/status.json` for real activity instead of only file mtimes
- Treats the bot as stuck after 300 seconds without `Player.log`, decision, or input activity unless `intentional_wait_until_epoch` says the bot is in a known wait window
- That generic stale-timeout fallback now only applies while the child reports a real in-game mode (`in_game` / `stuck_suspected`); if the bot is already on `HOME`, `home_ready`, `queue_ready`, or mid account-switch, the supervisor no longer creates a false gameplay incident just because no fresh in-game activity arrived
- Treats the bot as stuck immediately when the local player's first `MY_TIMER_CRITICAL` sand-clock event happens in the same match
- Treats the bot as stuck when the local player's `TimerType_Inactivity` is running but the bot has made no decision/input for 20 seconds, even if Arena never emits a late `MY_TIMER_CRITICAL`
- Treats a local timeout loss (`ResultReason_Timeout`) as an incident too, so a post-timeout match that fails to return home still triggers recovery and Codex notification
- Writes an incident bundle under `runtime/debug/incident-<timestamp>/`
- Stops the child bot, and for own sand-clock incidents first tries `ESC -> Concede -> optional OK confirm` to leave the match cleanly, then verifies `HOME` via `home_anchor.png`, then always attempts the Codex `stuck` desktop notification from that `HOME` state before restarting the bot
  - Late `TimerStateMessage` updates can omit `elapsedSec` / `remainingSec`; the controller now preserves the last known local inactivity-timer values instead of overwriting them with `null`, so the supervisor's rope-stall trigger still fires
  - Legitimate controller wait phases now publish short `intentional_wait` windows too: pending-message, target-selection, pay-costs, and delayed own-priority decision scheduling no longer look like rope-stalls to the supervisor while the bot is intentionally waiting to act
  - The delayed own-priority decision timer is now keyed per turn/phase/step/active-player window, so repeated `GameState` / `TimerStateMessage` updates from the same priority state no longer keep canceling and re-arming the bot's 4-second decision callback forever
  - Those delayed own-priority decisions are now also rope-aware: if the local `TimerType_Inactivity` rope is already low, the controller cancels/bypasses the artificial delay for that same priority window and decides immediately instead of burning the last few seconds on a stale combat/blocker wait
  - The local `GameState` now merges keyed diff lists cumulatively instead of replacing them wholesale, so partial Arena updates no longer erase earlier battlefield/hand/stack objects that later `SelectNReq` and combat handlers still need to resolve valid ids
  - The in-game `Concede` step now searches `Buttons/concede.png` across the full detected arena first and logs the match score, then retries inside the focused ROI around the calibrated/UI target, and only then falls back to the calibrated coordinate itself
- `Assign Damage Done` now keeps and reuses the first matched `assign_damage_done.png` point for repeated low-level clicks; if the saved `assign_damage_done` calibration is outside a plausible lower-center done-button band, the controller now skips that stale coordinate instead of clicking the wrong UI element
  - The supervisor now reads its default `Concede` fallback from `runtime/config/calibration_config.json` only when that saved point is already a valid 1920-relative in-window target; otherwise it uses the same `1286,611` default as the UI path instead of the older mismatched hardcoded fallback
  - The in-game `Concede` recovery now retries `ESC` up to three times, captures per-attempt debug screenshots, and only clicks a real `Buttons/concede.png` template match; it no longer sends a blind fallback click when the options menu never became visible
  - Every failed or ambiguous in-game `Concede` attempt now writes targeted debug artifacts into the incident bundle (`concede_debug_after_escape_*`, `concede_debug_template_not_found_*`, `concede_debug_post_concede_wait_*`) so the ESC menu and button region can be inspected directly
  - If anchor-based arena reacquire fails during an in-game recovery, the supervisor now still uses the detected MTGA client window region and does not skip the `Concede` attempt just because no known UI anchor matched
  - After a successful concede, the supervisor now watches `Player.log` for `MatchEndScene` / `MatchCompleted` / `IntermissionReq` markers and clicks the arena center to dismiss the defeat/victory screen before trying `ESC`/`HOME` recovery
  - The recovery block is now crash-hardened: if any exception happens after `incident.json` is written, the incident still gets a `recovery.json`, a `codex_notify.json`, and a `supervisor_crash.json` traceback dump instead of leaving a half-written bundle and killing the supervisor silently
  - With `--stop-after-incident`, the supervisor does not restart or requeue the bot after recovery; it leaves the newest incident bundle in place, sends the Codex `stuck` notification, captures post-recovery screenshots/state, and exits so the incident can be debugged before the next run
  - The inactivity-stall watchdog now also checks that the local seat still owns current priority/decision in `turn_info`; the supervisor no longer concedes just because Arena keeps the local inactivity timer running while the opponent is taking their turn
  - `MainNav load in` and queue-ready home markers now explicitly reset runtime bot state back to `HOME` and clear stale turn/timer telemetry, so a finished match or aborted logout cannot leave the supervisor thinking an old in-game combat state is still active
  - If account switching fails to reach the login screen but Arena has already returned to `HOME` / `MainNav`, the controller now aborts that switch attempt, writes a nav-debug bundle, marks the switch interval as consumed, and resumes queueing on the current account instead of idling forever at home until the supervisor times out
  - Stale target-selection state no longer survives into a normal own `Pass` window on the stack: if Arena already shows `pendingMessageCount=0` plus a legal local `ActionType_Pass`, the controller clears any leftover `target_selection_wait` instead of treating an old target annotation as still blocking and roping out its own main phase
  - Early fresh-game baseline diffs now hard-reset the local controller cache too: if Arena re-enters a new game with low `gameStateId` / mulligan signals before a full snapshot is seen, the controller no longer keeps old `turnInfo`, stack, or actions from the previous match and accidentally reasons against a hybrid `turn=10 combat` plus fresh mulligan hand state
  - Reused decision-delay timers now refresh `runtime/status.json` too: if the controller is still honoring the same 4-second own-priority delay on a later `GameState` update, it republishes `decision_delay_wait` with the remaining delay so the supervisor does not misread that legitimate wait as an `own_inactivity_timer_stalled` incident
  - The AI-side `Active turn delay` inside `Game.decision_method()` now also publishes `active_turn_delay` as an intentional wait while it sleeps; without that, the supervisor can still misclassify the bot as stalled in perfectly normal own-turn main-phase windows after the controller delay has already fired

Incident bundle contents:

- `incident.json` (stale duration, last runtime status, derived Player.log state, arena detection result)
- `bot_tail.txt`
- `player_tail.txt`
- `full_screen.png`
- `arena_region.png` (if MTGA was detected)
- `recovery.json`
- `codex_notify.json`
- `supervisor_crash.json` (only when the supervisor itself threw during recovery/notification)
- `tracking.json`
- `related_incidents.json`
- `signature_knowledge.json`
- `post_recovery_state.json`
- `post_recovery_full_screen.png`
- `post_recovery_arena_region.png` (if MTGA was still detectable after recovery)

The supervisor now writes placeholder `recovery.json` and `codex_notify.json` immediately after `incident.json` is created, so even a mid-recovery crash leaves a self-describing bundle instead of looking like recovery never started.

Optional flags:

- `--startup-grace-sec 45`
  - Startup guard against false-positive `stuck` notifications from an old/stale `runtime/status.json`
  - During this grace window, the supervisor waits until status updates belong to the newly spawned child PID
- `--my-timer-critical-threshold 1`
  - Number of local-player `MY_TIMER_CRITICAL` events in one match before the supervisor treats the bot as stuck immediately
- `--my-timer-stall-sec 20`
  - Treat a running local `TimerType_Inactivity` with no bot decision/input for this many seconds as a stuck incident, even if Arena stops sending late timer updates
  - Active controller `intentional_wait` windows suppress this rope-stall trigger until the rope is in its final few seconds, so the supervisor does not concede in the middle of a legitimate delayed combat/blocking decision
  - The controller now also rewrites stale `target_selection_wait` into `stack_resolution_wait` whenever a `SelectN` prompt has already cleared but the game is still intentionally waiting on the stack; otherwise old selection waits can survive into the next turn and make a later rope-stall incident look like a targeting hang
  - `repeated_own_timer_critical` now only uses the real `TimerType_Inactivity` rope; late `TimerType_ActivePlayer` criticals are still logged, but they no longer increment the watchdog counter or trigger an automatic concede by themselves
  - Recovery now also restores/focuses the MTGA window before `ESC`, `Concede`, and match-end dismiss clicks, because the detected MTGA client region alone is not enough if another desktop window is actually in the foreground
  - Before hover-based hand casts/selections, the controller now also checks for an open Arena options overlay and dismisses it with `ESC`; otherwise the menu can block the board while the bot keeps scanning and burns rope on a legal move
  - Repeated `SelectNReq` retries for the same discard/sacrifice prompt now reuse the same pending token/state instead of creating a brand-new pending request each time; this prevents stale discard retries from resetting their retry counters forever and roping out the game
  - `informationalUseOnly=true` `SelectNReq` payloads are now cleared immediately instead of being treated like actionable hand/discard prompts; Arena can emit those as UI telemetry after it has already auto-resolved the actual selection
  - Stack-resolution defers now inspect wrapped Arena action entries correctly; if `ActionType_Pass` is present inside the `{"seatId": ..., "action": {...}}` wrapper, the controller no longer misclassifies the turn as "must wait for stack" and stall through its own main phase
  - Stack/pending `SelectNReq` prompts that name `GameObjectType_Ability` ids now remap those prompt ids to their `parentId` hover targets before scanning; Arena often exposes triggered-choice prompts as ability-instance ids even though the UI hover stream only reports the underlying card/object ids
  - If our own `DeclareAttackersReq` arrives while a stack-mode `SelectNReq` retry is still running, the controller now preempts that stale stack selection immediately so combat submit/recovery can take priority instead of burning the inactivity rope behind an obsolete trigger-choice thread
  - Preempted stack-mode `SelectN` retries now also self-cancel after their built-in delay and before any later click/submit step if their prompt token was invalidated in the meantime; this prevents a stale worker from waking up after combat already took priority and still clicking old stack targets into the `DeclareAttackersReq` window
  - Stale `AnnotationType_PlayerSelectingTargets` state no longer blocks later main-phase/combat decisions indefinitely; if the target-selection signal is old and Arena has no pending target message anymore, the controller now auto-clears that pause instead of sitting forever in `target_selection_wait`
  - Attach/supervisor starts now also restore `has_mulled_keep` from `ClientMessageType_MulliganResp` and, if needed, infer it from live gameplay state; otherwise the controller can remain in a fake pre-mulligan state and ignore perfectly legal `Cast/Play/Pass` windows in a fresh game
  - Resolution-time `SelectNReq` prompts with concrete hand/battlefield/stack ids no longer re-enter the generic `stack_count > 0` wait loop inside the retry worker; if Arena already exposed real selectable ids, the controller now proceeds with the selection instead of repeatedly waiting for the stack to clear and then aborting the prompt
  - `GameStateType_Full` snapshots now replace the local cached game state instead of being merged like diffs; without that hard reset, a fresh match can inherit `turnInfo`, zones, timers, and annotations from the previous game and leave the controller reasoning against a hybrid impossible board state
  - Stale stack-mode `SelectN` workers no longer block a normal own-main-phase `Pass` window; if Arena already exposes `ActionType_Pass` with `pendingMessageCount=0`, the controller now auto-clears the old stack selection state and resumes real decisions instead of chasing dead stack ids
  - Matchstart mulligan handling now treats `mulligan timer armed` separately from `has kept hand`: early pregame diffs can already expose `turnInfo`, `actions`, and even a stack object before the real `GREMessageType_MulliganReq` is resolved, so live-state keep inference is now blocked while any mulligan prompt is still pending and an actual local `MulliganReq` clears any premature keep state before stack/defer logic runs
  - Stale merged `players[].pendingMessageType=ClientMessageType_MulliganResp` no longer keeps the bot trapped in `mulligan_wait` after the game is already underway; once Arena shows a real turn context (`turnNumber` plus `phase/step`) with normal `Play/Cast/Pass` actions, mulligan-pending detection is overridden and live keep inference is allowed to clear the old mulligan timer
  - Once a mulligan keep timer is already armed from a real local `GREMessageType_MulliganReq`, the delayed callback now trusts that armed prompt instead of re-checking the merged live action list; fresh-game snapshots can already contain turn/action data and still need the mulligan response
  - The AI now trusts Arena's live action list over its own cached `has_land_been_played_this_turn` flag: if `ActionType_Play` is still legally available, the flag is cleared and land play is reconsidered instead of incorrectly defaulting to `resolve`
- `--mtga-launch-cmd "<command>"`
  - Optional hard recovery path for cases where `ESC` is not enough
  - The supervisor kills the configured MTGA process names and relaunches the client before retrying `HOME` recovery
- `--mtga-process-names "MTGA.exe,MTGALauncher.exe"`
  - Override the Windows process names used for the optional hard client restart
- `--concede-rel-x 962 --concede-rel-y 631`
  - Override the 1920-relative in-game `Concede` button position used after `ESC` opens the options menu; by default the supervisor only trusts a saved `runtime/config/calibration_config.json` `concede` point when it is already a valid 1920-relative target, otherwise it falls back to `962,631`

Codex notification behavior:

- Always attempted after successful recovery back to `HOME`
- Searches the whole screen for `supervisor/codex_window.png`, clicks it, types `stuck`, presses Enter
- Result is written to `codex_notify.json`
- The notifier now also saves `codex_notify_before.png` / `codex_notify_after.png` into the incident bundle and uses a double-click plus delayed double-Enter retry, because the Codex desktop field can look focused while the first Enter is still swallowed
- This remains a best-effort desktop macro, not a guaranteed control channel; it only works if the Codex chat input is visible and still matches the template

Standalone notifier test:

```bash
python tools/test_codex_notify.py
```

- Uses `supervisor/codex_window.png` by default
- Prints the notifier result as JSON
- Exits with code `0` on success and `1` if the Codex input template was not found or the desktop macro failed

UI-path CLI runner:

```bash
python tools/run_bot_ui_path.py
```

- Uses the same configuration and setup path as pressing Start in the UI
- Unlike `run_bot.py`, it does not use hardcoded fallback click targets/screen bounds
- This is the default child command used by `tools/bot_supervisor.py`
- If it exits before `runtime/status.json` switches to the new child PID, check its direct stdout/stderr first; the supervisor depends on this Arena setup preflight completing successfully before telemetry takeover can happen

Incident notes:

- Real supervisor incidents are summarized in `supervisor/incidents.md`
- Each entry now records the incident folder/timestamp, a signature/cluster key, trigger, whether recovery / Codex notify succeeded, the actual bot-side root cause, the fix or next debug step, and a tracking block instead of a binary "fixed" claim
- Each supervisor incident bundle now also gets a machine-readable `tracking.json`, and repo-wide signature state lives in `runtime/incident_registry.json`
- If an older install still has `incident_registry.json` in the repo root, the tracking helper now folds that legacy file into `runtime/incident_registry.json` automatically so repeat history and guidance survive the runtime-path migration
- `tracking.json` now includes a `suggested_signature` plus `signature_basis`, generated from trigger, intentional wait reason, turn phase/step, and dominant log patterns in `bot_tail.txt` / `player_tail.txt`
- Each new incident bundle also gets `related_incidents.json`, which copies the relevant repeat-context from `runtime/incident_registry.json` into the bundle itself so Codex can immediately see whether the suggested/current signature has already been seen, what the last status was, and the latest evidence summary
- Each signature record can now also store reusable guidance (`hypothesis`, `applied_fix`, `next_debug_action`); the supervisor copies that into `signature_knowledge.json` for every new bundle so Codex sees the last known fix strategy without re-reading the full registry first
- `signature_knowledge.source_incident` now stays anchored to the incident that actually introduced or last changed that guidance; later repeats that only update status/evidence do not overwrite the provenance
- Tracking status is evidence-based:
  - `proposed`: Codex has a plausible diagnosis / fix, but it is not merged yet
  - `applied`: the change is in the repo, but long-run proof does not exist yet
  - `survived_n_runs`: the same signature has not recurred for a growing number of later runs
  - `reproduced_and_passed`: a replay-like recurrence happened and the bot got through it
  - `regressed`: the same signature came back again
- Confidence should be treated as a heuristic, not truth:
  - Start around `0.3` for a log-derived fix with no replay
  - Raise toward `0.6` after roughly `10+` comparable runs without the same signature
  - Raise toward `0.9` when a replay-like recurrence is survived cleanly
  - Drop toward `0.1` and/or mark `regressed` if the same signature returns
- Verification should stay indirect and longitudinal:
  - Did the same signature recur later?
  - Did the bot now progress through similar UI/log states?
  - Did time-to-next-same-incident improve?
  - Did the frequency of that signature decline over many runs?
- Older entries in `supervisor/incidents.md` remain legacy narrative notes; new entries should use the tracking block going forward
- The suggested signature is only a clustering hint:
  - confirm it if it matches the actual diagnosis
  - override it if two incidents only look similar on the surface
  - treat the attached `signature_basis` as the explanation of why that key was proposed

Tracking helper commands:

```powershell
python tools/incident_tracking.py init --incident-dir "%LOCALAPPDATA%\\BurningLotusBot\\debug\\incident-20260403-220121"
python tools/incident_tracking.py suggest --incident-dir "%LOCALAPPDATA%\\BurningLotusBot\\debug\\incident-20260403-220121"
python tools/incident_tracking.py set-status --incident-dir "%LOCALAPPDATA%\\BurningLotusBot\\debug\\incident-20260403-220121" --signature "own_main_phase_decision_delay_wait_dropped" --status applied --confidence 0.3 --hypothesis "Decision delay callback was dropped on reused priority window" --applied-fix "Republish decision_delay_wait and keep one keyed timer per priority window" --next-debug-action "If it recurs, capture delayed callback state before and after timer fire" --evidence "Patch merged after log analysis"
python tools/incident_tracking.py record-survival --signature "own_main_phase_decision_delay_wait_dropped" --runs 10 --evidence "10 later comparable runs without recurrence"
python tools/incident_tracking.py set-guidance --signature "own_main_phase_decision_delay_wait_dropped" --next-debug-action "If it recurs, capture delayed callback state before and after timer fire"
python tools/incident_tracking.py show --signature "own_main_phase_decision_delay_wait_dropped"
```

Notes:

- `set-status` is for the first diagnosis / applied fix state
- `set-status` can also snapshot the current hypothesis, applied fix, and next targeted debug action for that signature
- `record-survival` upgrades longitudinal evidence without pretending replay certainty
- `set-guidance` updates reusable per-signature knowledge without changing status/confidence counters
- `reproduced_and_passed` and `regressed` should be written with `set-status` when a truly comparable situation is later observed

## Verify Template Assets

For `Log = State, Vision = Verify`, add these templates under `assets/assert/`:

- `home_anchor.png`
- `play_menu_anchor.png`
- `find_match_anchor.png`
- `historic_anchor.png`
- `my_decks_anchor.png`
- `ingame_anchor.png`
- `options_anchor.png`
- `store_anchor.png`
- `global_anchor.png`
