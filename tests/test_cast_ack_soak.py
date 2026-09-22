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
from state.state_machine import BotState
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
    controller._get_state_from_log = lambda: BotState.IN_GAME
    controller._vision = None
    return controller


def seed_state(controller: Controller, *, card_in_hand=True, state_id=50, zone=None):
    zone = zone or ("ZoneType_Hand" if card_in_hand else "ZoneType_Battlefield")
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
            self.controller._Controller__note_cast_ack_pre_click(attempt_id, (100, 900))
            self.controller._Controller__note_cast_ack_click(attempt_id, (100, 900))
        return attempt_id

    def _decision_context(self, *, state_id=50, phase="Phase_Main1", action=True):
        return {
            "match_id": "match-1",
            "game_state_id": state_id,
            "turn": {
                "turnNumber": 3, "phase": phase, "step": "Step_Main",
                "activePlayer": 1, "priorityPlayer": 1, "decisionPlayer": 1,
            },
            "card_id": 10,
            "selected_actions": ([{
                "card_id": 10, "seat_id": 1, "type": "ActionType_Cast",
                "mana_cost": [], "ability_grp_id": None,
            }] if action else []),
        }

    def test_card_leaving_hand_acknowledges_attempt(self):
        attempt_id = self._begin_and_click()
        seed_state(self.controller, card_in_hand=False, state_id=51)

        self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = self.events[-1]
        self.assertEqual(event, "acknowledged")
        self.assertIn("card_left_hand", details["signals"])
        self.assertNotIn(attempt_id, self.controller._Controller__cast_ack_attempts)

    def test_unchanged_card_and_state_is_click_ineffective_and_writes_bundle(self):
        attempt_id = self._begin_and_click()
        bundles = []
        self.controller._Controller__write_cast_ack_bundle = lambda payload: bundles.append(payload)

        with mock.patch("Controller.MTGAController.Controller.threading.Thread", _ImmediateThread):
            self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = self.events[-1]
        self.assertEqual(event, "click_ineffective")
        self.assertEqual(details["card_id"], 10)
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["reason"], "card_and_game_state_unchanged")

    def test_state_advancing_while_card_stays_in_hand_is_not_bundled(self):
        attempt_id = self._begin_and_click()
        seed_state(self.controller, card_in_hand=True, state_id=51)
        bundles = []
        self.controller._Controller__write_cast_ack_bundle = lambda payload: bundles.append(payload)

        with mock.patch("Controller.MTGAController.Controller.threading.Thread", _ImmediateThread):
            self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = self.events[-1]
        self.assertEqual(event, "state_changed_elsewhere")
        self.assertEqual(details["reason"], "card_stayed_in_hand_while_state_changed")
        self.assertEqual(bundles, [])

    def test_no_progress_from_a_non_hand_zone_is_ambiguous_and_writes_bundle(self):
        seed_state(
            self.controller, card_in_hand=False, state_id=50,
            zone="ZoneType_Graveyard",
        )
        attempt_id = self._begin_and_click()
        bundles = []
        self.controller._Controller__write_cast_ack_bundle = lambda payload: bundles.append(payload)

        with mock.patch("Controller.MTGAController.Controller.threading.Thread", _ImmediateThread):
            self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = self.events[-1]
        self.assertEqual(event, "ambiguous")
        self.assertEqual(details["reason"], "no_strong_cast_progress_signal")
        self.assertEqual(len(bundles), 1)

    def test_pre_click_snapshot_is_the_acknowledgement_baseline(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")
        self.controller._Controller__note_cast_ack_pre_click(attempt_id, (100, 900))
        seed_state(self.controller, card_in_hand=False, state_id=51)
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__note_cast_ack_click(attempt_id, (100, 900))

        self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = self.events[-1]
        self.assertEqual(event, "acknowledged")
        self.assertIn("card_left_hand", details["signals"])

    def test_no_click_is_recorded_without_a_timeout(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")

        self.controller._Controller__finish_cast_ack_without_click(attempt_id, "hover_never_found")

        event, details = self.events[-1]
        self.assertEqual(event, "cast_not_clicked")
        self.assertEqual(details["reason"], "hover_never_found")
        self.assertNotIn(attempt_id, self.controller._Controller__cast_ack_attempts)

    def test_stale_state_aborts_before_the_hand_scan_and_redrives_decision(self):
        recovery = []
        self.controller._Controller__schedule_decision_recovery = (
            lambda delay, origin: recovery.append((delay, origin))
        )
        with mock.patch.object(self.controller, "_cast_once") as cast_once:
            result = self.controller.cast(
                10, decision_context=self._decision_context(state_id=49)
            )

        self.assertFalse(result)
        cast_once.assert_not_called()
        self.assertEqual(
            self.controller.get_last_cast_abort_reason(), "stale_decision_context"
        )
        self.assertEqual(recovery, [(0.2, "stale_cast_decision")])
        event, details = self.events[-1]
        self.assertEqual(event, "stale_decision_context")
        self.assertEqual(details["checkpoint"], "before_scan")
        self.assertIn("game_state_changed", details["mismatch_reasons"])

    def test_removed_selected_action_is_stale_even_while_card_remains_in_hand(self):
        recovery = []
        self.controller._Controller__schedule_decision_recovery = (
            lambda delay, origin: recovery.append((delay, origin))
        )
        seed_state(self.controller, card_in_hand=True, state_id=50)
        self.controller.updated_game_state = GameState({
            **self.controller.updated_game_state.get_full_state(), "actions": [],
        })
        with mock.patch.object(self.controller, "_cast_once") as cast_once:
            result = self.controller.cast(10, decision_context=self._decision_context())

        self.assertFalse(result)
        cast_once.assert_not_called()
        self.assertEqual(recovery, [(0.2, "stale_cast_decision")])
        self.assertIn("selected_action_missing", self.events[-1][1]["mismatch_reasons"])

    def test_state_change_during_scan_stops_before_any_cast_click(self):
        class _MutatingInput:
            def __init__(inner):
                inner.x, inner.y, inner.clicks = 0, 0, 0
                inner.changed = False

            def position(inner):
                return type("Pos", (), {"x": inner.x, "y": inner.y})()

            def move_abs(inner, x, y):
                inner.x, inner.y = x, y

            def move_rel(inner, dx, dy):
                inner.x += dx
                inner.y += dy
                if not inner.changed:
                    inner.changed = True
                    seed_state(self.controller, card_in_hand=True, state_id=51)

            def left_click(inner, _count=1):
                inner.clicks += 1

        fake_input = _MutatingInput()
        recovery = []
        self.controller.input = fake_input
        self.controller._Controller__schedule_decision_recovery = (
            lambda delay, origin: recovery.append((delay, origin))
        )
        self.controller._get_hand_scan_points_mapped = lambda **_kwargs: ((0, 0), (30, 0))
        self.controller._ensure_options_overlay_closed = lambda **_kwargs: True
        self.controller.log_reader.has_new_line = lambda _pattern: False
        self.controller.log_reader.clear_new_line_flag = lambda _pattern: None
        self.controller._write_hand_select_debug_bundle = lambda **_kwargs: None

        with mock.patch("Controller.MTGAController.Controller._describe_foreground_window", return_value={"is_mtga": True}), \
             mock.patch("time.sleep", return_value=None):
            result = self.controller._cast_once(
                10, expected_match_id="match-1", decision_context=self._decision_context()
            )

        self.assertFalse(result)
        self.assertEqual(fake_input.clicks, 0)
        self.assertEqual(recovery, [(0.2, "stale_cast_decision")])
        event, details = self.events[-1]
        self.assertEqual(event, "stale_decision_context")
        self.assertEqual(details["checkpoint"], "scan_motion")


class CastAcknowledgementAnalyzerTest(unittest.TestCase):
    def test_summarizes_all_classified_outcomes(self):
        events = [
            {"event": "cast_selected", "attempt_id": "a", "card_id": 10, "match_id": "m"},
            {"event": "acknowledged", "attempt_id": "a", "card_id": 10,
             "signals": ["card_left_hand"]},
            {"event": "cast_selected", "attempt_id": "b", "card_id": 11, "match_id": "m"},
            {"event": "click_ineffective", "attempt_id": "b", "card_id": 11,
             "reason": "card_and_game_state_unchanged"},
            {"event": "cast_selected", "attempt_id": "c", "card_id": 12, "match_id": "m"},
            {"event": "state_changed_elsewhere", "attempt_id": "c", "card_id": 12,
             "reason": "card_stayed_in_hand_while_state_changed"},
            {"event": "cast_selected", "attempt_id": "d", "card_id": 13, "match_id": "m"},
            {"event": "ambiguous", "attempt_id": "d", "card_id": 13,
             "reason": "no_strong_cast_progress_signal"},
            {"event": "cast_selected", "attempt_id": "e", "card_id": 14, "match_id": "m"},
            {"event": "stale_decision_context", "attempt_id": "e", "card_id": 14,
             "mismatch_reasons": ["game_state_changed"]},
        ]

        report = summarize(events)

        self.assertEqual(report["attempt_count"], 5)
        self.assertEqual(report["outcome_counts"], {
            "acknowledged": 1, "ambiguous": 1, "click_ineffective": 1,
            "stale_decision_context": 1, "state_changed_elsewhere": 1,
        })
        self.assertEqual(report["ack_signal_counts"], {"card_left_hand": 1})
        self.assertEqual(report["outcome_reasons"], {
            "card_and_game_state_unchanged": 1,
            "card_stayed_in_hand_while_state_changed": 1,
            "game_state_changed": 1,
            "no_strong_cast_progress_signal": 1,
        })
        self.assertEqual(
            [row["outcome"] for row in report["investigation_cases"]],
            ["click_ineffective", "ambiguous"],
        )
        self.assertEqual(
            [row["attempt_id"] for row in report["stale_decision_cases"]], ["e"]
        )


if __name__ == "__main__":
    unittest.main()
