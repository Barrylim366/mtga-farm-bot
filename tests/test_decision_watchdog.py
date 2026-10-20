import copy
import threading
import tempfile
import json
import unittest
from unittest.mock import Mock, patch

from decision_watchdog import DecisionWatchdog, REQUESTS
from Controller.Utilities.input_controller import ExclusiveInputController, NullInputController
from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState
from state.state_machine import BotState


class DecisionWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.w = DecisionWatchdog(lambda: self.now)
        self.state = {
            "gameStateId": 20,
            "turnInfo": {"turnNumber": 12, "phase": "Phase_Combat", "step": "Step_DeclareBlock",
                         "decisionPlayer": 1, "priorityPlayer": 2},
            "players": [{"systemSeatNumber": 1, "timerIds": [4]}],
            "gameObjects": [], "zones": [],
        }

    def request(self, short="DeclareBlockersReq", req=None, seat=1, msg_id=1):
        message = {"type": "GREMessageType_" + short, "systemSeatIds": [seat],
                   "msgId": msg_id, "gameStateId": 20,
                   short[0].lower() + short[1:]: req or {}}
        self.w.observe_requests({"greToClientEvent": {"greToClientMessages": [message]}},
                                "match", 1, self.state["turnInfo"])
        return self.w.observe_state("match", 1, self.state)

    def test_menace_one_blocker_then_rejected_submissions_do_not_earn_time(self):
        self.request()
        self.now = 5
        self.state["gameObjects"] = [{"instanceId": 336, "controllerSeatId": 1,
                                       "blockInfo": {"attackerIds": [327]}}]
        self.w.observe_requests({"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_GameStateMessage", "gameStateMessage": {
                "gameObjects": self.state["gameObjects"]}}]}}, "match", 1, self.state["turnInfo"])
        self.request(msg_id=2)
        self.assertEqual(self.w.pending.last_progress_at, 5)
        for t in (10, 20, 30, 35):
            self.now = t
            self.request(msg_id=t)
        self.assertEqual(self.w.describe()["age_sec"], 30)
        self.assertEqual(self.w.pending.kind, "blockers")

    def test_each_explicit_decision_overrides_stale_opponent_priority(self):
        for short in (*REQUESTS, "OptionalActionReq"):
            with self.subTest(short=short):
                self.w = DecisionWatchdog(lambda: self.now)
                self.request(short)
                self.assertIsNotNone(self.w.pending)
                self.assertTrue(self.w.pending.explicit)

    def test_simultaneous_opponent_request_cannot_hide_our_unresolved_choice(self):
        messages = [{"type": "GREMessageType_MulliganReq", "systemSeatIds": [seat],
                     "mulliganReq": {}} for seat in (1, 2)]
        for order in (messages, list(reversed(messages))):
            with self.subTest(order=order):
                self.w.reset("new game")
                self.w.observe_requests({"greToClientEvent": {"greToClientMessages": order}},
                                        "match", 1, self.state["turnInfo"])
                self.assertIsNotNone(self.w.observe_state("match", 1, self.state))
                self.assertEqual(self.w.pending.kind, "mulligan")

    def test_real_partial_targets_advance_high_water_not_retries_or_oscillation(self):
        req = {"sourceId": 99, "targets": [{"selectedTargets": 0, "minTargets": 2}]}
        self.request("SelectTargetsReq", req)
        self.now = 10
        req["targets"][0]["selectedTargets"] = 1
        self.request("SelectTargetsReq", req)
        for t, count in ((15, 0), (20, 1), (25, 0), (30, 1)):
            self.now = t
            req["targets"][0]["selectedTargets"] = count
            self.request("SelectTargetsReq", req, msg_id=t)
        self.assertEqual(self.w.pending.last_progress_at, 10)
        self.now = 35
        req["targets"][0]["selectedTargets"] = 2
        self.request("SelectTargetsReq", req)
        self.assertEqual(self.w.pending.last_progress_at, 35)

    def test_client_response_and_unrelated_churn_do_not_complete_a_request(self):
        baseline = self.request("SelectTargetsReq", {"sourceId": 99})
        self.now = 15
        self.w.observe_requests({"clientToGreEvent": {"clientToGreMessages": [
            {"type": "ClientMessageType_SelectTargetsResp"}]}}, "match", 1, self.state["turnInfo"])
        self.state.update(gameStateId=99, actions=[{"newAvailableAction": 3}],
                          timers=[{"timerId": 4, "elapsedMs": 12345}])
        self.state["gameObjects"] = [{"instanceId": 5, "controllerSeatId": 2, "damage": 1}]
        self.assertEqual(self.w.observe_state("match", 1, self.state), baseline)

    def test_opponent_wait_is_not_local_but_running_local_decision_timer_is(self):
        self.assertIsNone(self.w.observe_state("match", 1, self.state))
        self.state["timers"] = [{"timerId": 4, "running": True, "type": "TimerType_Inactivity"}]
        self.assertIsNone(self.w.observe_state("match", 1, self.state))
        self.state["timers"][0]["type"] = "TimerType_NonActivePlayer"
        self.assertIsNotNone(self.w.observe_state("match", 1, self.state))

    def test_local_timer_message_establishes_ownership_when_ids_are_missing(self):
        self.w.observe_requests({"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_TimerStateMessage", "timerStateMessage": {
                "seatId": 1, "timers": [{"type": "TimerType_NonActivePlayer", "running": True}]}
        }]}}, "match", 1, self.state["turnInfo"])
        self.assertIsNotNone(self.w.observe_state("match", 1, self.state))

    def test_fresh_opponent_decision_overrides_cached_running_local_timer(self):
        self.request()
        self.state["timers"] = [{"timerId": 4, "type": "TimerType_NonActivePlayer", "running": True}]
        self.state["turnInfo"]["decisionPlayer"] = 2
        self.w.observe_requests({"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_GameStateMessage", "gameStateMessage": {
                "turnInfo": {"decisionPlayer": 2}}}]}}, "match", 1, self.state["turnInfo"])
        self.assertIsNone(self.w.observe_state("match", 1, self.state))

    def test_cached_combat_assignments_do_not_extend_a_new_decision(self):
        self.state["gameObjects"] = [{"instanceId": 336, "controllerSeatId": 1,
                                       "blockInfo": {"attackerIds": [327]}}]
        baseline = self.request()
        self.now = 20
        self.assertEqual(self.w.observe_state("match", 1, self.state), baseline)
        self.assertEqual(self.w.pending.last_progress_at, 0)

    def test_new_game_reset_allows_state_ids_to_restart_in_the_same_match(self):
        self.request()
        self.w.reset("new game / reset")
        self.state["gameStateId"] = 1
        self.state["turnInfo"].update(decisionPlayer=1, priorityPlayer=1)
        self.assertIsNotNone(self.w.observe_state("match", 1, self.state))

    def test_actions_available_request_counts_as_normal_play_and_accepts_progress(self):
        self.request("ActionsAvailableReq")
        self.assertEqual(self.w.pending.kind, "priority")
        self.assertFalse(self.w.pending.explicit)
        self.now = 20
        self.state["zones"] = [{"zoneId": 10, "type": "ZoneType_Hand", "ownerSeatId": 1,
                                 "objectInstanceIds": [3]}]
        self.w.observe_state("match", 1, self.state)
        self.assertEqual(self.w.pending.last_progress_at, 20)

    def test_silence_preserves_deadline_and_failed_cycle_restarts_it(self):
        self.request()
        self.now = 30
        self.assertEqual(self.w.describe()["age_sec"], 30)
        self.now = 45
        self.w.retry()
        self.assertEqual(self.w.pending.last_progress_at, 45)
        self.now = 75
        self.assertEqual(self.w.describe()["age_sec"], 30)

    def test_server_response_transition_and_opponent_request_retire_old_prompt(self):
        self.state["players"][0]["pendingMessageType"] = "ClientMessageType_DeclareBlockersResp"
        self.request()
        self.state["players"][0]["pendingMessageType"] = "ClientMessageType_SelectTargetsResp"
        self.assertIsNone(self.w.observe_state("match", 1, self.state))
        self.request()
        self.request(seat=2)
        self.assertIsNone(self.w.pending)

    def test_match_change_game_over_and_turn_advance_cancel_old_request(self):
        self.request()
        self.assertIsNone(self.w.observe_state("next", 1, self.state))
        self.request()
        self.state["gameInfo"] = {"stage": "GameStage_GameOver"}
        self.assertIsNone(self.w.observe_state("match", 1, self.state))
        self.state.pop("gameInfo")
        self.request()
        self.state["turnInfo"]["step"] = "Step_CombatDamage"
        self.assertIsNone(self.w.observe_state("match", 1, self.state))


class RevocableInputTest(unittest.TestCase):
    def setUp(self):
        self.raw = NullInputController()
        self.raw.left_click = Mock()
        self.gate = ExclusiveInputController(self.raw)

    def test_delayed_worker_cannot_click_after_takeover_is_released(self):
        old = self.gate.worker_target(self.gate.left_click)
        self.assertTrue(self.gate.take_over_for_current_thread(timeout=.05))
        self.gate.release_exclusive()
        old()
        self.raw.left_click.assert_not_called()
        self.gate.worker_target(self.gate.left_click)()
        self.raw.left_click.assert_called_once()

    def test_old_transaction_finally_cannot_release_new_ownership(self):
        entered, finish = threading.Event(), threading.Event()
        def worker():
            with self.gate.input_transaction() as acquired:
                self.assertTrue(acquired)
                entered.set()
                finish.wait(1)
                self.gate.left_click()
        thread = threading.Thread(target=self.gate.worker_target(worker))
        thread.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.gate.take_over_for_current_thread(timeout=.05))
        finish.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.gate._exclusive)
        self.assertEqual(self.gate._transaction_depth, 0)
        self.raw.left_click.assert_not_called()

    def test_takeover_is_bounded_when_backend_is_actually_hung(self):
        entered, finish = threading.Event(), threading.Event()
        def click(*_):
            entered.set()
            finish.wait(1)
        self.raw.left_click.side_effect = click
        thread = threading.Thread(target=self.gate.worker_target(self.gate.left_click))
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertFalse(self.gate.take_over_for_current_thread(timeout=.02))
            self.assertFalse(self.gate._lease_valid())
        finally:
            finish.set()
            thread.join(1)

    def test_revoked_worker_cannot_schedule_new_authorized_children(self):
        children = []
        def worker():
            self.gate.revoke_workers()
            self.gate.resume_input()
            children.append(self.gate.worker_target(self.gate.left_click))
        self.gate.worker_target(worker)()
        children[0]()
        self.raw.left_click.assert_not_called()

    def test_cancelled_concede_finally_cannot_release_a_new_owner(self):
        finish = threading.Event()
        entered = threading.Event()
        def old():
            self.assertTrue(self.gate.take_over_for_current_thread(timeout=.05))
            entered.set()
            finish.wait(1)
            self.gate.release_exclusive()
        thread = threading.Thread(target=self.gate.worker_target(old))
        thread.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.gate.take_over_for_current_thread(timeout=.05))
        finish.set()
        thread.join(1)
        self.assertTrue(self.gate._exclusive)
        self.assertEqual(self.gate._owner_ident, threading.get_ident())

    def test_backend_monitor_revokes_input_and_reports_failure(self):
        callbacks = []
        timer = Mock()
        def schedule(delay, callback):
            self.assertEqual(delay, 5)
            callbacks.append(callback)
            return timer
        self.gate.backend_timeout_callback = Mock()
        self.raw.left_click.side_effect = lambda *_: callbacks[0]()
        with patch("Controller.Utilities.input_controller.threading.Timer", side_effect=schedule):
            self.gate.left_click()
        self.gate.backend_timeout_callback.assert_called_once_with("input backend unresponsive")
        self.assertFalse(self.gate._lease_valid())


class WatchdogControllerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        from pathlib import Path
        path = Path(self.directory.name) / "Player.log"
        path.write_text("")
        self.c = Controller(str(path), auto_concede_stalled_matches=True)
        self.c._get_state_from_log = lambda: BotState.IN_GAME
        self.c._Controller__system_seat_id = 1
        self.c._Controller__live_match_id = "match"
        self.c._Controller__last_seen_match_id = "match"
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = Mock(return_value=False)
        self.now = 0
        self.c._Controller__decision_watchdog = DecisionWatchdog(lambda: self.now)
        self.state = {"gameStateId": 20, "players": [{"systemSeatNumber": 1}],
                      "turnInfo": {"turnNumber": 12, "phase": "Phase_Combat", "step": "Step_DeclareBlock",
                                   "decisionPlayer": 1, "priorityPlayer": 2}, "gameObjects": [], "zones": []}
        self.c.updated_game_state = GameState(self.state)
        self.payload = {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_DeclareBlockersReq", "systemSeatIds": [1],
            "gameStateId": 20, "declareBlockersReq": {"blockers": [
                {"blockerInstanceId": 336, "attackerInstanceIds": [327]}]}}]}}
        self.timer_patch = patch("Controller.MTGAController.Controller.threading.Timer")
        self.timer = self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)
        self.addCleanup(self.c._Controller__clear_stall_watchdog, "bot stopped")

    def test_request_is_armed_before_its_handler_and_concedes_after_silence(self):
        def handle(_):
            self.assertIsNotNone(self.c._Controller__stall_watchdog_timer)
            self.assertEqual(self.c._Controller__decision_watchdog.pending.kind, "blockers")
        self.c._Controller__handle_declare_blockers_req = handle
        with patch("Controller.MTGAController.Controller.time.monotonic", return_value=0):
            self.c._Controller__log_callback(self.c.patterns["declare_blockers"], json.dumps(self.payload))
        token = self.c._Controller__stall_context_signature
        generation = self.c._Controller__stall_watchdog_generation
        self.now = 30
        with patch.object(self.c, "_Controller__claim_concession", return_value=True) as claim, \
             patch.object(self.c, "_Controller__run_claimed_concede_sequence") as concede, \
             patch("Controller.MTGAController.Controller.time.monotonic", return_value=30):
            self.c._Controller__attempt_stall_concede(generation, token, 0, "match")
        claim.assert_called_once_with("stalled_local_context")
        concede.assert_called_once_with("STALL_CONCEDE", token, "match")

    def test_failed_concession_rearms_same_decision_for_full_thirty_seconds(self):
        self.c._Controller__observe_watchdog_payload(self.payload)
        self.c._suppress_selections = True
        self.c._Controller__concession_claimed = True
        self.c._Controller__concede_completed_event = Mock()
        self.c._Controller__concede_completed_event.is_set.return_value = False
        self.c._Controller__concede_completed_event.wait.return_value = False
        self.c._Controller__perform_concede = Mock()
        self.now = 45
        with patch("Controller.MTGAController.Controller.ConcedeIncident.create", return_value=None), \
             patch("Controller.MTGAController.Controller.time.monotonic", return_value=45):
            self.c._Controller__run_claimed_concede_sequence("STALL_CONCEDE", expected_match_id="match")
        self.assertFalse(self.c._suppress_selections)
        self.assertFalse(self.c._Controller__concession_claimed)
        self.assertEqual(self.c._Controller__stall_context_started_at, 45)
        self.assertTrue(any(call.args[0] == 30 for call in self.timer.call_args_list))

    def test_resume_recreates_prompt_worker_without_resetting_countdown(self):
        self.c._Controller__observe_watchdog_payload(self.payload)
        tracker = self.c._Controller__decision_watchdog
        token = tracker.pending.token
        self.c._Controller__handle_declare_blockers_req = Mock()
        self.c._Controller__resume_stalled_decision("match", token)
        self.c._Controller__handle_declare_blockers_req.assert_called_once()
        self.assertEqual(tracker.pending.token, token)

    def test_resume_drops_a_prompt_that_changed_before_retry_worker_started(self):
        self.c._Controller__observe_watchdog_payload(self.payload)
        tracker = self.c._Controller__decision_watchdog
        token = tracker.pending.token
        tracker.progress("assigned blockers", 1)
        self.c._Controller__handle_declare_blockers_req = Mock()
        self.c._Controller__resume_stalled_decision("match", token)
        self.c._Controller__handle_declare_blockers_req.assert_not_called()

    def test_input_takeover_failure_stops_bot(self):
        self.c.input = ExclusiveInputController(NullInputController())
        self.c.input.take_over_for_current_thread = Mock(return_value=False)
        self.c._Controller__concede_needs_takeover = True
        with patch.object(self.c, "_request_stop_bot") as stop, \
             patch("Controller.MTGAController.Controller.ConcedeIncident.create", return_value=None):
            self.c._Controller__run_claimed_concede_sequence("STALL_CONCEDE", expected_match_id="match")
        stop.assert_called_once_with("Auto-concede failed: input backend unresponsive")

    def test_live_match_concedes_without_gear_icon(self):
        self.c._arena_region_provider = Mock()
        self.c._arena_region_provider.detect.return_value.ok = True
        self.c._arena_region_provider.detect.return_value.region = (0, 0, 1920, 1080)
        self.c._arena_region_provider.anchor_visible.return_value = False
        self.c.input = Mock()
        self.c._Controller__active_concede_incident = None
        self.c._loaded_click_targets = {"concede": {"x": 962, "y": 631}}
        self.c._map_abs_point_to_arena = Mock(return_value=((100, 200), "arena"))
        self.c._Controller__click_concede_and_confirm = Mock()
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True), \
             patch("Controller.MTGAController.Controller.time.sleep"):
            self.c._Controller__perform_concede("STALL_CONCEDE_1", "match")
        self.c.input.tap_escape.assert_called_once()
        self.c._Controller__click_concede_and_confirm.assert_called_once_with(
            (100, 200), label="STALL_CONCEDE_1", expected_match_id="match",
        )
        self.c._arena_region_provider.anchor_visible.assert_not_called()

    def test_disable_cancels_pending_takeover_before_it_reclaims_input(self):
        self.c.input = ExclusiveInputController(NullInputController())
        self.c._Controller__concession_claim_reason = "stalled_local_context"
        self.c._Controller__concede_needs_takeover = True
        self.c.set_auto_concede_stalled_matches(False)
        with patch.object(self.c.input, "take_over_for_current_thread") as takeover, \
             patch("Controller.MTGAController.Controller.ConcedeIncident.create", return_value=None):
            self.c._Controller__run_claimed_concede_sequence("STALL_CONCEDE", expected_match_id="match")
        takeover.assert_not_called()
        self.assertTrue(self.c.input.left_click())

    def test_enable_during_silent_existing_prompt_gets_fresh_window(self):
        self.c.set_auto_concede_stalled_matches(False)
        self.c._Controller__observe_watchdog_payload(self.payload)
        self.assertIsNone(self.c._Controller__stall_watchdog_timer)
        self.now = 100
        with patch("Controller.MTGAController.Controller.time.monotonic", return_value=100):
            self.c.set_auto_concede_stalled_matches(True)
        self.assertEqual(self.c._Controller__stall_context_started_at, 100)
        self.assertEqual(self.timer.call_args.args[0], 30)
        self.assertEqual(self.c._Controller__decision_watchdog.pending.kind, "blockers")

    def test_unknown_standalone_request_is_observed_without_a_gameplay_handler(self):
        payload = {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_OptionalActionReq", "systemSeatIds": [1],
            "optionalActionReq": {"sourceId": 14}}]}}
        self.c._Controller__log_callback(self.c.patterns["decision_request"], json.dumps(payload))
        self.assertIsNotNone(self.c._Controller__stall_watchdog_timer)
        self.assertEqual(self.c._Controller__decision_watchdog.pending.kind, "optionalaction")


if __name__ == "__main__":
    unittest.main()
