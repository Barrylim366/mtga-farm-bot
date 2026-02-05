# MTGA Bot

Automated MTGA bot with UI, calibration, account switching, and quest-based deck selection.

## Requirements

- Windows 10/11
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

2) Calibrate buttons via **Calibrate**:
   - Required: keep_hand, queue_button, next, concede, attack_all, opponent_avatar, hand_scan_p1, hand_scan_p2, assign_damage_done
   - Logout flow uses: log_out_btn, log_out_ok_btn

3) Settings:
   - **Switch Account** opens a window for **Switch account (min)** and **Account Play Order** (use **Save Order**).
     Saving shows a short confirmation.
   - **Record Action** opens a window for **Record** (uses F8 to stop) and **Show Records**.

4) Start Bot.

Stop bot any time with **Mouse Wheel Down**.

## Account Switching

- Accounts are defined in `credentials.txt` (do not commit secrets).
- Switch happens when the timer expires and the bot reaches a safe screen.
- Logout/login uses recorded action sequence + credentials injection.
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
3) selects a deck image from `Acc_1/Acc_2/Acc_3` folder that best matches quest colors

Deck images are matched by filename letters (e.g. `RG.png`, `WU.png`, `R.png`).
The `Acc_1`, `Acc_2`, `Acc_3` and `Buttons` folders are kept in Git, but their
contents are ignored (see .gitignore). Keep your local images there.

## Casting Logic

In main phases the bot tries to use as much available mana as possible across all castable spells:
- It chooses casts that maximize total CMC spent this turn.
- If multiple options spend the same total, it prefers a single higher-cost spell
  over multiple cheaper spells.
- Multi-spell plans are validated against color requirements, not just CMC.
 - Convoke is supported using untapped creatures as colored mana sources.

## Decision Safety

The bot defers decisions while the game reports pending messages to avoid acting
mid-resolution or while the UI is still busy.
It also auto-confirms mana payment prompts when MTGA requests pay costs.
In main phases, decisions are also deferred while the stack contains objects.

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
