"""Unit tests for counterspell detection and threat-based target selection."""
from __future__ import annotations

import unittest
from unittest import mock

import AI.Utilities.CounterLogic as CounterLogic


class CounterLogicTests(unittest.TestCase):
    def test_can_counter_restrictions(self):
        creature_obj = {"cardTypes": ["CardType_Creature"]}
        sorcery_obj = {"cardTypes": ["CardType_Sorcery"]}

        self.assertTrue(CounterLogic.can_counter({"restrict": "any"}, creature_obj))
        self.assertTrue(CounterLogic.can_counter({"restrict": "any"}, sorcery_obj))

        self.assertTrue(CounterLogic.can_counter({"restrict": "creature"}, creature_obj))
        self.assertFalse(CounterLogic.can_counter({"restrict": "creature"}, sorcery_obj))

        self.assertFalse(CounterLogic.can_counter({"restrict": "noncreature"}, creature_obj))
        self.assertTrue(CounterLogic.can_counter({"restrict": "noncreature"}, sorcery_obj))

    def test_score_spell_threat(self):
        bomb = {
            "cardTypes": ["CardType_Creature"],
            "power": 6,
            "toughness": 6,
            "grpId": 1001,
        }
        cantrip = {
            "cardTypes": ["CardType_Instant"],
            "grpId": 1002,
        }
        planeswalker = {
            "cardTypes": ["CardType_Planeswalker"],
            "grpId": 1003,
        }

        def mock_card_info(grp_id):
            if grp_id == 1001:
                return {"manaCost": "{4}{G}{G}", "rarity": "mythic"}
            if grp_id == 1002:
                return {"manaCost": "{U}", "rarity": "common"}
            if grp_id == 1003:
                return {"manaCost": "{3}{U}{U}", "rarity": "rare"}
            return {}

        with mock.patch("AI.Utilities.CardInfo.get_card_info_local", side_effect=mock_card_info):
            score_bomb = CounterLogic.score_spell_threat(bomb)
            score_cantrip = CounterLogic.score_spell_threat(cantrip)
            score_pw = CounterLogic.score_spell_threat(planeswalker)

            self.assertGreater(score_bomb, score_cantrip)
            self.assertGreater(score_pw, score_cantrip)

    def test_find_counterable_spell_prioritizes_higher_threat(self):
        # Two spells on stack: spell 10 (cantrip, top of stack) vs spell 20 (Sheoldred bomb)
        game_objects = [
            {
                "instanceId": 20,
                "controllerSeatId": 2,
                "zoneId": 5,
                "cardTypes": ["CardType_Creature"],
                "power": 4,
                "toughness": 5,
                "grpId": 8001,
            },
            {
                "instanceId": 10,
                "controllerSeatId": 2,
                "zoneId": 5,
                "cardTypes": ["CardType_Instant"],
                "grpId": 8002,
            },
        ]
        full_state = {
            "zones": [
                {"zoneId": 5, "type": "ZoneType_Stack", "objectInstanceIds": [20, 10]}
            ]
        }

        def mock_card_info(grp_id):
            if grp_id == 8001:
                return {"manaCost": "{2}{B}{B}", "rarity": "rare"}
            if grp_id == 8002:
                return {"manaCost": "{U}", "rarity": "common"}
            return {}

        with mock.patch("AI.Utilities.CardInfo.get_card_info_local", side_effect=mock_card_info):
            target = CounterLogic.find_counterable_spell(
                profile={"restrict": "any"},
                game_objects=game_objects,
                my_seat=1,
                stack_ids={5},
                full_state=full_state,
            )
            # Despite spell 10 being topmost, spell 20 is the game-winning bomb threat
            self.assertEqual(target, 20)
