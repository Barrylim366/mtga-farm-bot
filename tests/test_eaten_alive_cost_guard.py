"""Regression for Eaten Alive's sacrifice-or-mana additional cost.

Arena offers its base {B} cost in ActionType_Cast, but the card cannot resolve
unless we can sacrifice a creature or pay the 3B alternative. Selecting it with
two lands and an empty board left the live match stuck in its casting flow.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from AI.DummyAI import DummyAI
import AI.Utilities.CardPolicy as CardPolicy


class _GameState:
    def get_actions(self):
        return [{"action": {
            "actionType": "ActionType_Cast",
            "instanceId": 10,
            "manaCost": [{"color": ["ManaColor_Black"], "count": 1}],
        }}]

    def get_game_objects(self):
        return [
            {
                "instanceId": 10,
                "grpId": 93885,
                "zoneId": 35,
                "controllerSeatId": 1,
                "cardTypes": ["CardType_Sorcery"],
            },
            {
                "instanceId": 20,
                "grpId": 1,
                "zoneId": 28,
                "controllerSeatId": 2,
                "cardTypes": ["CardType_Creature"],
                "power": 3,
                "toughness": 1,
            },
        ]

    def get_turn_info(self):
        return {
            "activePlayer": 1,
            "decisionPlayer": 1,
            "priorityPlayer": 1,
            "phase": "Phase_Main2",
            "step": "Step_EndCombat",
            "turnNumber": 4,
        }

    def get_players(self):
        return [
            {"systemSeatNumber": 1, "lifeTotal": 19},
            {"systemSeatNumber": 2, "lifeTotal": 20},
        ]

    def get_full_state(self):
        return {
            "zones": [{
                "zoneId": 28,
                "type": "ZoneType_Battlefield",
                "objectInstanceIds": [20],
            }]
        }


class EatenAliveCostGuardTest(unittest.TestCase):
    def _move_with_mana(self, total_mana):
        ai = DummyAI()
        ai._debug = lambda message: None
        ai._get_available_mana_colors = lambda *args: (
            {"black", "green"}, total_mana,
            [{"black"}, {"green"}] + [set()] * max(0, total_mana - 2),
        )
        return ai.generate_move(_GameState(), {10: 93885, 20: 1})

    def test_empty_board_and_two_mana_do_not_start_the_cast(self):
        move = self._move_with_mana(2)

        self.assertNotEqual(move, {"cast": [10]})

    def test_empty_board_and_three_mana_do_not_start_the_cast(self):
        self.assertNotEqual(self._move_with_mana(3), {"cast": [10]})

    def test_four_mana_pays_the_non_sacrifice_alternative(self):
        self.assertEqual(self._move_with_mana(4), {"cast": [10]})

    def test_known_alternate_cost_is_exposed_to_the_ai(self):
        self.assertEqual(CardPolicy.sacrifice_or_alternate_total_mana(93885), 4)
        self.assertIsNone(CardPolicy.sacrifice_or_alternate_total_mana(0))


if __name__ == "__main__":
    unittest.main()
