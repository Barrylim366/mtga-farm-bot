"""Exercise global stop callbacks without installing hooks or creating Tk."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import ui


class StopHotkeyTest(unittest.TestCase):
    def setUp(self):
        self.listener = Mock()
        self.mouse = SimpleNamespace(
            Listener=Mock(return_value=self.listener),
        )
        self.app = SimpleNamespace(
            bot_running=True, game=None, _controller=None,
            _controller_lock=threading.RLock(),
            _stop_session_watchdog=Mock(), _set_running_state=Mock(),
            _set_startup_loading=Mock(), after=Mock(), after_cancel=Mock(),
            destroy=Mock(),
        )
        self.app._stop_bot = lambda: ui.MTGBotUI._stop_bot(self.app)
        self.app._poll_stop_hotkey = lambda: ui.MTGBotUI._poll_stop_hotkey(self.app)
        with patch.dict("sys.modules", {"pynput": SimpleNamespace(mouse=self.mouse)}):
            ui.MTGBotUI._setup_stop_hotkey(self.app)
        self.callbacks = self.mouse.Listener.call_args.kwargs

    def test_scroll_stops_on_both_runs_without_removing_hook(self):
        for dy in (-1, 1):
            with self.subTest(dy=dy):
                self.app.bot_running = True
                self.callbacks["on_scroll"](0, 0, 0, dy)
                self.app._poll_stop_hotkey()
                self.assertFalse(self.app.bot_running)
                self.listener.stop.assert_not_called()
        self.mouse.Listener.assert_called_once()

    def test_clicks_are_not_registered_and_horizontal_scroll_is_ignored(self):
        self.assertNotIn("on_click", self.callbacks)
        self.callbacks["on_scroll"](0, 0, 1, 0)
        self.app._poll_stop_hotkey()
        self.assertTrue(self.app.bot_running)

    def test_hook_callbacks_never_call_tk(self):
        self.app.after.reset_mock()
        self.callbacks["on_scroll"](0, 0, 0, -1)
        self.app.after.assert_not_called()
        self.assertTrue(self.app.bot_running)
        self.app._poll_stop_hotkey()
        self.assertFalse(self.app.bot_running)

    def test_input_while_stopped_does_not_stop_next_run(self):
        self.app._stop_bot()
        self.callbacks["on_scroll"](0, 0, 0, -1)
        self.app.bot_running = True
        self.app._poll_stop_hotkey()
        self.assertTrue(self.app.bot_running)

    def test_close_releases_listener(self):
        ui.MTGBotUI._on_window_close(self.app)
        self.listener.stop.assert_called_once()
        self.app.destroy.assert_called_once()

    def test_hook_failure_is_logged(self):
        self.mouse.Listener.side_effect = RuntimeError("hook unavailable")
        with patch.dict("sys.modules", {"pynput": SimpleNamespace(mouse=self.mouse)}):
            with patch("bot_logger.log_error") as log:
                ui.MTGBotUI._setup_stop_hotkey(self.app)
        self.assertIn("hook unavailable", log.call_args.args[0])
