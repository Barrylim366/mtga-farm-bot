"""Unit tests for the "In Progress" fallback in _navigate_starter_deck.

Background: when MTGA launches a batch of new events the Events grid grows and
pushes the Starter Deck Duel banner below the fold. No template match can find a
banner that is not rendered, so the queue loop failed every cycle until a human
scrolled. The fix uses the "In Progress" category in the right-hand menu: it
shortens the grid to the events we are actually playing, bringing the banner back
on screen.

Why the category is clicked by COORDINATE and not by template (observed live,
2026-07-27): in_progress_anchor.PNG was captured with its row selected -- filled
orange diamond, highlighted background -- so what it matches is "the selected
row", whichever one that is. With "All" selected it matched the All row, and the
bot clicked All on every single navigation cycle: it was clearing the filter
while logging "In Progress filter selected", and that false success suppressed
the fallback entirely.

    [CLICK] (2155, 397) - STARTER_IN_PROGRESS  arena=(429, 156, 1920, 1080)
    -> game-relative (1726, 241), i.e. the "All" row at y=244, not y=304.

The vision layer is stubbed out -- MTGA is not scriptable, so the template
matching itself is not under test; the decision flow is.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


class StarterBannerFilterTests(unittest.TestCase):
    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.c = Controller(log.name)

        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

        # Land on the Events page with nothing blocking it.
        self.c._dismiss_reward_popup = lambda: False
        self.c._dismiss_match_end_screen = lambda: False
        self.c._get_state_from_log = lambda: "home"
        self.c._cached_quests = [{"id": "q1"}]
        self.c._cached_active_colors = "GW"
        self.c.refresh_quests_cache = lambda: None
        self.swaps: list[str] = []
        self.c._swap_starter_deck_for_quest = lambda colors: self.swaps.append(colors)

        # Labels of every template click attempt, and the kwargs each was made
        # with, so tests can assert on the ROI a given pass searched.
        self.attempts: list[str] = []
        self.kwargs_by_label: dict[str, dict] = {}
        # Labels the stub reports as "clicked"; STARTER_EVENTS is always there so
        # navigation reaches the banner search.
        self.hits = {"STARTER_EVENTS"}

        def click_tpl(image_path, label, **kw):
            self.attempts.append(label)
            self.kwargs_by_label[label] = kw
            return label in self.hits

        self.c._click_image_in_scaled_arena_region = click_tpl
        # The selected-row read-back is diagnostics only; silence it by default.
        self.c._locate_image_center_in_scaled_arena_region = lambda *a, **kw: None

        # Coordinate clicks (the category menu) go through _click_abs.
        self.coord_clicks: list[str] = []
        self.c._click_abs = lambda x, y, tag, **kw: self.coord_clicks.append(tag)
        self.c._map_abs_point_to_arena = lambda point, **kw: (point, "test")

    def test_visible_banner_touches_no_category(self):
        """The common case. Nothing may be clicked in the menu here -- the bug was
        precisely a stray category click on every cycle."""
        self.hits |= {"STARTER_BANNER"}

        self.assertTrue(self.c._navigate_starter_deck())
        self.assertEqual(self.coord_clicks, [])
        self.assertNotIn("STARTER_BANNER_FILTERED", self.attempts)
        self.assertEqual(self.swaps, ["GW"])

    def test_banner_below_the_fold_is_found_after_filtering(self):
        """The fix: banner missing -> click "In Progress" by position -> found."""
        self.hits |= {"STARTER_BANNER_FILTERED"}

        self.assertTrue(self.c._navigate_starter_deck())
        self.assertEqual(self.coord_clicks, ["EVENT_CATEGORY_IN_PROGRESS"])
        self.assertEqual(
            self.attempts,
            [
                "STARTER_PLAY",
                "STARTER_EVENTS",
                "STARTER_BANNER",
                "STARTER_BANNER_FILTERED",
                "STARTER_PLAY_CONFIRM",
            ],
        )
        self.assertEqual(self.swaps, ["GW"], "the deck swap must still run before Play")

    def test_the_category_template_is_never_used_to_click(self):
        """in_progress_anchor.PNG follows the highlight, so a click driven by it
        lands on whatever row happens to be selected. It must not be on the click
        path at any point."""
        self.c._navigate_starter_deck()

        for label in self.attempts:
            self.assertNotIn("IN_PROGRESS", label, f"{label} clicks a matched category")

    def test_all_view_is_restored_when_the_banner_is_still_missing(self):
        """An event never entered is not "in progress", so the filter HIDES it --
        and MTGA remembers the category between visits. Leaving it applied would
        make every later cycle search a list the banner cannot be in."""
        self.assertFalse(self.c._navigate_starter_deck())
        self.assertEqual(
            self.coord_clicks,
            ["EVENT_CATEGORY_IN_PROGRESS", "EVENT_CATEGORY_ALL"],
        )
        self.assertEqual(self.swaps, [], "never queue a deck we could not select")

    def test_all_view_is_not_restored_when_the_banner_was_found(self):
        self.hits |= {"STARTER_BANNER_FILTERED"}

        self.c._navigate_starter_deck()
        self.assertNotIn("EVENT_CATEGORY_ALL", self.coord_clicks)

    def test_both_banner_passes_search_the_same_region(self):
        """The filter changes WHAT is on screen, not where we look for it."""
        self.c._navigate_starter_deck()

        first = self.kwargs_by_label["STARTER_BANNER"]
        second = self.kwargs_by_label["STARTER_BANNER_FILTERED"]
        self.assertEqual(first["rel_region"], Controller._STARTER_BANNER_ROI)
        self.assertEqual(second["rel_region"], Controller._STARTER_BANNER_ROI)
        self.assertEqual(first["confidence"], second["confidence"])

    def test_category_positions_match_the_observed_menu(self):
        """Measured from the 2026-07-27 capture: rows 60px apart, "All" first and
        "In Progress" directly below. Both must sit inside the menu ROI, which is
        where the selected-row read-back looks for them."""
        ys = Controller._EVENT_CATEGORY_Y
        self.assertEqual(ys["in_progress"] - ys["all"], 60)

        rx, ry, rw, rh = Controller._EVENT_MENU_ROI
        self.assertTrue(rx <= Controller._EVENT_CATEGORY_X <= rx + rw)
        for y in ys.values():
            self.assertTrue(ry <= y <= ry + rh, f"category y={y} outside the menu ROI")

    def test_unknown_category_is_never_clicked(self):
        self.assertFalse(self.c._click_event_category("nope"))
        self.assertEqual(self.coord_clicks, [])


class SelectedCategoryReadbackTests(unittest.TestCase):
    """The read-back turns the template's one real property -- it follows the
    highlight -- into a check that the coordinate click landed on the intended
    row. It is diagnostics: it must never change what navigation does."""

    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.c = Controller(log.name)
        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

        self.c._click_abs = lambda x, y, tag, **kw: None
        self.c._map_abs_point_to_arena = lambda point, **kw: (point, "test")
        # A 1:1 arena at the origin keeps the y conversion readable.
        self.c._arena_region = (0, 0, 1920, 1080)

        self.errors: list[str] = []
        patcher2 = patch(
            "Controller.MTGAController.Controller.bot_logger.log_error",
            side_effect=lambda msg: self.errors.append(msg),
        )
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def _selected_at(self, y):
        self.c._locate_image_center_in_scaled_arena_region = (
            lambda *a, **kw: None if y is None else (1700, y)
        )

    def test_expected_row_selected_is_quiet(self):
        self._selected_at(Controller._EVENT_CATEGORY_Y["in_progress"])
        self.assertTrue(self.c._click_event_category("in_progress"))
        self.assertEqual(self.errors, [])

    def test_wrong_row_selected_is_reported(self):
        """Exactly the live failure: "All" ends up highlighted after we aimed at
        "In Progress". Silent success is what cost a whole session."""
        self._selected_at(Controller._EVENT_CATEGORY_Y["all"])
        self.assertTrue(self.c._click_event_category("in_progress"))
        self.assertEqual(len(self.errors), 1)
        self.assertIn("in_progress", self.errors[0])

    def test_probe_failure_does_not_fail_the_click(self):
        self._selected_at(None)
        self.assertTrue(self.c._click_event_category("in_progress"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
