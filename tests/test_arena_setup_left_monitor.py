"""An Arena window on a monitor left of or above the primary is named as such.

Screen captures start at the primary monitor's origin (the virtual-screen grab
is cut there, see tests/test_vision_virtual_screen_origin.py), so a window at
a negative screen coordinate is never in a capture. Before this check the
setup failed on the anchors instead -- capture() clamps the region to the
primary, so the match ran against the wrong pixels -- which read as a wrong
screen or a wrong language. Reported by a reviewer who put the Arena on a
second monitor and got "weird failures".

The Windows detector is driven directly here, with the Win32 lookups replaced,
so the tests run on every platform.
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vision import window_locator as wl
from vision.vision import VisionEngine


def candidate(x, y, w=1600, h=900):
    rect = wl.WindowRect(x=x, y=y, w=w, h=h)
    return {"hwnd": 1, "title": "MTGA", "client_rect": rect, "window_rect": rect, "score": 0.0}


class LeftMonitorTest(unittest.TestCase):
    def detect(self, window, virtual_screen, anchors):
        provider = wl.ArenaRegionProvider(vision=VisionEngine(), assets_dir="x", expected_size=(1920, 1080))
        provider._verify_region_with_any_anchor = lambda region: anchors.append(region) or ("home_anchor.png", [])
        with patch.object(wl, "_list_mtga_window_rects_windows", lambda: [window]), \
             patch.object(wl, "_visible_screen_bounds_physical", lambda: virtual_screen), \
             patch.object(wl, "_get_windows_display_scaling_percent", lambda: 100):
            return provider._detect_windows()

    def test_a_window_on_the_monitor_left_of_the_primary_is_reported_as_such(self):
        anchors = []
        # Three-monitor desk, one display at x = -1920; the Arena sits on it.
        result = self.detect(candidate(x=-1920 + 160, y=49), (-1920, 0, 3840, 1080), anchors)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "window_left_of_primary")
        self.assertIn("left of or above the primary monitor", result.message)
        self.assertIn("(-1760,49)", result.message)
        self.assertEqual(anchors, [], "the anchors must not be consulted: the capture cannot reach that window")

    def test_a_window_above_the_primary_is_reported_too(self):
        anchors = []
        result = self.detect(candidate(x=160, y=-1080 + 49), (0, -1080, 1920, 1080), anchors)
        self.assertEqual(result.code, "window_left_of_primary")
        self.assertEqual(anchors, [])

    def test_a_window_on_the_monitor_right_of_the_primary_goes_on_to_the_anchors(self):
        """Monitors right of and below the primary are inside the captures, so
        the Arena may sit there: the check lets it through to the anchor match."""
        anchors = []
        result = self.detect(candidate(x=1920 + 160, y=49), (0, 0, 3840, 1080), anchors)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.code, "ok")
        self.assertEqual(anchors, [(2080, 49, 1600, 900)])

    def test_a_few_pixels_past_the_left_edge_are_tolerated(self):
        """The off-screen check tolerates 4 px past the edge; so does this one,
        so a window dragged two pixels too far left is not sent to another monitor."""
        anchors = []
        result = self.detect(candidate(x=-2, y=49), (0, 0, 1920, 1080), anchors)
        self.assertEqual(result.code, "ok")
        self.assertEqual(len(anchors), 1)


if __name__ == "__main__":
    unittest.main()
