Prompt:
"Erstelle einen erweiterbaren Python-Bot für Magic: The Gathering Arena (MTG Arena), der automatisch tägliche Quests erledigt, ähnlich wie im GitHub-Repo https://github.com/misprit7/MTGAI. Der Bot soll modular aufgebaut sein, um später komplexe Features wie das Spielen mit verschiedenen Decks, adaptive AI-Entscheidungen oder Multi-Account-Support hinzuzufügen.
Kernanforderungen:

Ziel: Der Bot startet MTG Arena, wählt ein einfaches Deck (z. B. ein Mono-Color-Deck für Quests wie 'Spiele 4 Spiele' oder 'Wirke 10 Zauber einer Farbe'), spielt minimale Spiele, um Quests abzuschließen, und beendet sich sicher. Vermeide vollständige AI-Spielsimulation zunächst – fokussiere auf UI-Automatisierung und grundlegende Entscheidungen (z. B. 'Mulligan nie', 'Immer angreifen', 'Einfache Zauber wirken').
Modularität (inspiriert von MTGAI): Strukturiere den Code in separate Module für einfache Erweiterung:
Log Parser (log_parser.py): Lies Echtzeit-Logs aus %APPDATA%\..\LocalLow\Wizards Of The Coast\MTGA\Player.log. Parse Game-State (z. B. Quest-Fortschritt, Phase des Spiels, Mana-Verfügbarkeit). Verwende Regex oder eine Library wie re für Events wie 'Quest updated' oder 'Turn begin'.
Game Model (game_model.py): Ein State-Machine-ähnliches Modell (z. B. Klasse GameState), das den aktuellen Spielzustand trackt (z. B. 'In Queue', 'Playing', 'Quest Complete'). Integriere Quest-Tracking (z. B. Dict mit Quest-ID und Fortschritt).
Quest AI (quest_ai.py): Einfache Entscheidungslogik für Quests. Z. B. Funktion get_action(state) die basierend auf Quest-Typ (z. B. 'Play X Games' → Queue und Surrender nach X Zügen; 'Cast Y Spells' → Priorisiere Zauber-Wirken). Mach es erweiterbar: Verwende eine Strategy-Pattern-Klasse, die später für Deck-spezifische AIs erweitert werden kann (z. B. DeckStrategy mit Subklassen pro Deck).
UI Controller (ui_controller.py): Verwende pyautogui für Bildschirm-Interaktionen (z. B. Klicks auf 'Queue', 'Surrender', Drag-and-Drop für Karten). Inkludiere Bilderkennung mit opencv-python oder pyautogui.locateOnScreen für UI-Elemente (z. B. suche nach Quest-Icons). Füge Pausen und Randomisierung hinzu, um Bans zu vermeiden.
Main Bot (main.py): Koordiniert alles: Starte mit Config-Laden (z. B. aus config.json mit Username, Log-Pfad, Deck-ID), loop über Quest-Check, führe Aktionen aus, logge Fortschritt. Inkludiere Error-Handling (z. B. bei Disconnects) und Graceful Shutdown (z. B. via KeyboardInterrupt).

Erweiterbarkeit priorisieren:
Verwende abstrakte Basisklassen (z. B. BaseAI für zukünftige Deck-AIs).
Konfiguriere Decks in JSON (z. B. decks.json mit Deck-Namen, Farben, Strategien).
Mach den Quest-Tracker skalierbar für Weekly Quests oder Events.
Füge Hooks für Plugins hinzu (z. B. register_strategy() Funktion).


Technische Details:

Python 3.10+, Dependencies: pyautogui, opencv-python, pynput (für Input-Überwachung), json (built-in).
Erstelle eine requirements.txt.
Inkludiere eine config.json-Vorlage mit Feldern wie "log_path", "player_name", "default_deck": "mono_red".
Testbarkeit: Füge Unit-Tests für Parser und Model (z. B. mit unittest).
wenn dir besser lösungen einfallen fühle dich frei diese zu verwenden
