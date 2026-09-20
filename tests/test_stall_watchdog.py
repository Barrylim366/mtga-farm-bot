import copy
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

        # _FakeTimer, not the real one: without it the re-arm path schedules a
        # live 29s threading.Timer that outlives the test run.
        with mock.patch("Controller.MTGAController.Controller.time.monotonic", side_effect=monotonic), \
             mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            worker = threading.Thread(target=attempt)
            worker.start()
            self.assertTrue(entered_monotonic.wait(timeout=1.0))
            controller._Controller__stall_context_started_at = None
            continue_monotonic.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        # The context it measured is gone, so it must not have re-armed either.
        self.assertIsNone(controller._Controller__stall_watchdog_timer)

    def test_progress_between_validation_and_claim_does_not_concede(self):
        """A fresh context armed while the callback reads the clock wins.

        The callback validated generation/signature/start before
        `time.monotonic()`; anything the log thread changes during that call was
        invisible to it, and it claimed a concession on a context that had
        already made progress.
        """
        controller = Controller.__new__(Controller)
        controller._Controller__auto_concede_stalled_matches = True
        controller._Controller__concession_claimed = False
        controller._Controller__stall_context_started_at = 10.0
        controller._Controller__stall_context_signature = ("old",)
        controller._Controller__stall_concede_threshold_sec = 30.0
        controller._Controller__stall_watchdog_generation = 1
        controller._Controller__stall_watchdog_timer = None
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._Controller__local_stall_signature = lambda: ("old",)

        def monotonic():
            # The log thread re-arms on a new context while the clock is read.
            controller._Controller__stall_watchdog_generation = 2
            controller._Controller__stall_context_started_at = 41.0
            controller._Controller__stall_context_signature = ("new",)
            return 41.0

        with mock.patch.object(controller, "_Controller__claim_concession") as claim, \
             mock.patch.object(controller, "_Controller__run_claimed_concede_sequence") as run, \
             mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.Controller.time.monotonic", side_effect=monotonic):
            controller._Controller__attempt_stall_concede(1, ("old",), 10.0, "match-1")

        claim.assert_not_called()
        run.assert_not_called()

    def test_disable_between_validation_and_claim_does_not_concede(self):
        controller = Controller.__new__(Controller)
        controller._Controller__auto_concede_stalled_matches = True
        controller._Controller__concession_claimed = False
        controller._Controller__stall_context_started_at = 10.0
        controller._Controller__stall_context_signature = ("old",)
        controller._Controller__stall_concede_threshold_sec = 30.0
        controller._Controller__stall_watchdog_generation = 1
        controller._Controller__stall_watchdog_timer = None
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._Controller__local_stall_signature = lambda: ("old",)

        def monotonic():
            controller._Controller__auto_concede_stalled_matches = False
            return 41.0

        with mock.patch.object(controller, "_Controller__claim_concession") as claim, \
             mock.patch.object(controller, "_Controller__run_claimed_concede_sequence") as run, \
             mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.Controller.time.monotonic", side_effect=monotonic):
            controller._Controller__attempt_stall_concede(1, ("old",), 10.0, "match-1")

        claim.assert_not_called()
        run.assert_not_called()

    def test_setting_change_waits_for_an_in_flight_deadline_check(self):
        """The UI toggle and the deadline check are serialised, not interleaved."""
        controller = Controller.__new__(Controller)
        controller._Controller__auto_concede_stalled_matches = True
        controller._Controller__concession_claimed = False
        controller._Controller__stall_context_started_at = 10.0
        controller._Controller__stall_context_signature = ("old",)
        controller._Controller__stall_concede_threshold_sec = 30.0
        controller._Controller__stall_watchdog_generation = 1
        controller._Controller__stall_watchdog_timer = None
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._Controller__local_stall_signature = lambda: ("old",)

        inside_check = threading.Event()
        let_check_finish = threading.Event()
        order = []

        def monotonic():
            inside_check.set()
            self.assertTrue(let_check_finish.wait(timeout=2.0))
            return 41.0

        def claim(_reason):
            order.append("claim")
            return False

        with mock.patch.object(controller, "_Controller__claim_concession", side_effect=claim), \
             mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.Controller.time.monotonic", side_effect=monotonic):
            worker = threading.Thread(
                target=controller._Controller__attempt_stall_concede,
                args=(1, ("old",), 10.0, "match-1"),
            )
            worker.start()
            self.assertTrue(inside_check.wait(timeout=2.0))

            toggled = threading.Event()

            def toggle():
                controller.set_auto_concede_stalled_matches(False)
                order.append("disable")
                toggled.set()

            toggler = threading.Thread(target=toggle)
            toggler.start()
            self.assertFalse(
                toggled.wait(0.3),
                "disabling must block until the in-flight deadline check is done",
            )
            let_check_finish.set()
            worker.join(timeout=2.0)
            toggler.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertFalse(toggler.is_alive())
        self.assertEqual(order, ["claim", "disable"])
        self.assertFalse(controller._Controller__auto_concede_stalled_matches)

    def test_stale_callback_does_not_drop_newer_arm_timer(self):
        controller = Controller.__new__(Controller)
        controller._Controller__auto_concede_stalled_matches = True
        controller._Controller__stall_context_started_at = 20.0
        controller._Controller__stall_context_signature = ("current",)
        controller._Controller__stall_concede_threshold_sec = 30.0
        controller._Controller__stall_watchdog_generation = 2
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._Controller__local_stall_signature = lambda: ("current",)
        newer_timer = object()
        controller._Controller__stall_watchdog_timer = newer_timer

        controller._Controller__attempt_stall_concede(1, ("old",), 10.0, "match-1")

        self.assertIs(controller._Controller__stall_watchdog_timer, newer_timer)

    def test_changed_context_at_deadline_is_rearmed(self):
        controller = Controller.__new__(Controller)
        controller._Controller__auto_concede_stalled_matches = True
        controller._Controller__concession_claimed = False
        controller._Controller__stall_context_started_at = 10.0
        controller._Controller__stall_context_signature = ("old",)
        controller._Controller__stall_concede_threshold_sec = 30.0
        controller._Controller__stall_watchdog_generation = 1
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._Controller__local_stall_signature = lambda: ("new",)
        controller._Controller__stall_watchdog_timer = _FakeTimer(0, None)

        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            controller._Controller__attempt_stall_concede(1, ("old",), 10.0, "match-1")

        self.assertEqual(controller._Controller__stall_watchdog_generation, 2)
        self.assertEqual(controller._Controller__stall_context_signature, ("new",))
        self.assertIsInstance(controller._Controller__stall_watchdog_timer, _FakeTimer)


class _FakeTimer:
    def __init__(self, _delay, _callback, *args, **kwargs):
        self.daemon = False
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class StallWatchdogSafetyTest(StallSignatureTest):
    def setUp(self):
        super().setUp()
        self.controller._Controller__auto_concede_stalled_matches = True
        self.controller._Controller__concession_claimed = False
        self.controller._Controller__stall_context_signature = None
        self.controller._Controller__stall_context_started_at = None
        self.controller._Controller__stall_watchdog_timer = None
        self.controller._Controller__stall_concede_threshold_sec = 30.0
        self.controller._Controller__stall_watchdog_generation = 0

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
        self.controller._Controller__stall_watchdog_generation = 1
        self.controller._get_state_from_log = lambda: BotState.HOME
        self.controller._Controller__live_match_id = None
        with mock.patch.object(self.controller, "_Controller__claim_concession") as claim, \
             mock.patch("Controller.MTGAController.Controller.time.monotonic", return_value=41.0):
            self.controller._Controller__attempt_stall_concede(1, signature, 10.0, "match-1")
        claim.assert_not_called()

if __name__ == "__main__":
    unittest.main()
