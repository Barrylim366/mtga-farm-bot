"""Tests for picking WHICH card to bring back from a graveyard prompt.

Reproduces the 2026-07-17 19:46 incident: Fiendish Panda's death trigger
("return another target non-Bear creature card ... from your graveyard") offered
two options and Controller.__pick_chooser_target returned the first one it
iterated over, with no ranking at all:

    SelectTargetsReq details: sourceId=383, targetCount=2, legalTargets=[340, 378]
    SelectTargetsReq (from game state, chooser): choosing card instanceId=340

The ranking is by ROLE in the deck's lifegain engine, never by raw stats:
Ajani's Pridemate is a 2/2 and must still outrank a 2/3 Vampire Nighthawk.

Controller uses name-mangled double-underscore attributes; from outside the class
body they must be accessed as `_Controller__name`.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import AI.Utilities.LifegainLogic as LifegainLogic
from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState

BATTLEFIELD_ZONE = 28
GRAVEYARD_ZONE = 33
STACK_ZONE = 27

AJANIS_PRIDEMATE = 93848      # 2/2, payoff
TWINBLADE_PALADIN = 93652     # 3/3, payoff
FIENDISH_PANDA = 93833        # 3/2, payoff
HINTERLAND_SANCTIFIER = 93636  # 1/2, enabler
VAMPIRE_NIGHTHAWK = 93899     # 2/3, enabler + flying + lifelink
HEALERS_HAWK = 93855          # 1/1, enabler + flying + lifelink
PLAINS = 95191                # not a creature

PT = {
    AJANIS_PRIDEMATE: (2, 2),
    TWINBLADE_PALADIN: (3, 3),
    FIENDISH_PANDA: (3, 2),
    HINTERLAND_SANCTIFIER: (1, 2),
    VAMPIRE_NIGHTHAWK: (2, 3),
    HEALERS_HAWK: (1, 1),
}


def card(instance_id, grp_id, *, zone=GRAVEYARD_ZONE, creature=True):
    power, toughness = PT.get(grp_id, (0, 0))
    obj = {
        "instanceId": instance_id,
        "grpId": grp_id,
        "zoneId": zone,
        "cardTypes": ["CardType_Creature"] if creature else ["CardType_Land"],
        "power": {"value": power},
        "toughness": {"value": toughness},
    }
    return obj


class TierRankingTest(unittest.TestCase):
    def test_the_three_payoffs_are_the_top_tier(self):
        for grp_id in (AJANIS_PRIDEMATE, TWINBLADE_PALADIN, FIENDISH_PANDA):
            self.assertEqual(
                LifegainLogic.creature_tier(grp_id), LifegainLogic.TIER_PAYOFF, grp_id
            )

    def test_lifelink_flier_is_an_evasive_enabler(self):
        for grp_id in (VAMPIRE_NIGHTHAWK, HEALERS_HAWK):
            self.assertEqual(
                LifegainLogic.creature_tier(grp_id),
                LifegainLogic.TIER_ENABLER_EVASIVE,
                grp_id,
            )

    def test_gain_life_text_alone_is_a_plain_enabler(self):
        self.assertEqual(
            LifegainLogic.creature_tier(HINTERLAND_SANCTIFIER),
            LifegainLogic.TIER_ENABLER,
        )

    def test_small_payoff_outranks_a_bigger_enabler(self):
        """The rule the whole design hangs on: Ajani's Pridemate is a 2/2 and
        must beat a 2/3 Vampire Nighthawk, because body size may never promote a
        card across a tier."""
        best = LifegainLogic.best_creature([
            card(1, VAMPIRE_NIGHTHAWK),
            card(2, AJANIS_PRIDEMATE),
        ])
        self.assertEqual(best["grpId"], AJANIS_PRIDEMATE)

    def test_body_only_breaks_ties_inside_a_tier(self):
        """Two payoffs of the same cost: now the body is allowed to decide."""
        best = LifegainLogic.best_creature([
            card(1, FIENDISH_PANDA),      # 3/2, cmc 4
            card(2, TWINBLADE_PALADIN),   # 3/3, cmc 4
        ])
        self.assertEqual(best["grpId"], TWINBLADE_PALADIN)

    def test_non_creature_candidates_are_ignored(self):
        best = LifegainLogic.best_creature([
            card(1, PLAINS, creature=False),
            card(2, HINTERLAND_SANCTIFIER),
        ])
        self.assertEqual(best["grpId"], HINTERLAND_SANCTIFIER)

    def test_no_creature_offered_returns_none(self):
        self.assertIsNone(LifegainLogic.best_creature([card(1, PLAINS, creature=False)]))
        self.assertIsNone(LifegainLogic.best_creature([]))


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._Controller__system_seat_id = 1
    return c


def seed(c: Controller, objects):
    c.updated_game_state = GameState({
        "turnInfo": {"turnNumber": 8, "phase": "Phase_Main1", "step": "Step_Main"},
        "timers": [], "annotations": [], "actions": [], "players": [],
        "gameObjects": list(objects),
        "zones": [
            {"zoneId": BATTLEFIELD_ZONE, "type": "ZoneType_Battlefield",
             "objectInstanceIds": []},
            {"zoneId": GRAVEYARD_ZONE, "type": "ZoneType_Graveyard",
             "objectInstanceIds": [o["instanceId"] for o in objects]},
            {"zoneId": STACK_ZONE, "type": "ZoneType_Stack",
             "objectInstanceIds": []},
        ],
        "gameStateId": 200,
    })


def req(*instance_ids):
    return {"targets": [{"targets": [
        {"targetInstanceId": i, "legalAction": "SelectAction_Select"}
        for i in instance_ids
    ]}]}


class ChooserTargetTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()

    def test_the_reported_case_no_longer_revives_the_worst_card(self):
        """Panda dies; graveyard holds Sanctifier (offered first) plus the three
        payoffs. The bot used to take whatever came first."""
        objects = [
            card(340, HINTERLAND_SANCTIFIER),
            card(341, FIENDISH_PANDA),
            card(342, TWINBLADE_PALADIN),
            card(343, AJANIS_PRIDEMATE),
        ]
        seed(self.c, objects)
        picked, is_stack = self.c._Controller__pick_chooser_target(req(340, 341, 342, 343))
        self.assertFalse(is_stack)
        self.assertNotEqual(picked, 340, "revived the Hinterland Sanctifier again")
        self.assertEqual(picked, 342, "Twinblade Paladin is the best of the three payoffs")

    def test_first_offered_wins_when_it_is_genuinely_the_best(self):
        objects = [card(341, FIENDISH_PANDA), card(340, HINTERLAND_SANCTIFIER)]
        seed(self.c, objects)
        picked, _ = self.c._Controller__pick_chooser_target(req(341, 340))
        self.assertEqual(picked, 341)

    def test_stack_targets_keep_the_original_first_offered_behaviour(self):
        """__pick_chooser_target also feeds counterspell targeting. Ranking by
        'best creature for us' there would silently change which spell we answer,
        so stack candidates must be left alone."""
        objects = [
            card(360, FIENDISH_PANDA, zone=STACK_ZONE),
            card(361, TWINBLADE_PALADIN, zone=STACK_ZONE),
        ]
        seed(self.c, objects)
        picked, is_stack = self.c._Controller__pick_chooser_target(req(360, 361))
        self.assertTrue(is_stack)
        self.assertEqual(picked, 360, "stack targeting must stay first-offered")

    def test_battlefield_targets_are_not_chooser_targets(self):
        objects = [card(350, FIENDISH_PANDA, zone=BATTLEFIELD_ZONE)]
        seed(self.c, objects)
        self.assertIsNone(self.c._Controller__pick_chooser_target(req(350)))

    def test_unknown_targets_are_ignored(self):
        seed(self.c, [card(340, HINTERLAND_SANCTIFIER)])
        picked, _ = self.c._Controller__pick_chooser_target(req(999, 340))
        self.assertEqual(picked, 340)

    def test_no_legal_targets_returns_none(self):
        seed(self.c, [card(340, HINTERLAND_SANCTIFIER)])
        self.assertIsNone(self.c._Controller__pick_chooser_target({"targets": []}))

    def test_noncreature_graveyard_choice_picks_highest_mana_value(self):
        objects = [
            card(350, 1001, creature=False),
            card(351, 1002, creature=False),
            card(352, 1003, creature=False),
        ]
        seed(self.c, objects)
        costs = {1001: "{U}", 1002: "{2}{U}", 1003: "{4}{B}"}
        with patch(
            "Controller.MTGAController.Controller.CardInfo.get_card_info_local",
            side_effect=lambda grp_id: {"manaCost": costs[grp_id]},
        ):
            picked, is_stack = self.c._Controller__pick_chooser_target(req(350, 351, 352))
        self.assertFalse(is_stack)
        self.assertEqual(picked, 352)


if __name__ == "__main__":
    unittest.main()
