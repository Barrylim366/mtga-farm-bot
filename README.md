# mtga_bot

Ein modularer, erweiterbarer Bot für Magic: The Gathering Arena, der sich auf tägliche Quests fokussiert und leicht um zusätzliche Strategien, Decks oder UI-Hooks ergänzt werden kann.

## Features
- Log-Parser (`Player.log`) mit einfachen Heuristiken für Quest-, Queue- und Match-Events.
- GameState-State-Machine mit Quest-Tracking.
- Austauschbare Quest-Strategien (Strategy Pattern) und Plugin-Hook `register_strategy`.
- UI-Controller auf Basis von `pyautogui` (standardmäßig im Dry-Run-Modus).
- Beispielkonfiguration (`config.example.json`) und Deck-Definitionen (`decks.example.json`).
- Unit-Tests für Parser und State-Model.

## Setup
1. Python 3.10+ installieren.
2. Abhängigkeiten installieren:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
   pip install -r requirements.txt
   ```
3. Konfiguration anlegen:
   ```bash
   cp config.example.json config.json
   # Passe log_path, default_deck, dry_run etc. an.
   ```
4. Optional: Decks definieren:
   ```bash
   cp decks.example.json decks.json
   # Ergänze deine Decks/Farben.
   ```

## Start
```bash
python -m mtga_bot.main --config config.json
```

Der UI-Controller läuft standardmäßig im Dry-Run-Modus und loggt nur Aktionen. Setze `dry_run` in der Config auf `false`, wenn echte Klicks gesendet werden sollen.

## Tests
```bash
python -m unittest
```
