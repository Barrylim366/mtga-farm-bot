"""Popup recovery decisions never inspect or click the real screen."""
import os
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.input_controller import ExclusiveInputController, NullInputController
from state.state_machine import BotState
from vision.vision import cv2
from vision.window_locator import ArenaDetectionResult


class PopupRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        log = os.path.join(folder.name, "Player.log")
        open(log, "w").close()
        self.c = Controller(log)
        self.c._popup_recovery_active = True
        self.c.input = ExclusiveInputController(NullInputController())
        self.c._get_state_from_log = Mock(return_value=BotState.HOME)
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = Mock(return_value=False)
        self.c._click_abs = Mock()
        self.c._quest_reroll_home_visible = Mock(return_value=False)
        self.claim = ("POPUP_CLAIM_REWARDS", (1700, 1000), (0, 0, 1920, 1080))
        self.continue_ = ("POPUP_CONTINUE", (960, 1050), (0, 0, 1920, 1080))
        self.c._find_blocking_popup = Mock(return_value=self.claim)
        clock = patch("Controller.MTGAController.popup_recovery.time.monotonic", return_value=100.0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        focus = patch("Controller.MTGAController.popup_recovery.focus_mtga_window", return_value=True)
        self.focus = focus.start()
        self.addCleanup(focus.stop)

    def arm(self):
        self.assertFalse(self.c._recover_blocking_popup())
        self.clock.return_value += 5

    def test_waits_five_seconds_even_when_failed_clicks_repeat(self):
        self.arm()
        self.clock.return_value = 104.99
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()
        self.clock.return_value = 105.0
        self.assertTrue(self.c._recover_blocking_popup())
        self.c._click_abs.assert_called_once_with(1700, 1000, "POPUP_CLAIM_REWARDS")

    def test_recovers_in_home_matchmaking_and_game_states(self):
        for state in (BotState.HOME, BotState.FIND_MATCH, BotState.IN_GAME):
            with self.subTest(state=state):
                self.c._popup_signature = None
                self.c._get_state_from_log.return_value = state
                self.arm()
                self.assertTrue(self.c._recover_blocking_popup())

    def test_game_progress_restarts_wait_but_timers_do_not(self):
        self.arm()
        self.c.updated_game_state.game_dict["turnInfo"] = {"turnNumber": 2}
        self.assertFalse(self.c._recover_blocking_popup())
        self.clock.return_value += 5
        self.c.updated_game_state.game_dict["timers"] = [{"elapsed": 5}]
        self.assertTrue(self.c._recover_blocking_popup())

    def test_claim_then_fresh_continue_requires_new_wait(self):
        self.arm()
        self.assertTrue(self.c._recover_blocking_popup())
        self.c._find_blocking_popup.return_value = self.continue_
        self.assertFalse(self.c._recover_blocking_popup())
        self.clock.return_value += 5
        self.assertTrue(self.c._recover_blocking_popup())
        self.assertEqual([call.args[2] for call in self.c._click_abs.call_args_list],
                         ["POPUP_CLAIM_REWARDS", "POPUP_CONTINUE"])

    def test_disappearance_or_changed_screen_prevents_stale_click(self):
        self.arm()
        self.c._find_blocking_popup.side_effect = [self.claim, None]
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()

    def test_missing_popup_does_not_click(self):
        self.c._find_blocking_popup.return_value = None
        self.arm()
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()

    def test_stop_during_probe_prevents_click(self):
        self.arm()
        def stopped():
            self.c._stop_requested = True
            return self.claim
        self.c._find_blocking_popup.side_effect = stopped
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()

    def test_input_owned_by_other_thread_defers_recovery(self):
        self.arm()
        self.c.input._exclusive = True
        self.c.input._owner_ident = -1
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()
        self.focus.assert_not_called()

    def test_inflight_decision_defers_recovery(self):
        self.arm()
        with self.c._Controller__decision_exec_lock:
            self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()

    def test_account_switch_owner_can_recover_but_other_threads_cannot(self):
        self.c._account_switch_in_progress = True
        self.c._switch_owner_ident = -1
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._find_blocking_popup.assert_not_called()
        self.c._switch_owner_ident = threading.get_ident()
        self.arm()
        self.assertTrue(self.c._recover_blocking_popup())

    def test_disabled_controller_never_sees_screen(self):
        self.c._popup_recovery_active = False
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._find_blocking_popup.assert_not_called()

    def test_old_session_worker_cannot_click_after_restart(self):
        self.arm()
        old_stop = threading.Event()
        def restarted():
            old_stop.set()
            self.c._popup_recovery_stop = threading.Event()
            return self.claim
        self.c._find_blocking_popup.side_effect = restarted
        self.assertFalse(self.c._recover_blocking_popup(stop_event=old_stop))
        self.c._click_abs.assert_not_called()

    def test_text_assets_and_thresholds(self):
        targets = self.c._POPUP_TARGETS
        self.assertEqual(targets[0][1], "POPUP_RECONNECT")
        self.assertEqual(targets[1][1], "POPUP_CLAIM_REWARDS")
        self.assertEqual(targets[2][1], "POPUP_CLAIM")
        self.assertEqual(targets[3][3], 0.70)
        self.assertGreater(targets[1][3], targets[3][3])
        for filename, *_ in targets:
            self.assertTrue(os.path.exists(os.path.join(self.c._buttons_dir(), filename)))

    def test_reconnect_waits_two_minutes_and_retries_only_after_another_wait(self):
        self.c._find_blocking_popup.return_value = (
            "POPUP_RECONNECT", (960, 595), (0, 0, 1920, 1080),
        )
        self.assertFalse(self.c._recover_blocking_popup())
        self.clock.return_value = 219.99
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()
        self.clock.return_value = 220.0
        self.assertTrue(self.c._recover_blocking_popup())
        self.c._click_abs.assert_called_once_with(960, 595, "POPUP_RECONNECT")
        self.clock.return_value = 221.0
        self.assertFalse(self.c._recover_blocking_popup())
        self.clock.return_value = 340.99
        self.assertFalse(self.c._recover_blocking_popup())
        self.clock.return_value = 341.0
        self.assertTrue(self.c._recover_blocking_popup())

    def test_failed_focus_prevents_click(self):
        self.arm()
        self.focus.return_value = False
        self.assertFalse(self.c._recover_blocking_popup())
        self.c._click_abs.assert_not_called()

    def test_home_navigation_waits_after_popup_click(self):
        self.c._recover_blocking_popup = Mock(return_value=True)
        self.c._ensure_arena_region = Mock()
        self.assertFalse(self.c._navigate_to_home())
        self.c._ensure_arena_region.assert_not_called()
        self.c._click_abs.assert_not_called()

    def test_rewards_are_not_rejected_by_home_or_event_play_false_positives(self):
        self.c._quest_reroll_home_visible = Mock(return_value=True)
        self.c._on_starter_event_landing_page = Mock(return_value=True)
        # Text, not the orange pill or the background page, decides the click.
        self.assertTrue(self.c._dismiss_reward_popup())
        self.c._click_abs.assert_not_called()
        self.clock.return_value += 5
        self.assertTrue(self.c._dismiss_reward_popup())
        self.c._click_abs.assert_called_once_with(1700, 1000, "POPUP_CLAIM_REWARDS")
        self.c._quest_reroll_home_visible.assert_not_called()
        self.c._on_starter_event_landing_page.assert_not_called()
        self.c._locate_image_center_in_scaled_arena_region.assert_not_called()

    def test_pending_reward_blocks_home_click_during_persistence_wait(self):
        self.c._ensure_arena_region = Mock()
        self.assertFalse(self.c._navigate_to_home())
        self.c._ensure_arena_region.assert_not_called()
        self.c._click_abs.assert_not_called()

    def test_queue_checks_rewards_before_a_reroll_that_would_fail(self):
        self.c.reroll_quest_on_landing = Mock(return_value=False)
        self.c.start_game_from_home_screen()
        self.c.reroll_quest_on_landing.assert_not_called()
        self.clock.return_value += 5
        self.c.start_game_from_home_screen()
        self.c._click_abs.assert_called_once_with(1700, 1000, "POPUP_CLAIM_REWARDS")
        self.c.reroll_quest_on_landing.assert_not_called()
        self.c._find_blocking_popup.return_value = None
        self.c.start_game_from_home_screen()
        self.c.reroll_quest_on_landing.assert_called_once()

    def test_direct_reroll_checks_rewards_before_consuming_pending_reroll(self):
        self.c._quest_reroll_pending = True
        self.c._extract_latest_quest_snapshot = Mock()
        self.assertFalse(self.c.reroll_quest_on_landing())
        self.assertTrue(self.c._quest_reroll_pending)
        self.c._extract_latest_quest_snapshot.assert_not_called()
        self.clock.return_value += 5
        self.assertFalse(self.c.reroll_quest_on_landing())
        self.c._click_abs.assert_called_once_with(1700, 1000, "POPUP_CLAIM_REWARDS")
        self.assertTrue(self.c._quest_reroll_pending)

    def test_disconnect_without_button_still_blocks_navigation(self):
        def disconnect_without_button():
            self.c._popup_modal_present = True
            return None
        self.c._find_blocking_popup.side_effect = disconnect_without_button
        self.assertTrue(self.c._dismiss_reward_popup())
        self.c._click_abs.assert_not_called()

    def unknown_screen(self):
        self.c._arena_region_provider.detect = Mock(return_value=ArenaDetectionResult(
            False, (200, 100, 1920, 1080), "anchor_not_found", "result screen",
        ))
        self.c.input.move_abs = Mock()
        self.c.input.left_click = Mock()
        self.c._get_ui_action_arena_region = Mock(return_value=None)

    def test_unknown_screen_uses_main_fallback_after_three_misses(self):
        self.unknown_screen()
        self.c._find_blocking_popup.return_value = None
        with patch("Controller.MTGAController.Controller.time.sleep"), patch(
            "Controller.MTGAController.Controller.focus_mtga_window", return_value=True,
        ):
            for _ in range(2):
                self.assertFalse(self.c._dismiss_match_end_screen())
            self.c.input.left_click.assert_not_called()
            self.assertTrue(self.c._dismiss_match_end_screen())
        self.assertEqual([call.args for call in self.c.input.move_abs.call_args_list],
                         [(1160, 1104), (1160, 640)])
        self.assertEqual(self.c.input.left_click.call_count, 2)
        self.assertEqual(self.c._unknown_screen_strikes, 0)

    def test_fallback_prefers_popup_text_and_waits_without_generic_clicks(self):
        self.unknown_screen()
        for _ in range(2):
            self.assertFalse(self.c._dismiss_match_end_screen())
        self.assertTrue(self.c._dismiss_match_end_screen())
        self.c._click_abs.assert_not_called()
        self.clock.return_value += 5
        self.assertTrue(self.c._dismiss_match_end_screen())
        self.c._click_abs.assert_called_once_with(1700, 1000, "POPUP_CLAIM_REWARDS")
        self.c.input.left_click.assert_not_called()

    def test_first_fallback_click_stops_when_screen_or_geometry_changes(self):
        outcomes = (
            ArenaDetectionResult(True, (200, 100, 1920, 1080), "ok", "Home"),
            ArenaDetectionResult(True, (200, 100, 1920, 1080), "ok", "event"),
            ArenaDetectionResult(False, (200, 100, 1200, 1000), "window_wrong_size", "resized"),
            ArenaDetectionResult(False, (300, 100, 1920, 1080), "anchor_not_found", "moved"),
            None,
            OSError("capture failed"),
        )
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                self.unknown_screen()
                initial = self.c._arena_region_provider.detect.return_value
                self.c._arena_region_provider.detect.side_effect = [initial, outcome]
                self.c._unknown_screen_strikes = 2
                self.c._find_blocking_popup.return_value = None
                with patch("Controller.MTGAController.Controller.time.sleep"), patch(
                    "Controller.MTGAController.Controller.focus_mtga_window", return_value=True,
                ):
                    self.assertTrue(self.c._dismiss_match_end_screen())
                self.c.input.left_click.assert_called_once_with(1)
                self.c.input.move_abs.assert_called_once_with(1160, 1104)

    def test_popup_appearing_after_first_fallback_click_blocks_centre_click(self):
        self.unknown_screen()
        self.c._unknown_screen_strikes = 2
        self.c._find_blocking_popup.return_value = None
        self.c.input.left_click.side_effect = lambda _: setattr(
            self.c._find_blocking_popup, "return_value", self.claim
        )
        with patch("Controller.MTGAController.Controller.time.sleep"), patch(
            "Controller.MTGAController.Controller.focus_mtga_window", return_value=True,
        ):
            self.assertTrue(self.c._dismiss_match_end_screen())
        self.c.input.left_click.assert_called_once_with(1)
        self.c._click_abs.assert_not_called()

    def test_fallback_rejects_invalid_geometry_even_with_a_rectangle(self):
        self.unknown_screen()
        self.c._find_blocking_popup.return_value = None
        for code in ("window_wrong_size", "window_off_screen"):
            with self.subTest(code=code):
                self.c._unknown_screen_strikes = 2
                self.c._arena_region_provider.detect.return_value = ArenaDetectionResult(
                    False, (200, 100, 1200, 1000), code, "rejected geometry",
                )
                with patch("Controller.MTGAController.Controller.focus_mtga_window") as focus:
                    self.assertFalse(self.c._dismiss_match_end_screen())
                focus.assert_not_called()
                self.c.input.move_abs.assert_not_called()
                self.c.input.left_click.assert_not_called()
                self.assertEqual(self.c._unknown_screen_strikes, 0)

    def test_fallback_failed_windows_focus_never_moves_or_clicks(self):
        self.unknown_screen()
        self.c._find_blocking_popup.return_value = None
        self.c._unknown_screen_strikes = 2
        with patch("Controller.MTGAController.Controller.sys.platform", "win32"), patch(
            "Controller.MTGAController.Controller.focus_mtga_window", return_value=False,
        ):
            self.assertFalse(self.c._dismiss_match_end_screen())
        self.c.input.move_abs.assert_not_called()
        self.c.input.left_click.assert_not_called()

    def test_fallback_non_windows_does_not_require_windows_focus_helper(self):
        self.unknown_screen()
        self.c._find_blocking_popup.return_value = None
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                self.c.input.left_click.reset_mock()
                self.c._unknown_screen_strikes = 2
                with patch("Controller.MTGAController.Controller.sys.platform", platform), patch(
                    "Controller.MTGAController.Controller.focus_mtga_window", return_value=False,
                ), patch("Controller.MTGAController.Controller.time.sleep"):
                    self.assertTrue(self.c._dismiss_match_end_screen())
                self.assertEqual(self.c.input.left_click.call_count, 2)

    def test_fallback_disconnect_wait_does_not_send_generic_clicks(self):
        self.unknown_screen()
        self.c._find_blocking_popup.return_value = ("POPUP_RECONNECT", (960, 600), (0, 0, 1920, 1080))
        for _ in range(3):
            self.c._dismiss_match_end_screen()
        self.clock.return_value += 119
        self.assertTrue(self.c._dismiss_match_end_screen())
        self.c.input.left_click.assert_not_called()
        self.c._click_abs.assert_not_called()

    def test_match_transition_during_fallback_focus_prevents_generic_clicks(self):
        self.unknown_screen()
        self.c._find_blocking_popup.return_value = None
        self.c._unknown_screen_strikes = 2
        def join_match():
            self.c._get_state_from_log.return_value = BotState.IN_GAME
            return True
        with patch("Controller.MTGAController.Controller.time.sleep"), patch(
            "Controller.MTGAController.Controller.focus_mtga_window", side_effect=join_match,
        ):
            self.c._dismiss_match_end_screen()
        self.c.input.left_click.assert_not_called()

    def test_visible_navigation_resets_unknown_screen_count(self):
        self.unknown_screen()
        self.c._unknown_screen_strikes = 2
        self.c._arena_region_provider.detect.return_value = ArenaDetectionResult(
            True, (200, 100, 1920, 1080), "ok", "Home",
        )
        self.assertFalse(self.c._dismiss_match_end_screen())
        self.assertEqual(self.c._unknown_screen_strikes, 0)
        self.c._find_blocking_popup.assert_not_called()

    def test_missing_claim_text_never_uses_ambiguous_button_fallback(self):
        self.c._find_blocking_popup.return_value = None
        self.c._locate_image_center_in_scaled_arena_region.return_value = (1700, 1000)
        self.assertFalse(self.c._dismiss_reward_popup())
        self.c._click_abs.assert_not_called()
        self.c._locate_image_center_in_scaled_arena_region.assert_not_called()

    def test_legacy_claim_guard_rejects_home_play_before_reroll(self):
        self.c._find_blocking_popup.return_value = None
        self.c._locate_image_center_in_scaled_arena_region.return_value = (1700, 1000)
        self.c._quest_reroll_home_visible.return_value = True
        self.c._quest_reroll_pending = True
        self.c._on_starter_event_landing_page = Mock(return_value=False)
        self.assertFalse(self.c._dismiss_reward_popup())
        self.c._click_abs.assert_not_called()
        self.assertTrue(self.c._quest_reroll_pending)

    def test_text_miss_can_recover_claim_on_a_later_observation(self):
        self.c._find_blocking_popup.return_value = None
        self.assertFalse(self.c._dismiss_reward_popup())
        self.c._find_blocking_popup.return_value = ("POPUP_CLAIM", (1700, 1000), (0, 0, 1920, 1080))
        self.assertTrue(self.c._dismiss_reward_popup())
        self.c._click_abs.assert_not_called()
        self.clock.return_value += 5
        self.assertTrue(self.c._dismiss_reward_popup())
        self.c._click_abs.assert_called_once_with(1700, 1000, "POPUP_CLAIM")

    def test_navigation_yields_to_a_competing_popup_probe(self):
        with self.c._popup_recovery_lock:
            self.assertTrue(self.c._dismiss_reward_popup())
        self.c._find_blocking_popup.assert_not_called()
        self.c._click_abs.assert_not_called()

    @unittest.skipIf(cv2 is None, "OpenCV unavailable")
    def test_recorded_disconnect_blocks_dimmed_rewards_without_navigation_anchors(self):
        frame = cv2.imread(str(Path(__file__).parent / "fixtures" / "season_rewards_disconnected.jpg"))
        self.assertIsNotNone(frame)
        # Anchor acquisition failed on the real incident. Live window bounds
        # remain available, including after a move/resize; the old cache is not.
        self.c._ensure_arena_region = Mock(return_value=None)
        for width, height in ((1920, 1080), (1280, 720)):
            arena = (200, 100, width, height)
            self.c._arena_region_provider._last_detection_result = ArenaDetectionResult(
                False, arena, "anchor_not_found", "modal hides anchors",
            )
            self.c._popup_vision.capture = Mock(return_value=cv2.resize(frame, (width, height)))
            candidate = Controller._find_blocking_popup(self.c)
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate[0], "POPUP_RECONNECT")
            self.assertEqual(candidate[2], arena)
            self.assertLess(abs(candidate[1][0] - (200 + width / 2)), 5)
            self.assertLess(abs(candidate[1][1] - (100 + 595 * height / 1080)), 5)
        for code in ("window_wrong_size", "window_off_screen", "window_not_found"):
            self.c._arena_region_provider._last_detection_result = ArenaDetectionResult(
                False, arena, code, "invalid geometry",
            )
            self.c._popup_vision.capture.reset_mock()
            self.assertIsNone(Controller._find_blocking_popup(self.c))
            self.c._popup_vision.capture.assert_not_called()

    @unittest.skipIf(cv2 is None, "OpenCV unavailable")
    def test_disconnect_message_blocks_rewards_when_reconnect_button_is_missing(self):
        frame = cv2.imread(str(Path(__file__).parent / "fixtures" / "season_rewards_disconnected.jpg"))
        frame[565:626, 820:1100] = 0
        self.c._ensure_arena_region = Mock(return_value=(0, 0, 1920, 1080))
        self.c._popup_vision.capture = Mock(return_value=frame)
        self.assertIsNone(Controller._find_blocking_popup(self.c))

    @unittest.skipIf(cv2 is None, "OpenCV unavailable")
    def test_matcher_claim_priority_continue_fallback_and_play_rejection(self):
        import numpy as np
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.c._ensure_arena_region = Mock(return_value=(0, 0, 1920, 1080))
        self.c._popup_vision.capture = lambda region: frame[
            region[1]:region[1] + region[3], region[0]:region[0] + region[2]
        ].copy()

        def paste(filename, x, y):
            image = cv2.imread(os.path.join(self.c._buttons_dir(), filename))
            h, w = image.shape[:2]
            frame[y:y + h, x:x + w] = image

        # Ordinary orange pills triggered the old generic Claim matcher.
        for filename in ("event_play.png", "play_btn.png", "submit_deck.PNG"):
            frame[:] = 0
            paste(filename, 1500, 920)
            self.assertIsNone(Controller._find_blocking_popup(self.c), filename)
        frame[:] = 0
        paste("claim.png", 1500, 920)
        self.assertEqual(Controller._find_blocking_popup(self.c)[0], "POPUP_CLAIM")
        frame[:] = 0
        paste("click_to_continue_text.png", 840, 1044)
        paste("claim_rewards_text.png", 1620, 990)
        self.assertEqual(Controller._find_blocking_popup(self.c)[0], "POPUP_CLAIM_REWARDS")
        frame[850:1040, 1300:] = 0
        self.assertEqual(Controller._find_blocking_popup(self.c)[0], "POPUP_CONTINUE")

        # Rewards/Continue must work with the exact missing-anchor outcome that
        # stranded the real session, rather than only a mock verified arena.
        self.c._ensure_arena_region.return_value = None
        self.c._arena_region_provider._last_detection_result = ArenaDetectionResult(
            False, (0, 0, 1920, 1080), "anchor_not_found", "season rankings",
        )
        self.assertEqual(Controller._find_blocking_popup(self.c)[0], "POPUP_CONTINUE")
        paste("claim_rewards_text.png", 1620, 990)
        self.assertEqual(Controller._find_blocking_popup(self.c)[0], "POPUP_CLAIM_REWARDS")


if __name__ == "__main__":
    unittest.main()
