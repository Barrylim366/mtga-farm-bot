"""Unit tests for login splash screen detection and handling during account switching."""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


class LoginSplashTests(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        self.c = Controller(f.name)
        self.c.input = Mock()
        self.c._click_abs = Mock()

    def test_is_login_splash_button_visible_false_when_not_located(self):
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.assertFalse(self.c._is_login_splash_button_visible())

    def test_is_login_splash_button_visible_true_when_located(self):
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=(960, 540))
        self.assertTrue(self.c._is_login_splash_button_visible())

    def test_handle_login_splash_returns_false_when_not_visible(self):
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.assertFalse(self.c._handle_login_splash_if_visible())
        self.c._click_abs.assert_not_called()

    @patch("Controller.MTGAController.Controller.time.sleep", return_value=None)
    @patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
    def test_handle_login_splash_clicks_and_returns_true_when_visible(self, mock_focus, mock_sleep):
        # First locate returns point, second locate (re-check) returns None (settled)
        self.c._locate_image_center_in_scaled_arena_region = Mock(side_effect=[(960, 540), None])
        self.assertTrue(self.c._handle_login_splash_if_visible())
        self.c._click_abs.assert_called_once_with(960, 540, "LOGIN_SPLASH_BTN")
        mock_focus.assert_called_once()

    def test_wait_for_logout_returns_true_on_playerlog_marker(self):
        self.c._wait_for_playerlog_marker = Mock(return_value=True)
        self.c._is_login_splash_button_visible = Mock(return_value=False)
        self.assertTrue(self.c._wait_for_logout_to_reach_login_screen(start_offset=0))
        self.c._is_login_splash_button_visible.assert_not_called()

    def test_wait_for_logout_falls_back_to_splash_button(self):
        self.c._wait_for_playerlog_marker = Mock(return_value=False)
        self.c._is_login_splash_button_visible = Mock(return_value=True)
        self.assertTrue(self.c._wait_for_logout_to_reach_login_screen(start_offset=0))

    def test_wait_for_logout_returns_false_when_neither_marker_nor_splash_found(self):
        self.c._wait_for_playerlog_marker = Mock(return_value=False)
        self.c._is_login_splash_button_visible = Mock(return_value=False)
        self.assertFalse(self.c._wait_for_logout_to_reach_login_screen(start_offset=0))
