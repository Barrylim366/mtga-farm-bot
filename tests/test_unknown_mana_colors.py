import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from AI.DummyAI import DummyAI


class UnknownManaColorTest(unittest.TestCase):
    def test_treasure_token_is_one_any_color_source_without_scryfall(self):
        ai = DummyAI()
        debug = []
        ai._debug = debug.append
        actions = [{"action": {
            "actionType": "ActionType_Activate_Mana",
            "instanceId": 304,
            "abilityGrpId": 183,
        }}]
        objects = [{
            "instanceId": 304,
            "grpId": 94178,
            "type": "GameObjectType_Token",
            "cardTypes": ["CardType_Artifact"],
            "subtypes": ["SubType_Treasure"],
        }]
        with patch("AI.DummyAI.CardInfo.get_land_produced_colors") as scryfall_lookup:
            colors, total, sources = ai._get_available_mana_colors(
                actions, {304: 94178}, objects
            )
        scryfall_lookup.assert_not_called()
        self.assertEqual(colors, {"white", "blue", "black", "red", "green"})
        self.assertEqual(total, 1)
        self.assertEqual(sources, [colors])
        self.assertEqual(ai._unknown_mana_sources, {})
        self.assertFalse(any("UNRESOLVED_MANA_DIAGNOSTIC" in line for line in debug))
        self.assertTrue(ai._can_cast_with_mana_cost(
            [{"color": ["ManaColor_Blue"], "count": 1}], colors, total, sources
        ))
        self.assertFalse(ai._can_cast_with_mana_cost(
            [{"color": ["ManaColor_Blue"], "count": 2}], colors, total, sources
        ))

    def test_unresolved_land_counts_for_generic_but_not_colored_costs(self):
        ai = DummyAI()
        debug = []
        ai._debug = debug.append
        actions = [{"action": {
            "actionType": "ActionType_Activate_Mana",
            "instanceId": 12,
            "grpId": 94178,
            "abilityGrpId": 1039,
        }}]
        with patch("AI.DummyAI.CardInfo.get_mana_color_from_ability", return_value=None), \
             patch("AI.DummyAI.CardInfo.get_land_produced_colors", return_value=set()):
            colors, total, sources = ai._get_available_mana_colors(
                actions, {12: 94178}, [{
                    "instanceId": 12,
                    "grpId": 94178,
                    "cardTypes": ["CardType_Land"],
                    "subtypes": ["SubType_Island"],
                    "zoneId": 28,
                }]
            )

        self.assertEqual(colors, set())
        self.assertEqual(total, 1)
        self.assertEqual(sources, [])
        self.assertTrue(ai._can_cast_with_mana_cost(
            [{"color": ["ManaColor_Generic"], "count": 1}], colors, total, sources
        ))
        self.assertFalse(ai._can_cast_with_mana_cost(
            [{"color": ["ManaColor_Blue"], "count": 1}], colors, total, sources
        ))
        diagnostic = next(line for line in debug if "UNRESOLVED_MANA_DIAGNOSTIC" in line)
        self.assertIn('"abilityGrpId": 1039', diagnostic)
        self.assertIn('"subtypes": ["SubType_Island"]', diagnostic)
        self.assertIn('"status": "unknown"', diagnostic)
        self.assertEqual(ai._unknown_mana_sources[12]["abilityGrpIds"], (1039,))

    def test_unknown_diagnostics_keep_distinct_abilities_and_clear_when_resolved(self):
        ai = DummyAI()
        debug = []
        ai._debug = debug.append
        def action(ability):
            return {"action": {"actionType": "ActionType_Activate_Mana",
                               "instanceId": 12, "grpId": 94178, "abilityGrpId": ability}}
        with patch("AI.DummyAI.CardInfo.get_mana_color_from_ability", return_value=None), \
             patch("AI.DummyAI.CardInfo.get_land_produced_colors", return_value=set()):
            ai._get_available_mana_colors([action(1039), action(1040)], {})
            ai._get_available_mana_colors([action(1039), action(1040)], {})
        self.assertEqual(ai._unknown_mana_sources[12]["grpId"], 94178)
        self.assertEqual(ai._unknown_mana_sources[12]["abilityGrpIds"], (1039, 1040))
        diagnostics = [line for line in debug if "UNRESOLVED_MANA_DIAGNOSTIC" in line]
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('"abilityGrpId": 1040', diagnostics[0])
        with patch("AI.DummyAI.CardInfo.get_mana_color_from_ability", return_value="blue"):
            colors, total, sources = ai._get_available_mana_colors([action(1039)], {})
        self.assertEqual(ai._unknown_mana_sources, {})
        self.assertEqual((colors, total, sources), ({"blue"}, 1, [{"blue"}]))
        ai.reset()
        self.assertEqual(ai._unknown_mana_sources, {})


if __name__ == "__main__":
    unittest.main()
