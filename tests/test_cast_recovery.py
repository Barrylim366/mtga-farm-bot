"""Regression tests for guarded hand casts and acknowledgement recovery."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState
from state.state_machine import BotState


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
    controller._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
    controller._click_image_in_scaled_arena_region = lambda *a, **k: False
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


class CastRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        seed_state(self.controller)
        self.events = []
        self.controller._Controller__cast_ack_event = (
            lambda event, **details: self.events.append((event, details))
        )
        # Blocker tests exercise the abort itself; do not leave live polling
        # timers attached to their mocked, permanently blocked controller.
        self.controller._Controller__schedule_cast_clear_poll = lambda *_args: None

    def _run_final_clicks(self, *, acquired=True, move_cursor=False,
                          post_press_hover=None, post_press_cursor=False,
                          post_press_foreground=False, cast_ack_id=None, blocker=None,
                          capture_on_reset=False, queued_hover=None,
                          hover_source="local_fragment", hover_age=0.0,
                          decision_context=None):
        class FakeLog:
            def __init__(inner):
                inner.available = queued_hover is not None
                inner.line = "target"

            def clear_new_line_flag(inner, _pattern):
                inner.available = True

            def has_new_line(inner, _pattern):
                return inner.available

            def get_latest_line_containing_pattern(inner, _pattern):
                inner.available = False
                return inner.line

        class FakeInput:
            def __init__(inner):
                inner.x, inner.y = (108 if move_cursor else 100), 900
                inner.clicks = []
                inner.moves = []
                inner.log = fake_log

            @contextmanager
            def input_transaction(inner, _timeout):
                yield acquired

            def move_abs(inner, x, y):
                inner.x, inner.y = x, y
                inner.moves.append((x, y))
                if move_cursor:
                    inner.x += 8

            def position(inner):
                return type("Pos", (), {"x": inner.x, "y": inner.y})()

            def left_click(inner, _count=1):
                inner.clicks.append((inner.x, inner.y))
                if len(inner.clicks) == 1 and post_press_hover is not None:
                    inner.log.line = post_press_hover
                    inner.log.available = True
                if len(inner.clicks) == 1 and post_press_cursor:
                    inner.x += 8

        fake_log = FakeLog()
        if queued_hover is not None:
            fake_log.line = queued_hover
        fake_input = FakeInput()
        self.controller.input = fake_input
        self.controller.log_reader = fake_log
        self.controller.patterns = {"hover_id": "hover"}
        self.controller.can_execute_game_action = lambda _match: True
        self.controller._Controller__abort_stale_cast_context = mock.Mock(return_value=False)
        self.controller._Controller__cast_blocking_ui = mock.Mock(return_value=blocker)
        if capture_on_reset:
            class FakeVision:
                def begin_tick(inner):
                    return None

                def capture(inner, _region):
                    self.assertEqual((fake_input.x, fake_input.y), (100, 900))
                    return np.zeros((16, 16, 3), dtype=np.uint8)

            self.controller._vision = FakeVision()
            self.controller._input_backend_name = "test"
            self.controller._arena_region = (0, 0, 16, 16)
        self.controller._Controller__parse_hover_observation = lambda line: {
            "target": (10, "local_fragment"),
            "other-local": (11, "local_fragment"),
            "relayed-other": (11, "relayed_ui"),
        }.get(line, (None, None))
        self.controller._Controller__schedule_decision_recovery = lambda *_args: None
        foregrounds = ([{"is_mtga": True}] * 2 + [{"is_mtga": False}] * 4
                       if post_press_foreground else [{"is_mtga": True}] * 8)
        with mock.patch("Controller.MTGAController.Controller._describe_foreground_window",
                        side_effect=foregrounds), mock.patch("time.sleep", return_value=None):
            result = self.controller._Controller__cast_final_clicks(
                10, click_position=(100, 900), hand_p1=(20, 900),
                expected_match_id="match-1", decision_context=decision_context,
                cast_ack_id=cast_ack_id, attempt=0,
                hover_observation={"hover_id": 10, "hover_source": hover_source},
                hover_observed_monotonic=time.monotonic() - hover_age,
            )
        return result, fake_input

    def test_final_click_pair_is_ordered_at_confirmed_target(self):
        result, fake_input = self._run_final_clicks()
        self.assertTrue(result)
        self.assertEqual(fake_input.clicks, [(100, 900), (100, 900)])
        self.assertEqual(fake_input.moves, [])

    def test_busy_transaction_and_cursor_displacement_send_no_clicks(self):
        result, fake_input = self._run_final_clicks(acquired=False)
        self.assertFalse(result)
        self.assertEqual(fake_input.clicks, [])
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "cast_input_busy")

        result, fake_input = self._run_final_clicks(move_cursor=True)
        self.assertFalse(result)
        self.assertEqual(fake_input.clicks, [])
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "cast_cursor_moved")

    def test_target_prompt_after_hover_recheck_stops_before_first_press(self):
        pending = [False]
        self.controller.should_defer_cast_for_target_selection = lambda _match: pending[0]

        def prompt_after_hover(_card_id):
            pending[0] = True
            return True, None

        with mock.patch.object(self.controller, "_Controller__check_cast_hover_queue",
                               side_effect=prompt_after_hover):
            result, fake_input = self._run_final_clicks()
        self.assertFalse(result)
        self.assertEqual(fake_input.clicks, [])
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "target_selection_pending")

    def test_relayed_mismatched_hover_after_first_press_does_not_cancel_pair(self):
        result, fake_input = self._run_final_clicks(post_press_hover="relayed-other")
        self.assertTrue(result)
        self.assertEqual(fake_input.clicks, [(100, 900), (100, 900)])

    def test_each_known_blocker_sends_zero_cast_clicks(self):
        for blocker in ("pay", "cancel", "your_turn"):
            with self.subTest(blocker=blocker):
                result, fake_input = self._run_final_clicks(blocker=blocker)
                self.assertFalse(result)
                self.assertEqual(fake_input.clicks, [])
                self.assertEqual(
                    self.controller.get_last_cast_abort_reason(), "cast_screen_blocked"
                )
                self.assertEqual(fake_input.moves, [])

    def test_scan_hover_is_sufficient_without_a_duplicate_hover_event(self):
        result, fake_input = self._run_final_clicks(capture_on_reset=True)
        self.assertTrue(result)
        self.assertEqual(fake_input.clicks, [(100, 900), (100, 900)])
        self.assertEqual(fake_input.moves, [])

    def test_stale_relayed_or_superseded_hover_sends_no_clicks(self):
        for kwargs in (
            {"hover_age": 1.6},
            {"hover_source": "relayed_ui"},
            {"queued_hover": "other-local"},
        ):
            with self.subTest(kwargs=kwargs):
                self.controller._Controller__cast_hover_failures.clear()
                result, fake_input = self._run_final_clicks(**kwargs)
                self.assertFalse(result)
                self.assertEqual(fake_input.clicks, [])
                self.assertEqual(self.controller.get_last_cast_abort_reason(), "cast_hover_lost")

    def test_persistent_blocker_bundle_contains_the_checked_frame(self):
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temp_dir:
            self.controller._arena_region = (0, 0, 16, 16)
            self.controller._vision = mock.Mock()
            self.controller._vision.save_image.side_effect = (
                lambda image, path: cv2.imwrite(path, image)
            )
            with mock.patch("Controller.MTGAController.Controller.bot_logger.ensure_debug_dir",
                            return_value=temp_dir):
                self.controller._Controller__write_cast_blocker_bundle(
                    frame, "cancel", 10, (100, 900),
                )
            saved = Path(temp_dir)
            self.assertTrue((saved / "arena.png").exists())
            metadata = json.loads((saved / "blocker.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["blocker"], "cancel")
            self.assertEqual(metadata["cursor_position"], [100, 900])

    def test_new_hover_after_first_press_does_not_delay_or_cancel_second_click(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")
        self.controller._Controller__cast_ack_event = (
            lambda event, **details: self.events.append((event, details))
        )
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            result, fake_input = self._run_final_clicks(
                post_press_hover="other-local", cast_ack_id=attempt_id,
            )
        self.assertTrue(result)
        self.assertEqual(fake_input.clicks, [(100, 900), (100, 900)])
        attempt = self.controller._Controller__cast_ack_attempts[attempt_id]
        self.assertEqual(attempt["press_count"], 2)
        event_names = [event for event, _details in self.events]
        self.assertIn("PRESS_2", event_names)
        self.assertNotIn("cast_partial_press", event_names)
        self.assertNotIn("cast_not_clicked", event_names)
        self.assertEqual(self.controller._Controller__cast_blocking_ui.call_count, 1)
        self.assertEqual(self.controller._Controller__abort_stale_cast_context.call_count, 2)

    def test_cursor_movement_after_first_press_withholds_second_click(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            result, fake_input = self._run_final_clicks(
                post_press_cursor=True, cast_ack_id=attempt_id,
            )
        self.assertFalse(result)
        self.assertEqual(fake_input.clicks, [(100, 900)])
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "cast_cursor_moved")
        self.assertIn("cast_partial_press", [event for event, _ in self.events])

    def test_foreground_loss_after_first_press_withholds_second_click(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            result, fake_input = self._run_final_clicks(
                post_press_foreground=True, cast_ack_id=attempt_id,
            )
        self.assertFalse(result)
        self.assertEqual(fake_input.clicks, [(100, 900)])
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "foreground_recovery_failed")

    def test_visual_blockers_match_fixed_regions_and_ignore_ordinary_preview_region(self):
        class FakeVision:
            def __init__(inner, frame):
                inner.frame = frame

            def begin_tick(inner):
                return None

            def capture(inner, _region):
                return inner.frame

        self.controller._arena_region = (0, 0, 1920, 1080)
        self.controller._input_backend_name = "test"
        root = Path(ROOT) / "assets" / "assert" / "cast_blockers"
        cases = (
            ("pay", (880, 443), "pay"),
            ("cancel", (1653, 918), "cancel"),
        )
        for name, (x, y), expected in cases:
            with self.subTest(name=name):
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                template = cv2.imread(str(root / f"{name}.png"))
                h, w = template.shape[:2]
                frame[y:y + h, x:x + w] = template
                self.controller._vision = FakeVision(frame)
                self.assertEqual(self.controller._Controller__cast_blocking_ui(), expected)

        clear = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # A preview placed in the far-right region is outside every blocker ROI.
        template = cv2.imread(str(root / "pay.png"))
        h, w = template.shape[:2]
        clear[500:500 + h, 1580:1580 + w] = template
        self.controller._vision = FakeVision(clear)
        self.assertIsNone(self.controller._Controller__cast_blocking_ui())

    def test_pay_label_survives_small_image_changes_and_uses_supplied_frame(self):
        self.controller._arena_region = (0, 0, 1920, 1080)
        self.controller._input_backend_name = "test"
        self.controller._vision = mock.Mock()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        template = cv2.imread(str(Path(ROOT) / "assets/assert/cast_blockers/pay.png"))
        # A slight render difference should not make the stable Pay label vanish.
        softened = cv2.GaussianBlur(template, (3, 3), 0.6)
        h, w = softened.shape[:2]
        for cost in ("1BB", "3RR", "XWU"):
            with self.subTest(cost=cost):
                frame.fill(0)
                frame[443:443 + h, 880:880 + w] = softened
                cv2.putText(frame, cost, (940, 472), cv2.FONT_HERSHEY_SIMPLEX,
                            0.8, (230, 230, 230), 2)
                self.assertEqual(self.controller._Controller__cast_blocking_ui(frame), "pay")
        self.controller._vision.capture.assert_not_called()

    def test_cyan_card_outline_is_not_a_blocker(self):
        class FakeVision:
            def begin_tick(inner):
                return None

            def capture(inner, _region):
                return inner.frame

        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        cv2.rectangle(frame, (480, 820), (800, 1079), (255, 255, 0), 10)
        vision = FakeVision()
        vision.frame = frame
        self.controller._vision = vision
        self.controller._arena_region = (0, 0, 1920, 1080)
        self.controller._input_backend_name = "test"
        self.assertIsNone(self.controller._Controller__cast_blocking_ui())

    def test_real_your_turn_frames_match_and_preview_frame_does_not(self):
        class FakeVision:
            def __init__(inner, frame):
                inner.frame = frame

            def begin_tick(inner):
                return None

            def capture(inner, _region):
                return inner.frame

        self.controller._arena_region = (0, 0, 1920, 1080)
        self.controller._input_backend_name = "test"
        root = Path(ROOT) / "tests" / "fixtures" / "cast_blockers"
        for name in ("your_turn_overlay_roi.png", "your_turn_overlay_variant_roi.png"):
            with self.subTest(name=name):
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                roi = cv2.imread(str(root / name))
                frame[350:650, 600:1320] = roi
                self.controller._vision = FakeVision(frame)
                self.assertEqual(self.controller._Controller__cast_blocking_ui(), "your_turn")

        for name in ("clear_board_roi.png", "clear_board_preview_roi.png"):
            with self.subTest(name=name):
                frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                roi = cv2.imread(str(root / name))
                frame[350:650, 600:1320] = roi
                self.controller._vision = FakeVision(frame)
                self.assertIsNone(self.controller._Controller__cast_blocking_ui())

    def test_your_turn_uses_distributed_letters_not_one_connected_shape(self):
        self.controller._arena_region = (0, 0, 1920, 1080)
        self.controller._input_backend_name = "test"
        self.controller._vision = mock.Mock()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        cv2.putText(frame, "YOUR TURN", (710, 520), cv2.FONT_HERSHEY_SIMPLEX,
                    2.6, (0, 150, 255), 7, cv2.LINE_AA)
        self.assertEqual(self.controller._Controller__cast_blocking_ui(frame), "your_turn")

        # A bright orange element confined to one part of the board is not a banner.
        frame.fill(0)
        cv2.rectangle(frame, (700, 445), (800, 530), (0, 150, 255), -1)
        self.assertIsNone(self.controller._Controller__cast_blocking_ui(frame))

    def test_hover_observation_distinguishes_local_and_relayed_messages(self):
        local = self.controller._Controller__parse_hover_observation('"objectId": 287')
        relayed = self.controller._Controller__parse_hover_observation(
            '{"greToClientEvent":{"greToClientMessages":[{"uiMessage":'
            '{"onHover":{"objectId":279}}}]}}'
        )
        self.assertEqual(local, (287, "local_fragment"))
        self.assertEqual(relayed, (279, "relayed_ui"))

    def _begin_and_click(self):
        attempt_id = self.controller._Controller__begin_cast_ack(10, "match-1")
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer), \
             mock.patch("Controller.MTGAController.Controller._describe_foreground_window",
                        return_value={"is_mtga": True}):
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
        class FakeVision:
            def begin_tick(inner):
                return None

            def capture(inner, _region):
                return np.zeros((16, 16, 3), dtype=np.uint8)

        self.controller._vision = FakeVision()
        self.controller._arena_region = (0, 0, 16, 16)
        attempt_id = self._begin_and_click()
        bundles = []
        self.controller._Controller__write_cast_ack_bundle = lambda payload: bundles.append(payload)
        self.controller._Controller__schedule_decision_recovery = lambda *_args: None

        with mock.patch("Controller.MTGAController.Controller.threading.Thread", _ImmediateThread):
            self.controller._Controller__probe_cast_ack(attempt_id, final_probe=True)

        event, details = next((event, details) for event, details in self.events
                              if event == "click_ineffective")
        self.assertEqual(event, "click_ineffective")
        self.assertEqual(details["card_id"], 10)
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["reason"], "card_and_game_state_unchanged")
        self.assertIsNotNone(bundles[0]["attempt"].get("delayed_image"))

    def test_guarded_escape_recovery_and_conditional_options_close(self):
        input_stub = mock.Mock()
        input_stub.input_transaction.return_value.__enter__ = mock.Mock(return_value=True)
        input_stub.input_transaction.return_value.__exit__ = mock.Mock(return_value=False)
        self.controller.input = input_stub
        self.controller.can_execute_game_action = lambda _match: True
        self.controller._Controller__abort_stale_cast_context = mock.Mock(return_value=False)
        unchanged = {"prompt_flags": {}, "game_state_id": 1, "match_id": "match-1",
                     "turn": {}, "card_in_hand": True, "cast_actions": []}
        self.controller._Controller__cast_ack_snapshot = mock.Mock(return_value=unchanged)
        self.controller._Controller__cast_blocking_ui = mock.Mock(return_value=None)
        self.controller._options_overlay_visible = mock.Mock(side_effect=[True, False])
        self.controller._Controller__schedule_decision_recovery = lambda *_args: None
        with mock.patch("Controller.MTGAController.Controller._describe_foreground_window",
                        return_value={"is_mtga": True}), mock.patch("time.sleep"):
            self.controller._Controller__recover_ineffective_cast("origin", {
                "attempt_id": "origin", "card_id": 10, "expected_match_id": "match-1",
                "decision_context": {"match_id": "match-1", "game_state_id": 1},
            }, unchanged)
        self.assertEqual(input_stub.tap_escape.call_count, 2)
        phases = [details.get("phase") for event, details in self.events
                  if event == "cast_escape_recovery"]
        self.assertIn("after_escape", phases)
        self.assertIn(("match-1", 1, 10), self.controller._Controller__escape_cast_retries)

    def test_escape_recovery_sends_no_key_when_transaction_is_busy(self):
        input_stub = mock.Mock()
        scope = mock.MagicMock()
        scope.__enter__.return_value = False
        input_stub.input_transaction.return_value = scope
        self.controller.input = input_stub
        self.controller.can_execute_game_action = lambda _match: True
        self.controller._Controller__cast_blocking_ui = mock.Mock(return_value=None)
        recovery = mock.Mock()
        self.controller._Controller__schedule_decision_recovery = recovery
        self.controller._Controller__recover_ineffective_cast("origin", {
            "attempt_id": "origin", "card_id": 10, "expected_match_id": "match-1",
            "decision_context": {"match_id": "match-1", "game_state_id": 1},
        }, {"game_state_id": 50})
        input_stub.tap_escape.assert_not_called()
        self.assertFalse(self.controller._Controller__cast_escape_in_progress)
        self.assertEqual(self.controller._Controller__escape_cast_retries, {})
        recovery.assert_called_once_with(0.2, "cast_escape_deferred")

    def test_blocked_two_click_cast_polls_without_escape(self):
        poll = mock.Mock()
        self.controller._Controller__schedule_cast_clear_poll = poll
        self.controller._Controller__cast_blocking_ui = mock.Mock(return_value="pay")
        self.controller.input = mock.MagicMock()
        self.controller._Controller__recover_ineffective_cast("origin", {
            "card_id": 10, "expected_match_id": "match-1", "delayed_image": object(),
        }, {"game_state_id": 50})
        poll.assert_called_once_with("match-1", 50)
        self.controller.input.tap_escape.assert_not_called()
        self.assertEqual(self.controller._Controller__escape_cast_retries, {})

    def test_clear_poll_resumes_once_and_stops_on_state_or_match_change(self):
        del self.controller._Controller__schedule_cast_clear_poll
        armed = []
        self.controller._Controller__arm_cast_clear_poll = lambda key: armed.append(key)
        self.controller.can_execute_game_action = lambda match: match == self.controller._Controller__live_match_id
        blockers = iter(["pay", None])
        self.controller._Controller__cast_blocking_ui = lambda: next(blockers)
        recovery = mock.Mock()
        self.controller._Controller__schedule_decision_recovery = recovery
        poll = self.controller._Controller__schedule_cast_clear_poll
        fire = self.controller._Controller__poll_cast_clear
        poll("match-1", 50)
        poll("match-1", 50)
        self.assertEqual(armed, [("match-1", 50)])
        fire("match-1", 50)
        self.assertEqual(len(armed), 2)
        fire("match-1", 50)
        fire("match-1", 50)
        recovery.assert_called_once_with(0.2, "cast_screen_cleared")

        poll("match-1", 50)
        seed_state(self.controller, state_id=51)
        fire("match-1", 50)
        self.assertIsNone(self.controller._Controller__cast_clear_poll_key)
        self.assertEqual(len(armed), 3)
        poll("match-1", 51)
        self.controller._Controller__live_match_id = "match-2"
        fire("match-1", 51)
        self.assertIsNone(self.controller._Controller__cast_clear_poll_key)
        recovery.assert_called_once()

    def test_lost_focus_before_escape_keeps_retry_available(self):
        self.controller.input = mock.MagicMock()
        self.controller.can_execute_game_action = lambda _match: True
        self.controller._Controller__cast_blocking_ui = mock.Mock(return_value=None)
        unchanged = {"game_state_id": 50, "match_id": "match-1", "prompt_flags": {}}
        self.controller._Controller__cast_ack_snapshot = mock.Mock(return_value=unchanged)
        self.controller._Controller__abort_stale_cast_context = mock.Mock(return_value=False)
        recovery = mock.Mock()
        self.controller._Controller__schedule_decision_recovery = recovery
        with mock.patch("Controller.MTGAController.Controller._describe_foreground_window",
                        return_value={"is_mtga": False}):
            self.controller._Controller__recover_ineffective_cast("origin", {
                "card_id": 10, "expected_match_id": "match-1",
            }, unchanged)
        self.controller.input.tap_escape.assert_not_called()
        self.assertEqual(self.controller._Controller__escape_cast_retries, {})
        recovery.assert_called_once_with(0.2, "cast_escape_deferred")

    def test_followup_telemetry_uses_live_state_when_decision_context_omits_it(self):
        self.controller._Controller__escape_cast_retries[("match-1", 50, 10)] = {
            "origin_attempt_id": "origin", "followup_attempt_id": None,
            "exhausted": False,
        }
        self.controller.can_execute_game_action = lambda _match: True
        self.controller.should_defer_cast_for_target_selection = lambda _match: False
        self.controller._is_cast_suppressed = lambda _card_id: False
        self.controller._cast_once = lambda *_args, **_kwargs: True
        self.controller.cast(10, decision_context={"match_id": "match-1", "game_state_id": None})
        event, details = next((event, details) for event, details in self.events
                              if event == "cast_escape_followup")
        self.assertEqual(details["linked_attempt_id"], "origin")
        self.assertEqual(details["game_state_id"], 50)

    def test_ineffective_followup_wakes_decision_and_blocks_third_cast(self):
        key = ("match-1", 50, 10)
        self.controller._Controller__escape_cast_retries[key] = {
            "origin_attempt_id": "origin", "followup_attempt_id": "followup",
            "exhausted": False,
        }
        self.controller._Controller__cast_blocking_ui = mock.Mock(return_value=None)
        recovery = mock.Mock()
        self.controller._Controller__schedule_decision_recovery = recovery

        self.controller._Controller__recover_ineffective_cast(
            "followup", {
                "attempt_id": "followup", "card_id": 10,
                "expected_match_id": "match-1",
            }, {"game_state_id": 50},
        )

        self.assertTrue(self.controller._Controller__escape_cast_retries[key]["exhausted"])
        recovery.assert_called_once_with(0.2, "cast_escape_retry_exhausted")
        self.assertFalse(self.controller._Controller__cast_escape_in_progress)
        self.controller.can_execute_game_action = lambda _match: True
        self.controller._cast_once = mock.Mock()
        self.assertFalse(self.controller.cast(10, decision_context=self._decision_context()))
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "cast_escape_retry_exhausted")
        self.controller._cast_once.assert_not_called()

    def test_exhausted_cast_does_not_pass_stale_decision_to_game(self):
        self.controller._Controller__escape_cast_retries[("match-1", 50, 10)] = {
            "origin_attempt_id": "origin", "followup_attempt_id": "followup",
            "exhausted": True,
        }
        self.controller.can_execute_game_action = lambda _match: True
        self.controller._Controller__schedule_decision_recovery = mock.Mock()
        self.controller._cast_once = mock.Mock()
        self.assertFalse(self.controller.cast(
            10, decision_context=self._decision_context(phase="Phase_Main2")
        ))
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "stale_decision_context")
        self.controller._cast_once.assert_not_called()

    def test_failed_bundle_serializes_state_and_saves_all_three_images(self):
        class FakeVision:
            def __init__(inner):
                inner.frame = np.zeros((16, 16, 3), dtype=np.uint8)

            def save_image(inner, frame, path):
                cv2.imwrite(path, frame)

            def begin_tick(inner):
                return None

            def capture(inner, _region):
                return inner.frame

        with tempfile.TemporaryDirectory() as temp_dir:
            self.controller._vision = FakeVision()
            self.controller._arena_region = (0, 0, 16, 16)
            payload = {
                "attempt": {
                    "pre_click_image": self.controller._vision.frame.copy(),
                    "post_wait_image": self.controller._vision.frame.copy(),
                    "delayed_image": self.controller._vision.frame.copy(),
                    "press_count": 2,
                },
                "outcome": "click_ineffective",
            }
            with mock.patch(
                "Controller.MTGAController.Controller.bot_logger.ensure_debug_dir",
                return_value=temp_dir,
            ):
                self.controller._Controller__write_cast_ack_bundle(payload)
            for name in ("arena_pre_click.png", "arena_post_wait.png", "arena_delayed.png"):
                self.assertTrue((Path(temp_dir) / name).exists(), name)
            with (Path(temp_dir) / "cast_ack_state.json").open(encoding="utf-8") as handle:
                serialized = json.load(handle)
            self.assertEqual(serialized["outcome"], "click_ineffective")
            self.assertEqual(serialized["attempt"]["press_count"], 2)
            self.assertNotIn("delayed_image", serialized["attempt"])

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

    def test_same_state_hover_loss_allows_one_retry_then_exhausts(self):
        recovery = []
        self.controller._Controller__schedule_decision_recovery = (
            lambda delay, origin: recovery.append((delay, origin))
        )
        context = self._decision_context()
        self.controller._Controller__cast_safety_abort(
            None, 10, "cast_hover_lost", decision_context=context,
        )
        self.assertEqual(self.controller.get_last_cast_abort_reason(), "cast_hover_lost")
        self.controller._Controller__cast_safety_abort(
            None, 10, "cast_hover_lost", decision_context=context,
        )
        self.assertEqual(
            self.controller.get_last_cast_abort_reason(), "cast_hover_retry_exhausted"
        )
        self.assertEqual(recovery, [(0.2, "cast_safety_abort")])

        self.controller._Controller__clear_stale_cast_hover_failures(
            self._decision_context(state_id=51)
        )
        self.assertEqual(
            self.controller._Controller__note_cast_hover_failure(
                10, self._decision_context(state_id=51), None,
            ),
            1,
        )

        # Exercise the actual stale-hover call site, including its decision
        # context, across the cleanup performed before the next cast.
        for expected_reason in ("cast_hover_lost", "cast_hover_retry_exhausted"):
            self.controller._Controller__clear_stale_cast_hover_failures(context)
            result, fake_input = self._run_final_clicks(
                hover_age=1.6, decision_context=context,
            )
            self.assertFalse(result)
            self.assertEqual(fake_input.clicks, [])
            self.assertEqual(self.controller.get_last_cast_abort_reason(), expected_reason)

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


if __name__ == "__main__":
    unittest.main()
