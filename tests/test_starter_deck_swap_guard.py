"""A deck that cannot be selected must not be queued as the old deck.

Live case (2026-11-11, mycoides): the win pass correctly chose WB, but the deck
chooser could not be opened. The swap was best-effort by design, so the queue
pressed Play anyway and the match ran on the expired GW deck (the raw game state
showed white/green cards in our opening hand). These tests pin the new rule:
skip the queue attempt, retry, and fall back only after a bounded number of
misses so an unreachable chooser cannot stop the bot playing altogether.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


class _SwapBase(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        self.c = Controller(f.name)
        self.c.input = Mock()
        self.c._click_abs = Mock()
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = Mock(return_value=True)
        self.c._starter_navigation_may_act = Mock(return_value=True)
        self.c._ensure_arena_region = Mock(return_value=(0, 0, 1920, 1080))
        self.c._choose_starter_deck_template = Mock(
            return_value=os.path.join("C:/decks", "WB.PNG")
        )
        self.c._starter_deck_grid_point = Mock(return_value=(100, 200))
        self.c._click_starter_back_arrow = Mock()
        self.c._starter_event_page_confirmed = Mock(return_value=False)
        self.c._starter_template_visible = Mock(return_value=False)
        self.c._detect_starter_screen = Mock(return_value=self.c._STARTER_SCREEN_PLAY)
        self.c._current_account_screen_name = "player#1"
        timer = patch("Controller.MTGAController.Controller.threading.Timer")
        timer.start()
        self.addCleanup(timer.stop)
        sleep = patch("Controller.MTGAController.Controller.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)


class SwapResultTests(_SwapBase):
    def test_no_colour_target_means_nothing_to_select(self):
        self.c._choose_starter_deck_template = Mock(return_value=None)
        self.c._open_starter_deck_chooser = Mock(return_value=False)
        self.assertTrue(self.c._swap_starter_deck_for_quest(""))
        self.c._open_starter_deck_chooser.assert_not_called()

    def test_unreachable_chooser_is_a_failure(self):
        self.c._open_starter_deck_chooser = Mock(return_value=False)
        self.assertFalse(self.c._swap_starter_deck_for_quest("WB"))

    def test_verified_submit_is_a_success(self):
        self.c._open_starter_deck_chooser = Mock(return_value=True)
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=(500, 500))
        self.c._detect_starter_screen = Mock(return_value=self.c._STARTER_SCREEN_PLAY)
        self.assertTrue(self.c._swap_starter_deck_for_quest("WB"))
        self.assertEqual(self.c._starter_selected_deck, ("player#1", "WB"))

    def test_a_chooser_that_stays_open_is_a_failure(self):
        self.c._open_starter_deck_chooser = Mock(return_value=True)
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=(500, 500))
        self.c._detect_starter_screen = Mock(return_value=self.c._STARTER_SCREEN_CHOOSER)
        self.assertFalse(self.c._swap_starter_deck_for_quest("WB"))
        self.c._click_starter_back_arrow.assert_called()


class BoundedRetryTests(_SwapBase):
    def test_two_misses_skip_the_queue_and_the_third_falls_back(self):
        self.c._swap_starter_deck_for_quest = Mock(return_value=False)
        self.assertFalse(self.c._prepare_starter_deck_for_queue("WB"))
        self.assertFalse(self.c._prepare_starter_deck_for_queue("WB"))
        # Third consecutive miss: playing on the old deck beats never queueing.
        self.assertTrue(self.c._prepare_starter_deck_for_queue("WB"))
        self.assertEqual(self.c._starter_swap_failures, 0)

    def test_a_successful_swap_resets_the_budget(self):
        self.c._swap_starter_deck_for_quest = Mock(return_value=False)
        self.assertFalse(self.c._prepare_starter_deck_for_queue("WB"))
        self.c._swap_starter_deck_for_quest = Mock(return_value=True)
        self.assertTrue(self.c._prepare_starter_deck_for_queue("WB"))
        self.assertEqual(self.c._starter_swap_failures, 0)

    def test_no_swap_is_attempted_when_navigation_may_not_act(self):
        self.c._starter_navigation_may_act = Mock(return_value=False)
        self.c._swap_starter_deck_for_quest = Mock(return_value=True)
        self.assertFalse(self.c._prepare_starter_deck_for_queue("WB"))
        self.c._swap_starter_deck_for_quest.assert_not_called()


class QueueAbortTests(_SwapBase):
    def setUp(self):
        super().setUp()
        self.c._prepare_starter_deck_for_queue = Mock(return_value=False)
        self.c._refresh_quests_cache = Mock()
        self.c._resolve_starter_target_colors = Mock(return_value="WB")
        self.c._find_event_banner_scrolling = Mock(return_value=True)
        self.c._press_starter_event_play = Mock(return_value=True)
        self.c._switch_became_due_before_play = Mock(return_value=False)
        self.c._dismiss_match_end_screen = Mock(return_value=False)
        self.c._foreign_event_landing_name = Mock(return_value=None)
        self.c._home_screen_confirmed = Mock(return_value=True)
        self.buttons = tempfile.TemporaryDirectory()
        self.addCleanup(self.buttons.cleanup)
        open(os.path.join(self.buttons.name, "event_play.png"), "w").close()
        self.c._buttons_dir = Mock(return_value=self.buttons.name)

    def test_the_re_queue_does_not_press_play_when_the_deck_cannot_be_selected(self):
        self.c._on_starter_event_landing_page = Mock(return_value=True)
        self.c._starter_event_page_confirmed = Mock(return_value=True)
        self.assertFalse(self.c._queue_from_event_landing("WB"))
        labels = [call.args[1] for call in self.c._click_image_in_scaled_arena_region.call_args_list]
        self.assertNotIn("STARTER_EVENT_PLAY", labels)

    def test_the_full_navigation_does_not_press_play_when_the_deck_cannot_be_selected(self):
        self.c._on_starter_event_landing_page = Mock(return_value=False)
        self.c._cached_quests = [{"id": "q"}]
        self.assertFalse(self.c._navigate_starter_deck())
        self.c._press_starter_event_play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
