"""Pile choices use visible Choose buttons, never the normal Resolve button."""
import json
from pathlib import Path
import unittest
from unittest import mock

import cv2

from Controller.Utilities.GameState import GameState
from state.state_machine import BotState
from tests.test_group_recovery import make_controller, _FakeTimer
from vision.vision import VisionEngine


def pile_payload(seat=1, source=468, state_id=559):
    return {"greToClientEvent": {"greToClientMessages": [{
        "type": "GREMessageType_SelectNGroupReq", "systemSeatIds": [seat],
        "gameStateId": state_id,
        "selectNGroupReq": {"minSel": 1, "maxSel": 1, "sourceId": source,
                            "groups": [{"groupId": 5004, "ids": [240, 241], "isFacedown": True},
                                       {"groupId": 5005, "ids": [242, 243, 245]}]},
    }]}}


class PileChoiceTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer).start()
        mock.patch("Controller.MTGAController.group_recovery.focus_mtga_window", return_value=True).start()
        self.now = 100.0
        mock.patch("Controller.MTGAController.group_recovery.time.monotonic",
                   side_effect=lambda: self.now).start()
        self.c = c = make_controller()
        c._get_state_from_log = lambda: BotState.IN_GAME
        c._Controller__auto_concede_stalled_matches = False
        c._Controller__has_mulled_keep = True
        c.updated_game_state = GameState({
            "gameStateId": 559,
            "turnInfo": {"turnNumber": 20, "phase": "Phase_Main1", "step": "Step_Draw",
                         "activePlayer": 2, "priorityPlayer": 2, "decisionPlayer": 1,
                         "nextPhase": "Phase_Combat", "nextStep": "Step_BeginCombat"},
            "gameObjects": [], "players": [{"systemSeatNumber": 1}], "timers": [],
            "actions": [], "zones": [], "annotations": [],
        })
        c._click_abs = mock.Mock()
        c._Controller__invoke_decision_callback = mock.Mock()

    def arm(self, payload=None):
        self.c._Controller__observe_watchdog_payload(payload or pile_payload())

    def check(self, state="open", advance=1.0):
        self.now += advance
        self.c._observe_group_overlay = mock.Mock(return_value=(
            state, (500, 950) if state == "open" else None, None, {},
        ))
        return self.c._recover_group_prompt()

    def test_callback_handles_our_choice_with_opponent_priority(self):
        c = self.c
        c._Controller__log_callback(c.patterns["select_n_group"], json.dumps(pile_payload()))
        self.assertEqual(c._group_prompt["kind"], "selectngroup")
        self.assertTrue(c._group_prompt_blocks_gameplay())
        self.assertFalse(c._Controller__safe_to_redrive_decision())
        c.resolve()
        c._click_abs.assert_not_called()
        self.check()
        c._click_abs.assert_called_once_with(500, 950, "PILE_CHOOSE")

    def test_duplicate_requests_reuse_worker_and_retry_budget(self):
        self.arm()
        prompt = self.c._group_prompt
        timer = self.c._Controller__group_resume_timer
        self.arm()
        self.assertIs(self.c._group_prompt, prompt)
        self.assertIs(self.c._Controller__group_resume_timer, timer)

    def test_pile_arriving_during_resolve_mapping_cancels_click(self):
        c = self.c
        c._Controller__should_pause_for_assign_damage = lambda: False
        c._Controller__should_pause_for_declare_blocks = lambda: False
        def map_point(*args, **kwargs):
            self.arm()
            return (1755, 944), "arena_relative"
        c._map_abs_point_to_arena = map_point
        c.input.move_abs = mock.Mock()
        c.input.left_click = mock.Mock()
        c.resolve()
        c.input.left_click.assert_not_called()
        self.assertTrue(c._group_prompt_blocks_gameplay())

    def test_clock_only_server_update_does_not_retire_choice(self):
        self.arm()
        prompt = self.c._group_prompt
        payload = {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_GameStateMessage", "systemSeatIds": [1],
            "gameStateMessage": {"turnInfo": {"decisionPlayer": 1}, "timers": []},
        }]}}
        self.c._Controller__observe_watchdog_payload(payload)
        self.assertIs(self.c._group_prompt, prompt)

    def test_server_pending_response_transition_retires_choice(self):
        self.c.updated_game_state.game_dict["players"][0]["pendingMessageType"] = "ClientMessageType_SelectNGroupResp"
        self.arm()
        self.c.updated_game_state.game_dict["players"][0]["pendingMessageType"] = "ClientMessageType_ActionsResp"
        self.c._Controller__observe_watchdog_payload({"greToClientEvent": {"greToClientMessages": []}})
        self.assertIsNone(self.c._group_prompt)

    def test_opponent_request_is_ignored(self):
        self.arm(pile_payload(seat=2))
        self.assertIsNone(self.c._group_prompt)
        self.c._click_abs.assert_not_called()

    def test_missing_buttons_wait_then_click_when_visible(self):
        self.arm()
        self.check("absent")
        self.check("unknown")
        self.c._click_abs.assert_not_called()
        self.assertTrue(self.c._group_prompt_blocks_gameplay())
        self.check()
        self.c._click_abs.assert_called_once()

    def test_clicks_are_spaced_capped_and_do_not_extend_watchdog(self):
        self.arm()
        pending = self.c._Controller__decision_watchdog.pending
        started = pending.last_progress_at
        self.check()
        self.check()
        self.assertEqual(self.c._click_abs.call_count, 1)
        self.check()
        self.assertEqual(self.c._click_abs.call_count, 2)
        for _ in range(20):
            self.check()
        self.assertEqual(self.c._click_abs.call_count, 3)
        self.assertEqual(self.c._group_prompt["checks"], self.c._GROUP_CHECK_LIMIT)
        self.assertEqual(pending.last_progress_at, started)
        self.assertTrue(self.c._group_prompt_blocks_gameplay())

    def test_input_error_still_obeys_click_spacing(self):
        self.arm()
        self.c._click_abs.side_effect = RuntimeError("input failed")
        self.check()
        self.check()
        self.assertEqual(self.c._click_abs.call_count, 1)
        self.check()
        self.assertEqual(self.c._click_abs.call_count, 2)
        self.assertTrue(self.c._group_prompt_blocks_gameplay())

    def test_two_absences_after_seen_close_prompt_but_unknown_does_not(self):
        self.arm()
        self.check()
        self.check("absent")
        self.check("unknown")
        self.check("absent")
        self.assertTrue(self.c._group_prompt_blocks_gameplay())
        self.assertTrue(self.check("absent"))
        self.assertIsNone(self.c._group_prompt)
        self.assertTrue(self.c._Controller__safe_to_redrive_decision())
        self.arm()  # A replay of the completed request must not reopen it.
        self.assertIsNone(self.c._group_prompt)

    def test_fresh_server_decision_retires_prompt_and_timer(self):
        self.arm()
        timer = self.c._Controller__group_resume_timer
        payload = {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_ActionsAvailableReq", "systemSeatIds": [2],
            "actionsAvailableReq": {},
        }]}}
        self.c._Controller__observe_watchdog_payload(payload)
        self.assertIsNone(self.c._group_prompt)
        self.assertTrue(timer.cancelled)
        timer.callback(**timer.kwargs)
        self.c._click_abs.assert_not_called()

    def test_replacement_pile_request_invalidates_old_callback(self):
        self.arm()
        timer = self.c._Controller__group_resume_timer
        self.arm(pile_payload(source=470, state_id=560))
        timer.callback(**timer.kwargs)
        self.c._click_abs.assert_not_called()
        self.assertEqual(self.c._group_prompt["watchdog_request"].request["selectNGroupReq"]["sourceId"], 470)

    def test_stop_concession_and_match_change_during_capture_prevent_click(self):
        for field, value in (("_stop_requested", True), ("_suppress_selections", True),
                             ("_Controller__live_match_id", "match-2")):
            with self.subTest(field=field):
                self.c._stop_requested = self.c._suppress_selections = False
                self.c._Controller__live_match_id = "match-1"
                self.c._last_pile_choice_key = None
                self.arm()
                self.now += 2
                def observe(prompt):
                    setattr(self.c, field, value)
                    return "open", (500, 950), None, {}
                self.c._observe_group_overlay = observe
                self.c._recover_group_prompt()
                self.c._click_abs.assert_not_called()

    def test_failed_capture_keeps_gameplay_paused(self):
        self.arm()
        self.now += 1
        self.c._observe_group_overlay = mock.Mock(side_effect=RuntimeError("capture failed"))
        self.c._recover_group_prompt()
        self.assertTrue(self.c._group_prompt_blocks_gameplay())
        self.c._click_abs.assert_not_called()

    def test_match_reset_retires_pile_callback(self):
        self.arm()
        timer = self.c._Controller__group_resume_timer
        self.c.reset_for_new_game()
        timer.callback(**timer.kwargs)
        self.assertFalse(self.c._group_prompt_blocks_gameplay())
        self.c._click_abs.assert_not_called()


class PileChoiceVisionTest(unittest.TestCase):
    def test_recorded_overlay_matches_at_multiple_window_sizes(self):
        root = Path(__file__).resolve().parents[1]
        original = cv2.imread(str(root / "tests/fixtures/pile_choice.jpg"))
        self.assertIsNotNone(original)
        vision = VisionEngine()
        c = make_controller()
        c._vision = vision
        for width in (960, 1280, 1600, 1920, 2560):
            with self.subTest(width=width):
                frame = cv2.resize(original, (width, width * 9 // 16))
                # Exercise the production observer with a disk-backed capture.
                vision.capture = mock.Mock(return_value=frame)
                c._get_ui_action_arena_region = lambda **kw: (100, 200, width, width * 9 // 16)
                state, point, _, _ = c.__class__._observe_group_overlay(c, {"kind": "selectngroup"})
                self.assertEqual(state, "open")
                x = (point[0] - 100) * 1280 / width
                y = (point[1] - 200) * 1280 / width
                self.assertTrue(244 <= x <= 423 or 854 <= x <= 1040)
                self.assertTrue(612 <= y <= 656)

    def test_scry_done_button_is_not_a_pile_choose_button(self):
        root = Path(__file__).resolve().parents[1]
        c = make_controller()
        c._vision = VisionEngine()
        frame = cv2.imread(str(root / "tests/fixtures/surveil_open.jpg"))
        c._vision.capture = mock.Mock(return_value=frame)
        c._get_ui_action_arena_region = lambda **kw: (0, 0, 1920, 1080)
        state, point, _, _ = c.__class__._observe_group_overlay(c, {"kind": "selectngroup"})
        self.assertEqual(state, "absent")
        self.assertIsNone(point)


if __name__ == "__main__":
    unittest.main()
