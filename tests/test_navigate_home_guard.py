"""The blind Home click must not fire outside the nav bar.

_navigate_to_home clicks a fixed 1920-frame point, (104, 39), because a template
match only works when Home is already the active tab. That point is the Home tab
*only while the nav bar is on screen*. On the match-end screen the same pixel is
the opponent's name, and clicking it opens Arena's player menu and then "Report a
Player" -- a modal with a Submit Report button, aimed at a real person, that no
image probe in this bot recognises.

Live on 2026-08-21: three GO_HOME clicks at 18:47:46, 18:47:52 and 18:47:59
(every one already flagged `risky` by the click recorder) landed on the opponent's
name while the end screen was still up. The Report dialog opened, every screen
probe failed against it for the next fifteen minutes, and the match the bot had
queued was lost on the rope. The gate below is therefore a safety check before it
is a stall fix: the cost of a wrong click here is a false report against a real
player, not a missed quest read.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._ensure_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
    c._map_base_point_into_arena = lambda arena, point: point
    return c


class NavigateHomeGuardTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        self.clicks = []
        self.c._click_abs = lambda x, y, label: self.clicks.append((x, y, label))
        self.probes = []

    def _probe(self, *, navbar, home_reached=True):
        """Stub the template matcher, recording which anchor was asked for."""

        def locate(path, label, **kwargs):
            self.probes.append(label)
            if label == "GO_HOME_NAVBAR_CHECK":
                return (100, 40) if navbar else None
            if label == "GO_HOME_VERIFY":
                return (100, 40) if home_reached else None
            return None

        self.c._locate_image_center_in_scaled_arena_region = locate

    def test_no_click_when_the_nav_bar_is_absent(self):
        """The match-end screen: (104, 39) is the opponent's name here."""
        self._probe(navbar=False)
        self.assertFalse(self.c._navigate_to_home())
        self.assertEqual(
            self.clicks, [],
            "clicked the opponent's name -- this is what opened Report a Player",
        )

    def test_the_nav_bar_is_checked_before_anything_is_clicked(self):
        self._probe(navbar=False)
        self.c._navigate_to_home()
        self.assertEqual(self.probes[:1], ["GO_HOME_NAVBAR_CHECK"])

    def test_the_click_still_happens_on_a_main_screen(self):
        """The gate must not break the navigation it protects."""
        self._probe(navbar=True)
        self.assertTrue(self.c._navigate_to_home())
        self.assertEqual([(c[0], c[1]) for c in self.clicks], [(104, 39)])
        self.assertEqual(self.clicks[0][2], "GO_HOME")

    def test_a_click_that_misses_home_is_still_reported_as_a_failure(self):
        """Nav bar present but Home not reached: unchanged behaviour, the caller
        falls back to the normal re-queue."""
        self._probe(navbar=True, home_reached=False)
        self.assertFalse(self.c._navigate_to_home())
        self.assertEqual(len(self.clicks), 1)

    def test_a_stop_request_beats_everything(self):
        self._probe(navbar=True)
        self.c._stop_requested = True
        self.assertFalse(self.c._navigate_to_home())
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.probes, [])

    def test_no_click_without_an_arena_region(self):
        self._probe(navbar=True)
        self.c._ensure_arena_region = lambda *a, **k: None
        self.assertFalse(self.c._navigate_to_home())
        self.assertEqual(self.clicks, [])


if __name__ == "__main__":
    unittest.main()
