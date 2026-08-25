"""Unit tests for the two overlay probes added to the cast retry path.

Measured on 2026-08-25 across 31 hand-sweep failure bundles (mean brightness of
the hand zone separates the groups cleanly):

  5/31   Arena's "Report a Player" dialog was open mid-match (brightness 2.8-4.4)
  5/31   a card-selection overlay -- a graveyard browser opened by an additional
         cost -- was left open with its Done button unanswered (brightness 16-31)
  21/31  the board was fully visible with the cards demonstrably on the scan line
         (brightness 68-104); still unexplained, and deliberately not addressed
         here

Neither of the two acts on the sweep's verdict alone: each fires only when its
own template matches on screen. That is the difference from the rescue reverted
in 1.3.0, which reached for a Done button because the sweep had failed. These
tests pin that distinction -- a clear board must produce no click -- and pin the
cost bound (first failed attempt only, not all three).

Nothing here touches a real screen: both template helpers are stubbed, per the
screen-isolation rule in CLAUDE.md.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


class _Pos:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _FakeInput:
    """Cursor never moves and no hover ever arrives, so every hand scan exits via
    SCAN_STOPPED -- which is exactly the path the probes hang off."""

    def __init__(self):
        self._pos = _Pos(0, 0)

    def position(self):
        return self._pos

    def move_abs(self, x, y):
        self._pos = _Pos(x, y)

    def move_rel(self, dx, dy):
        self._pos = _Pos(self._pos.x, self._pos.y)

    def left_click(self, n=1):
        pass

    def left_down(self):
        pass

    def left_up(self):
        pass

    def tap_enter(self):
        pass

    def tap_shift_enter(self):
        pass


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c.input = _FakeInput()
    c._get_hand_scan_points_mapped = lambda **k: ((0, 0), (0, 0))
    c._ensure_options_overlay_closed = lambda **k: True
    c._write_hand_select_debug_bundle = lambda **k: None
    c.log_reader.has_new_line = lambda pattern: False
    c.log_reader.clear_new_line_flag = lambda pattern: None
    # Keep the suite off the real screen: a live template match here would click
    # into the running MTGA for real. See CLAUDE.md.
    c._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
    c._click_image_in_scaled_arena_region = lambda *a, **k: False
    return c


class StrayDoneOverlayTest(unittest.TestCase):
    """The graveyard/selection overlay case: 5 of 31 failures, and invisible to
    every existing net (casting_time_options_open was False each time and the
    session logged zero CASTING_TIME_OPTION_UNANSWERED)."""

    def setUp(self):
        self.c = make_controller()

    def test_a_clear_board_produces_no_click(self):
        """The whole point of gating on the template: with no overlay up, this
        must be a no-op. A rescue that clicks because the sweep failed is what
        got reverted in 1.3.0."""
        calls = []
        self.c._click_image_in_scaled_arena_region = (
            lambda *a, **k: calls.append(a) or False
        )
        self.assertFalse(self.c._dismiss_stray_done_overlay(context="test"))

    def test_a_matching_done_button_is_clicked(self):
        self.c._click_image_in_scaled_arena_region = lambda *a, **k: True
        self.assertTrue(self.c._dismiss_stray_done_overlay(context="test"))

    def test_it_searches_the_bottom_centre_band_where_done_sits(self):
        """Measured at arena ~(960, 875) -- the same place scry and surveil put
        their Done button, which is why the same template is reused."""
        seen = {}

        def fake(template, label, **kw):
            seen["label"] = label
            seen["rel_region"] = kw.get("rel_region")
            return False

        self.c._click_image_in_scaled_arena_region = fake
        self.c._dismiss_stray_done_overlay(context="ctx")
        x, y, w, h = seen["rel_region"]
        self.assertTrue(x <= 960 <= x + w, f"x band {x}..{x + w} misses 960")
        self.assertTrue(y <= 875 <= y + h, f"y band {y}..{y + h} misses 875")
        self.assertIn("ctx", seen["label"])

    def test_a_missing_template_file_is_not_an_error(self):
        self.c._buttons_dir = lambda: os.path.join(tempfile.gettempdir(), "no-such-dir")
        self.assertFalse(self.c._dismiss_stray_done_overlay(context="test"))


class CastProbeBudgetTest(unittest.TestCase):
    """Both probes cost a template scan even when nothing is there, and cast()
    retries up to three times against a 150s inactivity timer. So they run on
    the first failed attempt only."""

    def setUp(self):
        self.c = make_controller()
        self.report = []
        self.done = []
        self.c._dismiss_report_player_dialog = (
            lambda **k: self.report.append(k) or False
        )
        self.c._dismiss_stray_done_overlay = lambda **k: self.done.append(k) or False
        self.c._dismiss_are_you_sure_if_present = lambda **k: False

    def test_each_probe_runs_once_per_failed_cast_not_once_per_attempt(self):
        self.assertFalse(self.c.cast(999))
        self.assertEqual(len(self.report), 1, f"report probe ran {len(self.report)}x")
        self.assertEqual(len(self.done), 1, f"done probe ran {len(self.done)}x")

    def test_the_probes_name_the_card_so_the_log_joins_up(self):
        self.c.cast(999)
        self.assertIn("999", str(self.report[0].get("context")))
        self.assertIn("999", str(self.done[0].get("context")))

    def test_a_cast_that_hovers_the_card_probes_nothing(self):
        """No failure, no evidence, no probe -- and no cost."""
        self.c._cast_once = lambda card_id, attempt=0: True
        self.assertTrue(self.c.cast(999))
        self.assertEqual(self.report, [])
        self.assertEqual(self.done, [])


if __name__ == "__main__":
    unittest.main()
