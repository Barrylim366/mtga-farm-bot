"""Duration policy tests use fake clocks/input, never the screen."""
import json
import pathlib
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from Game import Game
from Controller.MTGAController.Controller import Controller
import run_bot
import ui


class MatchTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.clock = patch('Game.time.monotonic', return_value=100.0).start()
        self.timer = patch('Game.threading.Timer').start()
        self.addCleanup(patch.stopall)
        self.controller = Mock()
        self.controller.get_current_match_id.return_value = 'game-1'
        self.controller.concede.return_value = True
        self.game = Game(self.controller, Mock())
        self.game._mark_match_started()

    def advance(self, seconds):
        self.clock.return_value = 100.0 + seconds

    def test_deadline_uses_elapsed_time_and_is_independent_of_stall_setting(self):
        self.controller.get_auto_concede_stalled_matches.return_value = False
        self.advance(599)
        self.assertFalse(self.game._check_match_timeout())
        self.controller.concede.assert_not_called()
        self.advance(600)
        self.assertTrue(self.game._check_match_timeout())
        self.game._check_match_timeout()
        self.controller.concede.assert_called_once_with(
            'match_duration_timeout_exceeded', expected_match_id='game-1')

    def test_live_changes_reschedule_without_resetting_elapsed_time(self):
        self.advance(601)
        self.game.set_match_timeout_minutes(20)
        self.assertAlmostEqual(self.timer.call_args.args[0], 599)
        self.assertFalse(self.game._check_match_timeout())
        self.game.set_match_timeout_minutes(5)
        self.assertEqual(self.timer.call_args.args[0], 0)
        self.assertTrue(self.game._check_match_timeout())

    def test_disable_invalidates_pending_callback_and_enable_uses_elapsed_time(self):
        generation = self.game._duration_generation
        self.game.set_match_timeout_minutes(0)
        self.advance(1000)
        self.assertFalse(self.game._check_match_timeout(generation))
        self.assertFalse(self.game._check_match_timeout())
        self.controller.concede.assert_not_called()
        self.game.set_match_timeout_minutes(10)
        self.assertTrue(self.game._check_match_timeout())

    def test_busy_input_retries_and_competing_callback_is_invalidated(self):
        self.advance(600)
        generation = self.game._duration_generation
        self.controller.concede.side_effect = [False, True]
        self.game._check_match_timeout(generation)
        self.assertEqual(self.timer.call_args.args[0], 1.0)
        self.assertFalse(self.game._check_match_timeout(generation))
        self.game._check_match_timeout()
        self.assertEqual(self.controller.concede.call_count, 2)

    def test_failed_concede_sequence_can_be_retried(self):
        self.advance(600)
        self.game._check_match_timeout()
        self.controller.is_concession_pending.return_value = False
        self.game._check_match_timeout(self.game._duration_generation)
        self.assertEqual(self.controller.concede.call_count, 2)

    def test_large_integer_does_not_overflow_timer(self):
        self.game.set_match_timeout_minutes(10 ** 500)
        self.assertLessEqual(self.timer.call_args.args[0], threading.TIMEOUT_MAX)
        self.assertFalse(self.game._check_match_timeout())

    def test_stale_callback_cannot_concede_next_game(self):
        generation = self.game._duration_generation
        self.controller.get_current_match_id.return_value = 'game-2'
        self.game._mark_match_started()
        self.advance(1000)
        self.assertFalse(self.game._check_match_timeout(generation))
        self.controller.concede.assert_not_called()

    def test_changed_match_id_retires_timer(self):
        self.advance(600)
        self.controller.get_current_match_id.return_value = 'game-2'
        self.assertFalse(self.game._check_match_timeout())
        self.assertIsNone(self.game._duration_started)
        self.controller.concede.assert_not_called()

    def test_match_end_and_stop_invalidate_callbacks(self):
        for action in (lambda: self.game.on_match_end(False), self.game.stop):
            with self.subTest(action=action):
                self.game._mark_match_started()
                generation = self.game._duration_generation
                action()
                self.advance(1000)
                self.assertFalse(self.game._check_match_timeout(generation))
                self.controller.concede.assert_not_called()

    def test_clock_adjustments_do_not_change_deadline(self):
        with patch('Game.time.time', return_value=9999999999):
            self.advance(100)
            self.assertFalse(self.game._check_match_timeout())


class TimeoutSettingsTest(unittest.TestCase):
    def test_missing_config_uses_ten_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'settings.json'
            self.assertEqual(run_bot._load_runtime_bot_config(path)[2], 10)
            self.assertEqual(ui.ConfigManager(config_path=path).get_match_timeout_minutes(), 10)

    def test_gui_and_cli_share_defaults_and_saved_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'settings.json'
            for stored, expected in [(None, 10), (0, 0), (15, 15),
                                     (-1, 10), (True, 10), (2.5, 10), ('5', 10)]:
                with self.subTest(stored=stored):
                    path.write_text(json.dumps({'match_timeout_minutes': stored}))
                    config = ui.ConfigManager(config_path=path)
                    self.assertEqual(config.get_match_timeout_minutes(), expected)
                    self.assertEqual(run_bot._load_runtime_bot_config(path)[2], expected)
            config.set_match_timeout_minutes(7)
            self.assertEqual(ui.ConfigManager(config_path=path).get_match_timeout_minutes(), 7)

    def test_apply_rejects_bad_input_without_saving(self):
        window = types.SimpleNamespace(
            _timeout_minutes=Mock(), _timeout_status=Mock(),
            _config_manager=Mock(), _parent=types.SimpleNamespace(master=None))
        for text in ('', '-1', '2.5', 'abc'):
            window._timeout_minutes.get.return_value = text
            ui.BotBehaviorWindow._apply_timeout_setting(window)
        window._config_manager.set_match_timeout_minutes.assert_not_called()

    def test_apply_saves_valid_input_and_reports_failures(self):
        config = Mock()
        config.get_match_timeout_minutes.return_value = 0
        window = types.SimpleNamespace(
            _timeout_minutes=Mock(), _timeout_status=Mock(),
            _config_manager=config, _parent=types.SimpleNamespace(master=None))
        window._timeout_minutes.get.return_value = ' 0 '
        ui.BotBehaviorWindow._apply_timeout_setting(window)
        config.set_match_timeout_minutes.assert_called_once_with(0)
        window._timeout_status.set.assert_called_with('')
        config.set_match_timeout_minutes.side_effect = OSError('full')
        ui.BotBehaviorWindow._apply_timeout_setting(window)
        window._timeout_status.set.assert_called_with(
            'Could not save. Please try again.')

    def test_live_setter_persists_and_applies(self):
        app = types.SimpleNamespace(_controller_lock=threading.RLock(),
                                    config_manager=Mock(), game=Mock())
        app.config_manager.get_match_timeout_minutes.return_value = 5
        self.assertEqual(ui.MTGBotUI._set_match_timeout_minutes(app, 5), 5)
        app.config_manager.set_match_timeout_minutes.assert_called_once_with(5)
        app.game.set_match_timeout_minutes.assert_called_once_with(5)

    def test_setting_during_startup_is_available_to_new_game(self):
        config = object.__new__(ui.ConfigManager)
        config.config = {}
        config._save_config = Mock()
        app = types.SimpleNamespace(_controller_lock=threading.RLock(),
                                    config_manager=config, game=None)
        ui.MTGBotUI._set_match_timeout_minutes(app, 3)
        with app._controller_lock:
            app.game = Game(Mock(), Mock(),
                            match_timeout_minutes=config.get_match_timeout_minutes())
        self.assertEqual(app.game._match_timeout_minutes, 3)

    def test_settings_layout_builds_duration_controls(self):
        window = types.SimpleNamespace(
            _theme=ui.BotBehaviorWindow._THEME, _canvas=Mock(),
            _enabled=Mock(), _forge_enabled=Mock(), _timeout_minutes=Mock(),
            _timeout_status=Mock(), _create_panel=Mock(), _create_toggle=Mock(),
            _create_button=Mock(), _toggle_auto_concede=Mock(), _toggle_forge=Mock(),
            _apply_timeout_setting=Mock(), _forge_status_text=Mock(return_value=''),
            _close_behavior_window=Mock(), geometry=Mock(), update_idletasks=Mock(), _WIDTH=460,
            winfo_y=lambda: 20, winfo_x=lambda: 20, winfo_screenheight=lambda: 1080)
        bounds = {}

        def create_text(x, y, **kwargs):
            item = len(bounds) + 1
            # Simulate enlarged Windows fonts and wrapped explanatory text.
            height = 180 if kwargs.get('text', '').startswith("Forge's") else 30
            bounds[item] = (x, y, x + 380, y + height)
            return item

        window._canvas.create_text.side_effect = create_text
        window._canvas.bbox.side_effect = bounds.__getitem__
        window._canvas.find_all.return_value = [1]
        with patch.object(ui.tk, 'Entry') as entry, patch.object(ui.tkfont, 'Font') as font:
            entry.return_value.winfo_reqheight.return_value = 30
            font.return_value.metrics.return_value = 28
            ui.BotBehaviorWindow._build_ui(window)
        self.assertEqual(window._create_panel.call_count, 3)
        self.assertEqual(window._create_button.call_count, 1)  # Back only
        entry.return_value.bind.assert_any_call('<FocusOut>', unittest.mock.ANY)
        entry.return_value.bind.assert_any_call('<Return>', unittest.mock.ANY)
        self.assertEqual(window._canvas.create_window.call_args.args[0], 336)
        forge_bounds = [box for item, box in bounds.items()
                        if box[3] - box[1] == 180][0]
        _, top, _, height = window._create_panel.call_args.args
        self.assertGreater(top + height, forge_bounds[3])

    def test_save_failure_keeps_previous_value(self):
        config = object.__new__(ui.ConfigManager)
        config.config = {'match_timeout_minutes': 15}
        config._save_config = Mock(side_effect=OSError('full'))
        with self.assertRaises(OSError):
            config.set_match_timeout_minutes(0)
        self.assertEqual(config.get_match_timeout_minutes(), 15)


class TimeoutClaimTest(unittest.TestCase):
    def test_expected_match_is_checked_before_and_after_input_claim(self):
        for live_states in ([False], [True, False]):
            controller = object.__new__(Controller)
            controller._Controller__concession_claim_lock = threading.Lock()
            controller._Controller__concession_claimed = False
            controller._stop_requested = False
            controller.input = Mock()
            controller._Controller__is_live_match = Mock(side_effect=live_states)
            self.assertFalse(controller.concede('timeout', expected_match_id='old'))
            self.assertFalse(controller._Controller__concession_claimed)
            if len(live_states) == 2:
                controller.input.release_exclusive.assert_called_once()
            else:
                controller.input.claim_exclusive_for_current_thread.assert_not_called()
