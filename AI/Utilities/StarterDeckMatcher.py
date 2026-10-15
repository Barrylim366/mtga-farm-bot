"""Smart Quest-to-Deck Matcher for Starter Deck Duel.

Analyzes current active quests and selects the dual-color Starter Deck
that maximizes quest progress per game.
"""
from __future__ import annotations

import re

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

    color_demand: dict[str, int] = {c: 0 for c in _COLOR_KEYWORDS}
    for q in quest_texts:
        q_colors = parse_quest_colors(q)
        for c in q_colors:
            color_demand[c] += 1

    # Score each starter deck
    best_deck = None
    best_score = -1

    for (c1, c2), deck_name in _STARTER_DECKS.items():
        score = color_demand[c1] + color_demand[c2]
        # Bonus if both colors contribute to active quests
        if color_demand[c1] > 0 and color_demand[c2] > 0:
            score += 2

        if score > best_score:
            best_score = score
            best_deck = deck_name

    # If no color demand found (e.g. "Attack with 30 creatures", "Kill 15 creatures"),
    # recommend Savage Lands (RG aggro) or Legion of Knights (RW aggro)
    if best_score <= 0:
        return "Savage Lands"

    return best_deck
