"""Tests for passive hand-cast acknowledgement soak instrumentation."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState
from tools.analyze_cast_ack_soak import summarize


class _FakeTimer:
    def __init__(self, delay, callback, args=None, kwargs=None):
        self.delay = delay
        self.callback = callback
        self.args = tuple(args or ())
        self.kwargs = dict(kwargs or {})
        self.daemon = False
        self.cancelled = False

    def start(self):
        return None

    def cancel(self):
        self.cancelled = True


class _ImmediateThread:
    def __init__(self, target, args=(), **_kwargs):
        self.target = target
        self.args = args
        self.daemon = False

    def start(self):
        self.target(*self.args)


def make_controller() -> Controller:
    handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    handle.close()
    controller = Controller(handle.name)
    controller._Controller__live_match_id = "match-1"
    controller._Controller__last_seen_match_id = "match-1"
    controller._Controller__system_seat_id = 1
    controller._vision = None
    return controller


def seed_state(controller: Controller, *, card_in_hand=True, state_id=50):
    zone = "ZoneType_Hand" if card_in_hand else "ZoneType_Battlefield"
    controller.updated_game_state = GameState({
        "gameStateId": state_id,
        "turnInfo": {
            "turnNumber": 3, "phase": "Phase_Main1", "step": "Step_Main",
            "activePlayer": 1, "priorityPlayer": 1, "decisionPlayer": 1,
        },
        "timers": [],
        "gameObjects": [{"instanceId": 10, "grpId": 93833, "zoneId": 31}],
        "players": [{"systemSeatNumber": 1}],
        "annotations": [],
        "actions": ([{
            "seatId": 1,
            "action": {"actionType": "ActionType_Cast", "instanceId": 10},
        }] if card_in_hand else []),
        "zones": [{"zoneId": 31, "type": zone, "objectInstanceIds": [10]}],
    })


class CastAcknowledgementSoakTest(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        seed_state(self.controller)
        self.events = []
        self.controller._Controller__cast_ack_event = (
            lambda event, **details: self.events.append((event, details))
        )

    def _begin_and_click(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__note_cast_ack_click(attempt_id, (100, 900))
        return attempt_id

    def test_card_leaving_hand_acknowledges_attempt(self):
        attempt_id = self._begin_and_click()
        seed_state(self.controller, card_in_hand=False, state_id=51)

        self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = self.events[-1]
        self.assertEqual(event, "acknowledged")
        self.assertIn("card_left_hand", details["signals"])
        self.assertNotIn(attempt_id, self.controller._Controller__cast_ack_attempts)

    def test_no_acknowledgement_writes_timeout_bundle(self):
        attempt_id = self._begin_and_click()
        bundles = []
        self.controller._Controller__write_cast_ack_bundle = lambda payload: bundles.append(payload)

        with mock.patch("Controller.MTGAController.Controller.threading.Thread", _ImmediateThread):
            self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = self.events[-1]
        self.assertEqual(event, "ack_timeout")
        self.assertEqual(details["card_id"], 10)
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["reason"], "no_cast_acknowledgement")

    def test_no_click_is_recorded_without_a_timeout(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")

        self.controller._Controller__finish_cast_ack_without_click(attempt_id, "hover_never_found")

        event, details = self.events[-1]
        self.assertEqual(event, "cast_not_clicked")
        self.assertEqual(details["reason"], "hover_never_found")
        self.assertNotIn(attempt_id, self.controller._Controller__cast_ack_attempts)


class CastAcknowledgementAnalyzerTest(unittest.TestCase):
    def test_summarizes_ack_and_timeout(self):
        events = [
            {"event": "cast_selected", "attempt_id": "a", "card_id": 10, "match_id": "m"},
            {"event": "acknowledged", "attempt_id": "a", "card_id": 10,
             "signals": ["card_left_hand"]},
            {"event": "cast_selected", "attempt_id": "b", "card_id": 11, "match_id": "m"},
            {"event": "ack_timeout", "attempt_id": "b", "card_id": 11,
             "reason": "no_cast_acknowledgement"},
        ]

        report = summarize(events)

        self.assertEqual(report["attempt_count"], 2)
        self.assertEqual(report["outcome_counts"], {"acknowledged": 1, "timeout": 1})
        self.assertEqual(report["ack_signal_counts"], {"card_left_hand": 1})
        self.assertEqual(report["timeout_reasons"], {"no_cast_acknowledgement": 1})


if __name__ == "__main__":
    unittest.main()
