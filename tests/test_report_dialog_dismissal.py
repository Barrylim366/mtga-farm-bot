"""Arena's "Report a Player" dialog must be cancelled, never submitted.

The dialog is not a game prompt and no game message announces it, so the bot is
blind to it: while it is up, every screen probe simply fails. It happened twice
on 2026-08-21. The first time the trigger was found and blocked -- a blind
Home-tab click at (104, 39) landing on the opponent's name on the match-end
screen (see test_navigate_home_guard). The second time, at 19:53, there was no
logged click at all between the last DISMISS_END_SCREEN and the open dialog, so
the trigger is unknown and guarding another click path would not have helped.
Both times the bot then failed every probe until the rope expired.

The stall alone would be survivable. What makes this worth a dedicated net is
the Submit Report button sitting next to Cancel, pointed at a real player: the
tests below pin down that Submit can never be the thing that gets clicked.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller

# Measured from the live capture of the dialog (1920x1080 game frame).
CANCEL_CENTRE = (797, 875)
SUBMIT_CENTRE = (1122, 875)


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._arena_region = (0, 0, 1920, 1080)
    c._ensure_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
    c._map_base_point_into_arena = lambda arena, point: point
    return c


class ReportDialogDismissalTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        self.clicks = []
        self.image_clicks = []
        self.c._click_abs = lambda x, y, label: self.clicks.append((x, y, label))

    def _wire(self, *, title_found, cancel_click_ok):
        def locate(path, label, **kwargs):
            if "report_player_title" in path:
                return (960, 205) if title_found else None
            return None

        def click_image(path, label, **kwargs):
            self.image_clicks.append((os.path.basename(path), kwargs.get("rel_region")))
            if "report_player_cancel" in path:
                return cancel_click_ok
            return False

        self.c._locate_image_center_in_scaled_arena_region = locate
        self.c._click_image_in_scaled_arena_region = click_image

    def test_the_dialog_is_cancelled_via_the_button_template(self):
        self._wire(title_found=True, cancel_click_ok=True)
        self.assertTrue(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(
            [name for name, _ in self.image_clicks], ["report_player_cancel.png"]
        )
        self.assertEqual(self.clicks, [], "fell back to a fixed click unnecessarily")

    def test_the_search_band_cannot_contain_the_submit_button(self):
        """The one assertion that matters. The Cancel template is 285px wide, so
        a match must fit entirely inside the search band; Submit Report starts
        around x=988 and could only match from 988 to 1273."""
        self._wire(title_found=True, cancel_click_ok=True)
        self.c._dismiss_report_player_dialog(context="TEST")
        _, rel_region = self.image_clicks[0]
        x, _, w, _ = rel_region
        self.assertLessEqual(
            x + w, 1000,
            "the search band reaches into the Submit Report button",
        )
        self.assertLess(x + w, SUBMIT_CENTRE[0], "band overlaps Submit's centre")

    def test_the_fallback_click_is_cancel_and_nothing_else(self):
        self._wire(title_found=True, cancel_click_ok=False)
        self.assertTrue(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(len(self.clicks), 1)
        x, y, _ = self.clicks[0]
        self.assertEqual((x, y), CANCEL_CENTRE)
        self.assertNotEqual((x, y), SUBMIT_CENTRE)

    def test_nothing_is_clicked_when_the_dialog_is_not_there(self):
        self._wire(title_found=False, cancel_click_ok=True)
        self.assertFalse(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.image_clicks, [], "hunted for Cancel with no dialog up")

    def test_no_absolute_click_without_an_arena_region(self):
        """A fixed-point fallback with no arena region would be a desktop click."""
        self._wire(title_found=True, cancel_click_ok=False)
        self.c._ensure_arena_region = lambda *a, **k: None
        self.assertFalse(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(self.clicks, [])


class BlockingOverlayChecksTheDialogFirstTest(unittest.TestCase):
    """The report dialog is checked before the card-viewer templates: it is the
    only thing covering the hand that can do damage outside the game."""

    def setUp(self):
        self.c = make_controller()
        self.order = []
        self.c._dismiss_report_player_dialog = lambda **k: (
            self.order.append("report") or self.found
        )
        self.c._click_image_in_scaled_arena_region = lambda *a, **k: (
            self.order.append("done_button") or False
        )
        self.found = False

    def test_the_report_dialog_is_probed_before_the_done_templates(self):
        self.c._dismiss_blocking_overlay(context="TEST")
        self.assertEqual(self.order[0], "report")

    def test_a_dismissed_report_dialog_short_circuits(self):
        self.found = True
        self.assertTrue(self.c._dismiss_blocking_overlay(context="TEST"))
        self.assertEqual(self.order, ["report"], "kept clicking after the fix")

    def test_a_stop_request_prevents_every_probe(self):
        self.c._stop_requested = True
        self.assertFalse(self.c._dismiss_blocking_overlay(context="TEST"))
        self.assertEqual(self.order, [])


class TemplateAssetsExistTest(unittest.TestCase):
    """The dismissal is a no-op without its templates, and a silent no-op is
    exactly how this failure hid in the first place."""

    def test_both_templates_are_installed(self):
        for name in ("report_player_title.png", "report_player_cancel.png"):
            path = os.path.join(ROOT, "assets", "assert", name)
            with self.subTest(asset=name):
                self.assertTrue(os.path.exists(path), f"missing template: {path}")
                self.assertGreater(os.path.getsize(path), 500, "template looks empty")


if __name__ == "__main__":
    unittest.main()
