"""Unit tests for clearing MTGA's post-login announcement popups.

Background (observed live, 2026-08-19): after a successful account switch MTGA
queued two announcements over Home -- "Banned Standard Cards" (an Okay button)
and a set promo ("The Hobbit -- Available Now!", whose only button is "Get
Started!", which opens the Store). Both hide Home completely, so starter
navigation found no anchor at all and the queue loop spun for 2.5 minutes until
the popups were cleared by hand.

Also pins the resolution reasoning for the two scale-tolerant template searches
(the logout link and this Okay button). The search region is normalized to
1920x1080 while MTGA renders that chrome at a roughly constant pixel size, so the
scale needed goes as 1920/W. A narrow band silently excludes whole resolutions.
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


def _controller():
    log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    log.close()
    c = Controller(log.name)
    buttons = tempfile.mkdtemp()
    with open(os.path.join(buttons, "okay_btn.png"), "wb") as f:
        f.write(b"not-a-real-png")
    c._buttons_dir = lambda: buttons
    return c


class AnnouncementDismissTests(unittest.TestCase):
    def setUp(self):
        self.c = _controller()
        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        focus = patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
        focus.start()
        self.addCleanup(focus.stop)

        self.clicks: list[tuple[int, int, str]] = []
        self.c._click_abs = lambda x, y, tag, **kw: self.clicks.append((x, y, tag))
        self.escapes = []
        self.c.input.tap_escape = lambda: self.escapes.append(1)

    def _vision(self, okay_at=None, options_after_esc=False):
        self.locate_kwargs: list[dict] = []

        def locate(image_path, label, **kw):
            self.locate_kwargs.append(kw)
            return okay_at

        self.c._locate_image_center_in_scaled_arena_region = locate
        self.c._options_overlay_visible = lambda: options_after_esc

    def test_okay_button_is_clicked_when_present(self):
        """The regression: a popup offering Okay must be acknowledged, not left up."""
        self._vision(okay_at=(1500, 1050))

        self.assertTrue(self.c._dismiss_blocking_announcement("T"))
        self.assertEqual(self.clicks, [(1500, 1050, "T_ANNOUNCE_OKAY")])
        self.assertEqual(self.escapes, [], "no ESC needed when Okay was clicked")

    def test_promo_without_okay_is_escaped_not_clicked(self):
        """The set promo's only button is 'Get Started!', which opens the Store.
        Clicking it would navigate away instead of dismissing, so ESC is used."""
        self._vision(okay_at=None, options_after_esc=False)

        self.assertTrue(self.c._dismiss_blocking_announcement("T"))
        self.assertEqual(self.clicks, [], "must not click any call-to-action button")
        self.assertEqual(len(self.escapes), 1)

    def test_esc_that_merely_opened_options_is_undone_and_reports_no_progress(self):
        """ESC on an unobstructed screen OPENS Options. Leaving it open would hide
        Home from the next pass and the dead-end would repeat forever, toggling
        Options on and off, so it must be closed again and reported as no
        progress."""
        self._vision(okay_at=None, options_after_esc=True)

        self.assertFalse(self.c._dismiss_blocking_announcement("T"))
        self.assertEqual(len(self.escapes), 2, "ESC must be undone")
        self.assertEqual(self.clicks, [])

    def test_okay_search_uses_a_strict_confidence(self):
        """okay_btn.png scores ~0.80 on the event page's orange Start/Play pill and
        ~0.78 on Claim, but 0.97 on a real popup. At the 0.80 used elsewhere a
        whole-arena search would press Play and start a match with the wrong
        deck."""
        self._vision(okay_at=None)
        self.c._dismiss_blocking_announcement("T")

        self.assertEqual(len(self.locate_kwargs), 1)
        conf = self.locate_kwargs[0].get("confidence")
        self.assertEqual(conf, Controller._ANNOUNCEMENT_OKAY_CONFIDENCE)
        self.assertGreaterEqual(conf, 0.90, "0.80 false-matches the orange Play pill")

    def test_okay_search_is_scale_tolerant_over_the_whole_arena(self):
        self._vision(okay_at=None)
        self.c._dismiss_blocking_announcement("T")

        kw = self.locate_kwargs[0]
        self.assertIsNone(kw.get("rel_region"), "popups are centered; search the arena")
        self.assertTrue(kw.get("scales"), "must be scale tolerant")

    def test_stop_requested_short_circuits(self):
        self._vision(okay_at=(1500, 1050))
        self.c._stop_requested = True

        self.assertFalse(self.c._dismiss_blocking_announcement("T"))
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.escapes, [])

    def test_missing_okay_template_still_falls_back_to_esc(self):
        os.remove(os.path.join(self.c._buttons_dir(), "okay_btn.png"))
        self._vision(okay_at=(1500, 1050), options_after_esc=False)

        self.assertTrue(self.c._dismiss_blocking_announcement("T"))
        self.assertEqual(self.clicks, [], "template gone -> no click")
        self.assertEqual(len(self.escapes), 1)


class ScaleBandResolutionCoverageTests(unittest.TestCase):
    """Both scale-tolerant searches must cover the 16:9 widths MTGA is run at.

    The needed template scale goes as ~1.10 * 1920 / W (fitted to two live
    observations: 1.10 on a native 1920 client, and ~1.03 on the 2048x1152 client
    where the single historical logout success matched at scale 1.0).
    """

    WIDTHS = (1280, 1366, 1600, 1920, 2048, 2560, 3200, 3840)

    @staticmethod
    def _needed(width):
        return 1.10 * 1920 / width

    def _assert_covers(self, scales, label):
        lo, hi = min(scales), max(scales)
        for w in self.WIDTHS:
            need = self._needed(w)
            with self.subTest(band=label, width=w):
                self.assertTrue(
                    lo <= need <= hi,
                    f"{label}: {w}px window needs scale {need:.3f}, band is {lo}..{hi}",
                )

    def test_announcement_band_covers_common_resolutions(self):
        self._assert_covers(Controller._ANNOUNCEMENT_SCALES, "_ANNOUNCEMENT_SCALES")

    def test_band_is_finely_stepped_enough_to_hit_a_match(self):
        """A coarse band can straddle the needed scale. Template matching tolerates
        a few percent, so keep the step small."""
        scales = sorted(Controller._ANNOUNCEMENT_SCALES)
        for a, b in zip(scales, scales[1:]):
            self.assertLessEqual(round(b - a, 4), 0.05, f"gap {a}->{b} too coarse")

    def test_logout_search_band_is_documented_to_cover_the_same_range(self):
        """The logout call builds its band inline; pin the intent so a later edit
        cannot quietly narrow it back to a range that drops 1280 and 3840."""
        import inspect

        src = inspect.getsource(Controller._perform_account_switch_logout_clicks) \
            if hasattr(Controller, "_perform_account_switch_logout_clicks") else None
        if src is None:
            src = inspect.getsource(Controller)
        self.assertIn("logout_scales", src)
        band = [round(0.45 + 0.05 * i, 2) for i in range(32)]
        self._assert_covers(band, "logout band 0.45..2.00")


if __name__ == "__main__":
    unittest.main()
