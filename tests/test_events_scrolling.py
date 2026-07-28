"""Unit tests for finding an event banner in a scrollable Events list.

MTGA reorders the Events list as events come and go, and the list is taller than
the viewport. Two things follow, both observed against a live client:

  * A banner can sit below the fold entirely, or -- worse, because it looks
    present -- be CLIPPED by the bottom edge. The Starter Deck Duel template
    includes its "Starter Deck Duel / Resume" label strip, so a banner whose
    strip is cut off does not match at any usable confidence.
  * The list ignores the mouse wheel. Dragging the scrollbar is the only way to
    move it, which is why the code drags a thumb instead of scrolling.

The step size is the subtle part: the thumb is about a quarter of its track, so
the list moves ~4x whatever the thumb does, and a step larger than one banner
steps straight over the banner being searched for. Measured live, a 170px step
jumped from just above the matchable band to just below it and never matched.

Screen interaction is stubbed here; the live behaviour these encode was verified
against a real client separately.
"""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


def band(height: int, thumb: tuple[int, int] | None, *, width: int = 30,
         thumb_width: int = 10) -> np.ndarray:
    """A scrollbar-band capture: dark chrome, with an optional bright thumb.

    Brightness and width are taken from a real client: chrome around 22, thumb
    around 170, and the thumb covering only ~10 of the band's 30 columns. Those
    proportions are the point -- a mean across the width lands at ~71, under the
    detection floor, while the per-row maximum sits at 170, well over it. Draw the
    thumb any wider or brighter and the test stops reproducing the regression that
    made detection return None on the real UI.
    """
    img = np.full((height, width, 3), 22, dtype=np.uint8)
    if thumb is not None:
        top, bottom = thumb
        left = max(0, (width - thumb_width) // 2)
        img[top:bottom, left:left + thumb_width] = 170
    return img


class _RecordingInput:
    """Records input calls, and moves the simulated scrollbar the way a real one
    responds: the thumb follows the cursor 1:1 while the button is held (measured
    against a live client -- a 250px drag moved the thumb exactly 250px).

    Modelling that is what lets the tests exercise the real drag body, including
    its "did the thumb actually move" end-of-list test, which reads the screen
    back rather than trusting arithmetic.
    """

    def __init__(self, owner=None, fail_on_move=False):
        self.owner = owner
        self.calls = []
        self.down = False
        self.fail_on_move = fail_on_move
        self._press_y = None
        self._last_y = None

    def move_abs(self, x, y):
        self.calls.append(("move", x, y))
        if self.fail_on_move:
            raise RuntimeError("input backend exploded")
        if self.down:
            if self._press_y is None:
                self._press_y = y
            self._last_y = y
            if self.owner is not None:
                self.owner._slide_thumb(y - self._press_y)
                self._press_y = y

    def left_down(self):
        self.calls.append(("down",))
        self.down = True
        self._press_y = None

    def left_up(self):
        self.calls.append(("up",))
        self.down = False
        self._press_y = None


class _Stub(Controller):
    """A Controller with the screen replaced by a scripted scrollbar.

    The drag itself is REAL -- only the pixels, the clock and the input backend
    are stubbed. An earlier version of this file overrode _drag_events_scrollbar
    wholesale, which meant deleting `self.input.left_up()` from production left
    the suite green: the single most safety-critical line in the change had no
    coverage at all.
    """

    def __init__(self, thumb=(100, 300), band_h=900, on_events_page=True, input_=None,
                 thumb_width=10):
        self._band_h = band_h
        self._thumb = thumb
        self._thumb_width = thumb_width
        self.drags = []
        self.probe_results = []
        self.probes = []
        self._stop_requested = False
        self._on_events = on_events_page
        self.input = input_ or _RecordingInput(owner=self)

    def _slide_thumb(self, dy: int) -> None:
        """Move the simulated thumb, clamped to its track like a real one."""
        if self._thumb is None or not dy:
            return
        top, bottom = self._thumb
        new_top = max(0, min(self._band_h - (bottom - top), top + dy))
        self._thumb = (new_top, new_top + (bottom - top))

    # -- screen stand-ins ------------------------------------------------
    def _ensure_arena_region(self, force_reacquire=False):
        return (0, 0, 1920, 1080)

    def _scale_base_region_to_arena(self, arena, rel):
        return tuple(int(v) for v in rel)

    def _on_events_page(self):
        return self._on_events

    @property
    def _vision(self):
        outer = self

        class _V:
            def begin_tick(self):
                pass

            def capture(self, region):
                return band(outer._band_h, outer._thumb, thumb_width=outer._thumb_width)

        return _V()

    def _click_image_in_scaled_arena_region(self, image_path, label, *, rel_region=None,
                                            confidence=0.82, timeout=1.5):
        self.probes.append(label)
        return self.probe_results.pop(0) if self.probe_results else False


class _ScriptedDrag(_Stub):
    """Moves the thumb without any input, for tests about the SEARCH rather than
    the drag: they care which way and how often it scrolls, not how a drag is
    physically performed."""

    def _drag_events_scrollbar(self, dy_ref):
        self.drags.append(dy_ref)
        if self._thumb is None:
            return False
        top, bottom = self._thumb
        new_top = max(0, min(self._band_h - (bottom - top), top + dy_ref))
        if new_top == top:
            return False
        self._thumb = (new_top, new_top + (bottom - top))
        return True


class ThumbDetectionTest(unittest.TestCase):
    def test_finds_the_thumb(self):
        c = _Stub(thumb=(200, 420))
        x, top, bottom = c._locate_events_scrollbar_thumb()
        self.assertEqual((top, bottom), (110 + 200, 110 + 419))
        self.assertEqual(x, 1480 + 28 // 2)

    def test_a_thumb_narrower_than_the_band_is_still_found(self):
        """The regression that made detection return None: the thumb covers only
        part of the band's width, so a mean across the width averages it away."""
        c = _Stub(thumb=(300, 500))
        self.assertIsNotNone(c._locate_events_scrollbar_thumb())

    def test_no_scrollbar_reads_as_none(self):
        """A list short enough to fit draws no bar. That means "nothing to
        scroll", not "scroll blindly"."""
        c = _Stub(thumb=None)
        self.assertIsNone(c._locate_events_scrollbar_thumb())

    def test_bright_artwork_spanning_the_band_is_not_a_thumb(self):
        """The false positive that made this dangerous. Off the Events page this
        band cuts through the Home promo banners; measured there, 81% of rows
        cleared the brightness threshold and a 177-row "thumb" was returned on
        artwork -- a press point on a clickable banner. A real thumb fills a
        contiguous slice of the bar; artwork fills the whole width."""
        c = _Stub(thumb=(200, 500), thumb_width=30)   # spans the entire band
        self.assertIsNone(c._locate_events_scrollbar_thumb())

    def test_a_short_highlight_is_not_a_thumb(self):
        """Stray bright chrome must not be grabbed: dragging from it scrolls
        nothing, and "the thumb did not move" would then end the sweep early."""
        c = _Stub(thumb=(400, 410))
        self.assertIsNone(c._locate_events_scrollbar_thumb())


class DragTest(unittest.TestCase):
    """Exercises the real _drag_events_scrollbar body."""

    def setUp(self):
        import Controller.MTGAController.Controller as cm
        real_sleep, real_focus = cm.time.sleep, cm.focus_mtga_window
        cm.time.sleep = lambda _s: None
        cm.focus_mtga_window = lambda: False
        self.addCleanup(lambda: (setattr(cm.time, "sleep", real_sleep),
                                 setattr(cm, "focus_mtga_window", real_focus)))

    def test_the_button_is_released(self):
        c = _Stub(thumb=(100, 300))
        c._drag_events_scrollbar(40)
        self.assertFalse(c.input.down, "the mouse button was left held down")
        self.assertEqual(c.input.calls[-1], ("up",))

    def test_the_button_is_released_even_when_the_backend_throws(self):
        """A held button turns every later click into a drag across the whole UI,
        so the release has to survive the failure that caused it."""
        c = _Stub(thumb=(100, 300), input_=_RecordingInput(fail_on_move=True))
        self.assertFalse(c._drag_events_scrollbar(40))
        self.assertFalse(c.input.down)
        self.assertIn(("up",), c.input.calls)

    def test_a_backend_failure_does_not_propagate(self):
        """This runs on the queue loop's daemon thread, which has no handler --
        an escaping exception stops the bot playing altogether."""
        c = _Stub(thumb=(100, 300), input_=_RecordingInput(fail_on_move=True))
        self.assertFalse(c._drag_events_scrollbar(40))   # no exception

    def test_it_presses_on_the_middle_of_the_thumb(self):
        """Grabbing an edge means a small detection error lands on the track."""
        c = _Stub(thumb=(100, 300))
        detected = c._locate_events_scrollbar_thumb()
        c._drag_events_scrollbar(40)
        first_move = next(x for x in c.input.calls if x[0] == "move")
        self.assertEqual(first_move[2], (detected[1] + detected[2]) // 2)

    def test_it_refuses_to_drag_when_not_on_the_events_page(self):
        """The drag is the one input in this flow aimed by geometry rather than by
        a template match, so without this it would press at a fixed column on
        whatever screen replaced Events -- in a match, the battlefield."""
        c = _Stub(thumb=(100, 300), on_events_page=False)
        self.assertFalse(c._drag_events_scrollbar(40))
        self.assertEqual(c.input.calls, [], "it touched the mouse off the Events page")

    def test_it_reports_movement(self):
        c = _Stub(thumb=(100, 300))
        self.assertTrue(c._drag_events_scrollbar(40))

    def test_a_thumb_that_cannot_move_reads_as_end_of_list(self):
        """Already at the bottom of its track, dragging further down. This is the
        signal the sweep uses to stop, so it has to come from the screen rather
        than from arithmetic."""
        c = _Stub(thumb=(620, 900), band_h=900)   # top is already the maximum
        self.assertFalse(c._drag_events_scrollbar(40))

    def test_losing_sight_of_the_bar_is_not_end_of_list(self):
        """Distinct outcomes: a frame caught mid-repaint says nothing about the
        scroll position, and treating it as "the list ended" aborted rewinds."""
        c = _Stub(thumb=(100, 300))
        original = c._locate_events_scrollbar_thumb
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return None if calls["n"] > 1 else original()

        c._locate_events_scrollbar_thumb = flaky
        self.assertFalse(c._drag_events_scrollbar(40))
        self.assertFalse(c.input.down)


class ScrollStepTest(unittest.TestCase):
    def test_the_step_scales_with_the_thumb(self):
        """The step is in thumb space but the constraint (one banner) is in list
        space, and the ratio is the thumb's own length. A constant tuned at one
        list length silently becomes too coarse as the list grows."""
        c = _Stub()
        short = c._events_scroll_step((0, 0, 60))     # long list -> short thumb
        tall = c._events_scroll_step((0, 0, 300))     # short list -> tall thumb
        self.assertLess(short, tall)

    def test_it_never_returns_a_step_below_the_movement_tolerance(self):
        """"Did the thumb move" allows a few pixels of slop; a step under that
        reads as end-of-list and the sweep stops on its first iteration."""
        c = _Stub()
        self.assertGreater(c._events_scroll_step((0, 0, 3)), 3)

    def test_it_is_capped(self):
        c = _Stub()
        self.assertLessEqual(c._events_scroll_step((0, 0, 900)), Controller._EVENTS_SCROLL_STEP)


class BannerSearchTest(unittest.TestCase):
    """About the search strategy, so these use the scripted-drag stub."""

    def test_a_visible_banner_costs_nothing(self):
        """The normal case, and the reason the visible page is checked first: no
        drag, and the user's scroll position is left alone."""
        c = _ScriptedDrag()
        c.probe_results = [True]
        self.assertTrue(c._find_event_banner_scrolling("t.png", "L", (0, 0, 10, 10), 0.72))
        self.assertEqual(c.drags, [])

    def test_a_list_that_does_not_scroll_fails_without_dragging(self):
        c = _ScriptedDrag(thumb=None)
        c.probe_results = [False]
        self.assertFalse(c._find_event_banner_scrolling("t.png", "L", (0, 0, 10, 10), 0.72))
        self.assertEqual(c.drags, [])

    def test_the_sweep_starts_from_the_top(self):
        """A previous pass can leave the list BELOW the banner, and a
        downward-only sweep could never recover it."""
        c = _ScriptedDrag(thumb=(600, 820))
        c.probe_results = [False, False, True]
        self.assertTrue(c._find_event_banner_scrolling("t.png", "L", (0, 0, 10, 10), 0.72))
        self.assertTrue(
            any(d < 0 for d in c.drags),
            f"expected an upward drag before sweeping down, got {c.drags}",
        )

    def test_the_step_is_small_enough_not_to_skip_a_banner(self):
        """Live-measured: the band where the banner is fully on screen is ~60
        reference pixels of thumb travel. A step at or above that can land above
        it on one iteration and below it on the next, matching neither."""
        self.assertLess(Controller._EVENTS_SCROLL_STEP, 60)

    def test_the_sweep_can_cross_the_whole_track(self):
        """Step size and step count have to be chosen together, or a small step
        turns into a sweep that gives up halfway down the list."""
        reach = Controller._EVENTS_SCROLL_STEP * Controller._EVENTS_SCROLL_MAX_STEPS
        self.assertGreaterEqual(reach, Controller._EVENTS_SCROLLBAR_BAND[3] * 0.8)

    def test_it_gives_up_at_the_end_of_the_list(self):
        """The thumb refusing to move is the end-of-list signal; without it the
        sweep would drag uselessly MAX_STEPS times on every miss."""
        c = _ScriptedDrag(thumb=(0, 880))  # nearly fills the track -> cannot move far
        c.probe_results = [False] * 40
        self.assertFalse(c._find_event_banner_scrolling("t.png", "L", (0, 0, 10, 10), 0.72))
        self.assertLess(len(c.drags), Controller._EVENTS_SCROLL_MAX_STEPS + 6)

    def test_a_failed_search_rewinds_the_list(self):
        """Leaving the list at the bottom would mean the next attempt starts with
        nothing left below it to find."""
        c = _ScriptedDrag(thumb=(0, 220))
        c.probe_results = [False] * 60
        c._find_event_banner_scrolling("t.png", "L", (0, 0, 10, 10), 0.72)
        self.assertEqual(c._thumb[0], 0, "the list was not rewound to the top")

    def test_the_sweep_is_bounded_in_time(self):
        """The step count is a poor bound: each step costs a page probe, a drag
        and two banner probes. Unbounded, a miss stalls the queue loop for
        minutes between matches -- delaying the next game and any pending account
        switch, and going quiet long enough to trip the session watchdog."""
        import Controller.MTGAController.Controller as cm

        c = _ScriptedDrag(thumb=(0, 60), band_h=900)   # short thumb -> many steps
        c.probe_results = [False] * 200
        clock = {"t": 1000.0}
        real_time = cm.time.time
        cm.time.time = lambda: clock["t"]
        original = c._drag_events_scrollbar

        def slow(dy):
            clock["t"] += 5.0
            return original(dy)

        c._drag_events_scrollbar = slow
        try:
            c._find_event_banner_scrolling("t.png", "L", (0, 0, 10, 10), 0.72)
        finally:
            cm.time.time = real_time
        elapsed = clock["t"] - 1000.0
        self.assertLess(
            elapsed, Controller._EVENTS_SCROLL_BUDGET_SEC * 3,
            f"the search ran for {elapsed}s against a {Controller._EVENTS_SCROLL_BUDGET_SEC}s budget",
        )

    def test_a_stop_request_ends_the_sweep(self):
        c = _ScriptedDrag()
        c.probe_results = [False] * 60
        original = c._drag_events_scrollbar

        def stop_after_one(dy):
            c._stop_requested = True
            return original(dy)

        c._drag_events_scrollbar = stop_after_one
        c._find_event_banner_scrolling("t.png", "L", (0, 0, 10, 10), 0.72)
        self.assertLessEqual(len(c.drags), 3)


if __name__ == "__main__":
    unittest.main()
