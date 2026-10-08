# Forge brain

Uses the [Forge](https://github.com/Card-Forge/forge) rules engine and AI to choose plays for
this bot.

The bot keeps doing everything it already does (reading `Player.log`, finding cards on screen,
clicking). What changes is who picks the move: at each decision the current Arena position is rebuilt
inside Forge, Forge's AI is asked what it would do, and the bot carries that out. If Forge has no
usable answer, the bot's own choice is used, exactly as before.

## Layout

| Path | What it is |
| --- | --- |
| `forge_bridge/` | Python: Arena log tracker, position builder, advisor client, the hooks into the bot. |
| `forge_advisor/` | Java process that loads a position into Forge and returns a decision. |
| `forge_tests/` | Unit tests and hand-built positions (the bot's own suite stays in `tests/`). |
| `scripts/build_advisor.py`, `scripts/start_bot.py` | Build the advisor; start the bot with the hooks installed. |
| `start_with_forge.bat` | Windows launcher for the above. |

None of the bot's own files are changed. `scripts/start_bot.py` imports the bot, swaps a handful of
its functions in memory, and runs its normal entry point. Starting `ui.py` or `start_windows.bat`
directly gives the plain bot.

Two things are not in this repository and have to be provided:

- **A clone of Forge**, as `forge/` inside this repository or next to it:
  `git clone --depth 1 https://github.com/Card-Forge/forge.git`
- **A JDK 17 and Maven**, unpacked into `forge_tools/` inside this repository, or `tools/` next to it
  (for example Eclipse Temurin 17 and Apache Maven 3.9). Nothing needs installing system-wide.
  `BURNING_FORGE_JAVA_HOME` can point at an existing JDK instead.

`BURNING_FORGE_HOME` names another folder to look in for `forge/` and `forge_tools/`.

## Build

```bash
python scripts/build_advisor.py --forge
```

Builds Forge's `forge-core`, `forge-game`, `forge-ai` and `forge-gui` modules, then compiles the
advisor against them. After editing only the advisor, drop `--forge`. After `git pull` in the Forge
clone, run it with `--forge` again. The build writes one ignored settings file into Forge's
`forge-gui/` so Forge keeps its preferences under `forge_advisor/run/`.

## Check it without a game

```bash
python -m unittest forge_tests.test_tracker forge_tests.test_forge_ai forge_tests.test_pointer forge_tests.test_scry_hook forge_tests.test_attack_hook forge_tests.test_advisor_scenarios
```

```bash
python -m forge_bridge.replay --verbose
```

`replay` runs a recorded `Player.log` through Forge and prints, for every decision in it, what Forge
would have done next to what was actually played, plus how cleanly each position loaded.

```bash
python -m forge_bridge.dryrun --verbose
```

`dryrun` goes one step further: it runs the bot's real AI class with the Forge hook installed over
the same log and reports the move the bot would have made. No game client, no clicks.

Both accept one or more log paths; with none they read the client's current `Player.log`.

## Run the bot with Forge

1. Run `start_windows.bat` once. It creates the bot's Python environment and walks
   through the bot's own first-time setup. Close the bot.
2. Start `start_with_forge.bat`.

The advisor starts in the background (about ten seconds). Until it is ready the bot plays as it
always did. Forge's choices are written to the bot's log as `FORGE ...` lines and, in full, to
`forge_advisor/run/decisions.jsonl`.

### Settings

Environment variables, read at every decision:

| Variable | Values | Default |
| --- | --- | --- |
| `MTGA_FORGE` | `on`, `shadow` (log Forge's choice, play the bot's), `off` | `on` |
| `MTGA_FORGE_BLOCKS` | `on`, `off` | `on` |
| `MTGA_FORGE_DECKS` | `on`, `off` (deck picked from colour icons) | `on` |
| `MTGA_FORGE_ATTACK` | `select` (attack with Forge's creatures), `shadow` (log only), `gate` | `select` |
| `MTGA_FORGE_FALLBACK` | `bot` (no Forge play: the bot decides), `pass` | `bot` |
| `MTGA_FORGE_WINDOW` | `on`, `off` (the Forge Brain window listing each move and why) | `on` |
| `MTGA_FORGE_INSTANTS` | `on`, `off` (cast instants and flash cards outside our main phases) | `on` |
| `MTGA_FORGE_POINTER` | `on`, `off` (slower, self-checking cursor sweep on the battlefield) | `on` |
| `MTGA_FORGE_HOVER_DWELL` | seconds the cursor waits at each point of that sweep | `0.09` |
| `MTGA_FORGE_SCRY` | `on` (drag unwanted cards away), `advise` (log only), `off` | `on` |
| `MTGA_BOT_UPDATES` | `off`, `on` (let the bot offer its own updates) | `off` |
| `MTGA_FORGE_TIMEOUT` | seconds per decision | `4` |
| `MTGA_PLAYER_LOG` | path to `Player.log` | the Windows default |
| `MTGA_DATA_DIR` | `<MTGA>/MTGA_Data/Downloads/Raw` | found from `Player.log` |

For a first live session, `MTGA_FORGE=shadow` shows what Forge would do without it doing anything.
In PowerShell:

```powershell
$env:MTGA_FORGE = "shadow"; .\start_with_forge.bat
```

The variable stays set for that PowerShell window. `Remove-Item Env:MTGA_FORGE` clears it.

## What Forge decides

**Main phase.** Which land to play and which spell to cast, limited to the actions Arena is offering.
Forge's pick is then put to the bot's own AI as the only option; it is cast only if the bot agrees it
can (it can pay, and it is not a card whose prompts the bot cannot click through). Forge's AI likes to
hold creatures for the second main phase; because Arena does not always stop there, a card Forge
would cast after combat is cast before it instead.

**Instants.** When the bot holds priority outside its own main phases with nothing on the stack (the
opponent's turn, a combat step, an end step) and would only pass, Forge is asked in that phase, with
the attackers and blockers Arena shows. A card it wants to cast still needs the bot's agreement.

**Scry and surveil.** Forge says which cards to keep on top. The others are dragged out of the pile
(to the bottom side for a scry, into the Graveyard box for a surveil) before the bot presses Done.
Drop positions were measured from one screenshot of each overlay and have not been tried live.

**Blocks.** Forge's blocks replace the bot's, filtered through the legal blocker list Arena sends.

**Attacks.** The bot can only attack with everything, so by default Forge's attack is only logged,
together with a score for attacking with everything and for not attacking. Each score comes from
playing the attack out on a copy of the game: the opponent blocks, damage is dealt, the opponent takes
their next turn and attacks back, and the resulting board is evaluated. With `MTGA_FORGE_ATTACK=gate`,
when not attacking scores higher the bot passes the turn (the Enter key) instead of going to combat.

## Picking the deck (Historic)

The bot normally finds a deck on the My Decks grid by matching a thumbnail you saved per deck
(`UR.png`, ...). With the launcher here it first reads the mana-colour icons Arena draws on every
deck tile and clicks the tile whose colours fit the quest: the exact colours if a deck has them,
otherwise the deck sharing the most. No thumbnails are needed for that. Quests that force a specific
deck file (creature-type quests, `C.png`) and any case where no tile fits still use the bot's
thumbnail method. Lines start with `FORGE decks:` in the bot's log. `MTGA_FORGE_DECKS=off` turns it off.

Checked against two screenshots of the client at three window sizes; not yet run live.

## How a position gets into Forge

- `forge_bridge/tracker.py` follows the game state from `Player.log`. Arena re-sends changed objects
  whole with default values left out, so each one replaces the previous copy.
- `forge_bridge/carddb.py` reads card names from the client's own card database.
- `forge_bridge/snapshot.py` describes the board from our side: zones, tapped, summoning sick,
  counters, damage, current power/toughness, keywords.
- `forge_advisor/.../Position.java` writes that in Forge's dev-mode state format and lets Forge's loader
  build the game. Then Arena's numbers win: where Forge computes a different power/toughness (an
  until-end-of-turn effect it never saw), the difference is applied as a boost.
- Tokens use Forge's token scripts when one matches. A permanent Forge has no script for becomes a
  plain body with Arena's types, power/toughness and keywords. Unknown cards in hand are left out.
- The opponent's hand and both libraries are filled with placeholder cards of the right count.

## Limits

- **Live testing so far is partial.** The log tracking, Forge's decisions and the moves window have
  run in real matches. Selective attacks, "No Attacks", the scry/surveil drag, instant-speed casts,
  the cursor sweep and the deck picker were verified with tests and screenshots at 1600x900 and have
  had little or no live use.
- Nothing is asked of Forge while a spell or ability is on the stack. Targets and modes are still
  chosen by the bot's own code.
- Fixed screen positions (No Attacks, the scry and surveil drop points) were measured from single
  screenshots; the surveil one came from an image at another resolution.
- Aura and equipment attachments are read from an annotation type that did not occur in the logs
  used for testing.
- Forge's AI never sees the opponent's hand, so it does not play around tricks.
- The hooks reach into the bot's internals by name. A bot update can rename them; the launcher
  therefore switches the bot's update check off unless `MTGA_BOT_UPDATES=on`.

## Licence

Forge is GPL-3.0. The advisor links against it, so distributing the advisor means distributing it
under GPL-3.0 as well.
