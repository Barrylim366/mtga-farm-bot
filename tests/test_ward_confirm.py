"""The Controller side of ward: answering MTGA's "Are You Sure?" confirm.

The dialog is client-side and emits nothing, so the bot cannot read what it is
being asked -- the answer has to be decided when the target is chosen, where the
board and the spare mana are both readable. What these pin:

  1. Yes only when we already priced this exact ward and can pay it. Everything
     else -- no acknowledgement, a stale one -- stays No, because a blind Yes
     would confirm plays the bot does not understand (the dialog also guards
     things like "target the opponent's creature with this pump spell");
  2. a No records the declined (spell, target) pair, so the decision loop cannot
     re-derive it and land back in front of the same dialog.

Controller uses name-mangled double-underscore attributes; from outside the
class body they must be accessed as `_Controller__name`.
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import AI.Utilities.RemovalLogic as RemovalLogic
from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState

MY_SEAT = 1
OPP_SEAT = 2
BATTLEFIELD_ZONE = 28
MORTIFY = 94090
TOLARIAN_TERROR = 82124
PLAIN_BEAR = 900003

CARDS = {
    TOLARIAN_TERROR: {
        "name": "Tolarian Terror",
        "keywords": ["Ward"],
        "oracleText": "Ward {2} (Whenever this creature becomes the target of a "
                      "spell or ability an opponent controls, counter it unless "
                      "that player pays {2}.)",
    },
    PLAIN_BEAR: {"name": "Plain Bear", "keywords": [], "oracleText": "Vanilla."},
    MORTIFY: {"name": "Mortify", "keywords": [], "oracleText": "Destroy target creature."},
}


def creature(instance_id, grp_id, seat=OPP_SEAT, power=5, toughness=5):
    return {
        "instanceId": instance_id,
        "grpId": grp_id,
        "zoneId": BATTLEFIELD_ZONE,
        "controllerSeatId": seat,
        "ownerSeatId": seat,
        "cardTypes": ["CardType_Creature"],
        "power": {"value": power},
        "toughness": {"value": toughness},
    }


class WardConfirmTestBase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        handle.close()
        self.controller = Controller(handle.name)
        self.controller._Controller__system_seat_id = MY_SEAT
        self.addCleanup(self._cancel_timers)
        self.addCleanup(RemovalLogic.reset_declined_targets)

        patcher = mock.patch.object(
            RemovalLogic.CardInfo, "get_card_info", side_effect=CARDS.get
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # Make the dialog "visible" and every click observable, without touching
        # the screen: the probe finds the title, the arena mapping is identity.
        self.clicks = []
        self.controller._buttons_dir = lambda: os.path.dirname(__file__)
        self.controller._locate_image_center_in_scaled_arena_region = (
            lambda *a, **k: (0, 0)
        )
        self.controller._map_abs_point_to_arena = (
            lambda point, **k: (point, "test_identity")
        )
        self.controller._click_abs = (
            lambda x, y, label: self.clicks.append((x, y, label))
        )
        os.path.exists  # documented dependency; patched below
        exists_patcher = mock.patch(
            "Controller.MTGAController.Controller.os.path.exists", return_value=True
        )
        exists_patcher.start()
        self.addCleanup(exists_patcher.stop)
        sleep_patcher = mock.patch(
            "Controller.MTGAController.Controller.time.sleep", return_value=None
        )
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def _cancel_timers(self):
        for attr in (
            "_Controller__inactivity_timer",
            "_Controller__decision_execution_thread",
            "_Controller__decision_heartbeat_timer",
        ):
            timer = getattr(self.controller, attr, None)
            if timer is not None and hasattr(timer, "cancel"):
                timer.cancel()

    def seed(self, game_objects, mana_sources=0, stack=()):
        actions = [
            {"seatId": MY_SEAT, "action": {"actionType": "ActionType_Activate_Mana",
                                           "instanceId": 500 + i}}
            for i in range(mana_sources)
        ]
        # An opponent mana source must never count towards OUR ward budget.
        actions.append(
            {"seatId": OPP_SEAT,
             "action": {"actionType": "ActionType_Activate_Mana", "instanceId": 999}}
        )
        self.controller.updated_game_state = GameState({
            "turnInfo": {"turnNumber": 11, "phase": "Phase_Main1", "step": "Step_Main",
                         "activePlayer": MY_SEAT, "priorityPlayer": MY_SEAT,
                         "decisionPlayer": MY_SEAT},
            "timers": [],
            "gameObjects": list(game_objects) + list(stack),
            "players": [{"systemSeatNumber": MY_SEAT, "lifeTotal": 20},
                        {"systemSeatNumber": OPP_SEAT, "lifeTotal": 20}],
            "annotations": [],
            "actions": actions,
            "zones": [{"zoneId": BATTLEFIELD_ZONE, "type": "ZoneType_Battlefield",
                       "objectInstanceIds": [o["instanceId"] for o in game_objects]}],
        })

    def labels(self):
        return [label for _x, _y, label in self.clicks]


class WardBudgetTest(WardConfirmTestBase):
    def test_budget_counts_only_our_own_mana_actions(self):
        self.seed([creature(301, TOLARIAN_TERROR)], mana_sources=3)
        self.assertEqual(self.controller._Controller__available_mana_for_ward(), 3)

    def test_budget_is_unknown_not_zero_without_an_actions_list(self):
        self.seed([creature(301, TOLARIAN_TERROR)], mana_sources=0)
        # get_full_state() hands back a shallow copy, so the real dict is the one
        # to empty here.
        self.controller.updated_game_state.game_dict["actions"] = []
        self.assertIsNone(self.controller._Controller__available_mana_for_ward())


class WardAcknowledgementTest(WardConfirmTestBase):
    def _note(self, mana_sources):
        spell = {"instanceId": 161, "grpId": MORTIFY, "cardTypes": ["CardType_Instant"]}
        self.seed([creature(301, TOLARIAN_TERROR)], mana_sources=mana_sources,
                  stack=[spell])
        self.controller._Controller__note_ward_payment_ack(161, 301)

    def test_affordable_ward_is_acknowledged(self):
        self._note(mana_sources=2)
        ack = self.controller._Controller__ward_payment_ack
        self.assertIsNotNone(ack)
        self.assertEqual(ack["target"], 301)
        self.assertEqual(ack["mana"], 2)

    def test_unaffordable_ward_is_not_acknowledged_and_is_declined_immediately(self):
        self._note(mana_sources=1)
        self.assertIsNone(self.controller._Controller__ward_payment_ack)
        self.assertTrue(
            RemovalLogic.is_declined_target(MORTIFY, 301),
            "an unpayable ward must be blacklisted at decision time, not after "
            "the client dialog has already cost us a turn",
        )

    def test_unwarded_target_leaves_no_acknowledgement(self):
        spell = {"instanceId": 161, "grpId": MORTIFY, "cardTypes": ["CardType_Instant"]}
        self.seed([creature(301, PLAIN_BEAR)], mana_sources=5, stack=[spell])
        self.controller._Controller__note_ward_payment_ack(161, 301)
        self.assertIsNone(self.controller._Controller__ward_payment_ack)
        self.assertFalse(RemovalLogic.is_declined_target(MORTIFY, 301))


class WardDialogAnswerTest(WardConfirmTestBase):
    def setUp(self):
        super().setUp()
        spell = {"instanceId": 161, "grpId": MORTIFY, "cardTypes": ["CardType_Instant"]}
        self.seed([creature(301, TOLARIAN_TERROR)], mana_sources=2, stack=[spell])
        self.controller._Controller__pending_target_select = {
            "source_id": 161, "token": 1, "last_target": 301,
        }

    def test_answers_yes_when_the_ward_was_priced_and_is_payable(self):
        self.controller._Controller__note_ward_payment_ack(161, 301)
        self.assertTrue(
            self.controller._dismiss_are_you_sure_if_present(context="TEST")
        )
        self.assertEqual(self.labels(), ["ARE_YOU_SURE_YES"])
        self.assertFalse(
            RemovalLogic.is_declined_target(MORTIFY, 301),
            "a target we are paying for is not a declined one",
        )

    def test_the_yes_plate_is_the_mirror_of_the_no_plate(self):
        self.controller._Controller__note_ward_payment_ack(161, 301)
        self.controller._dismiss_are_you_sure_if_present(context="TEST")
        yes_x = self.clicks[0][0]
        no_x = Controller._ARE_YOU_SURE_NO_BASE[0]
        # Both plates sit symmetrically around the dialog's centre line.
        self.assertEqual(yes_x + no_x, 1920)
        self.assertEqual(self.clicks[0][1], Controller._ARE_YOU_SURE_NO_BASE[1])

    def test_answers_no_without_an_acknowledgement(self):
        self.assertTrue(
            self.controller._dismiss_are_you_sure_if_present(context="TEST")
        )
        self.assertEqual(self.labels(), ["ARE_YOU_SURE_NO"])

    def test_a_no_blacklists_the_target_so_it_cannot_loop(self):
        self.controller._dismiss_are_you_sure_if_present(context="TEST")
        self.assertTrue(RemovalLogic.is_declined_target(MORTIFY, 301))

    def test_a_stale_acknowledgement_does_not_confirm_a_later_dialog(self):
        self.controller._Controller__note_ward_payment_ack(161, 301)
        self.controller._Controller__ward_payment_ack["ts"] = (
            time.time() - Controller._WARD_ACK_MAX_AGE_SEC - 1
        )
        self.controller._dismiss_are_you_sure_if_present(context="TEST")
        self.assertEqual(self.labels(), ["ARE_YOU_SURE_NO"])

    def test_an_ack_does_not_confirm_a_dialog_about_a_different_target(self):
        """The dialog looks the same whatever raised it. Confirming the wrong one
        is how the bot would end up buffing an opponent's creature."""
        self.controller._Controller__note_ward_payment_ack(161, 301)
        self.controller._Controller__pending_target_select["last_target"] = 999
        self.controller._dismiss_are_you_sure_if_present(context="TEST")
        self.assertEqual(self.labels(), ["ARE_YOU_SURE_NO"])

    def test_the_acknowledgement_is_one_shot(self):
        self.controller._Controller__note_ward_payment_ack(161, 301)
        self.controller._dismiss_are_you_sure_if_present(context="TEST")
        self.controller._dismiss_are_you_sure_if_present(context="TEST")
        self.assertEqual(
            self.labels(), ["ARE_YOU_SURE_YES", "ARE_YOU_SURE_NO"],
            "one acknowledgement must not confirm every later dialog too",
        )


if __name__ == "__main__":
    unittest.main()
