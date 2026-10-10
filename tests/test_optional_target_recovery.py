"""Replay multi-group targeting without reading the screen or touching input."""
import json
import threading
import unittest
from unittest.mock import Mock, patch

from Controller.MTGAController.Controller import Controller


def fiery_request(selected=0):
    # Shape of GRE msgId=169 in the 2026-09-30 incident: protobuf omits
    # the optional Equipment group's zero-valued minimum and selected count.
    optional = {"targetIdx": 2, "maxTargets": 1, "prompt": {"promptId": 13396},
                "targets": [{"targetInstanceId": 287, "legalAction": "SelectAction_Select"}]}
    if selected:
        optional.update(selectedTargets=selected)
        optional["targets"][0]["legalAction"] = "SelectAction_Unselect"
    return {"sourceId": 303, "targets": [
        {"targetIdx": 1, "minTargets": 1, "maxTargets": 1, "selectedTargets": 1,
         "prompt": {"promptId": 1010},
         "targets": [{"targetInstanceId": 297, "legalAction": "SelectAction_Unselect"}]},
        optional,
    ]}


class OptionalTargetRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.callbacks = []
        self.clock = 100.0
        c = self.c = Controller.__new__(Controller)
        c._Controller__system_seat_id = 2
        c._Controller__live_match_id = "match"
        c._Controller__pending_target_select = None
        c._Controller__pending_select_n = None
        c._Controller__target_select_token_counter = 0
        c._Controller__submit_selection_lock = threading.Lock()
        c._Controller__last_target_select_source_id = None
        c._Controller__last_target_select_ts = 0
        c._Controller__last_submit_selection_ts = 0
        c._suppress_selections = False
        c._stop_requested = False
        c._arena_region = (1377, 155, 1920, 1080)
        c.can_execute_game_action = lambda expected=None: (
            expected in (None, "match") and not c._stop_requested and not c._suppress_selections)
        c.updated_game_state = Mock()
        c.updated_game_state.get_game_objects.return_value = [
            {"instanceId": 303, "grpId": 93799},
            {"instanceId": 297, "controllerSeatId": 1, "cardTypes": ["CardType_Creature"]},
            {"instanceId": 287, "controllerSeatId": 1, "cardTypes": ["CardType_Artifact"]},
        ]
        c.updated_game_state.get_players.return_value = [
            {"systemSeatNumber": 1}, {"systemSeatNumber": 2}]
        c.updated_game_state.get_turn_info.return_value = {"decisionPlayer": 2}
        c._locate_image_center_in_scaled_arena_region = Mock(return_value=(3145, 1105))
        c._click_image_in_scaled_arena_region = Mock(return_value=False)
        c._locate_image_center = Mock(return_value=None)
        c._click_abs = Mock()
        c.input = Mock()
        c._buttons_dir = lambda: "Buttons"
        c._Controller__write_target_debug_bundle = Mock()
        c._Controller__record_decision = Mock()
        c._Controller__get_delay_timer_remaining = lambda: 0
        c._Controller__resolve_removal_target = lambda source: None
        c._Controller__is_self_buff_source = lambda source: False
        c._Controller__get_avatar_retry_points = lambda: [(2337, 350, "opponent")]
        c._Controller__click_opponent_avatar_at_screen = Mock()
        c.select_opponent_battlefield_permanent = Mock(return_value=True)
        c.submit_selection = Mock(return_value=True)
        c._dismiss_are_you_sure_if_present = Mock(return_value=None)
        self.addCleanup(patch.stopall)
        patch("Controller.MTGAController.Controller.threading.Timer", self.timer).start()
        patch("Controller.MTGAController.Controller.time.monotonic", lambda: self.clock).start()
        self.logs = patch("Controller.MTGAController.Controller.bot_logger.log_info").start()
        patch("Controller.MTGAController.Controller.bot_logger.log_error").start()

    def timer(self, delay, callback, args=(), kwargs=None):
        outer = self
        class Timer:
            def start(self):
                outer.callbacks.append((delay, lambda: callback(*args, **(kwargs or {}))))
        return Timer()

    def run_next(self):
        delay, callback = self.callbacks.pop(0)
        callback()
        return delay

    def remember(self, req=None):
        self.c._Controller__remember_target_request(req or fiery_request(), "AllowCancel_Abort")

    def remember_required_ready(self):
        req = fiery_request()
        req["targets"] = req["targets"][:1]
        self.remember(req)
        return req

    def test_required_ready_submit_retries_a_missed_button_without_new_request(self):
        req = self.remember_required_ready()
        self.c.submit_selection.side_effect = [False, True]
        self.c._Controller__schedule_ready_target_submit()
        self.run_next()
        self.assertEqual(self.callbacks[0][0], 1.0)
        # A duplicate ready update must not start a competing submit flow.
        self.remember(req)
        self.c._Controller__schedule_ready_target_submit()
        self.assertEqual(len(self.callbacks), 1)
        self.run_next()
        self.assertEqual(self.c.submit_selection.call_count, 2)
        self.assertEqual(self.callbacks, [])

    def test_ready_submit_lock_wait_expires_without_clicking_or_restarting(self):
        req = self.remember_required_ready()
        lock = self.c._Controller__submit_selection_lock
        lock.acquire()
        self.c._Controller__schedule_ready_target_submit()
        self.run_next()
        self.assertEqual(self.callbacks[0][0], 0.2)
        self.clock += self.c._TARGET_SUBMIT_LOCK_WAIT_SEC
        self.run_next()
        self.remember(req)
        self.c._Controller__schedule_ready_target_submit()
        self.assertEqual(self.callbacks, [])
        self.c.submit_selection.assert_not_called()
        self.assertTrue(lock.locked())
        self.c._Controller__write_target_debug_bundle.assert_called_once_with(
            "target_submit_lock_wait_timeout")
        lock.release()

    def test_ready_submit_resumes_when_lock_is_released_before_deadline(self):
        self.remember_required_ready()
        lock = self.c._Controller__submit_selection_lock
        lock.acquire()
        self.c._Controller__schedule_ready_target_submit()
        self.run_next()
        self.clock += 0.2
        lock.release()
        self.run_next()
        self.c.submit_selection.assert_called_once()
        self.assertEqual(self.callbacks, [])
        self.c._Controller__write_target_debug_bundle.assert_not_called()

    def test_zero_recovery_lock_wait_expires_without_releasing_owner_lock(self):
        self.remember()
        lock = self.c._Controller__submit_selection_lock
        lock.acquire()
        self.assertTrue(self.c._Controller__target_recovery_exhausted("test"))
        self.run_next()
        self.clock += self.c._TARGET_SUBMIT_LOCK_WAIT_SEC
        self.run_next()
        self.assertFalse(self.c._Controller__target_recovery_exhausted("repeat"))
        self.assertEqual(self.callbacks, [])
        self.c._click_abs.assert_not_called()
        self.assertTrue(lock.locked())
        self.c._Controller__write_target_debug_bundle.assert_called_once_with(
            "target_submit_lock_wait_timeout")
        lock.release()

    def test_required_ready_submit_retries_are_bounded_across_duplicate_requests(self):
        req = self.remember_required_ready()
        self.c.submit_selection.return_value = False
        self.c._Controller__schedule_ready_target_submit()
        for _ in range(3):
            self.run_next()
            self.remember(req)
            self.c._Controller__schedule_ready_target_submit()
        self.assertEqual(self.c.submit_selection.call_count, 3)
        self.assertEqual(self.callbacks, [])
        self.c._click_abs.assert_not_called()

    def test_required_ready_retry_stops_after_stop_match_or_stage_change(self):
        for change in ("stop", "match", "stage"):
            with self.subTest(change=change):
                self.callbacks.clear()
                self.c._stop_requested = False
                self.c._Controller__pending_target_select = None
                self.c.can_execute_game_action = lambda match: not self.c._stop_requested
                req = self.remember_required_ready()
                self.c.submit_selection.reset_mock()
                self.c.submit_selection.return_value = False
                self.c._Controller__schedule_ready_target_submit()
                self.run_next()
                if change == "stop":
                    self.c._stop_requested = True
                elif change == "match":
                    self.c.can_execute_game_action = lambda match: False
                else:
                    req["targets"][0]["prompt"]["promptId"] += 1
                    self.remember(req)
                self.run_next()
                self.c.submit_selection.assert_called_once()
                self.assertEqual(self.callbacks, [])

    def test_required_ready_update_can_restart_after_selection_changes(self):
        req = self.remember_required_ready()
        self.c._Controller__schedule_ready_target_submit()
        req["targets"][0]["selectedTargets"] = 0
        self.remember(req)
        self.run_next()
        self.c.submit_selection.assert_not_called()
        req["targets"][0]["selectedTargets"] = 1
        self.remember(req)
        self.c._Controller__schedule_ready_target_submit()
        self.run_next()
        self.c.submit_selection.assert_called_once()

    def test_optional_ready_submit_failure_keeps_zero_recovery(self):
        self.remember()
        self.c.submit_selection.return_value = False
        self.c._Controller__schedule_ready_target_submit()
        self.run_next()
        self.assertEqual(self.callbacks[0][0], 0.0)
        self.run_next()
        self.c._click_abs.assert_called_once_with(3145, 1105, "SUBMIT_ZERO")
        self.c.submit_selection.assert_called_once()

    def test_creature_ack_then_equipment_ack_then_submit(self):
        self.remember()
        self.assertTrue(self.c._Controller__try_handle_fiery_equipment())
        self.run_next()
        self.c.select_opponent_battlefield_permanent.assert_called_once_with(
            287, clicks=1, max_scan_sec=4.0)
        self.c.submit_selection.assert_not_called()
        token = self.c._Controller__pending_target_select["token"]
        self.remember(fiery_request(1))
        self.assertEqual(self.c._Controller__pending_target_select["token"], token)
        self.assertTrue(self.c._Controller__try_handle_fiery_equipment())
        self.run_next()
        self.c.submit_selection.assert_called_once()
        self.c._click_abs.assert_not_called()

    def test_scan_miss_submits_zero_immediately(self):
        self.remember()
        self.c.select_opponent_battlefield_permanent.return_value = False
        self.c._Controller__try_handle_fiery_equipment()
        self.run_next()
        self.assertEqual(self.callbacks[0][0], 0.0)
        self.run_next()
        self.c._click_abs.assert_called_once_with(3145, 1105, "SUBMIT_ZERO")
        self.c.submit_selection.assert_not_called()
        self.assertEqual(self.c._locate_image_center_in_scaled_arena_region.call_args.kwargs["confidence"], 0.85)

    def test_scan_click_waits_for_ack_before_zero(self):
        self.remember()
        self.c._Controller__try_handle_fiery_equipment()
        self.run_next()
        self.c._click_abs.assert_not_called()
        self.clock = 103.1
        self.run_next()
        self.run_next()
        self.c._click_abs.assert_called_once()
        self.c.select_opponent_battlefield_permanent.assert_called_once()

    def test_no_friendly_or_unknown_equipment_is_clicked(self):
        for owner in (2, None):
            with self.subTest(owner=owner):
                self.c._Controller__pending_target_select = None
                self.c.updated_game_state.get_game_objects.return_value[-1]["controllerSeatId"] = owner
                self.remember()
                self.c._Controller__try_handle_fiery_equipment()
                self.run_next()
                self.run_next()
                self.callbacks.clear()
        self.c.select_opponent_battlefield_permanent.assert_not_called()

    def test_no_equipment_offered_skips_scan(self):
        req = fiery_request()
        req["targets"][1]["targets"] = []
        self.remember(req)
        self.c._Controller__try_handle_fiery_equipment()
        self.run_next()
        self.run_next()
        self.c.select_opponent_battlefield_permanent.assert_not_called()
        self.c._click_abs.assert_called_once()

    def test_multiple_candidates_choose_lowest_id(self):
        req = fiery_request()
        req["targets"][1]["targets"].append(
            {"targetInstanceId": 285, "legalAction": "SelectAction_Select"})
        self.c.updated_game_state.get_game_objects.return_value.append(
            {"instanceId": 285, "controllerSeatId": 1})
        self.remember(req)
        self.c._Controller__try_handle_fiery_equipment()
        self.run_next()
        self.assertEqual(self.c.select_opponent_battlefield_permanent.call_args.args[0], 285)

    def test_required_targets_and_malformed_counts_block_recovery(self):
        for value in (0, None, "1", -1):
            with self.subTest(value=value):
                req = fiery_request()
                req["targets"][0]["selectedTargets"] = value
                self.remember(req)
                self.assertFalse(self.c._Controller__target_recovery_exhausted("test"))
                self.assertFalse(self.c._Controller__try_handle_fiery_equipment())
        self.assertEqual(self.callbacks, [])

    def test_all_required_groups_are_checked(self):
        req = fiery_request()
        req["targets"][1]["minTargets"] = 1
        self.remember(req)
        self.assertFalse(self.c._Controller__pending_target_ready_to_submit())
        self.assertFalse(self.c._Controller__target_recovery_exhausted("test"))

    def test_duplicate_request_starts_only_one_equipment_flow(self):
        self.remember()
        self.c._Controller__try_handle_fiery_equipment()
        self.remember()
        self.c._Controller__try_handle_fiery_equipment()
        self.assertEqual(len(self.callbacks), 1)

    def test_changed_stage_cancels_old_flow(self):
        self.remember()
        self.c._Controller__try_handle_fiery_equipment()
        req = fiery_request()
        req["targets"][1]["prompt"]["promptId"] = 123
        self.remember(req)
        self.run_next()
        self.c.select_opponent_battlefield_permanent.assert_not_called()

    def test_stop_or_match_end_cancels_equipment_callback(self):
        self.remember()
        self.c._Controller__try_handle_fiery_equipment()
        self.c._stop_requested = True
        self.run_next()
        self.c.select_opponent_battlefield_permanent.assert_not_called()

    def test_changed_request_during_recognition_prevents_click(self):
        self.remember()
        req = fiery_request()
        req["targets"][1]["prompt"]["promptId"] = 123
        def changed(*args, **kwargs):
            self.remember(req)
            return (3145, 1105)
        self.c._locate_image_center_in_scaled_arena_region.side_effect = changed
        self.c._Controller__target_recovery_exhausted("test")
        self.run_next()
        self.c._click_abs.assert_not_called()

    def test_busy_submission_defers_zero_probe(self):
        self.remember()
        self.c._Controller__submit_selection_lock.acquire()
        self.c._Controller__target_recovery_exhausted("test")
        self.run_next()
        self.c._locate_image_center_in_scaled_arena_region.assert_not_called()
        self.c._Controller__submit_selection_lock.release()
        self.run_next()
        self.c._click_abs.assert_called_once()

    def test_zero_attempt_is_bounded_and_checks_ack(self):
        self.remember()
        self.assertTrue(self.c._Controller__target_recovery_exhausted("test"))
        self.assertFalse(self.c._Controller__target_recovery_exhausted("duplicate"))
        self.run_next()
        self.assertEqual(self.callbacks[0][0], 3.0)
        self.run_next()
        self.c._Controller__write_target_debug_bundle.assert_called_once_with("submit_zero_unconfirmed")
        self.assertFalse(self.c._Controller__target_recovery_exhausted("again"))
        self.c._click_abs.assert_called_once()

    def test_acknowledged_zero_has_no_failure_bundle(self):
        self.remember()
        self.c._Controller__target_recovery_exhausted("test")
        self.run_next()
        self.c._Controller__pending_target_select = None
        self.run_next()
        self.c._Controller__write_target_debug_bundle.assert_not_called()

    def test_absent_button_uses_no_okay_or_coordinate_fallback(self):
        self.remember()
        self.c._locate_image_center_in_scaled_arena_region.return_value = None
        self.c._Controller__target_recovery_exhausted("test")
        self.run_next()
        self.c._click_abs.assert_not_called()
        self.c._locate_image_center.assert_not_called()
        self.c.input.left_click.assert_not_called()

    def test_opponent_priority_does_not_receive_zero_click(self):
        self.remember()
        self.c.updated_game_state.get_turn_info.return_value = {"decisionPlayer": 1}
        self.c._Controller__target_recovery_exhausted("test")
        self.run_next()
        self.c._click_abs.assert_not_called()

    def test_normal_submit_failure_hands_off_without_watchdog_wait(self):
        self.remember()
        token = self.c._Controller__pending_target_select["token"]
        self.c._Controller__watch_target_submission(token, False, "normal")
        self.assertEqual(self.run_next(), 0)
        self.assertEqual(self.run_next(), 0)
        self.c._click_abs.assert_called_once()

    def test_normal_submit_waits_three_seconds_for_ack(self):
        self.remember()
        token = self.c._Controller__pending_target_select["token"]
        self.c._Controller__watch_target_submission(token, True, "normal")
        self.assertEqual(self.callbacks[0][0], 3.0)
        self.c._Controller__pending_target_select = None
        self.run_next()
        self.assertEqual(self.callbacks, [])
        self.c._click_abs.assert_not_called()

    def test_failed_normal_submit_calls_recovery_after_releasing_lock(self):
        self.remember()
        self.c.submit_selection = Controller.submit_selection.__get__(self.c)
        self.c._locate_image_center_in_scaled_arena_region.return_value = None
        self.c._locate_image_center.return_value = None
        self.assertFalse(self.c.submit_selection(reason="target_selection_ready"))
        self.assertFalse(self.c._Controller__submit_selection_lock.locked())
        self.assertEqual(self.callbacks[0][0], 0.0)
        self.run_next()
        self.c._locate_image_center_in_scaled_arena_region.return_value = (3145, 1105)
        self.run_next()
        self.c._click_abs.assert_called_once_with(3145, 1105, "SUBMIT_ZERO")

    def test_equipment_scan_exception_uses_zero_fallback(self):
        self.remember()
        self.c.select_opponent_battlefield_permanent.side_effect = RuntimeError("scan unavailable")
        self.c._Controller__try_handle_fiery_equipment()
        self.run_next()
        self.run_next()
        self.c._click_abs.assert_called_once_with(3145, 1105, "SUBMIT_ZERO")

    def test_unknown_or_missing_request_does_not_authorize_recovery(self):
        self.c._Controller__update_pending_target_select(303, min_t=0, selected=0)
        self.assertFalse(self.c._Controller__target_recovery_exhausted("missing_request"))
        self.assertEqual(self.callbacks, [])

    def test_required_creature_selection_keeps_existing_retry_flow(self):
        req = fiery_request()
        req["targets"][0].pop("selectedTargets")
        req["targets"][0]["targets"][0]["legalAction"] = "SelectAction_Select"
        self.remember(req)
        self.c._Controller__note_ward_payment_ack = Mock()
        self.c._Controller__schedule_creature_target_selection(303, 297, "test")
        self.run_next()
        self.c.select_opponent_battlefield_permanent.assert_called_once_with(297, clicks=1)
        self.run_next()
        self.assertEqual(self.callbacks[0][0], 0.5)
        self.c._click_abs.assert_not_called()
        self.c._locate_image_center_in_scaled_arena_region.assert_not_called()

    def test_avatar_retries_finish_before_recovery_handoff(self):
        req = {"sourceId": 44, "targets": [{"minTargets": 1, "maxTargets": 1,
               "targets": [{"targetInstanceId": 1, "legalAction": "SelectAction_Select"}]}]}
        self.remember(req)
        handoff = self.c._Controller__target_recovery_exhausted = Mock(return_value=False)
        self.c._Controller__schedule_target_selection(
            44, "test", legal_creature_ids=[], own_creature_ids=[], face_legal=True)
        self.run_next()  # Initial avatar click queues normal submit check and retry.
        handoff.assert_not_called()
        self.run_next()  # The acknowledgement/submit check does not give up early.
        handoff.assert_not_called()
        self.run_next()  # One-point fan exhausted.
        handoff.assert_called_once_with("avatar_points_exhausted")

    def test_submit_zero_failure_preserves_existing_cancellation(self):
        self.remember()
        self.c._Controller__pending_target_select["cancel_after_zero_failure"] = True
        self.c._locate_image_center_in_scaled_arena_region.return_value = None
        self.c._Controller__target_recovery_exhausted("creature_ack_timeout")
        self.c.input.tap_escape.assert_not_called()
        self.run_next()
        self.c.input.tap_escape.assert_called_once()

    def test_other_spell_does_not_start_equipment_policy(self):
        self.remember()
        self.c.updated_game_state.get_game_objects.return_value[0]["grpId"] = 999
        with patch("Controller.MTGAController.Controller.CardInfo.get_card_info_local", return_value=None):
            self.assertFalse(self.c._Controller__try_handle_fiery_equipment())
        self.assertEqual(self.callbacks, [])

    def test_stage_ends_before_zero_result_check_is_not_a_failure(self):
        self.remember()
        self.c._Controller__target_recovery_exhausted("test")
        self.run_next()
        self.c._stop_requested = True
        self.run_next()
        self.c._Controller__write_target_debug_bundle.assert_not_called()

    def test_equipment_ack_during_zero_recognition_prevents_zero_click(self):
        self.remember()
        def acknowledged(*args, **kwargs):
            self.remember(fiery_request(1))
            return (3145, 1105)
        self.c._locate_image_center_in_scaled_arena_region.side_effect = acknowledged
        self.c._Controller__target_recovery_exhausted("test")
        self.run_next()
        self.c._click_abs.assert_not_called()

    def test_both_entrypoints_handle_equipment_before_generic_targeting(self):
        payload = {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_SelectTargetsReq", "systemSeatIds": [2],
            "selectTargetsReq": fiery_request(), "allowCancel": "AllowCancel_Abort"}]}}
        for handler in (lambda: self.c._Controller__handle_select_targets_req(json.dumps(payload)),
                        lambda: self.c._Controller__handle_target_selection_from_raw_dict(payload)):
            with self.subTest(handler=handler):
                self.c._Controller__pending_target_select = None
                self.callbacks.clear()
                handler()
                self.assertEqual(len(self.callbacks), 1)
                self.c._Controller__click_opponent_avatar_at_screen.assert_not_called()

    def test_own_and_opponent_player_targets_remain_available(self):
        for seat in (1, 2):
            with self.subTest(seat=seat):
                self.callbacks.clear()
                self.c._Controller__pending_target_select = None
                self.c._click_abs.reset_mock()
                self.c._Controller__click_opponent_avatar_at_screen.reset_mock()
                req = {"sourceId": 44, "targets": [{"minTargets": 1, "maxTargets": 1,
                       "targets": [{"targetInstanceId": seat, "legalAction": "SelectAction_Select"}]}]}
                self.remember(req)
                enemy, own, face = self.c._Controller__analyze_legal_targets(req)
                self.c._Controller__schedule_target_selection(
                    44, "test", legal_creature_ids=enemy, own_creature_ids=own, face_legal=face)
                self.run_next()
                if seat == 2:
                    self.c._click_abs.assert_called_once()
                    self.assertEqual(self.c._click_abs.call_args.args[-1], "SELECT_OWN_AVATAR")
                    self.c._Controller__click_opponent_avatar_at_screen.assert_not_called()
                else:
                    self.c._Controller__click_opponent_avatar_at_screen.assert_called_once()
                    self.c._click_abs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
