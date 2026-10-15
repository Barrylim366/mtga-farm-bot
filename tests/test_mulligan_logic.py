"""Unit tests for London Mulligan evaluation and card tucking logic."""
from __future__ import annotations

import unittest
from unittest import mock

from AI.Utilities.MulliganLogic import evaluate_opening_hand, select_cards_to_tuck
from AI.DummyAI import DummyAI


class LondonMulliganTests(unittest.TestCase):
    def test_empty_hand_defaults_to_keep(self):
        keep, reason = evaluate_opening_hand([])
        self.assertTrue(keep)
        self.assertIn("default keep", reason)

    def test_zero_lands_mulligans(self):
        # 7 non-lands
        hand = [{"grpId": 100, "instanceId": i} for i in range(1, 8)]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details") as m:
            m.side_effect = lambda c: {
                "instanceId": c["instanceId"], "name": "Lightning Bolt",
                "is_land": False, "cmc": 1, "types": ["Instant"]
            }
            keep, reason = evaluate_opening_hand(hand)
            self.assertFalse(keep)
            self.assertIn("0 lands", reason)

    def test_all_lands_mulligans(self):
        hand = [{"grpId": 200, "instanceId": i} for i in range(1, 8)]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details") as m:
            m.side_effect = lambda c: {
                "instanceId": c["instanceId"], "name": "Mountain",
                "is_land": True, "cmc": 0, "types": ["Basic Land"]
            }
            keep, reason = evaluate_opening_hand(hand)
            self.assertFalse(keep)
            self.assertIn("All 7 cards are lands", reason)

    def test_ideal_seven_card_hand_keeps(self):
        hand = [{"instanceId": i} for i in range(1, 8)]
        # 3 lands, 4 spells (with early plays)
        def mock_details(c):
            iid = c["instanceId"]
            if iid <= 3:
                return {"instanceId": iid, "name": "Forest", "is_land": True, "cmc": 0}
            elif iid == 4:
                return {"instanceId": iid, "name": "Llanowar Elves", "is_land": False, "cmc": 1}
            else:
                return {"instanceId": iid, "name": "Grizzly Bears", "is_land": False, "cmc": 2}

        with mock.patch("AI.Utilities.MulliganLogic._get_card_details", side_effect=mock_details):
            keep, reason = evaluate_opening_hand(hand)
            self.assertTrue(keep)
            self.assertIn("Ideal land count", reason)

    def test_small_hand_size_preserves_presence(self):
        # Hand size <= 4 should never mulligan further
        hand = [{"instanceId": 1}, {"instanceId": 2}, {"instanceId": 3}]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details") as m:
            m.return_value = {"instanceId": 1, "name": "Card", "is_land": False, "cmc": 5}
            keep, reason = evaluate_opening_hand(hand)
            self.assertTrue(keep)
            self.assertIn("minimum threshold", reason)

    def test_select_cards_to_tuck_excess_lands_first(self):
        # Hand of 4 lands and 3 high-cmc spells, tuck 1 card
        cards = [
            {"instanceId": 1, "name": "Plains", "is_land": True, "cmc": 0},
            {"instanceId": 2, "name": "Plains", "is_land": True, "cmc": 0},
            {"instanceId": 3, "name": "Plains", "is_land": True, "cmc": 0},
            {"instanceId": 4, "name": "Plains", "is_land": True, "cmc": 0},
            {"instanceId": 5, "name": "Serra Angel", "is_land": False, "cmc": 5},
            {"instanceId": 6, "name": "Knight", "is_land": False, "cmc": 2},
            {"instanceId": 7, "name": "Lion", "is_land": False, "cmc": 1},
        ]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details", side_effect=lambda c: c):
            tucked = select_cards_to_tuck(cards, count_to_tuck=1)
            self.assertEqual(tucked, [4])  # 4th excess land tucked

    def test_select_cards_to_tuck_highest_cmc_spells(self):
        # Hand of 2 lands and 5 spells, tuck 2 cards
        cards = [
            {"instanceId": 1, "name": "Island", "is_land": True, "cmc": 0},
            {"instanceId": 2, "name": "Island", "is_land": True, "cmc": 0},
            {"instanceId": 3, "name": "Curiosity", "is_land": False, "cmc": 1},
            {"instanceId": 4, "name": "Storm Crow", "is_land": False, "cmc": 2},
            {"instanceId": 5, "name": "Djinn", "is_land": False, "cmc": 4},
            {"instanceId": 6, "name": "Tidebinder", "is_land": False, "cmc": 3},
            {"instanceId": 7, "name": "Leviathan", "is_land": False, "cmc": 8},
        ]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details", side_effect=lambda c: c):
            tucked = select_cards_to_tuck(cards, count_to_tuck=2)
            self.assertEqual(tucked, [7, 5])  # CMC 8 and CMC 4 tucked


class DummyAIIntegrationTests(unittest.TestCase):
    def test_dummy_ai_generate_keep_delegates(self):
        ai = DummyAI()
        # Empty list should keep by default
        self.assertTrue(ai.generate_keep([]))
