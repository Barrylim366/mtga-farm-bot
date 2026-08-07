"""Ward pricing and the decline list in RemovalLogic.

Ward is the one targeting restriction MTGA does NOT enforce for us: it lets the
spell be aimed at a warded creature and then raises a client-side confirm that
emits no GRE message at all. Decline it and the spell fizzles back to hand with
the board unchanged, so a memoryless decision loop re-derives the same target and
tries again -- which is exactly what happened on 2026-07-30 17:31-17:37, where a
Mortify aimed at Tolarian Terror (Ward {2}) looped across four turns.

So two things have to hold, and these pin both:

  1. the ward is *priced before targeting* -- unaffordable warded creatures never
     become the target, affordable ones still do (paying {2} to kill the 5/5 is
     the right play, and refusing to ever pay makes removal useless);
  2. a target we backed out of is remembered, so nothing can loop even if the
     pricing is wrong.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import AI.Utilities.RemovalLogic as RemovalLogic

MY_SEAT = 1
OPP_SEAT = 2

# grpId -> what CardInfo would return.
CARDS = {
    # Tolarian Terror, the card that caused the loop.
    82124: {
        "name": "Tolarian Terror",
        "keywords": ["Ward"],
        "oracleText": (
            "This spell costs {1} less to cast for each instant and sorcery card "
            "in your graveyard.\nWard {2} (Whenever this creature becomes the "
            "target of a spell or ability an opponent controls, counter it unless "
            "that player pays {2}.)"
        ),
    },
    # Coloured ward: the cost is the pip count, not the number of symbols.
    900001: {
        "name": "Warded Duo",
        "keywords": ["Ward"],
        "oracleText": "Ward {1}{U}",
    },
    # A ward this bot cannot drive: there is no mana to auto-pay.
    900002: {
        "name": "Life Warder",
        "keywords": ["Ward"],
        "oracleText": "Ward—Pay 3 life.",
    },
    # No ward at all. "toward" must not trip the word-boundary match.
    900003: {
        "name": "Plain Bear",
        "keywords": [],
        "oracleText": "Whenever this creature attacks, it gets +1/+0 toward the end of turn.",
    },
}


def fake_card_info(grp_id):
    return CARDS.get(grp_id)


def creature(instance_id, grp_id, power=2, toughness=2, seat=OPP_SEAT):
    return {
        "instanceId": instance_id,
        "grpId": grp_id,
        "controllerSeatId": seat,
        "ownerSeatId": seat,
        "cardTypes": ["CardType_Creature"],
        "power": {"value": power},
        "toughness": {"value": toughness},
    }


DESTROY = {"kind": "destroy"}


class WardCostTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            RemovalLogic.CardInfo, "get_card_info", side_effect=fake_card_info
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(RemovalLogic.reset_declined_targets)

    def test_mana_ward_is_priced_from_the_symbols_after_the_keyword(self):
        cost = RemovalLogic.ward_cost(82124)
        # The reminder text also contains "{2}", so a naive "first mana symbol"
        # match would work here by luck -- but the printed "{1} less to cast"
        # comes first in the text and is what a naive match would actually find.
        self.assertEqual(cost["mana"], 2)

    def test_coloured_ward_costs_its_pip_count(self):
        self.assertEqual(RemovalLogic.ward_cost(900001)["mana"], 2)

    def test_non_mana_ward_is_priced_as_unpayable_not_free(self):
        cost = RemovalLogic.ward_cost(900002)
        self.assertIsNotNone(cost, "the card is warded; None would read as 'no ward'")
        self.assertIsNone(cost["mana"])
        self.assertFalse(RemovalLogic.ward_is_affordable(creature(1, 900002), 99))

    def test_unwarded_card_has_no_ward(self):
        self.assertIsNone(RemovalLogic.ward_cost(900003))

    def test_unwarded_card_is_affordable_on_an_empty_budget(self):
        self.assertTrue(RemovalLogic.ward_is_affordable(creature(1, 900003), 0))

    def test_unknown_budget_never_makes_a_ward_unaffordable(self):
        # None means "the caller does not know", which must not be read as zero.
        self.assertTrue(RemovalLogic.ward_is_affordable(creature(1, 82124), None))


class ChooseTargetWithWardTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            RemovalLogic.CardInfo, "get_card_info", side_effect=fake_card_info
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(RemovalLogic.reset_declined_targets)

    def _board(self):
        # The warded creature is also the biggest, so every test here is a real
        # choice between "kill the threat" and "do not get countered".
        return [
            creature(301, 82124, power=5, toughness=5),   # Ward {2}
            creature(302, 900003, power=2, toughness=3),  # unwarded
        ]

    def _choose(self, **kwargs):
        return RemovalLogic.choose_removal_target(
            DESTROY, self._board(), MY_SEAT, **kwargs
        )

    def test_pays_the_ward_when_the_mana_is_there(self):
        self.assertEqual(self._choose(ward_budget=2), 301)

    def test_falls_back_to_an_unwarded_target_when_it_cannot_pay(self):
        self.assertEqual(self._choose(ward_budget=1), 302)

    def test_skips_the_cast_when_only_an_unaffordable_warded_target_exists(self):
        board = [creature(301, 82124, power=5, toughness=5)]
        target = RemovalLogic.choose_removal_target(
            DESTROY, board, MY_SEAT, ward_budget=0
        )
        # Not "target it anyway": a countered removal spell is gone for good,
        # while an uncast one is still in hand next turn.
        self.assertIsNone(target)

    def test_unknown_budget_prefers_the_unwarded_creature_only_as_a_tiebreak(self):
        # Equal bodies -> take the one that raises no confirm.
        board = [creature(301, 82124, power=2, toughness=3),
                 creature(302, 900003, power=2, toughness=3)]
        self.assertEqual(
            RemovalLogic.choose_removal_target(DESTROY, board, MY_SEAT), 302
        )
        # Bigger warded body -> still the right target; the Controller prices the
        # ward against a live mana reading before it commits.
        self.assertEqual(self._choose(), 301)

    def test_declined_target_is_not_re_picked(self):
        self.assertEqual(self._choose(ward_budget=2, source_grp_id=94090), 301)
        RemovalLogic.note_declined_target(94090, 301)
        self.assertEqual(
            self._choose(ward_budget=2, source_grp_id=94090), 302,
            "a target we already backed out of must not come straight back",
        )

    def test_decline_is_scoped_to_the_spell_that_declined_it(self):
        RemovalLogic.note_declined_target(94090, 301)
        # A different removal spell may well be able to pay that ward.
        self.assertEqual(self._choose(ward_budget=2, source_grp_id=93882), 301)

    def test_decline_list_resets_between_matches(self):
        RemovalLogic.note_declined_target(94090, 301)
        RemovalLogic.reset_declined_targets()
        self.assertEqual(self._choose(ward_budget=2, source_grp_id=94090), 301)

    def test_declining_every_target_skips_the_cast(self):
        RemovalLogic.note_declined_target(94090, 301)
        RemovalLogic.note_declined_target(94090, 302)
        self.assertIsNone(self._choose(ward_budget=9, source_grp_id=94090))


if __name__ == "__main__":
    unittest.main()
