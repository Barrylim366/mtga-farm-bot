import copy
import threading
import unittest
from unittest import mock

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState


def _state():
    return {
        "gameStateId": 100,
        "turnInfo": {
            "turnNumber": 4,
            "phase": "Phase_Main1",
            "step": "Step_Main",
            "decisionPlayer": 1,
        },
        "timers": [{"timerId": 1, "durationSec": 30}],
        "actions": [{"actionType": "ActionType_Pass"}],
        "players": [
            {"systemSeatNumber": 1, "lifeTotal": 20},
            {"systemSeatNumber": 2, "lifeTotal": 20},
        ],
        "annotations": [],
        "zones": [
            {"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1, "objectInstanceIds": [101]},
            {"zoneId": 20, "type": "ZoneType_Hand", "ownerSeatId": 2, "objectInstanceIds": [201]},
            {"zoneId": 11, "type": "ZoneType_Battlefield", "ownerSeatId": 1, "objectInstanceIds": [102]},
            {"zoneId": 21, "type": "ZoneType_Battlefield", "ownerSeatId": 2, "objectInstanceIds": [202]},
            {"zoneId": 30, "type": "ZoneType_Stack", "objectInstanceIds": []},
            {"zoneId": 31, "type": "ZoneType_Pending", "objectInstanceIds": []},
        ],
        "gameObjects": [
            {"instanceId": 101, "zoneId": 10, "grpId": 1001},
            {"instanceId": 201, "zoneId": 20, "grpId": 2001},
            {"instanceId": 102, "zoneId": 11, "grpId": 1002, "tapped": False, "damage": 0},
            {"instanceId": 202, "zoneId": 21, "grpId": 2002, "tapped": False, "damage": 0},
        ],
    }


class StallSignatureTest(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller._stop_requested = False
        self.controller._suppress_selections = False
        self.controller._Controller__system_seat_id = 1
        self.controller._Controller__pending_card_prompt = None
        self.controller._Controller__pending_target_select = None
        self.controller._Controller__pending_select_n = None
        self.controller._Controller__select_n_in_progress = False
        self.controller._Controller__pending_pay_costs_ts = 0.0
        self.controller._Controller__assign_damage_in_progress = False
        self.controller._Controller__casting_time_options_until = 0.0
        self.controller._Controller__pending_mulligan = None

    def signature(self, state):
        self.controller.updated_game_state = GameState(state)
        return self.controller._Controller__local_stall_signature()

    def test_membership_changes_in_each_players_hand_and_battlefield_are_progress(self):
        baseline_state = _state()
        baseline = self.signature(baseline_state)

        for zone_id in (10, 20, 11, 21):
            with self.subTest(zone_id=zone_id):
                changed = copy.deepcopy(baseline_state)
                zone = next(zone for zone in changed["zones"] if zone["zoneId"] == zone_id)
                new_id = zone_id * 100
                zone["objectInstanceIds"].append(new_id)
                changed["gameObjects"].append({"instanceId": new_id, "zoneId": zone_id, "grpId": new_id})
                self.assertNotEqual(self.signature(changed), baseline)

    def test_existing_battlefield_object_change_is_progress(self):
        baseline_state = _state()
        baseline = self.signature(baseline_state)
        changed = copy.deepcopy(baseline_state)
        permanent = next(obj for obj in changed["gameObjects"] if obj["instanceId"] == 102)
        permanent["tapped"] = True
        permanent["damage"] = 2

        self.assertNotEqual(self.signature(changed), baseline)

    def test_timer_and_game_state_ids_do_not_count_as_progress(self):
        baseline_state = _state()
        baseline = self.signature(baseline_state)
        changed = copy.deepcopy(baseline_state)
        changed["gameStateId"] = 101
        changed["timers"] = [{"timerId": 99, "durationSec": 10}]

        self.assertEqual(self.signature(changed), baseline)


class StallTimerRaceTest(unittest.TestCase):
    def test_timestamp_clear_during_age_calculation_does_not_raise(self):
        controller = Controller.__new__(Controller)
        controller._Controller__stall_watchdog_timer = None
        controller._Controller__auto_concede_stalled_matches = True
        controller._Controller__stall_context_started_at = 10.0
        controller._Controller__stall_concede_threshold_sec = 30.0
        entered_monotonic = threading.Event()
        continue_monotonic = threading.Event()
        errors = []

        def monotonic():
            entered_monotonic.set()
            continue_monotonic.wait(timeout=1.0)
            return 11.0

        def attempt():
            try:
                controller._Controller__attempt_stall_concede()
            except Exception as exc:
                errors.append(exc)

        with mock.patch("Controller.MTGAController.Controller.time.monotonic", side_effect=monotonic):
            worker = threading.Thread(target=attempt)
            worker.start()
            self.assertTrue(entered_monotonic.wait(timeout=1.0))
            controller._Controller__stall_context_started_at = None
            continue_monotonic.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
