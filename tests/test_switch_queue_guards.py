"""Guards around the switch/queue overlap seen on 2026-11-09.

thuringiensis had its quest target met, but the post-login routine decided the
switch was not due a few milliseconds before the fresh quest count arrived. It
spent the deck-selection window and pressed Play; the queue loop then started
the logout with a match already queueing, and the logout's blind coordinate
fallback landed on the Store. These tests pin the three guards:

* wait for the account's own fresh quest count before the switch decision,
* re-check the switch immediately before any Play press,
* never click the blind Log Out point from a screen that is not Home (unless the
  Options overlay is verified).

No screen, no network; a Controller against a throwaway log and stubs.
"""
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from Controller.MTGAController.Controller import Controller


class _GuardCase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        handle.close()
        self.log_path = handle.name
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))
        self.c = Controller(self.log_path)
        self.c._stop_requested = False
        self.c._account_switch_pending = False

    def tearDown(self):
        self.c._stop_requested = False


class QuestCountWaitTest(_GuardCase):
    def test_it_returns_at_once_when_the_count_is_already_fresh(self):
        self.c._quest_count_confirmed_fresh = True
        started = time.monotonic()
        self.assertTrue(self.c._wait_for_quest_count_fresh(timeout=2.0))
        self.assertLess(time.monotonic() - started, 0.3)

    def test_it_waits_for_the_log_reader_to_confirm(self):
        self.c._quest_count_confirmed_fresh = False

        def confirm():
            self.c._quest_count_confirmed_fresh = True

        timer = threading.Timer(0.2, confirm)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        self.assertTrue(self.c._wait_for_quest_count_fresh(timeout=2.0))
        self.assertGreaterEqual(time.monotonic() - started, 0.15)

    def test_it_gives_up_and_the_caller_proceeds_as_before(self):
        self.c._quest_count_confirmed_fresh = False
        started = time.monotonic()
        self.assertFalse(self.c._wait_for_quest_count_fresh(timeout=0.15))
        self.assertGreaterEqual(time.monotonic() - started, 0.1)

    def test_a_stop_ends_the_wait(self):
        self.c._quest_count_confirmed_fresh = False
        self.c._stop_requested = True
        started = time.monotonic()
        self.assertFalse(self.c._wait_for_quest_count_fresh(timeout=2.0))
        self.assertLess(time.monotonic() - started, 0.3)


class SwitchDueBeforePlayTest(_GuardCase):
    def test_a_due_switch_blocks_play(self):
        self.c._account_switch_due = lambda: True
        self.assertTrue(self.c._switch_became_due_before_play("Test"))

    def test_a_pending_switch_blocks_play(self):
        self.c._account_switch_due = lambda: False
        self.c._account_switch_pending = True
        self.assertTrue(self.c._switch_became_due_before_play("Test"))

    def test_an_idle_switch_allows_play(self):
        self.c._account_switch_due = lambda: False
        self.c._account_switch_pending = False
        self.assertFalse(self.c._switch_became_due_before_play("Test"))

    def test_the_starter_play_is_not_clicked_when_the_switch_is_due(self):
        self.c._account_switch_due = lambda: True
        self.c._account_switch_pending = False
        self.c.check_play_time_limit = lambda: False
        self.c._starter_event_page_confirmed = lambda *_a, **_k: True
        calls = []
        self.c._click_starter_button = lambda *a, **k: calls.append((a, k)) or True

        self.assertFalse(self.c._press_starter_event_play())
        self.assertEqual(calls, [])

    def test_the_starter_play_clicks_when_the_switch_is_not_due(self):
        self.c._account_switch_due = lambda: False
        self.c._account_switch_pending = False
        self.c.check_play_time_limit = lambda: False
        self.c._starter_event_page_confirmed = lambda *_a, **_k: True
        calls = []
        self.c._click_starter_button = lambda *a, **k: calls.append((a, k)) or True

        with mock.patch("time.sleep"):
            self.assertTrue(self.c._press_starter_event_play())
        self.assertEqual(len(calls), 1)


class LogoutFallbackGuardTest(_GuardCase):
    def test_the_screen_table(self):
        cases = [
            (None, False, True),          # unknown scene: the switch's usual start
            ("Home", False, True),        # normal switch
            ("Store", False, False),      # where the blind click must never go
            ("EventLanding", False, False),  # the 2026-11-09 case
            ("EventLanding", True, True),  # overlay verified: the point is real
        ]
        for scene, options_visible, expected in cases:
            with self.subTest(scene=scene, options=options_visible):
                self.assertEqual(
                    Controller._logout_fallback_allowed(scene, options_visible), expected
                )

    def test_the_switch_does_not_click_log_out_from_an_event_page(self):
        self.c._get_last_scene_name = lambda: "EventLanding"
        self.c._manual_login_options_visible = lambda: False
        self.c._press_esc_for_options = lambda: None
        self.c._locate_log_out_button = lambda **_kw: None
        clicked = []
        self.c._click_abs = lambda *a, **k: clicked.append((a, k))

        self.assertFalse(self.c._run_mapped_logout_sequence())
        self.assertEqual(clicked, [])

    def test_the_switch_still_uses_the_fallback_from_home(self):
        self.c._get_last_scene_name = lambda: "Home"
        self.c._manual_login_options_visible = lambda: False
        self.c._press_esc_for_options = lambda: None
        self.c._locate_log_out_button = lambda **_kw: None
        self.c._resolve_target_from_queue_anchor_rebase = lambda **_kw: ((10, 20), "test")
        self.c._map_abs_point_to_arena = lambda point, **_kw: ((0, 0), "test")
        clicked = []
        self.c._click_abs = lambda *a, **k: clicked.append(a)

        with mock.patch("time.sleep"):
            self.assertTrue(self.c._run_mapped_logout_sequence())
        self.assertGreaterEqual(len(clicked), 1)


class HomeBeforeLogoutTest(_GuardCase):
    def test_home_needs_no_navigation(self):
        self.c._get_last_scene_name = lambda: "Home"

        def unexpected():
            raise AssertionError("Home must not be re-navigated")

        self.c._navigate_to_home = unexpected
        self.assertTrue(self.c._ensure_home_before_logout())

    def test_an_event_page_is_left_for_home_before_logout(self):
        self.c._get_last_scene_name = lambda: "EventLanding"
        calls = []
        self.c._navigate_to_home = lambda: calls.append(1) or True
        self.assertTrue(self.c._ensure_home_before_logout())
        self.assertEqual(calls, [1])

    def test_a_failed_home_navigation_aborts_the_switch(self):
        self.c._get_last_scene_name = lambda: "EventLanding"
        self.c._navigate_to_home = lambda: False
        self.assertFalse(self.c._ensure_home_before_logout())


if __name__ == "__main__":
    unittest.main()
