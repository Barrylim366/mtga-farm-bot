"""Run deadline tests never inspect the screen or execute OS shutdown."""
import pathlib
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import ui
from bot_behavior import parse_max_play_hours
from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState
from Game import Game


class PlayTimeConfigTest(unittest.TestCase):
    def test_decimal_formats_and_disabled(self):
        for raw, expected in [("0,1", .1), ("0.1", .1), (0, 0), (" 3 ", 3)]:
            self.assertEqual(parse_max_play_hours(raw), expected)

    def test_invalid_values_are_rejected(self):
        for raw in ["", "abc", "1,2.3", -1, True, None, "NaN", "inf", "1e309", "1e308"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_max_play_hours(raw)

    def test_default_persistence_and_invalid_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            config = ui.ConfigManager(config_path=path)
            self.assertEqual(config.get_max_play_hours(), 0)
            config.set_max_play_hours("0,1")
            self.assertEqual(ui.ConfigManager(config_path=path).get_max_play_hours(), .1)
            with self.assertRaises(ValueError):
                config.set_max_play_hours(-1)
            self.assertEqual(config.get_max_play_hours(), .1)
            config.config["max_play_hours"] = "broken"
            self.assertEqual(config.get_max_play_hours(), 0)


class PlayDeadlineTest(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller._stop_requested = False
        self.controller._get_state_from_log = Mock(return_value=BotState.HOME)
        self.controller._request_stop_bot = Mock()
        self.controller.set_play_deadline(100)
        self.clock = patch("Controller.MTGAController.Controller.time.monotonic", return_value=100).start()
        self.addCleanup(patch.stopall)

    def test_disabled_and_not_due_do_nothing(self):
        self.clock.return_value = 99
        self.assertFalse(self.controller.check_play_time_limit())
        self.controller.set_play_deadline(None)
        self.assertFalse(self.controller.check_play_time_limit())
        self.controller._request_stop_bot.assert_not_called()

    def test_idle_expiry_is_claimed_once(self):
        self.assertTrue(self.controller.check_play_time_limit())
        self.controller.check_play_time_limit()
        self.controller._request_stop_bot.assert_called_once_with("play time limit reached")

    def test_stop_callback_does_not_hold_the_deadline_lock(self):
        completed = threading.Event()
        def stop(reason):
            def poll():
                self.controller.check_play_time_limit()
                completed.set()
            worker = threading.Thread(target=poll, daemon=True)
            worker.start()
            self.assertTrue(completed.wait(1), "UI poll deadlocked with stop callback")
            worker.join(1)
        self.controller._request_stop_bot.side_effect = stop
        self.controller.check_play_time_limit()

    def test_match_and_matchmaking_finish_before_stop(self):
        for state in [BotState.IN_GAME, BotState.FIND_MATCH]:
            self.controller._get_state_from_log.return_value = state
            self.assertTrue(self.controller.check_play_time_limit())
            self.controller._request_stop_bot.assert_not_called()
        self.controller.check_play_time_limit(match_finished=True)
        self.controller._request_stop_bot.assert_called_once()

    def test_final_recording_finishes_before_stop(self):
        self.controller._play_match_end_in_progress = True
        self.controller.check_play_time_limit(match_finished=True)
        self.controller._request_stop_bot.assert_not_called()
        self.controller._play_match_end_in_progress = False
        self.controller.check_play_time_limit(match_finished=True)
        self.controller._request_stop_bot.assert_called_once()

    def test_dismissal_records_before_requesting_stop(self):
        order = []
        def record():
            self.controller.check_play_time_limit(match_finished=True)
            self.controller._request_stop_bot.assert_not_called()
            order.append("recorded")
        self.controller._dismiss_end_screen_and_record = record
        self.controller._request_stop_bot.side_effect = lambda reason: order.append("stopped")
        self.controller.dismiss_end_screen()
        self.assertEqual(order, ["recorded", "stopped"])

    def test_manual_stop_never_requests_shutdown(self):
        self.controller._stop_requested = True
        self.controller.check_play_time_limit()
        self.controller._request_stop_bot.assert_not_called()

    def test_expired_deadline_blocks_queue_and_switch_without_screen_access(self):
        self.controller.start_queueing()
        self.controller._perform_account_switch()
        self.controller._request_stop_bot.assert_called_once()

    def test_new_deadline_resets_expiry_claim(self):
        self.controller.check_play_time_limit()
        self.controller.set_play_deadline(200)
        self.assertFalse(self.controller.check_play_time_limit())
        self.clock.return_value = 200
        self.controller.check_play_time_limit()
        self.assertEqual(self.controller._request_stop_bot.call_count, 2)


class RunCallbackTest(unittest.TestCase):
    def app(self):
        controller = object()
        token = object()
        app = types.SimpleNamespace(bot_running=True, _controller=controller,
            _controller_lock=threading.RLock(), _play_run_token=token)
        def stop():
            app.bot_running = False
            app._play_run_token = None
        app._stop_bot = Mock(side_effect=stop)
        app._announce_round_complete = Mock()
        return app, controller, token

    def test_duplicate_finish_announces_only_once(self):
        app, controller, token = self.app()
        for _ in range(2):
            ui.MTGBotUI._finish_controller_run(app, controller, token, "play time limit reached")
        app._stop_bot.assert_called_once()
        app._announce_round_complete.assert_called_once_with(time_limit=True)

    def test_retired_callback_cannot_stop_new_run(self):
        app, controller, token = self.app()
        app._play_run_token = object()
        ui.MTGBotUI._finish_controller_run(app, controller, token, "play time limit reached")
        app._stop_bot.assert_not_called()
        app._announce_round_complete.assert_not_called()

    def test_old_poll_cannot_check_new_run(self):
        app, _, _ = self.app()
        app.after = Mock()
        ui.MTGBotUI._poll_play_time_limit(app, object())
        app.after.assert_not_called()

    def test_cancelled_game_cannot_restart_during_loading(self):
        game = Game(Mock(), Mock())
        game._stop_requested = True
        game._refresh_card_data = Mock()
        game.start()
        game._refresh_card_data.assert_not_called()
        game.controller.begin_session.assert_not_called()
