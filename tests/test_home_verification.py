"""Wiring tests for the visual Home gate and Home navigation verification.

The old gate trusted ``Player.log``'s last scene name: on 2026-11-09 it said
Home while the client was on Mastery, so the switch pressed ESC there and
clicked into the reward track. These tests pin three things:

* ``_ensure_home_before_logout`` asks the frame, not the log;
* ``_navigate_to_home`` verifies the selected Home tab plus the Play button;
* a frame that cannot be captured is "not Home", not an excuse to trust the log.

No screen access: frames come from the classifier fixtures and every capture
or click path is stubbed.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from Controller.MTGAController.Controller import Controller

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "screen_state"


def frame(name: str) -> np.ndarray:
    with Image.open(FIXTURES / name) as image:
        return np.asarray(image.convert("RGB"))


class _HomeGateCase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        handle.close()
        self.log_path = handle.name
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))
        self.c = Controller(self.log_path)
        self.c._stop_requested = False
        self.c.input = mock.Mock()
        self.c._arena_region = (0, 0, 1920, 1080)
        self.c._ensure_arena_region = lambda force_reacquire=False: (0, 0, 1920, 1080)
        self.c._map_base_point_into_arena = lambda arena, point: (10, 20)
        self.c._remember_arena_region = lambda arena: None
        self.c._dismiss_reward_popup = lambda: False
        self.clicks = []
        self.c._click_abs = lambda *args, **kwargs: self.clicks.append(args)

    def tearDown(self):
        self.c._stop_requested = False


class HomeScreenCheckTest(_HomeGateCase):
    def test_home_fixture_is_home(self):
        self.c._capture_home_frame = lambda: frame("home_color_challenge.jpg")
        self.assertTrue(self.c._is_home_screen())

    def test_mastery_fixture_is_not_home(self):
        self.c._capture_home_frame = lambda: frame("mastery.jpg")
        self.assertFalse(self.c._is_home_screen())

    def test_a_failed_capture_is_not_home(self):
        self.c._capture_home_frame = lambda: None
        self.assertFalse(self.c._is_home_screen())


class EnsureHomeBeforeLogoutTest(_HomeGateCase):
    def test_home_frame_needs_no_navigation(self):
        self.c._is_home_screen = lambda: True
        self.c._navigate_to_home = lambda: (_ for _ in ()).throw(
            AssertionError("Home must not be re-navigated")
        )
        self.assertTrue(self.c._ensure_home_before_logout())

    def test_mastery_frame_navigates_and_confirms(self):
        seen = [False, True]
        self.c._is_home_screen = lambda: seen.pop(0)
        calls = []
        self.c._navigate_to_home = lambda: calls.append(1) or True
        self.assertTrue(self.c._ensure_home_before_logout())
        self.assertEqual(calls, [1])

    def test_a_handled_popup_gets_another_home_attempt(self):
        # The first Home attempt is consumed by a reward popup (nav returns
        # False); the gate waits and tries again instead of aborting.
        seen = [False, False, True]
        self.c._is_home_screen = lambda: seen.pop(0)
        nav = [False, True]
        self.c._navigate_to_home = lambda: nav.pop(0)
        with mock.patch("time.sleep"):
            self.assertTrue(self.c._ensure_home_before_logout())

    def test_a_blocked_home_gets_an_escape_and_a_retry(self):
        # A deck chooser or event overlay swallows the Home tab; ESC closes it
        # and the next Home attempt verifies.
        self.c._HOME_BEFORE_LOGOUT_TIMEOUT = 10.0
        self.c.input = mock.Mock()
        seen = [False, False, True]
        nav = [False, True]
        self.c._is_home_screen = lambda: seen.pop(0)
        self.c._navigate_to_home = lambda: nav.pop(0)
        with mock.patch("time.sleep"):
            self.assertTrue(self.c._ensure_home_before_logout())
        self.c.input.tap_escape.assert_called_once()

    def test_failed_navigation_aborts(self):
        self.c._HOME_BEFORE_LOGOUT_TIMEOUT = 0.0
        self.c._is_home_screen = lambda: False
        self.c._navigate_to_home = lambda: False
        self.assertFalse(self.c._ensure_home_before_logout())

    def test_a_still_non_home_frame_after_navigation_aborts(self):
        self.c._HOME_BEFORE_LOGOUT_TIMEOUT = 0.0
        seen = [False, False]
        self.c._is_home_screen = lambda: seen.pop(0)
        self.c._navigate_to_home = lambda: True
        self.assertFalse(self.c._ensure_home_before_logout())

    def test_the_log_scene_home_does_not_bypass_the_frame(self):
        # The exact 2026-11-09 false pass: log says Home, frame says Mastery.
        self.c._HOME_BEFORE_LOGOUT_TIMEOUT = 0.0
        self.c._get_last_scene_name = lambda: "Home"
        self.c._is_home_screen = lambda: False
        self.c._navigate_to_home = lambda: False
        self.assertFalse(self.c._ensure_home_before_logout())


class NavigateToHomeTest(_HomeGateCase):
    def test_it_clicks_home_and_verifies_the_frame(self):
        seen = [False, True]
        self.c._is_home_screen = lambda: seen.pop(0)
        with mock.patch("time.sleep"):
            self.assertTrue(self.c._navigate_to_home())
        self.assertEqual(len(self.clicks), 1)
        self.assertEqual(self.clicks[0][2], "GO_HOME")

    def test_it_retries_and_then_fails(self):
        self.c._is_home_screen = lambda: False
        with mock.patch("time.sleep"):
            self.assertFalse(self.c._navigate_to_home())
        self.assertEqual(len(self.clicks), 1)

    def test_a_stop_before_the_click_does_nothing(self):
        self.c._stop_requested = True
        self.c._is_home_screen = lambda: (_ for _ in ()).throw(
            AssertionError("must not verify after a stop")
        )
        self.assertFalse(self.c._navigate_to_home())
        self.assertEqual(self.clicks, [])


if __name__ == "__main__":
    unittest.main()
