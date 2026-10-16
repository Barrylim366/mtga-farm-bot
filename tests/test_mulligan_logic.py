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


    def test_color_mismatch_triggers_mulligan(self):
        # 3 Mountains, but only Blue and Black spells in hand -> color mismatch mulligan
        cards = [
            {"instanceId": 1, "name": "Mountain", "is_land": True, "cmc": 0, "produced_colors": {"red"}, "required_colors": set()},
            {"instanceId": 2, "name": "Mountain", "is_land": True, "cmc": 0, "produced_colors": {"red"}, "required_colors": set()},
            {"instanceId": 3, "name": "Mountain", "is_land": True, "cmc": 0, "produced_colors": {"red"}, "required_colors": set()},
            {"instanceId": 4, "name": "Counterspell", "is_land": False, "cmc": 2, "produced_colors": set(), "required_colors": {"blue"}},
            {"instanceId": 5, "name": "Dark Ritual", "is_land": False, "cmc": 1, "produced_colors": set(), "required_colors": {"black"}},
            {"instanceId": 6, "name": "Murder", "is_land": False, "cmc": 3, "produced_colors": set(), "required_colors": {"black"}},
            {"instanceId": 7, "name": "Cancel", "is_land": False, "cmc": 3, "produced_colors": set(), "required_colors": {"blue"}},
        ]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details", side_effect=lambda c: c):
            keep, reason = evaluate_opening_hand(cards)
            self.assertFalse(keep)
            self.assertIn("Color mismatch", reason)

    def test_matching_colors_keeps(self):
        # 3 Mountains, and Red spells -> should keep
        cards = [
            {"instanceId": 1, "name": "Mountain", "is_land": True, "cmc": 0, "produced_colors": {"red"}, "required_colors": set()},
            {"instanceId": 2, "name": "Mountain", "is_land": True, "cmc": 0, "produced_colors": {"red"}, "required_colors": set()},
            {"instanceId": 3, "name": "Mountain", "is_land": True, "cmc": 0, "produced_colors": {"red"}, "required_colors": set()},
            {"instanceId": 4, "name": "Shock", "is_land": False, "cmc": 1, "produced_colors": set(), "required_colors": {"red"}},
            {"instanceId": 5, "name": "Lightning Strike", "is_land": False, "cmc": 2, "produced_colors": set(), "required_colors": {"red"}},
            {"instanceId": 6, "name": "Dragon", "is_land": False, "cmc": 5, "produced_colors": set(), "required_colors": {"red"}},
            {"instanceId": 7, "name": "Phoenix", "is_land": False, "cmc": 4, "produced_colors": set(), "required_colors": {"red"}},
        ]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details", side_effect=lambda c: c):
            keep, reason = evaluate_opening_hand(cards)
            self.assertTrue(keep)

    def test_select_cards_to_tuck_fallback_index_when_missing_instance_id(self):
        # Cards with no instanceId skip grpId (to prevent protocol errors) and fall back to index tokens
        cards = [
            {"grpId": 101, "name": "Island", "is_land": True, "cmc": 0},
            {"grpId": 102, "name": "Island", "is_land": True, "cmc": 0},
            {"grpId": 103, "name": "Island", "is_land": True, "cmc": 0},
            {"grpId": 104, "name": "Island", "is_land": True, "cmc": 0},
            {"grpId": 201, "name": "Big Spell", "is_land": False, "cmc": 7},
        ]
        with mock.patch("AI.Utilities.MulliganLogic._get_card_details", side_effect=lambda c: c):
            tucked = select_cards_to_tuck(cards, count_to_tuck=1)
            self.assertEqual(len(tucked), 1)
            self.assertEqual(tucked[0], 0)

    def test_get_card_details_uses_local_card_lookup(self):
        from AI.Utilities.MulliganLogic import _get_card_details
        with mock.patch("AI.Utilities.CardInfo.get_card_info_local", return_value={"name": "Plains", "types": ["Basic Land"], "manaCost": ""}) as mock_local:
            with mock.patch("AI.Utilities.CardInfo.get_card_info") as mock_remote:
                details = _get_card_details({"grpId": 12345})
                self.assertEqual(details["name"], "Plains")
                self.assertTrue(details["is_land"])
                mock_local.assert_called_once_with(12345)
                mock_remote.assert_not_called()


class DummyAIIntegrationTests(unittest.TestCase):
    def test_dummy_ai_generate_keep_delegates(self):
        ai = DummyAI()
        # Empty list should keep by default
        self.assertTrue(ai.generate_keep([]))
