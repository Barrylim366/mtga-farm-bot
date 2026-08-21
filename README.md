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
- OS display scaling: **any (the bot converts coordinates for scaled displays)**

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
- **ZIP / website installs** (no `.git` folder): the bot compares its local `version.py` against the one on the `main` branch at GitHub. If `main` has a newer version, it downloads the branch archive and overlays it onto the install folder. The archive only contains tracked source files, so user data (`runtime/`, `Accounts/`, `.venv/`, `.venv-macos/`, `credentials.txt`, …) is never touched. Executable bits on the launcher scripts are preserved, so `start_macos.command` / `start_linux.sh` stay double-clickable after an update.

Either check is skipped when there's no network access. Every check writes its outcome (up to date, update available, or why it was skipped) to `bot.log`, so a missing update dialog can be diagnosed afterwards. Dependencies from `requirements.txt` are reinstalled automatically if they changed as part of the update.

The app's current version (`1.2.2`, sourced from `version.py`) is shown in **Settings**, above the Manage Accounts button.

## Configuration

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

> Fixed in this version: the **Log Out** click missed the button on every attempt but one, in the whole recorded history. "Log Out" is a centered text link on the Options overlay, and it is found by template match with a coordinate fallback behind it. Both halves were broken. The match passed no scale tolerance, and because the search region is the arena itself — normalized to 1920×1080 — nothing gets rescaled on a 1920-wide client, so only scale 1.0 was ever tried: measured against a live overlay the template scores 0.549 at 1.0 and 0.950 at 1.10, i.e. it could not match at all. The fallback coordinate then pointed at (1716, 851), the legacy bottom-right layout, which is empty background — the link sits at (959, 667). Across the recorded history the image match hit **once** (on a 2048×1152 client) and produced the only successful logout, while the coordinate fallback ran **11 times and produced none**; when the Arena window changed to 1920×1080 the last working path disappeared and switching went from unreliable to impossible, disabling itself after three failures. The match is now scale-tolerant (0.70–1.50) and the fallback coordinate corrected, so both paths agree on the same point.

> Fixed in this version: after a successful switch, MTGA's post-login announcements could strand the bot. Observed live: "Banned Standard Cards" (an Okay button) followed by a set promo ("The Hobbit — Available Now!"), both covering Home completely — so navigation found no anchor at all and the queue loop spun for two and a half minutes until they were cleared by hand. The bot now clears them, but only once every navigation anchor has already been ruled out, so a dismissal can never steal a click from a real screen. A plain **Okay** is clicked; anything else gets **ESC**, deliberately *not* the promo's own call-to-action, because "Get Started!" opens the Store instead of dismissing. If that ESC merely opened the Options overlay then nothing was covering the screen after all, so it is closed again and the attempt reports no progress — otherwise the overlay would hide Home from the next pass and the dead-end would repeat forever. The Okay search runs at confidence 0.90, not the 0.80 used elsewhere: measured against real screens it scores 0.970 on an actual popup but 0.817 on the event page's orange Play pill and 0.808 on plain Home, so a looser threshold would press Play and start a match with the wrong deck.

> **Known issue:** in quests mode, if a switch becomes due while the bot is on the Starter Deck Duel event page (`game_mode = starter`), the logout sequence can fail to open the Options menu and misclick into the event page instead of logging out. Time-mode switching from Home is unaffected. A fix (navigate to Home before attempting logout) is planned as a follow-up. This is separate from the calibration fix above and still open.

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

> Fixed in this version: running the test suite moved the real mouse and clicked. A Controller arms fire-and-forget timers (the card-prompt settle timer among them) that nothing cancels, so tests that built one produced real clicks seconds later, at absolute screen coordinates, on whatever was in front — 231 input events including 77 clicks per full run. Constructing a Controller without naming an input backend now gets an inert one; everything that actually drives Arena names its backend explicitly, so nothing changed for the bot itself. `MTGA_BOT_INPUT_BACKEND` still overrides.

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
- Type priority when CMC is tied: creature → instant → sorcery → enchantment
- Supports Convoke (untapped creatures as mana sources)
- Kicker: the "Cast with Kicker?" chooser is answered automatically (always the plain, non-kicked version for now) so the bot never stalls on it
- The mid-screen "Choose One" overlay — kicker plates, a modal spell's or creature's mode plates (e.g. Apothecary Stomper's enter-the-battlefield choice), and the "sacrifice or pay" buttons — is treated as blocking: while it is up, no other move is dispatched, and the chosen plate is clicked again if the game has not moved on. Previously a single click that failed to register left the dialog open and the bot immediately swept the hand row for its next play behind the overlay, which could not be reached — the match then idled until it was conceded. "Has the game moved on?" is answered by the newest `gameStateId` seen on **any** GRE message, including the timer messages that never reach the merged game state — reading only the merged state made an already-answered dialog look open for another two seconds, and the retry then clicked onto the battlefield behind it
- The retries for an "additional cost" dialog (`CastingTimeOptionType_ChooseOrCost`) try *different* points rather than repeating one. Every button position in this handler is a measured guess, and a wrong one is invisible in the log: the clicks are all recorded, the dialog simply never closes. Live on 2026-08-21, turn 19, the AI picked Eaten Alive ("as an additional cost, sacrifice a creature or pay {3}{B}"), chose its target, and the handler clicked the same bottom-right point three times 1.6s apart — the dialog stayed open through all of them and the spell was lost. Repeating one point can only rescue a click the client swallowed, never a point that is not on the button, so the three attempts now walk a candidate list: the previously shipped bottom-right position first (so anything it already worked for keeps working), then the two mid-screen plate positions this same handler uses for `CastingTimeOptionType_Modal`, on the reasoning that a ChooseOrCost is structurally the same "pick one of two" prompt. Which plate is the sacrifice is not known, and that is accepted — paying the mana still casts the spell, which beats losing the card. Every click stays gated on the dialog being open (checked before the move and again after the settle sleep), so the sweep stops the moment one lands and a miss costs a click into the overlay rather than onto the board. If all candidates miss, the dialog is photographed to `runtime/debug/casting-option-*/` and logged as `CASTING_TIME_OPTION_UNANSWERED` — the screenshot is the only thing a corrected coordinate can be measured from
- The blind Home-tab click is refused unless the nav bar is actually on screen. Returning to Home clicks a fixed point, (104, 39), because a template match only works when Home is *already* the active tab — but that pixel is the Home tab only while the nav bar is up. On the match-end screen it is the **opponent's name**, and clicking it opens Arena's player menu and then "Report a Player": a modal aimed at a real person, with a Submit Report button, that none of the bot's image probes recognise. Live on 2026-08-21 it went exactly that way — three Home clicks at 18:47:46/52/59, all three already flagged `risky` by the click recorder, landed on the opponent's name while the end screen was still up; the bot then failed every screen probe for fifteen minutes behind that dialog and lost the match it had queued to the rope. The gate is the Profile tab (`global_anchor.png`), which exists on every main screen and on no match screen. A refused click is reported as a plain navigation failure, and the caller already falls back to the normal re-queue — a missed quest read costs far less than a false report against a real player
- A target click that is made and has no effect leaves evidence behind. The hover scan proves the cursor was over the right object — Arena named it by instanceId — and the click is logged, but nothing in the log tells "clicked the card" apart from "clicked twenty pixels past its edge", and the difference is expensive: while a target selection is pending Arena emits no hand hovers, so every following cast sweep goes blind for reasons that look nothing like the actual cause. Live on 2026-08-21, Scorching Dragonfire's only legal target was chosen correctly (creature 285, `can_hit_face: False`), the click went to arena-relative (852, 314), the prompt was still open 40 seconds later, three blind hand sweeps followed and the bot came within seconds of an emergency concede. If the prompt is still pending 2.5s after such a click, `TARGET_CLICK_INEFFECTIVE` is logged and `runtime/debug/target-click-*/` gets the board plus the click point in arena-relative form, directly comparable to the card in the screenshot. Diagnostic only — it clicks nothing and changes no decision, and `select_opponent_battlefield_permanent`'s own docstring still calls its scan band "a first estimate [that] needs in-game calibration"; this is what that calibration can be measured from
- **Known gap (open):** blind hand sweeps with Arena's window *active* keep recurring across sessions and accounts, and they regularly cost the rope. Measured from the debug bundles, three of the obvious explanations are ruled out: nothing covers the hand (checked twice by screenshot); the hand band does not cut the cards off (on a seven-card hand, x=1700 lands in the middle of the rightmost card, which spans roughly 1572–1830); and the window is genuinely in the foreground. What the screenshots do show is Arena sitting in a special input mode — in one case a target selection still pending, in another a card visibly mid-cast, raised and glowing — while the controller reported `pending_card_prompt=None`, `casting_time_options_open=False` and `pending_select_n=None` at that very moment. Working hypothesis: Arena emits no hand hovers while the client is in such a mode, and the bot's state model does not know about them, so it believes a sweep is allowed. If that is right, the fix belongs in *when* a sweep is permitted, not in the recovery — the recovery is downstream of a wrong premise and can only pick the wrong remedy (reactivate a window that was never inactive, hunt for a Done button that does not exist)
- Arena's "Report a Player" dialog is detected and **cancelled**. It is not a game prompt and no game message announces it, so the bot is blind to it: while it is up every screen probe fails and the bot burns its rope. It happened twice on 2026-08-21. The first time the trigger was found and blocked (the blind Home-tab click above); the second time there was no logged click at all between the last end-screen dismissal and the open dialog, so the trigger is still unknown — which is why this net does not depend on knowing it. The stall alone would be survivable; what makes it worth a dedicated check is the **Submit Report** button next to Cancel, pointed at a real player. Only Cancel is ever clicked: the dialog is confirmed by its title template, the button is searched for in a band that ends at x=1000 while Submit starts near x=988 and is 285px wide (so no match for it can fit), and the fallback is the measured Cancel position (797, 875), never an absolute desktop click. The probe runs only from the blind-sweep recovery path — after a full hand sweep came back empty with Arena's window active — so a normal match never pays for it. Both templates cross-validate against the *other* incident's capture at 0.99 (title) and 0.96 (button), well above the 0.80 threshold
- Client-side "Are You Sure?" confirmations are handled reactively after a failed cast attempt, avoiding speculative screen probes during normal casts
- Decision recovery is guarded against open payment/selection prompts and resumes safely after modal, stack, or scry interruptions
- Ties between otherwise equal casting plans favor lifegain-payoff creatures, so decks built around gaining life develop toward their game plan sooner
- Removal only ever targets creatures still on the battlefield and never redirects a harmful spell at your own board when no valid enemy target exists
- Ward is priced before a target is chosen, not discovered afterwards. A warded creature makes Magic Arena raise a confirmation window that exists only in the client — it is announced nowhere in the game's own messages, so nothing tells the bot it is there. Declining it hands back the same board the bot just looked at, so it picks the same target, and the same window opens again; a single such loop burned six minutes across four turns. Now the ward's cost is read from the card and weighed against the mana left over once the spell is paid for. Affordable, and the bot pays it and kills the creature — refusing to ever pay would make removal useless against the cards it most needs to answer. Unaffordable, and it picks a different target, or holds the spell rather than feeding it to a counter. A target it does back out of is remembered for the rest of the match, so nothing can loop even if the pricing is wrong
- Damage marked on a creature is forgotten when the turn ends, as the rules say it should be. Magic Arena announces damage but never announces its removal — it sends the field once and then simply stops mentioning it — and since the game state is merged from diffs, nothing would otherwise unset it. Left alone the damage accumulated for the rest of the match and every "how much toughness is left" reading drifted below the truth, until creatures that were healthy and attacking looked to the bot like they were already dead. That number decides which creatures removal can kill, which fights are winnable, and which blocks are worth making
- When a card lets you choose which creature to return from the graveyard or exile, the bot ranks candidates by their role in the deck's strategy instead of taking whatever the game offers first
- A card in hand is located by sweeping the mouse across the hand row until Magic Arena logs a hover naming it — the client never says where a card is drawn, so this is the only way to find one. When that sweep failed, all three retries used to re-run the *identical* 1000 px/s pass, each taking exactly 2.0s. Retrying at the same speed cannot find what the first pass missed: a hover is only logged after a client → server → log round trip, so it is not synchronous with the mouse, and a fast sweep can cross a card without one ever being emitted. Observed live: the bot decided to play a land on nine consecutive decisions, executed none of them, passed priority every time, kept an empty board from turn 2 to turn 6 and sat there until Arena's 150-second inactivity timer expired twice. Each retry now sweeps slower and in finer steps (10px/10ms → 8px/18ms → 7px/24ms). Only a failing sweep pays for it — the loop stops the moment the target card is hovered, so a healthy hand still resolves at the original speed (measured live: 0.45–1.40s per card)
- Hover lines are read only from log entries that actually describe a hover. A game-state message is packed with object ids and describes no hover at all, yet the old parser fell through to a generic nested search and then a regex over the whole line, so the scan could adopt an unrelated object as "the card under the cursor" and clear its cast suppression. Note that seat filtering is deliberately *not* attempted: the incoming hover shape carries two complementary seat fields and which one identifies the player doing the hovering could not be established from the logs, while a filter with the polarity inverted would discard the bot's own hovers and keep only the opponent's — turning an intermittent failure into a permanent one. It is safe to leave open because instance ids are unique per game, so a foreign id can never match the card being looked for; the sweep simply continues. Measured over a 21 MB `Player.log`, every one of the 1382 bare `"objectId"` hover fragments — the shape behind roughly 97% of identifications — originates in an *outgoing* client message, i.e. the local player's own hover
- The hand sweep stays on the hand row instead of crossing the whole window. It used to run the game frame's full width, which passes two zones holding nothing castable and everything expensive: on the left the player avatar and the graveyard/exile piles, on the right the Next button and the auto-pass arrow. That matters because a hover is not synchronous with the mouse — the scan already documents the client → server → log round trip — so a late event can name the target card while the cursor has moved on, and the sweep clicks wherever it currently is. Live on 2026-08-21: a cast click for card 281 landed at rel x=120, on the avatar beside the graveyard pile, which opened Arena's graveyard viewer; that overlay covers the board, so every later sweep hovered nothing *with the window correctly focused*, and the match was lost to the rope (`MY_TIMER_CRITICAL`, ActivePlayer remaining=0.0s) rather than to anything that happened on board. Not a one-off either: of 1988 recorded hand clicks spanning two months, 18 landed at rel x ≤ 170 in that strip. The band is now 220–1700, derived from the same data — real clicks run 272–1572, so it excludes every recorded misfire without cutting a single observed real click, and keeps a wide margin to the buttons on the right. The band is also *clamped* rather than merely defaulted, because a saved `calibration_config.json` overrides the default outright: narrowing only the default left the running bot sweeping the full width from its stored calibration, and the next session's first `HAND_SCAN mapped` line still read `raw_p1=(0, 1050) raw_p2=(1920, 1050)`. Only the horizontal limits are clamped — a hand row measured at a different height is kept as calibrated
- When a sweep comes back blind, the focus state decides what to do about it, because two different faults look identical from the sweep's side. An inactive window cannot emit hover events at all — that gets the window reactivated. An *active* window that still reports nothing is being covered, and the hand sits behind it — that gets the overlay dismissed. Conflating the two is what made the recovery useless for ten minutes in the case above: it kept reactivating a window that was never inactive. Dismissal reuses the scry/surveil Done template, which is not a guess — measured against the real graveyard viewer, its Done button sits exactly where the existing `SCRY_DONE` clicks already land, so one template covers all three overlays
- A sweep that ran the full width of the hand row and hovered *nothing at all* is treated as its own failure, not as evidence about the hand. Magic Arena runs on Unity, and Unity only reports what the mouse is over while its window is the active one — so a sweep over an inactive window crosses the whole hand row, hovers nothing, and looks exactly like a card that is not there. Observed live on 2026-08-21: from 15:56:58 on, every cast sweep ended in `SCAN_STOPPED` without a single hover event, while the discard prompts running seconds later on the *identical* coordinates hovered and clicked fine — because the fallback click that passes priority had briefly reactivated the window. The bot swept blind for ten minutes and lost a match it was winning at 22 life. Consequences: a blind sweep logs `CAST_NO_HOVER_EVENTS` and triggers one reactivation per cast instead of two more blind sweeps; the card is no longer marked unreachable, since a blind sweep never observed the hand; and the reactivation first re-focuses through the window API, then falls back to a click on the inert background strip at the left edge of the arena — but only after asking the OS whether that pixel actually belongs to the game, because the premise of the whole recovery is that some other window may be sitting on top of it. What counts as blind is deliberately narrow: only a sweep that completed. Every other way out of a cast attempt — a stuck options overlay, no arena region, or the scry/modal handlers signalling a running scan to stand down — says nothing about whether the game is answering, and answering *those* with a board click would land it on the very prompt whose handler asked the scan to get out of the way. A hover scan from another flow overlapping ours also voids the verdict: the hover queue is shared, those flows run from timer threads outside the decision lock, and each of their scans clears it, so one can eat the events we were waiting for and make a live window look dead
- Refocusing the game window is verified rather than assumed. Windows silently refuses a foreground change requested by a process that does not already own the foreground window, so the old call fired `SetForegroundWindow` and reported success either way — leaving the hand scan sweeping a window that could not answer. It now borrows the foreground thread's input queue (`AttachThreadInput`), which is the documented way to be permitted the change at all, and then waits for the activation to actually land: the switch is asynchronous, and reading the foreground window immediately after the call still returns the old one. A failure is logged with both window titles, and every hand-select debug bundle records who owned the input — without that field an inactive window and a coordinate bug are indistinguishable after the fact
- Every hover scan maps its region through the game window's origin before moving the mouse, and refuses to run when that mapping is unavailable. The stack/prompt scan did not map at all: with Magic Arena windowed at x=1519 it drove the cursor to (1248, 190) and (672, 136) — across whatever else was on the desktop, hundreds of pixels left of the game — so it could never hover a stack item, and dragged the mouse out of the window while failing. The battlefield, opponent-battlefield and chooser scans had the subtler half of the same bug: they mapped, then ignored the result that said the mapping had failed, so with no window located they swept raw coordinates too. All of them now abort, and the sweep itself refuses any region outside the window — or any region at all while the window's position is unknown — so a caller that forgets cannot sweep the desktop either
- A second copy of a legendary permanent already on our board is never cast. The action is legal, so Magic Arena offers it and the AI used to pick it — and then the client raises its own "Are You Sure?" confirm, which no game message announces. Observed live: the cast never lands, the hand sweep retries three times, `_dismiss_are_you_sure_if_present` answers No (the right default for a question the bot did not understand, so the loop cannot resolve itself), and the card is finally written off as unreachable although it was in hand the whole time — around 20 seconds of the inactivity timer per decision. Confirming would be no better: the copy resolves and the legend rule immediately bins one of the two, leaving the same board and a card fewer. The check needs no card database — the game state carries `superTypes` on the objects themselves (on hand objects too, not just battlefield ones) and matches the copies on `name`, which is a title id shared by every printing of a card, so an older reprint of a legend is still recognised where a grpId comparison would miss it. It fails open in both directions that matter: an unknown battlefield zone or a card with no supertype information means "cast it", because refusing a second Llanowar Elves, or refusing every cast on the turns Arena has not re-declared its battlefield zone, would cost far more than the loop being avoided. Known gap: land plays go through a separate path that never sees the board, so a duplicate legendary *land* is still cast — no starter or precon deck the bot farms contains one
- Modal card windows are handled: the library browser from "search your library for …" (e.g. Circuitous Route) and the card-ordering window that follows it. The bot reads how many cards it may take and which ones are legal straight from the game's own request — Magic Arena pre-filters the browser to valid choices, so it takes the required number and confirms. Everything behind such a window is unreachable, so while one is open no other move is attempted; previously the bot hunted the hand row for a card it could not click and idled until the game was conceded. The result is verified against the client's own response, and a partial answer is logged as an error rather than passing silently.

### Combat

`AI/Utilities/CombatLogic.py` decides both halves of combat, but only one half is allowed to move the mouse. Every choice is made from Magic Arena's own request rather than from our own reading of the rules: `DeclareAttackersReq` lists exactly which creatures may attack, and `DeclareBlockersReq` hands over the complete legal blocker-to-attacker graph — so flying, menace, "can't block" and summoning sickness never have to be worked out here, and a block that is offered is a block that is legal.

**Attacking: the bot swings with everything, deliberately.** This is not a gap waiting to be filled. The bot exists to farm gold, and the measured numbers say attacking selectively would cost gold rather than earn it. Replaying real `Player.log` files through the selective logic showed it would decline to attack at all on 31% of combats (20% even in the narrowest version, which only holds back a creature that dies to an available block for nothing). Meanwhile the match records say a *lost* match takes longer than a won one — 308s against 285s median — so the thing that hurts throughput is a match that will not end, and holding creatures back is exactly what stops matches ending. The logic also assumes the opponent always blocks optimally, which real opponents frequently do not, so it declines attacks that would in fact have connected. `choose_attackers` is kept, and still logs what it would have done, so the decision can be revisited if selection ever becomes cheap enough to be worth it.

**Blocking: the bot blocks.** Two passes. First the blocks that are good on their own terms — kill the attacker and survive, soak damage for free, or trade evenly or up. Then, only if the damage still coming through would be lethal, it keeps adding blocks until it is not, chumping if that is all that is left. It never spends more creatures than survival actually needs. On the same replay, 72% of block prompts would have produced a block and two otherwise-lethal attacks would have been survived, at a cost of 0.78 assignments per match. Live, across 41 matches, the win rate rose from 17% to 27% — but matches also got longer, and wins became slower than losses rather than faster, which reverses the throughput assumption the attacking decision above was built on. Blocking buys survival, and survival is what makes a match run long. Any future change that makes the bot block *more* has to be measured in gold per hour before it is believed.

Blocking is **on**; switch it off with `MTGA_COMBAT_BLOCKS=0` if a session misbehaves. The first live sessions found the weak point, and it was not the one expected. Magic Arena uses a single bottom-right button for "pass priority" and for "No Blocks", and the ordinary decision loop presses it whenever it has nothing to do — so while a block was still being clicked out, that loop would reach past it and submit an empty block step, and combat damage resolved before a single blocker had been assigned. On ordinary turns every block still landed; on turns where the incoming attack was lethal almost none did, because that is when the prompts come thick enough to lose the race, and three matches the blocking logic had already solved were lost that way. Deciding to block now mutes that button until the blocks are submitted. The mute expires on its own, so a block that goes wrong can never leave the bot unable to pass priority for the rest of the match. Finding the creatures needs no new machinery: the attackers were expected to slide off the opponent's row into the middle of the board during the declare-blockers step, and measuring twelve real captures showed they simply do not. They edge forward but stay inside the opponent's scan region, and our blockers ride up but stay inside ours, so blocking reuses both. Every block prompt still writes a `runtime/debug/declare-block-*` bundle (board capture plus the decision and the regions in play, capped at 12 per session) whether or not blocking is enabled; set `MTGA_COMBAT_BLOCK_CAPTURE=0` to stop it.

When blocking runs, each block is clicked out as blocker-then-attacker and the bottom-right button submits afterwards. The whole sequence is on a 12-second budget and every failure path — a creature that cannot be found, a scan that runs long, an exception — still presses that button, because an unassigned blocker only costs a block whereas a combat that never submits costs the rope and then the match.

Both decisions are also written to `runtime/logs/bot.log` as `COMBAT_SHADOW` lines and attached to the matching decision snapshot under `extra.combat_shadow`, with the numbers behind them (life totals, incoming damage, what would have been left unblocked). Card data is read from the local database only — a combat decision never waits on a network call — and the computation is wrapped so that a failure inside it cannot stop the bot from declaring no blocks and moving on. Disable the logging with `MTGA_COMBAT_SHADOW=0`.

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

## See also on
[elitepvpers](https://www.elitepvpers.com/)
