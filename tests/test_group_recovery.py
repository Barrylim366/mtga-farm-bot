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


class _FakeTimer:
    instances = []

    def __init__(self, delay, callback, args=None, kwargs=None):
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


if __name__ == "__main__":
    unittest.main()
