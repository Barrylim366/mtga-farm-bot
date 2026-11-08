"""Regression tests for the exclusive screen-state classifier.

Fixtures are real 1920x1080 client captures from 2026-11-09:

* ``home_color_challenge`` and ``home_star_trek`` are two Home pages with
  different carousel art (the banner changes; the Play button does not),
* ``store`` and ``mastery`` are the nav pages that were mistaken for Home,
* ``in_game`` has no nav strip at all,
* ``server_wait`` is the near-black "Waiting for the Server..." transition.

No screen access: every frame is read from disk.
"""
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from vision.screen_state import (
    classify_screen,
    home_play_button_score,
    is_home,
    is_transient,
    selected_nav_tab,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "screen_state"


def frame(name: str) -> np.ndarray:
    with Image.open(FIXTURES / name) as image:
        return np.asarray(image.convert("RGB"))


class SelectedNavTabTest(unittest.TestCase):
    def test_home_is_the_underlined_home_tab(self):
        for name in ("home_color_challenge.jpg", "home_star_trek.jpg"):
            with self.subTest(name=name):
                self.assertEqual(selected_nav_tab(frame(name)), "Home")

    def test_other_pages_are_named_by_their_underlined_tab(self):
        self.assertEqual(selected_nav_tab(frame("store.jpg")), "Store")
        self.assertEqual(selected_nav_tab(frame("mastery.jpg")), "Mastery")

    def test_the_in_game_board_has_no_nav_tab(self):
        self.assertIsNone(selected_nav_tab(frame("in_game.jpg")))


class HomeTest(unittest.TestCase):
    def test_home_needs_the_selected_tab_and_the_play_button(self):
        for name in ("home_color_challenge.jpg", "home_star_trek.jpg"):
            with self.subTest(name=name):
                self.assertTrue(is_home(frame(name)))

    def test_nav_pages_are_not_home(self):
        for name in ("store.jpg", "mastery.jpg"):
            with self.subTest(name=name):
                self.assertFalse(is_home(frame(name)))

    def test_in_game_and_transitions_are_not_home(self):
        self.assertFalse(is_home(frame("in_game.jpg")))
        self.assertFalse(is_home(frame("server_wait.jpg")))

    def test_the_home_tab_alone_is_not_enough(self):
        # The old check was the nav Home tab, which is visible on every page.
        # With the Play button covered, the selected tab still says Home, but
        # the page is not Home.
        image = frame("home_color_challenge.jpg")
        masked = image.copy()
        masked[970:1045, 1590:1875] = 0
        self.assertEqual(selected_nav_tab(masked), "Home")
        self.assertFalse(is_home(masked))


class TransientTest(unittest.TestCase):
    def test_server_wait_is_a_transition(self):
        self.assertTrue(is_transient(frame("server_wait.jpg")))

    def test_content_pages_are_not_transitions(self):
        for name in ("home_color_challenge.jpg", "store.jpg", "mastery.jpg", "in_game.jpg"):
            with self.subTest(name=name):
                self.assertFalse(is_transient(frame(name)))


class ClassifyScreenTest(unittest.TestCase):
    def test_home_kind(self):
        guess = classify_screen(frame("home_color_challenge.jpg"))
        self.assertEqual(guess.kind, "home")
        self.assertEqual(guess.tab, "Home")
        self.assertGreaterEqual(guess.play_btn_score, 0.80)

    def test_nav_kinds(self):
        store = classify_screen(frame("store.jpg"))
        mastery = classify_screen(frame("mastery.jpg"))
        self.assertEqual((store.kind, store.tab), ("nav", "Store"))
        self.assertEqual((mastery.kind, mastery.tab), ("nav", "Mastery"))
        self.assertLess(mastery.play_btn_score, 0.80)

    def test_in_game_is_unknown(self):
        guess = classify_screen(frame("in_game.jpg"))
        self.assertEqual(guess.kind, "unknown")
        self.assertIsNone(guess.tab)

    def test_transition_kind(self):
        guess = classify_screen(frame("server_wait.jpg"))
        self.assertEqual(guess.kind, "transient")
        self.assertIsNone(guess.tab)

    def test_bgr_frames_are_supported(self):
        bgr = cv2.imread(str(FIXTURES / "home_color_challenge.jpg"))
        self.assertIsNotNone(bgr)
        self.assertEqual(classify_screen(bgr, bgr=True).kind, "home")
        self.assertTrue(is_home(bgr, bgr=True))


class PlayButtonAnchorTest(unittest.TestCase):
    def test_the_play_button_is_strong_on_home_and_weak_elsewhere(self):
        home = home_play_button_score(frame("home_color_challenge.jpg"))
        store = home_play_button_score(frame("store.jpg"))
        mastery = home_play_button_score(frame("mastery.jpg"))
        self.assertGreaterEqual(home, 0.90)
        self.assertLess(store, 0.80)
        self.assertLess(mastery, 0.80)


if __name__ == "__main__":
    unittest.main()
