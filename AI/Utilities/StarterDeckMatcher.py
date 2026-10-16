"""Smart Quest-to-Deck Matcher for Starter Deck Duel.

Analyzes current active quests and selects the dual-color Starter Deck
that maximizes quest progress per game.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from runtime_paths import runtime_file

# Standard MTGA Starter Deck Duel decks mapped to color pairs
_STARTER_DECKS = {
    ("white", "blue"): "Aerial Aggression",
    ("white", "black"): "Grave Matters",
    ("white", "red"): "Legion of Knights",
    ("white", "green"): "Selesnya Lifegain",
    ("blue", "black"): "Subtle Thoughts",
    ("blue", "red"): "Spellweaver",
    ("blue", "green"): "Primal Surge",
    ("black", "red"): "Chaos Unleashed",
    ("black", "green"): "Gorgon's Gaze",
    ("red", "green"): "Savage Lands",
}

_COLOR_KEYWORDS = {
    "white": "white",
    "blue": "blue",
    "black": "black",
    "red": "red",
    "green": "green",
}


def _load_starter_decks() -> dict[tuple[str, str], str]:
    """Load starter decks from config/starter_decks.json if present, falling back to defaults."""
    decks = dict(_STARTER_DECKS)
    try:
        path = runtime_file("config", "starter_decks.json")
        if path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    if isinstance(k, str) and "," in k:
                        c1, c2 = [c.strip().lower() for c in k.split(",", 1)]
                        decks[(c1, c2)] = str(v)
    except Exception:
        pass
    return decks


def parse_quest_colors(quest_text: str) -> set[str]:
    """Extract colors referenced in a quest description."""
    text = quest_text.lower()
    found = set()
    for color, kw in _COLOR_KEYWORDS.items():
        if re.search(rf"\b{kw}\b", text):
            found.add(color)
    return found


def choose_best_starter_deck(quest_texts: list[str]) -> str | None:
    """Find the best starter deck matching current quest requirements.
    
    If multiple quests exist, weights decks that satisfy multiple quests.
    """
    if not quest_texts:
        return None

    parsed_quests: list[set[str]] = []
    color_demand: dict[str, int] = {c: 0 for c in _COLOR_KEYWORDS}
    for q in quest_texts:
        q_colors = parse_quest_colors(q)
        if q_colors:
            parsed_quests.append(q_colors)
            for c in q_colors:
                color_demand[c] += 1

    # Score each starter deck
    best_deck = None
    best_score = -1

    decks = _load_starter_decks()
    for (c1, c2), deck_name in decks.items():
        # Count how many distinct active quests this color pair advances
        quests_satisfied = sum(1 for qc in parsed_quests if (c1 in qc or c2 in qc))
        color_overlap = color_demand.get(c1, 0) + color_demand.get(c2, 0)
        score = (quests_satisfied * 10) + color_overlap

        # Bonus if both colors contribute to active quests
        if color_demand.get(c1, 0) > 0 and color_demand.get(c2, 0) > 0:
            score += 2

        if score > best_score:
            best_score = score
            best_deck = deck_name

    # If no color demand found (e.g. "Attack with 30 creatures", "Kill 15 creatures"),
    # recommend Savage Lands (RG aggro) or Legion of Knights (RW aggro)
    if best_score <= 0:
        return decks.get(("red", "green")) or decks.get(("green", "red")) or "Savage Lands"

    return best_deck
