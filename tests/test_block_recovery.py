"""Insufficient-blocker recovery, driven by fresh Arena requests; no screen I/O."""
import copy
import json
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

import cv2

from Controller.Utilities.input_controller import ExclusiveInputController
from tests.test_combat_shadow import make_controller, cleanup, seed_board, creature, MY_SEAT, OPP_SEAT


class BlockRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        self.addCleanup(cleanup, self.c)
        self.c._suppress_selections = False
        self.c.input = mock.MagicMock()
        seed_board(self.c, [creature(305, OPP_SEAT, 3, 2, attacking_seat=MY_SEAT),
                            creature(288, MY_SEAT, 2, 2),
                            creature(280, MY_SEAT, 1, 1), creature(310, MY_SEAT, 2, 3)])
        for name in ("_locate_image_center_in_scaled_arena_region",
                     "_click_image_in_scaled_arena_region", "select_battlefield_permanent",
                     "select_attacking_creature", "_Controller__click_combat_submit_button",
                     "_Controller__record_decision", "_write_declare_block_debug_bundle"):
            patcher = mock.patch.object(self.c, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.c.select_battlefield_permanent.return_value = True
        self.request = {
            "blockers": [
                {"blockerInstanceId": 280, "attackerInstanceIds": [305], "maxAttackers": 1},
                {"blockerInstanceId": 288, "selectedAttackerInstanceIds": [305], "maxAttackers": 1},
                {"blockerInstanceId": 310, "attackerInstanceIds": [305], "maxAttackers": 1}],
            "hasRestrictions": True,
            "blockWarnings": [{"instanceId": 305,
                               "type": "BlockWarningType_InsufficientBlockers", "warningPromptId": 117}],
        }
        self.msg_id = 235
        self.publish()

    def publish(self, request=None, msg_id=None):
        self.msg_id += 1
        self.c._observe_block_request({
            "type": "GREMessageType_DeclareBlockersReq", "systemSeatIds": [MY_SEAT],
            "msgId": self.msg_id if msg_id is None else msg_id,
            "declareBlockersReq": copy.deepcopy(self.request if request is None else request)})

    def assign(self, blocker, attacker=305, *, clear=True):
        for entry in self.request["blockers"]:
            if entry["blockerInstanceId"] == blocker:
                entry["selectedAttackerInstanceIds"] = [attacker]
        if clear:
            self.request["blockWarnings"] = []
        self.publish()
        return True

    def cancel(self, *args, **kwargs):
        for entry in self.request["blockers"]:
            entry.pop("selectedAttackerInstanceIds", None)
        self.request["blockWarnings"] = []
        self.publish()
        return True

    def finish(self):
        self.c._finish_block_assignment("SUBMIT_BLOCKS")

    def test_selection_wrapper_passes_guard_into_physical_scan(self):
        from Controller.MTGAController.Controller import Controller
        guard = lambda: False
        scanner = mock.Mock(return_value=False)
        with mock.patch.object(self.c, "_get_battlefield_scan_points_mapped", return_value=((0, 0), (1, 1))), \
                mock.patch.object(self.c, "_Controller__select_object_in_region", scanner):
            self.assertFalse(Controller.select_battlefield_permanent(self.c, 280, still_active=guard))
        self.assertIs(scanner.call_args.kwargs["still_active"], guard)
        self.c._write_hand_select_debug_bundle = mock.Mock()
        with mock.patch.object(self.c, "_get_opponent_battlefield_scan_points_mapped", return_value=((0, 0), (1, 1))), \
                mock.patch.object(self.c, "_Controller__select_object_in_region", scanner):
            self.assertFalse(Controller.select_opponent_battlefield_permanent(self.c, 305, still_active=guard))
        self.assertIs(scanner.call_args.kwargs["still_active"], guard)

    def test_incident_adds_hawk_instead_of_more_expensive_nighthawk(self):
        self.c.select_attacking_creature.side_effect = lambda *a, **k: self.assign(280)
        self.finish()
        self.assertEqual(self.c.select_battlefield_permanent.call_args.args, (280,))
        self.assertEqual(self.c.select_attacking_creature.call_args.args, (305,))
        self.c._Controller__click_combat_submit_button.assert_called_once_with("SUBMIT_BLOCKS")
        self.c._click_image_in_scaled_arena_region.assert_not_called()

    def test_adds_further_blocker_if_warning_persists(self):
        choices = iter([280, 310])
        def click(*args, **kwargs):
            blocker = next(choices)
            return self.assign(blocker, clear=blocker == 310)
        self.c.select_attacking_creature.side_effect = click
        self.finish()
        self.assertEqual([c.args[0] for c in self.c.select_battlefield_permanent.call_args_list], [280, 310])
        self.c._Controller__click_combat_submit_button.assert_called_once_with("SUBMIT_BLOCKS")

    def test_warning_cleared_while_scanning_does_not_cancel_valid_blocks(self):
        def scan(*args, **kwargs):
            self.assign(310)
            self.assertFalse(kwargs["still_active"]())
            return False
        self.c.select_battlefield_permanent.side_effect = scan
        self.finish()
        self.c.select_attacking_creature.assert_not_called()
        self.c.input.tap_escape.assert_not_called()
        self.c._click_image_in_scaled_arena_region.assert_not_called()
        self.c._Controller__click_combat_submit_button.assert_called_once_with("SUBMIT_BLOCKS")

    def test_already_accepted_extra_blocker_is_never_selected_again(self):
        def scan(*args, **kwargs):
            self.assign(280)
            self.assertFalse(kwargs["still_active"]())
            return True
        self.c.select_battlefield_permanent.side_effect = scan
        self.finish()
        self.c.select_attacking_creature.assert_not_called()
        self.c._click_image_in_scaled_arena_region.assert_not_called()
        self.c._Controller__click_combat_submit_button.assert_called_once_with("SUBMIT_BLOCKS")

    def test_warning_cleared_during_attacker_scan_is_not_a_selection_failure(self):
        def scan(*args, **kwargs):
            self.assign(310)
            self.assertFalse(kwargs["still_active"]())
            return False
        self.c.select_attacking_creature.side_effect = scan
        self.finish()
        self.c.input.tap_escape.assert_not_called()
        self.c._click_image_in_scaled_arena_region.assert_not_called()
        self.c._Controller__click_combat_submit_button.assert_called_once_with("SUBMIT_BLOCKS")

    def test_late_acceptance_before_cancel_prevents_cancel_click(self):
        self.c._block_reserved = {280, 310}
        def locate(*args, **kwargs):
            self.assign(280)
            self.assertFalse(kwargs["action_guard"]())
            return False
        self.c._click_image_in_scaled_arena_region.side_effect = locate
        self.finish()
        self.assertFalse(self.c._block_failed)
        self.c._Controller__click_combat_submit_button.assert_called_once_with("SUBMIT_BLOCKS")

    def test_accepted_blocks_before_recovery_starts_need_no_repair(self):
        self.assign(280)
        self.finish()
        self.c.select_battlefield_permanent.assert_not_called()
        self.c._click_image_in_scaled_arena_region.assert_not_called()
        self.c._Controller__click_combat_submit_button.assert_called_once_with("SUBMIT_BLOCKS")

    def test_reserved_and_assigned_creatures_are_not_reused(self):
        self.c._block_reserved = {280}
        self.c.select_attacking_creature.side_effect = lambda *a, **k: self.assign(310)
        self.finish()
        self.assertEqual(self.c.select_battlefield_permanent.call_args.args, (310,))

    def test_no_available_blocker_cancels_every_assignment_then_submits_no_blocks(self):
        self.c._block_reserved = {280, 310}
        self.c._click_image_in_scaled_arena_region.side_effect = self.cancel
        self.finish()
        self.c.select_battlefield_permanent.assert_not_called()
        self.c._click_image_in_scaled_arena_region.assert_called_once()
        self.assertFalse(self.c._block_selected(self.c._block_request))
        self.c._Controller__click_combat_submit_button.assert_called_once_with("NO_BLOCKS")

    def test_failed_blocker_or_attacker_selection_cancels(self):
        for failed in ("select_battlefield_permanent", "select_attacking_creature"):
            with self.subTest(failed=failed):
                self.publish(self.request)
                self.c.select_battlefield_permanent.return_value = True
                self.c.select_attacking_creature.return_value = True
                getattr(self.c, failed).return_value = False
                self.c._click_image_in_scaled_arena_region.side_effect = self.cancel
                self.finish()
                self.c._Controller__click_combat_submit_button.assert_called_with("NO_BLOCKS")
                # Restore the rejected request for the next iteration.
                self.request["blockers"][1]["selectedAttackerInstanceIds"] = [305]
                self.request["blockWarnings"] = [{"instanceId": 305,
                                                  "type": "BlockWarningType_InsufficientBlockers"}]

    def test_assignment_must_be_confirmed_before_submission(self):
        self.c.select_attacking_creature.return_value = True
        with mock.patch.object(self.c, "_wait_block_request", return_value=False):
            self.finish()
        self.c._Controller__click_combat_submit_button.assert_not_called()
        self.assertTrue(self.c._block_failed)
        self.c._write_declare_block_debug_bundle.assert_called_once()

    def test_expired_budget_cancels_without_searching(self):
        self.c._click_image_in_scaled_arena_region.side_effect = self.cancel
        self.assertEqual(self.c._repair_blocks(self.c._block_context, time.monotonic() - 1), "NO_BLOCKS")
        self.c.select_battlefield_permanent.assert_not_called()

    def test_cancellation_requires_fresh_empty_request(self):
        self.c._block_reserved = {280, 310}
        self.c._click_image_in_scaled_arena_region.return_value = True
        with mock.patch.object(self.c, "_wait_block_request", return_value=False):
            self.finish()
        self.assertTrue(self.c._block_failed)
        self.assertTrue(self.c._Controller__should_pause_for_declare_blocks())
        self.c._Controller__click_combat_submit_button.assert_not_called()

    def test_missing_assignment_ack_cancels_and_submits_after_cancellation_ack(self):
        self.c.select_attacking_creature.return_value = True
        self.c._click_image_in_scaled_arena_region.side_effect = self.cancel
        real_wait = self.c._wait_block_request
        calls = 0
        def wait(*args):
            nonlocal calls
            calls += 1
            return False if calls == 1 else real_wait(*args)
        with mock.patch.object(self.c, "_wait_block_request", side_effect=wait):
            self.finish()
        self.c._Controller__click_combat_submit_button.assert_called_once_with("NO_BLOCKS")

    def test_no_unused_blocker_for_warned_attacker_does_not_use_another_pairing(self):
        for entry in self.request["blockers"]:
            if entry["blockerInstanceId"] != 288:
                entry["attackerInstanceIds"] = [999]
        self.publish()
        self.c._click_image_in_scaled_arena_region.side_effect = self.cancel
        self.finish()
        self.c.select_battlefield_permanent.assert_not_called()
        self.c._Controller__click_combat_submit_button.assert_called_once_with("NO_BLOCKS")

    def test_reset_invalidates_recovery_even_before_turn_state_changes(self):
        context = self.c._block_context
        self.c._reset_block_recovery()
        self.c._finish_block_assignment("SUBMIT_BLOCKS", context)
        self.assertFalse(self.c._block_live(context))
        self.assertFalse(self.c._block_request)
        self.c._Controller__click_combat_submit_button.assert_not_called()

    def test_duplicate_or_older_request_cannot_acknowledge_a_click(self):
        revision = self.c._block_revision
        self.publish()
        self.assertEqual(self.c._block_revision, revision)
        old = copy.deepcopy(self.request)
        old["blockWarnings"] = []
        self.publish(old, msg_id=200)
        self.assertTrue(self.c._block_warnings(self.c._block_request))
        self.assertFalse(self.c._wait_block_request(revision, lambda req: True,
                                                   self.c._block_context, 0))

    def test_warning_bypasses_throttle_and_running_assignment_guard(self):
        self.c._Controller__last_declare_blockers_ts = time.time()
        self.c._Controller__declaring_blocks_until = time.time() + 20
        self.c.decision_hooks = mock.Mock()
        with mock.patch.object(self.c, "_input_thread") as thread:
            payload = {"greToClientEvent": {"greToClientMessages": [{
                "type": "GREMessageType_DeclareBlockersReq", "systemSeatIds": [MY_SEAT],
                "msgId": 999, "declareBlockersReq": self.request}]}}
            self.c._Controller__handle_declare_blockers_req(json.dumps(payload))
            self.c._Controller__handle_declare_blockers_req(json.dumps(payload))
        thread.assert_called_once()
        self.c.decision_hooks.review_blocks.assert_not_called()

    def test_stop_match_change_and_combat_exit_do_not_click(self):
        context = self.c._block_context
        for change in ("stop", "match", "step"):
            with self.subTest(change=change):
                self.c._stop_requested = change == "stop"
                self.c._Controller__live_match_id = "other" if change == "match" else context[0]
                self.c.updated_game_state.game_dict["turnInfo"]["step"] = (
                    "Step_CombatDamage" if change == "step" else "Step_DeclareBlock")
                self.c._finish_block_assignment("SUBMIT_BLOCKS", context)
        self.c.select_battlefield_permanent.assert_not_called()
        self.c._click_image_in_scaled_arena_region.assert_not_called()
        self.c._Controller__click_combat_submit_button.assert_not_called()

    def test_scheduled_old_assignment_does_not_run_after_recovery_invalidates_it(self):
        context, plan_id = self.c._block_context, self.c._block_plan_id
        with mock.patch.object(self.c, "_input_thread"):
            self.c._schedule_block_recovery()
        self.c._Controller__execute_blocks([(288, 305)], context=context, plan_id=plan_id)
        self.c.select_battlefield_permanent.assert_not_called()

    def test_multiple_warning_attackers_keep_existing_blocks(self):
        self.request["blockers"].append({"blockerInstanceId": 311, "attackerInstanceIds": [306]})
        self.request["blockWarnings"].append({"instanceId": 306,
                                               "type": "BlockWarningType_InsufficientBlockers"})
        self.c.updated_game_state.game_dict["gameObjects"].extend([
            creature(306, OPP_SEAT), creature(311, MY_SEAT)])
        self.c.updated_game_state.game_dict["zones"][0]["objectInstanceIds"].extend([306, 311])
        self.publish()
        def click(attacker, **kwargs):
            blocker = 280 if attacker == 305 else 311
            self.assign(blocker, attacker, clear=False)
            self.request["blockWarnings"] = [w for w in self.request["blockWarnings"]
                                             if w["instanceId"] != attacker]
            self.publish()
            return True
        self.c.select_attacking_creature.side_effect = click
        self.finish()
        self.assertEqual(self.c._block_selected(self.c._block_request), {(288, 305), (280, 305), (311, 306)})

    def test_warning_during_submission_aborts_retries_even_if_later_cleared(self):
        self.request["blockWarnings"] = []
        self.publish()
        def marker(*args, **kwargs):
            # Recovery cancelled the assignment while the original submission waited.
            self.c._block_plan_id += 1
            return False
        self.c._wait_for_playerlog_marker = mock.Mock(side_effect=marker)
        with mock.patch("Controller.MTGAController.Controller.time.sleep"):
            result = self.c._Controller__press_combat_button_verified(
                (100, 100), "SUBMIT_BLOCKS", declared_markers=["BlockState_Declared"],
                submitted_markers=["SubmitBlockersReq"])
        self.assertFalse(result)
        self.c.input.left_click.assert_called_once()

    def test_worker_serializes_with_existing_assignment_and_runs_once(self):
        entered = threading.Event()
        workers = []
        def make_worker(**kwargs):
            worker = threading.Thread(**kwargs)
            workers.append(worker)
            return worker
        with mock.patch.object(self.c, "_input_thread", side_effect=make_worker), \
                mock.patch.object(self.c, "_finish_block_assignment", side_effect=lambda *a: entered.set()):
            with self.c._block_execution_lock:
                self.c._schedule_block_recovery()
                self.c._schedule_block_recovery()
                self.assertFalse(entered.wait(0.02))
            self.assertTrue(entered.wait(1))
            workers[0].join(1)
            self.assertFalse(workers[0].is_alive())
            self.assertEqual(len(workers), 1)

    def test_reset_releases_recovery_when_revoked_worker_never_starts(self):
        self.c.input = ExclusiveInputController(mock.Mock())
        # Construct the real lease-wrapped worker, but defer its execution.
        workers = []
        real_thread = threading.Thread
        def defer(**kwargs):
            worker = real_thread(**kwargs)
            worker.start = mock.Mock()
            workers.append(worker)
            return worker
        with mock.patch("Controller.MTGAController.Controller.threading.Thread", side_effect=defer):
            self.c._schedule_block_recovery()
        self.c._retire_input_workers()
        self.c._reset_block_recovery()
        workers[0].run()
        self.assertFalse(self.c._block_recovery_scheduled)
        self.assertIsNone(self.c._block_recovery_owner)
        self.publish()
        with mock.patch.object(self.c, "_input_thread") as replacement:
            self.c._schedule_block_recovery()
        replacement.assert_called_once()

    def test_retired_worker_cleanup_preserves_new_recovery_and_pause(self):
        callbacks = []
        def defer(**kwargs):
            callbacks.append(kwargs["target"])
            return mock.Mock()
        def replace(*args):
            self.c._reset_block_recovery()
            self.publish()
            self.c._schedule_block_recovery()
        with mock.patch.object(self.c, "_input_thread", side_effect=defer), \
                mock.patch.object(self.c, "_finish_block_assignment", side_effect=replace), \
                mock.patch.object(self.c, "_Controller__end_declare_blocks_pause") as end_pause:
            self.c._schedule_block_recovery()
            callbacks[0]()
            self.assertEqual(len(callbacks), 2)
            self.assertTrue(self.c._block_recovery_scheduled)
            self.assertIsNotNone(self.c._block_recovery_owner)
            end_pause.assert_not_called()
            with mock.patch.object(self.c, "_finish_block_assignment") as finish:
                callbacks[1]()
            finish.assert_called_once()
            self.assertFalse(self.c._block_recovery_scheduled)
            self.assertIsNone(self.c._block_recovery_owner)
            end_pause.assert_called_once()


class CancelBlocksTemplateTest(unittest.TestCase):
    def test_saved_incident_button_matches_in_scaled_arena_region(self):
        root = Path(__file__).resolve().parents[1]
        image = cv2.imread(str(root / "tests/fixtures/insufficient_blockers.png"))
        template = cv2.imread(str(root / "Buttons/cancel_blocks.png"))
        region = cv2.resize(image, (420, 150), interpolation=cv2.INTER_AREA)
        _, confidence, _, location = cv2.minMaxLoc(cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED))
        self.assertGreater(confidence, 0.55)
        # Its matched center lies on Cancel Blocks, above the submit control.
        self.assertEqual(location, (174, 84))


if __name__ == "__main__":
    unittest.main()
