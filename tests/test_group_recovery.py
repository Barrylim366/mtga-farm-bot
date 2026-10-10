"""Regression tests for scry/surveil prompt handling and decision resume."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState
from Controller.Utilities.input_controller import ExclusiveInputController, NullInputController


class _FakeTimer:
    instances = []

    def __init__(self, delay, callback, args=None, kwargs=None):
        self.delay = delay
        self.callback = callback
        self.kwargs = dict(kwargs or {})
        self.cancelled = False
        self.__class__.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def make_controller():
    handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    handle.close()
    controller = Controller(handle.name)
    controller._Controller__system_seat_id = 1
    controller._Controller__live_match_id = "match-1"
    controller._Controller__last_seen_match_id = "match-1"
    controller._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
    controller._click_image_in_scaled_arena_region = lambda *a, **k: False
    controller._click_abs = lambda *a, **k: None
    controller.input = ExclusiveInputController(NullInputController())
    controller._observe_group_overlay = lambda *a: ("unknown", None, None, {})
    return controller


def group_line(context="GroupingContext_Scry"):
    return json.dumps({
        "greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_GroupReq", "systemSeatIds": [1],
            "groupReq": {"context": context},
        }]},
    })


class GroupRecoveryTest(unittest.TestCase):
    def setUp(self):
        _FakeTimer.instances = []
        self.controller = make_controller()
        self.addCleanup(self._cancel_timers)

    def _cancel_timers(self):
        for name in ("__inactivity_timer", "__group_resume_timer", "__decision_recovery_timer"):
            timer = getattr(self.controller, f"_Controller{name}", None)
            if timer is not None and hasattr(timer, "cancel"):
                timer.cancel()

    def _set_recovery_decision(self, *, step="Step_Main"):
        self.controller._suppress_selections = False
        self.controller._stop_requested = False
        self.controller.updated_game_state = GameState({
            "gameStateId": 50,
            "turnInfo": {
                "turnNumber": 4, "phase": "Phase_Main1", "step": step,
                "decisionPlayer": 1,
            },
        })

    def test_duplicate_group_prompt_does_not_replace_resume(self):
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__handle_group_req(group_line())
            timer = self.controller._Controller__group_resume_timer
            self.controller._Controller__handle_group_req(group_line("GroupingContext_Surveil"))
        self.assertEqual(self.controller._Controller__group_prompt_seq, 1)
        self.assertIs(self.controller._Controller__group_resume_timer, timer)

    def test_old_done_callback_cannot_click_after_new_prompt(self):
        clicks = []
        self.controller._click_abs = lambda *a, **k: clicks.append((a, k))
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__handle_group_req(group_line())
            old_done = _FakeTimer.instances[0]
            self.controller._Controller__group_prompt_seq += 1
            old_done.callback()
        self.assertEqual(clicks, [])

    def test_stale_group_resume_is_ignored(self):
        calls = []
        self.controller._Controller__decision_callback = lambda state: calls.append(state)
        self.controller._Controller__group_prompt_seq = 2
        self.controller._Controller__group_prompt_match_id = "match-1"
        self.controller._Controller__resume_decision_after_group_req(
            prompt_seq=1, match_id="match-1"
        )
        self.assertEqual(calls, [])

    def test_valid_group_resume_redrives_clean_priority(self):
        self.controller.updated_game_state = GameState({
            "gameStateId": 50,
            "turnInfo": {
                "turnNumber": 4, "phase": "Phase_Main1", "step": "Step_Main",
                "activePlayer": 1, "priorityPlayer": 1, "decisionPlayer": 1,
                "nextPhase": "Phase_Combat", "nextStep": "Step_BeginCombat",
            },
            "timers": [], "gameObjects": [], "players": [{"systemSeatNumber": 1}],
            "annotations": [], "actions": [], "zones": [],
        })
        self.controller._Controller__group_prompt_seq = 1
        self.controller._Controller__group_prompt_match_id = "match-1"
        self.controller._Controller__has_mulled_keep = True
        calls = []
        self.controller._Controller__decision_callback = lambda state: calls.append(state)
        self.controller._Controller__resume_decision_after_group_req(
            prompt_seq=1, match_id="match-1"
        )
        self.assertEqual(len(calls), 1)

    def test_non_group_recovery_does_not_cancel_group_resume_timer(self):
        self.controller._Controller__group_prompt_seq = 1
        self.controller._Controller__group_prompt_match_id = "match-1"
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__schedule_group_resume(1.0)
            group_timer = self.controller._Controller__group_resume_timer
            self.controller._Controller__schedule_decision_recovery(1.0, "modal_recovery")
            recovery_timer = self.controller._Controller__decision_recovery_timer
        self.assertIsNot(group_timer, recovery_timer)
        self.assertFalse(group_timer.cancelled)
        self.assertEqual(group_timer.kwargs["prompt_seq"], 1)
        self.assertEqual(recovery_timer.kwargs["origin"], "modal_recovery")

    def test_decision_recovery_retries_until_temporary_guard_clears(self):
        self._set_recovery_decision()
        ready = {"value": False}
        self.controller._Controller__safe_to_redrive_decision = lambda: ready["value"]
        calls = []
        self.controller._Controller__invoke_decision_callback = (
            lambda reason: calls.append(reason)
        )
        self.controller.reset_inactivity_timer = lambda: None

        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.Controller.runtime_status.clear_intentional_wait"):
            self.controller._Controller__schedule_decision_recovery(0.2, "modal_recovery")
            first = self.controller._Controller__decision_recovery_timer
            first.callback(**first.kwargs)
            retry = self.controller._Controller__decision_recovery_timer
            self.assertIsNot(retry, first)
            self.assertEqual(retry.kwargs["match_id"], "match-1")
            self.assertEqual(retry.kwargs["decision_key"], (4, "Phase_Main1", "Step_Main", 1))

            ready["value"] = True
            retry.callback(**retry.kwargs)

        self.assertEqual(calls, ["modal recovery"])

    def test_decision_recovery_stops_when_match_or_turn_decision_changes(self):
        for change in ("match", "decision"):
            with self.subTest(change=change):
                _FakeTimer.instances = []
                self._set_recovery_decision()
                self.controller._Controller__safe_to_redrive_decision = lambda: True
                calls = []
                self.controller._Controller__invoke_decision_callback = (
                    lambda reason: calls.append(reason)
                )
                with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
                     mock.patch("Controller.MTGAController.Controller.runtime_status.clear_intentional_wait"):
                    self.controller._Controller__schedule_decision_recovery(0.2, "cast_failure")
                    timer = self.controller._Controller__decision_recovery_timer
                    if change == "match":
                        self.controller._Controller__live_match_id = "match-2"
                    else:
                        self._set_recovery_decision(step="Step_Combat")
                    timer.callback(**timer.kwargs)

                self.assertEqual(calls, [])
                self.assertIsNone(self.controller._Controller__decision_recovery_timer)

    def _open_surveillance(self):
        c = self.controller
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            c._Controller__handle_group_req(group_line("GroupingContext_Surveil"))
        c._group_prompt["next_check"] = 0
        c._Controller__has_mulled_keep = True
        c.updated_game_state = GameState({
            "gameStateId": 50,
            "turnInfo": {"turnNumber": 22, "phase": "Phase_Combat", "step": "Step_CombatDamage",
                         "activePlayer": 1, "priorityPlayer": 1, "decisionPlayer": 1,
                         "nextPhase": "Phase_Ending", "nextStep": "Step_End"},
            "timers": [], "gameObjects": [], "players": [{"systemSeatNumber": 1}],
            "annotations": [], "actions": [], "zones": [],
        })
        c._Controller__invoke_decision_callback = mock.Mock()
        c.reset_inactivity_timer = mock.Mock()
        c._click_abs = mock.Mock()
        return c

    def _check_group(self, observation):
        c = self.controller
        c._group_prompt["next_check"] = 0
        c._observe_group_overlay = mock.Mock(return_value=observation)
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.group_recovery.focus_mtga_window", return_value=True):
            c._Controller__resume_decision_after_group_req(
                prompt_seq=c._Controller__group_prompt_seq, match_id="match-1"
            )

    def test_failed_done_stays_pending_past_old_six_second_deadline(self):
        c = self._open_surveillance()
        self._check_group(("open", (960, 925), None, {}))
        self.assertTrue(c._group_prompt_blocks_gameplay())
        self.assertFalse(c._Controller__safe_to_redrive_decision())
        c._Controller__invoke_decision_callback.assert_not_called()
        c._click_abs.assert_called_once_with(960, 925, "GROUP_DONE")
        self.assertEqual(c._Controller__local_stall_signature()[0], "surveil")

    def test_retry_then_two_confirmations_resume_without_fresh_state(self):
        c = self._open_surveillance()
        self._check_group(("open", (960, 925), None, {}))
        self._check_group(("open", (960, 925), None, {}))
        self.assertEqual(c._click_abs.call_count, 2)
        self._check_group(("absent", None, None, {}))
        c._Controller__invoke_decision_callback.assert_not_called()
        self._check_group(("absent", None, None, {}))
        self.assertIsNone(c._group_prompt)
        c._Controller__invoke_decision_callback.assert_called_once_with("group/scry resume")

    def test_unknown_capture_interrupts_closure_confirmation(self):
        c = self._open_surveillance()
        for state in ("open", "absent", "unknown", "absent"):
            self._check_group((state, (960, 925) if state == "open" else None, None, {}))
        self.assertIsNotNone(c._group_prompt)
        c._Controller__invoke_decision_callback.assert_not_called()

    def test_never_seen_overlay_cannot_clear_gate_or_click_fixed_point(self):
        c = self._open_surveillance()
        for _ in range(3):
            self._check_group(("absent", None, None, {}))
        c._click_abs.assert_not_called()
        c._Controller__invoke_decision_callback.assert_not_called()

    def test_retry_and_check_limits_do_not_reset_watchdog_progress(self):
        c = self._open_surveillance()
        baseline = c._Controller__local_stall_signature()
        for _ in range(c._GROUP_CHECK_LIMIT):
            self._check_group(("open", (960, 925), None, {}))
        self.assertEqual(c._click_abs.call_count, 3)
        self.assertEqual(c._Controller__local_stall_signature(), baseline)
        c.reset_inactivity_timer.assert_not_called()
        self.assertIsNone(c._Controller__group_resume_timer)
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.assertTrue(c._group_watchdog_recovery())
            self.assertFalse(c._group_watchdog_recovery())
        self._check_group(("open", (960, 925), None, {}))
        self.assertEqual(c._click_abs.call_count, 4)

    def test_match_change_during_observation_prevents_click(self):
        c = self._open_surveillance()
        def observe(prompt):
            c._Controller__live_match_id = "match-2"
            return "open", (960, 925), None, {}
        c._observe_group_overlay = observe
        with mock.patch("Controller.MTGAController.group_recovery.focus_mtga_window", return_value=True):
            c._recover_group_prompt()
        c._click_abs.assert_not_called()

    def test_stop_during_observation_prevents_click(self):
        c = self._open_surveillance()
        def observe(prompt):
            c._stop_requested = True
            return "open", (960, 925), None, {}
        c._observe_group_overlay = observe
        with mock.patch("Controller.MTGAController.group_recovery.focus_mtga_window", return_value=True):
            c._recover_group_prompt()
        c._click_abs.assert_not_called()

    def test_input_ownership_covers_observation_and_press(self):
        from contextlib import contextmanager
        c = self._open_surveillance()
        held = []
        @contextmanager
        def transaction(**kwargs):
            held.append(True)
            yield True
            held.clear()
        c.input = mock.Mock()
        c.input.input_transaction = transaction
        def observe(prompt):
            self.assertTrue(held)
            return "open", (960, 925), None, {}
        c._observe_group_overlay = observe
        c._click_abs.side_effect = lambda *args: self.assertTrue(held)
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.group_recovery.focus_mtga_window", return_value=True):
            c._recover_group_prompt()
        c._click_abs.assert_called_once()

    def test_missing_focus_never_clicks_or_clears_pending(self):
        c = self._open_surveillance()
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.group_recovery.sys.platform", "win32"), \
             mock.patch("Controller.MTGAController.group_recovery.focus_mtga_window", return_value=False):
            c._recover_group_prompt()
        c._click_abs.assert_not_called()
        self.assertTrue(c._group_prompt_blocks_gameplay())

    def test_non_windows_group_overlay_can_complete_without_focus_helper(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                self.controller._Controller__last_group_req_ts = 0.0
                c = self._open_surveillance()
                c._observe_group_overlay = mock.Mock(side_effect=[
                    ("open", (960, 925), None, {}),
                    ("absent", None, None, {}),
                    ("absent", None, None, {}),
                ])
                with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
                     mock.patch("Controller.MTGAController.group_recovery.sys.platform", platform), \
                     mock.patch("Controller.MTGAController.group_recovery.focus_mtga_window", return_value=False):
                    for _ in range(3):
                        c._group_prompt["next_check"] = 0
                        c._Controller__resume_decision_after_group_req(
                            prompt_seq=c._Controller__group_prompt_seq, match_id="match-1",
                        )
                c._click_abs.assert_called_once_with(960, 925, "GROUP_DONE")
                self.assertFalse(c._group_prompt_blocks_gameplay())
                c._Controller__invoke_decision_callback.assert_called_once_with("group/scry resume")

    def test_watchdog_defers_once_without_resetting_original_deadline(self):
        c = self._open_surveillance()
        c._Controller__auto_concede_stalled_matches = True
        c._Controller__stall_context_started_at = 60.0
        c._Controller__stall_watchdog_generation = 1
        signature = c._Controller__local_stall_signature()
        c._Controller__stall_context_signature = signature
        c._Controller__is_live_match = lambda *args: True
        c._Controller__arm_stall_watchdog_timer = mock.Mock()
        c._Controller__claim_concession = mock.Mock(return_value=False)
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.Controller.time.monotonic", return_value=100.0):
            c._Controller__attempt_stall_concede(1, signature, 60.0, "match-1")
            c._Controller__claim_concession.assert_not_called()
            c._Controller__arm_stall_watchdog_timer.assert_called_once_with(
                1, signature, 60.0, "match-1", 5.0
            )
            self.assertEqual(c._Controller__stall_context_started_at, 60.0)
            c._Controller__attempt_stall_concede(1, signature, 60.0, "match-1")
            c._Controller__claim_concession.assert_called_once_with("stalled_local_context")

    def test_stale_resume_does_not_clear_new_prompt_timer(self):
        c = self._open_surveillance()
        timer = c._Controller__group_resume_timer
        c._Controller__resume_decision_after_group_req(prompt_seq=0, match_id="match-1")
        self.assertIs(c._Controller__group_resume_timer, timer)

    def test_live_match_change_before_resume_is_ignored(self):
        c = self._open_surveillance()
        c._Controller__live_match_id = "match-2"
        self._check_group(("open", (960, 925), None, {}))
        c._click_abs.assert_not_called()

    def test_match_reset_releases_gate(self):
        c = self._open_surveillance()
        c.reset_for_new_game()
        self.assertFalse(c._group_prompt_blocks_gameplay())



class GroupOverlayRecognitionTest(unittest.TestCase):
    def setUp(self):
        import cv2
        from pathlib import Path
        from vision.vision import VisionEngine
        self.controller = make_controller()
        self.frame = cv2.resize(cv2.imread(str(Path(__file__).parent / "fixtures" / "surveil_open.jpg")), (1920, 1080))
        self.controller._vision = VisionEngine()
        self.controller._vision.capture = mock.Mock(return_value=self.frame)
        self.controller._get_ui_action_arena_region = lambda **kwargs: (1323, 122, 1920, 1080)

    def observe(self):
        from Controller.MTGAController.group_recovery import GroupRecoveryMixin
        return GroupRecoveryMixin._observe_group_overlay(
            self.controller, {"context": "GroupingContext_Surveil"}
        )

    def test_real_surveillance_button_is_located_in_recorded_window(self):
        state, point, frame, details = self.observe()
        self.assertEqual(state, "open")
        self.assertAlmostEqual(point[0], 2283, delta=3)
        self.assertAlmostEqual(point[1], 1047, delta=3)
        self.assertGreater(details["done_score"], .78)

    def test_heading_proves_overlay_when_done_template_misses(self):
        vision = self.controller._vision
        find = vision.find_template
        vision.find_template = lambda image, path, **kwargs: (
            None if path.endswith("scry_done.png") else find(image, path, **kwargs)
        )
        state, point, frame, details = self.observe()
        self.assertEqual(state, "open")
        self.assertEqual(point, (2283, 1047))
        self.assertTrue(details["fixed_fallback"])

    def test_no_visual_evidence_has_no_fixed_fallback(self):
        self.controller._vision.find_template = mock.Mock(return_value=None)
        state, point, frame, details = self.observe()
        self.assertEqual(state, "absent")
        self.assertIsNone(point)

    def test_missing_or_blank_capture_is_unknown(self):
        import numpy as np
        for frame in (None, np.zeros((1080, 1920, 3), dtype=np.uint8)):
            self.controller._vision.capture.return_value = frame
            self.assertEqual(self.observe()[0], "unknown")


if __name__ == "__main__":
    unittest.main()
