"""Unit tests for Starter Deck Duel quest-to-deck matcher."""
from __future__ import annotations

import unittest

from AI.Utilities.StarterDeckMatcher import parse_quest_colors, choose_best_starter_deck


class DeckMatcherTests(unittest.TestCase):
    def test_parse_quest_colors(self):
        text = "Cast 20 White or Black spells"
        colors = parse_quest_colors(text)
        self.assertEqual(colors, {"white", "black"})

    def test_choose_best_starter_deck_dual_match(self):
        quests = ["Cast 30 Red or Green spells"]
        deck = choose_best_starter_deck(quests)
        self.assertEqual(deck, "Savage Lands")

    def test_choose_best_starter_deck_fallback(self):
        quests = ["Attack with 45 creatures"]
        deck = choose_best_starter_deck(quests)
        self.assertIsNotNone(deck)
