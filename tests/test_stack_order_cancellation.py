"""Obsolete trigger ordering must yield to targeting without real input."""
import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState

module = importlib.import_module("Controller.MTGAController.Controller")


class StackOrderCancellationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "Player.log")
        with open(path, "w", encoding="utf-8"):
            pass
        self.c = c = Controller(path)
        c._Controller__system_seat_id = 1
        c._Controller__live_match_id = "match"
        c.can_execute_game_action = Mock(return_value=True)
        c.input = Mock()
        c.log_reader = Mock()
        c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        c._click_image_in_scaled_arena_region = Mock(return_value=False)
        c._locate_image_center = Mock(return_value=None)
        c._Controller__record_decision = Mock()
        c._Controller__clear_target_wait_if_unblocked = Mock()
        c._Controller__try_handle_fiery_equipment = Mock(return_value=True)
        c.submit_selection = Mock(return_value=True)
        c._get_stack_scan_points_mapped = Mock(return_value=((10, 100), (30, 100)))
        c._get_stack_fallback_scan_points_mapped = Mock(return_value=((40, 100), (60, 100)))
        c.stack_scan_step = c.stack_scan_fallback_step = 10
        c.screen_bounds = ((0, 0), (1920, 1080))
        c._Controller__parse_hover_id_line = Mock(return_value=427)
        self.state = {
            "turnInfo": {"decisionPlayer": 1, "priorityPlayer": 1},
            "pendingMessageCount": 0,
            "gameObjects": [{"instanceId": 427, "type": "GameObjectType_Ability"}],
            "zones": [
                {"zoneId": 27, "type": "ZoneType_Stack", "objectInstanceIds": [427]},
                {"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": 1,
                 "objectInstanceIds": []},
            ],
        }
        c.updated_game_state = GameState(self.state)
        self.tasks = []

        def timer(delay, callback, args=(), kwargs=None):
            return Mock(start=lambda: self.tasks.append(
                lambda: callback(*args, **(kwargs or {}))))

        c._input_timer = timer
        for context in (
            patch.object(module.time, "sleep"),
            patch.object(module.bot_logger, "log_info"),
            patch.object(module.bot_logger, "log_error"),
            patch.object(module.bot_logger, "log_move"),
            patch.object(module.bot_logger, "log_click"),
            patch.object(module.bot_logger, "log_hover"),
            patch.object(module.bot_logger, "set_hover_logging"),
        ):
            context.start()
            self.addCleanup(context.stop)

    def target(self, source=427, *, raw=False, seat=1):
        payload = {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_SelectTargetsReq", "systemSeatIds": [seat],
            "selectTargetsReq": {"sourceId": source, "targets": []},
        }]}}
        if raw:
            self.c._Controller__handle_target_selection_from_raw_dict(payload)
        else:
            self.c._Controller__handle_select_targets_req(json.dumps(payload))

    def ordering(self):
        self.c._Controller__handle_select_n_req(json.dumps({
            "greToClientEvent": {"greToClientMessages": [{
                "type": "GREMessageType_SelectNReq", "systemSeatIds": [1],
                "selectNReq": {"ids": [427], "minSel": 1, "maxSel": 1,
                               "optionContext": "OptionContext_Stack"},
            }]}
        }))
        self.assertEqual(self.c._Controller__pending_select_n["mode"], "stack")
        self.assertEqual(len(self.tasks), 1)

    def replace(self):
        self.replacement = {"token": 999, "mode": "stack", "ids": [500], "ts": 123}
        self.c._Controller__pending_select_n = self.replacement
        self.c._Controller__select_n_in_progress = True
        self.c._Controller__select_n_in_progress_since = 456

    def assert_replacement_preserved(self):
        self.assertIs(self.c._Controller__pending_select_n, self.replacement)
        self.assertEqual(self.replacement["ts"], 123)
        self.assertTrue(self.c._Controller__select_n_in_progress)
        self.assertEqual(self.c._Controller__select_n_in_progress_since, 456)

    def test_either_target_handler_cancels_ordering_and_queued_worker(self):
        for raw in (False, True):
            with self.subTest(raw=raw):
                self.ordering()
                token = self.c._Controller__pending_select_n["token"]
                self.target(raw=raw)
                self.assertIsNone(self.c._Controller__pending_select_n)
                self.assertFalse(self.c._Controller__select_n_in_progress)
                self.assertEqual(self.c._Controller__select_n_in_progress_since, 0)
                module.bot_logger.log_info.assert_any_call(
                    f"SelectN stack cancelled: token={token} target sourceId=427.")
                self.tasks.pop(0)()
                self.c.input.move_abs.assert_not_called()
                self.c.submit_selection.assert_not_called()

    def test_unrelated_non_stack_and_remote_target_requests_preserve_selection(self):
        for raw in (False, True):
            for mode, source, seat in (("stack", 428, 1), ("hand", 427, 1),
                                       ("battlefield", 427, 1), ("stack", 427, 2)):
                with self.subTest(raw=raw, mode=mode, source=source, seat=seat):
                    pending = {"mode": mode, "ids": [427], "token": 1}
                    self.c._Controller__pending_select_n = pending
                    self.c._Controller__select_n_in_progress = True
                    self.target(source, raw=raw, seat=seat)
                    self.assertIs(self.c._Controller__pending_select_n, pending)
                    self.assertTrue(self.c._Controller__select_n_in_progress)

    def test_cancelled_scan_does_not_clear_hover_or_move(self):
        self.assertFalse(self.c.select_stack_item(427, still_active=lambda: False))
        self.c.log_reader.clear_new_line_flag.assert_not_called()
        self.c.input.move_abs.assert_not_called()
        self.c._get_stack_fallback_scan_points_mapped.assert_not_called()

    def test_cancellation_during_reset_or_hover_wait_stops_scan_and_fallback(self):
        for wait, expected_moves in ((0.1, 1), (0.05, 2)):
            with self.subTest(wait=wait):
                self.ordering()
                token = self.c._Controller__pending_select_n["token"]
                self.c.input.reset_mock()
                self.c.log_reader.reset_mock()
                self.c._get_stack_fallback_scan_points_mapped.reset_mock()
                module.time.sleep.side_effect = lambda duration: (
                    self.target() if duration == wait else None)
                valid = lambda: (self.c._Controller__pending_select_n or {}).get("token") == token
                self.assertFalse(self.c.select_stack_item(427, still_active=valid))
                self.assertEqual(self.c.input.move_abs.call_count, expected_moves)
                self.assertEqual(self.c.log_reader.clear_new_line_flag.call_count, expected_moves)
                self.c.input.left_click.assert_not_called()
                self.c._get_stack_fallback_scan_points_mapped.assert_not_called()
                self.tasks.clear()

    def test_cancellation_before_click_prevents_click(self):
        self.ordering()
        token = self.c._Controller__pending_select_n["token"]
        self.c._Controller__parse_hover_id_line.side_effect = lambda line: (self.target() or 427)
        valid = lambda: (self.c._Controller__pending_select_n or {}).get("token") == token
        self.assertFalse(self.c.select_stack_item(427, still_active=valid))
        self.c.input.left_click.assert_not_called()
        self.c._get_stack_fallback_scan_points_mapped.assert_not_called()

    def test_cancellation_between_clicks_prevents_remaining_clicks(self):
        self.ordering()
        token = self.c._Controller__pending_select_n["token"]
        self.c.input.left_click.side_effect = lambda count: self.target()
        valid = lambda: (self.c._Controller__pending_select_n or {}).get("token") == token
        self.assertFalse(self.c.select_stack_item(427, clicks=2, still_active=valid))
        self.c.input.left_click.assert_called_once_with(1)
        self.c._get_stack_fallback_scan_points_mapped.assert_not_called()

    def test_cancellation_after_mapping_prevents_clearing_shared_hover(self):
        self.ordering()
        token = self.c._Controller__pending_select_n["token"]
        self.c._get_stack_scan_points_mapped.side_effect = lambda **kwargs: (
            self.target() or ((10, 100), (30, 100)))
        valid = lambda: (self.c._Controller__pending_select_n or {}).get("token") == token
        self.assertFalse(self.c.select_stack_item(427, still_active=valid))
        self.c.log_reader.clear_new_line_flag.assert_not_called()
        self.c.input.move_abs.assert_not_called()
        self.c._get_stack_fallback_scan_points_mapped.assert_not_called()

    def test_ordinary_selection_and_fallback_retain_behavior(self):
        self.assertTrue(self.c.select_stack_item(427, clicks=2))
        self.assertEqual(self.c.input.left_click.call_count, 2)
        self.c._get_stack_fallback_scan_points_mapped.assert_not_called()
        self.c.log_reader.has_new_line.side_effect = [False, False, False, True]
        self.assertTrue(self.c.select_stack_item(427))
        self.c._get_stack_fallback_scan_points_mapped.assert_called_once()

    def test_cancel_during_pre_submit_sleep_prevents_submission(self):
        self.ordering()
        self.c.select_stack_item = Mock(return_value=True)
        module.time.sleep.side_effect = lambda duration: self.target() if duration == 0.8 else None
        self.tasks.pop(0)()
        self.c.submit_selection.assert_not_called()
        self.assertIsNone(self.c._Controller__pending_select_n)
        self.assertFalse(self.tasks)
        guard = self.c.select_stack_item.call_args.kwargs["still_active"]
        self.assertFalse(guard())

    def test_replacement_during_pre_submit_sleep_is_preserved(self):
        self.ordering()
        self.c.select_stack_item = Mock(return_value=True)
        module.time.sleep.side_effect = lambda duration: self.replace() if duration == 0.8 else None
        self.tasks.pop(0)()
        self.c.submit_selection.assert_not_called()
        self.assert_replacement_preserved()
        self.assertFalse(self.tasks)

    def test_obsolete_cleanup_cannot_clear_replacement_after_failure(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                self.ordering()

                def obsolete_scan(*args, **kwargs):
                    self.replace()
                    if raises:
                        raise RuntimeError("scan failed after replacement")
                    return False

                self.c.select_stack_item = Mock(side_effect=obsolete_scan)
                self.tasks.pop(0)()
                self.assert_replacement_preserved()
                self.c.submit_selection.assert_not_called()
                self.c._Controller__pending_select_n = None

    def test_old_queued_cleanup_on_stop_preserves_replacement(self):
        self.ordering()
        self.replace()
        self.c._stop_requested = True
        self.tasks.pop(0)()
        self.assert_replacement_preserved()

    def test_submit_callback_does_not_update_replacement_timestamp(self):
        self.ordering()
        self.c.select_stack_item = Mock(return_value=True)
        self.c.submit_selection.side_effect = lambda **kwargs: self.replace() or True
        self.tasks.pop(0)()
        self.assert_replacement_preserved()
        self.assertFalse(self.tasks)

    def test_valid_worker_submits_normally(self):
        self.ordering()
        self.c.select_stack_item = Mock(return_value=True)
        self.tasks.pop(0)()
        self.c.submit_selection.assert_called_once_with(reason="select_n_initial_submit", force=True)
        self.assertTrue(self.c.select_stack_item.call_args.kwargs["still_active"]())
        self.assertEqual(len(self.tasks), 2)


if __name__ == "__main__":
    unittest.main()
