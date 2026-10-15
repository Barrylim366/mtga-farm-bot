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

    def test_custom_config_override(self):
        from unittest import mock
        from pathlib import Path
        custom_data = '{"white,black": "Custom Orzhov Deck", "red,green": "Custom Gruul Deck"}'
        with mock.patch("pathlib.Path.is_file", return_value=True):
            with mock.patch("builtins.open", mock.mock_open(read_data=custom_data)):
                deck = choose_best_starter_deck(["Cast 20 White or Black spells"])
                self.assertEqual(deck, "Custom Orzhov Deck")
