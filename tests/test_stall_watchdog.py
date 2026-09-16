import copy
import json
import threading
import unittest
from unittest import mock

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState
from state.state_machine import BotState


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
        self.controller._Controller__live_match_id = "match-1"
        self.controller._Controller__last_seen_match_id = "match-1"
        self.controller._Controller__failed_stall_signature = None
        self.controller._get_state_from_log = lambda: BotState.IN_GAME

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
        controller._Controller__stall_context_signature = ("sig",)
        controller._Controller__stall_concede_threshold_sec = 30.0
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._Controller__local_stall_signature = lambda: ("sig",)
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


class _FakeTimer:
    def __init__(self, _delay, _callback, *args, **kwargs):
        self.daemon = False
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class StallSoakTelemetryTest(StallSignatureTest):
    def setUp(self):
        super().setUp()
        self.controller._Controller__auto_concede_stalled_matches = True
        self.controller._Controller__concession_claimed = False
        self.controller._Controller__stall_context_signature = None
        self.controller._Controller__stall_context_started_at = None
        self.controller._Controller__stall_watchdog_timer = None
        self.controller._Controller__stall_concede_threshold_sec = 30.0
        self.controller._Controller__soak_stall_arm_id = 0
        self.controller._Controller__soak_stall_updates = 0
        self.controller._Controller__soak_stall_heartbeat_bucket = 0
        self.controller._Controller__soak_stall_reset_total = 0

    def test_stale_or_menu_state_cannot_arm_watchdog(self):
        self.controller.updated_game_state = GameState(_state())
        self.controller._Controller__live_match_id = None
        self.controller._Controller__last_seen_match_id = None
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__update_stall_watchdog()
        self.assertIsNone(self.controller._Controller__stall_watchdog_timer)

        self.controller._Controller__live_match_id = "match-1"
        self.controller._Controller__last_seen_match_id = "match-1"
        self.controller._get_state_from_log = lambda: BotState.HOME
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__update_stall_watchdog()
        self.assertIsNone(self.controller._Controller__stall_watchdog_timer)

    def test_timer_rejects_mainnav_after_arm(self):
        state = _state()
        self.controller.updated_game_state = GameState(state)
        signature = self.controller._Controller__local_stall_signature()
        self.controller._Controller__stall_context_signature = signature
        self.controller._Controller__stall_context_started_at = 10.0
        self.controller._Controller__soak_stall_arm_id = 1
        self.controller._get_state_from_log = lambda: BotState.HOME
        self.controller._Controller__live_match_id = None
        with mock.patch.object(self.controller, "_Controller__claim_concession") as claim, \
             mock.patch("Controller.MTGAController.Controller.time.monotonic", return_value=41.0):
            self.controller._Controller__attempt_stall_concede(1, signature, 10.0, "match-1")
        claim.assert_not_called()

    @staticmethod
    def _events(log_info):
        prefix = "[SOAK_STALL_V1] "
        return [
            json.loads(call.args[0][len(prefix):])
            for call in log_info.call_args_list
            if call.args and call.args[0].startswith(prefix)
        ]

    def test_arm_heartbeat_and_progress_reset_are_machine_readable(self):
        state = _state()
        self.controller.updated_game_state = GameState(state)

        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.Controller.bot_logger.log_info") as log_info, \
             mock.patch("Controller.MTGAController.Controller.time.monotonic", side_effect=[100.0, 106.0, 107.0, 107.0]):
            self.controller._Controller__update_stall_watchdog()
            self.controller._Controller__update_stall_watchdog()
            changed = copy.deepcopy(state)
            changed["gameObjects"][2]["tapped"] = True
            self.controller.updated_game_state = GameState(changed)
            self.controller._Controller__update_stall_watchdog()

        events = self._events(log_info)
        self.assertEqual([event["event"] for event in events], ["armed", "unchanged", "cleared", "armed"])
        self.assertEqual(events[1]["age_sec"], 6.0)
        self.assertEqual(events[1]["signature"], events[0]["signature"])
        self.assertIn("zone_objects", events[3]["changed"])
        self.assertNotEqual(events[3]["signature"], events[0]["signature"])
        self.assertEqual(events[2]["reason"], "meaningful game progress")

    def test_signature_digest_is_stable_and_compact(self):
        signature = self.signature(_state())
        digest = self.controller._Controller__soak_stall_signature_digest(signature)

        self.assertEqual(digest, self.controller._Controller__soak_stall_signature_digest(signature))
        self.assertRegex(digest, r"^[0-9a-f]{16}$")


if __name__ == "__main__":
    unittest.main()
