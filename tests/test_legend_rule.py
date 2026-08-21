"""Tests for the legend rule guard.

Seen live: with a legend already on the board and a second copy in hand, the bot
picked the cast, Magic Arena answered with its client-side "Are You Sure?"
confirm (which no GRE message announces), the cast never landed, and after three
sweeps the card was written off as unreachable. Confirming would not have helped
either -- CR 704.5j bins one of the two immediately after.

The guard reads the game state only: `superTypes` for legendary, and `name` (a
title id) to match the copies, so it works offline and across printings.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import AI.Utilities.CardInfo as CardInfo
import AI.Utilities.CardPolicy as CardPolicy
import AI.Utilities.CounterLogic as CounterLogic
import AI.Utilities.LegendRule as LegendRule
import AI.Utilities.LifegainLogic as LifegainLogic
import AI.Utilities.RemovalLogic as RemovalLogic
from AI.DummyAI import DummyAI

BATTLEFIELD, HAND = 28, 31
MY_SEAT = 1

# Two printings of one legend: different grpIds, same title id -- the case a
# grpId comparison would miss.
LEGEND_TITLE = 823640
LEGEND_GRP_OLD, LEGEND_GRP_NEW = 93941, 176406
PLAIN_TITLE, PLAIN_GRP = 420253, 93645


def obj(instance_id, *, zone, title, legendary=False, seat=MY_SEAT, grp=None):
    o = {
        "instanceId": instance_id,
        "grpId": grp if grp is not None else LEGEND_GRP_OLD,
        "zoneId": zone,
        "controllerSeatId": seat,
        "ownerSeatId": seat,
        "name": title,
        "cardTypes": ["CardType_Creature"],
    }
    if legendary:
        o["superTypes"] = ["SuperType_Legendary"]
    return o


class DuplicateLegendTest(unittest.TestCase):
    """The board copy and the hand copy, matched by title id."""

    def call(self, objects, cast_instance_id=400, **kw):
        params = dict(
            cast_instance_id=cast_instance_id,
            game_objects=objects,
            my_seat=MY_SEAT,
            battlefield_zone_ids={BATTLEFIELD},
            live_instance_ids=None,
        )
        params.update(kw)
        return LegendRule.duplicate_legend_in_play(**params)

    def test_the_duplicate_is_reported(self):
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
        ]
        self.assertEqual(self.call(objects), 281)

    def test_a_reprint_still_matches(self):
        """Different grpId, same card. Matching on grpId would let the second
        copy through."""
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True, grp=LEGEND_GRP_OLD),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True, grp=LEGEND_GRP_NEW),
        ]
        self.assertEqual(self.call(objects), 281)

    def test_one_side_reporting_legendary_is_enough(self):
        """A shared title id means it is the same card, so whichever object
        carries superTypes answers for both."""
        for board_leg, hand_leg in ((True, False), (False, True)):
            with self.subTest(board=board_leg, hand=hand_leg):
                objects = [
                    obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=board_leg),
                    obj(400, zone=HAND, title=LEGEND_TITLE, legendary=hand_leg),
                ]
                self.assertEqual(self.call(objects), 281)

    def test_a_non_legendary_duplicate_is_allowed(self):
        """A second Llanowar Elves is a perfectly good play."""
        objects = [
            obj(281, zone=BATTLEFIELD, title=PLAIN_TITLE, grp=PLAIN_GRP),
            obj(400, zone=HAND, title=PLAIN_TITLE, grp=PLAIN_GRP),
        ]
        self.assertIsNone(self.call(objects))

    def test_a_different_legend_is_allowed(self):
        objects = [
            obj(281, zone=BATTLEFIELD, title=PLAIN_TITLE, legendary=True),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
        ]
        self.assertIsNone(self.call(objects))

    def test_the_opponents_copy_does_not_block_ours(self):
        """The legend rule is per player. Their Ajani has no bearing on ours."""
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True, seat=2),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
        ]
        self.assertIsNone(self.call(objects))

    def test_two_copies_in_hand_do_not_block_each_other(self):
        """The exact trap the zone filter exists for: this object list spans every
        zone, so without it the first copy in hand would 'already be in play'."""
        objects = [
            obj(399, zone=HAND, title=LEGEND_TITLE, legendary=True),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
        ]
        self.assertIsNone(self.call(objects))

    def test_a_dead_copy_does_not_block(self):
        """gameObjects is merged across diffs and a dead creature keeps its
        battlefield zoneId indefinitely, so the zone's own id list is the
        authority on what is actually on the board."""
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
        ]
        self.assertIsNone(self.call(objects, live_instance_ids={999}))
        self.assertEqual(self.call(objects, live_instance_ids={281}), 281)

    def test_unknown_battlefield_zones_fail_open(self):
        """Refusing here would skip legitimate casts whenever MTGA has not
        re-declared the battlefield zone -- worse than the loop we are avoiding."""
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
        ]
        for zones in (None, set(), frozenset()):
            with self.subTest(zones=zones):
                self.assertIsNone(self.call(objects, battlefield_zone_ids=zones))

    def test_no_supertypes_anywhere_fails_open(self):
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE),
            obj(400, zone=HAND, title=LEGEND_TITLE),
        ]
        self.assertIsNone(self.call(objects))

    def test_an_unknown_cast_card_fails_open(self):
        objects = [obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True)]
        self.assertIsNone(self.call(objects, cast_instance_id=4242))

    def test_a_missing_title_fails_open(self):
        board = obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True)
        hand = obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True)
        hand.pop("name")
        self.assertIsNone(self.call([board, hand]))

    def test_garbage_objects_are_survived(self):
        objects = [None, 42, "x", obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True)]
        self.assertIsNone(self.call(objects))
        self.assertFalse(LegendRule.is_legendary(None))
        self.assertIsNone(LegendRule.title_id("nope"))


class _FakeGameState:
    """Only the five methods generate_move calls."""

    def __init__(self, objects, actions, turn_info):
        self._objects = objects
        self._actions = actions
        self._turn_info = turn_info

    def get_game_objects(self):
        return self._objects

    def get_actions(self):
        return self._actions

    def get_turn_info(self):
        return self._turn_info

    def get_players(self):
        return [
            {"systemSeatNumber": 1, "lifeTotal": 20},
            {"systemSeatNumber": 2, "lifeTotal": 20},
        ]

    def get_full_state(self):
        return {"zones": [{"zoneId": BATTLEFIELD, "type": "ZoneType_Battlefield"}]}


class TheAiNeverPicksTheDuplicateTest(unittest.TestCase):
    """End to end through generate_move: the guard has to keep the candidate out
    of cast_actions, not merely be correct in isolation."""

    def setUp(self):
        self.ai = DummyAI()

        def card_info(grp_id):
            names = {
                LEGEND_GRP_OLD: "Legendary Bear",
                LEGEND_GRP_NEW: "Legendary Bear",
                PLAIN_GRP: "Plain Bear",
            }
            if int(grp_id) not in names:
                return None
            return {
                "name": names[int(grp_id)],
                "types": ["Creature"],
                "manaCost": "{2}",
            }

        self.patch(CardInfo, "get_card_info", card_info)
        self.patch(CardInfo, "calculate_cmc", lambda cost: 2)
        self.patch(CardInfo, "card_has_convoke", lambda grp_id: False)
        self.patch(CardPolicy, "is_unsupported_to_cast", lambda grp_id: False)
        self.patch(CounterLogic, "get_counter_profile", lambda grp_id: None)
        self.patch(LifegainLogic, "is_lifegain_payoff", lambda grp_id: False)
        self.patch(RemovalLogic, "get_removal_profile", lambda grp_id: None)
        self.patch(RemovalLogic, "is_self_buff", lambda grp_id: False)
        self.patch(RemovalLogic, "battlefield_zone_ids", lambda state: {BATTLEFIELD})
        self.patch(
            RemovalLogic, "battlefield_instance_ids", lambda state, zones: {281}
        )
        self.patch(RemovalLogic, "opponent_life_from_players", lambda players, seat: 20)
        # Mana is not what these tests are about.
        self.ai._get_available_mana_colors = lambda *a, **k: ({"white", "black"}, 6, [{"white"}, {"black"}])
        self.ai._can_cast_with_mana_costs = lambda *a, **k: True
        self.ai._get_convoke_sources = lambda *a, **k: (set(), [])

    def patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def move_for(self, objects, actions):
        state = _FakeGameState(
            objects,
            actions,
            {
                "activePlayer": MY_SEAT,
                "decisionPlayer": MY_SEAT,
                "priorityPlayer": MY_SEAT,
                "phase": "Phase_Main1",
                "step": "Step_Draw",
                "turnNumber": 5,
            },
        )
        grp_map = {o["instanceId"]: o["grpId"] for o in objects if isinstance(o, dict)}
        return self.ai.generate_move(state, grp_map)

    def cast(self, instance_id):
        return {"action": {"actionType": "ActionType_Cast", "instanceId": instance_id,
                           "manaCost": [{"color": ["ManaColor_Generic"], "count": 2}]}}

    def test_the_second_copy_is_not_cast(self):
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
        ]
        move = self.move_for(objects, [self.cast(400)])
        self.assertNotEqual(
            move, {"cast": [400]},
            "cast the duplicate legend: Arena answers that with 'Are You Sure?' and "
            "the cast never lands",
        )

    def test_a_castable_alternative_is_taken_instead(self):
        """The guard must skip the one candidate, not abandon the turn."""
        objects = [
            obj(281, zone=BATTLEFIELD, title=LEGEND_TITLE, legendary=True),
            obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True),
            obj(401, zone=HAND, title=PLAIN_TITLE, grp=PLAIN_GRP),
        ]
        move = self.move_for(objects, [self.cast(400), self.cast(401)])
        self.assertEqual(move, {"cast": [401]})

    def test_the_first_copy_is_still_cast(self):
        """Nothing on our board yet: this is a normal, good cast."""
        objects = [obj(400, zone=HAND, title=LEGEND_TITLE, legendary=True)]
        self.patch(RemovalLogic, "battlefield_instance_ids", lambda state, zones: set())
        move = self.move_for(objects, [self.cast(400)])
        self.assertEqual(move, {"cast": [400]})


if __name__ == "__main__":
    unittest.main()
