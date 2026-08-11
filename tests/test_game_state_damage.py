"""Marked damage must not survive the turn it was dealt in.

The GRE announces damage but never announces its removal: in a full match log
the `damage` field is sent ~100 times and *never* as 0, while the same creatures
reappear in later diffs without the field hundreds of times. A merge only
overwrites the keys it is handed, so nothing here would ever unset it.

That mattered enough to be worth its own file because `effective_toughness` --
printed toughness minus marked damage -- is read by removal targeting, fight
logic, creature ranking and blocking. Left unfixed it drifts monotonically
downwards: a real farming session produced readings like "Sun-Blessed Healer
3/-1" and "Giada, Font of Hope 4/0", i.e. creatures the bot believed were
already dead while they were attacking it.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import AI.Utilities.RemovalLogic as RemovalLogic
from Controller.Utilities.GameState import GameState

BATTLEFIELD = 28


def diff(turn_number, game_objects=None):
    state = {"turnInfo": {"turnNumber": turn_number}}
    if game_objects is not None:
        state["gameObjects"] = game_objects
    return GameState(state)


def bear(damage=None):
    obj = {
        "instanceId": 10,
        "zoneId": BATTLEFIELD,
        "cardTypes": ["CardType_Creature"],
        "power": {"value": 2},
        "toughness": {"value": 2},
    }
    if damage is not None:
        obj["damage"] = damage
    return obj


class MarkedDamageTest(unittest.TestCase):
    def setUp(self):
        self.state = GameState({})
        self.state.update(diff(3, [bear()]))

    def _bear(self):
        return self.state.get_game_objects()[0]

    def test_damage_dealt_this_turn_is_kept(self):
        """Within the turn the damage is real -- a 2/2 with 1 marked damage dies
        to one more point, and blocking has to see that."""
        self.state.update(diff(3, [bear(damage=1)]))
        self.assertEqual(self._bear().get("damage"), 1)
        self.assertEqual(RemovalLogic.effective_toughness(self._bear()), 1)

    def test_damage_is_gone_once_the_turn_advances(self):
        self.state.update(diff(3, [bear(damage=1)]))
        self.state.update(diff(4, [{"instanceId": 11, "zoneId": BATTLEFIELD}]))
        self.assertNotIn("damage", self._bear())
        self.assertEqual(RemovalLogic.effective_toughness(self._bear()), 2)

    def test_a_turn_change_that_mentions_no_creatures_still_clears(self):
        """Most turn-boundary diffs carry only turnInfo. Clearing only when the
        diff happens to include gameObjects would leave the damage stuck."""
        self.state.update(diff(3, [bear(damage=2)]))
        self.state.update(diff(4))
        self.assertNotIn("damage", self._bear())

    def test_damage_in_the_same_diff_that_advances_the_turn_survives(self):
        """The clear runs before the merge, so damage carried by the very diff
        that turns the page is new damage, not stale damage."""
        self.state.update(diff(3, [bear(damage=1)]))
        self.state.update(diff(4, [bear(damage=2)]))
        self.assertEqual(self._bear().get("damage"), 2)

    def test_damage_never_goes_below_zero_over_several_turns(self):
        """The bug's signature: repeated combats driving toughness negative."""
        for turn in range(4, 12):
            self.state.update(diff(turn, [bear(damage=1)]))
            self.assertGreaterEqual(RemovalLogic.effective_toughness(self._bear()), 1)

    def test_an_out_of_order_diff_does_not_clear(self):
        """Diffs can restate an earlier turn; only a genuine advance clears."""
        self.state.update(diff(3, [bear(damage=1)]))
        self.state.update(diff(3))
        self.assertEqual(self._bear().get("damage"), 1)

    def test_a_full_snapshot_is_still_taken_verbatim(self):
        self.state.update(diff(3, [bear(damage=1)]))
        full = GameState({
            "type": "GameStateType_Full",
            "turnInfo": {"turnNumber": 1},
            "gameObjects": [bear(damage=1)],
        })
        self.state.update(full)
        self.assertEqual(self._bear().get("damage"), 1)


if __name__ == "__main__":
    unittest.main()
