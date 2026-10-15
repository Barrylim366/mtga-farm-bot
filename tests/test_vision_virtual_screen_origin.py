"""A monitor to the left of (or above) the primary must not shift every capture.

Measured 2026-10-15 on a three-monitor desk (one display at x=-1920): every
Arena setup check failed with `anchor_not_found` while the Arena sat on the
Home screen in English on the primary monitor. The debug bundle's
`arena_region.png` showed the *left* monitor -- a browser and the bot's own
windows -- at the Arena's coordinates.

The cause: `_grab_full_frame_uncached` grabs `mss.monitors[0]`, the whole
virtual screen, whose first column is virtual x = -1920 on that desk, while
`capture()`, `pixel_check()` and every `capture(None)` consumer index the
frame as if column 0 were screen x = 0 (the primary monitor's origin). With
no monitor left of or above the primary the two coincide, which is why it
works on most desks and silently captures the wrong monitor on this one.

The frame handed out therefore starts at virtual (0, 0): the part of the
grab lying left of or above the primary origin is dropped. Monitors to the
right of and below the primary keep working as before.
"""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vision import vision as vision_module
from vision.vision import VisionEngine


class FakeMss:
    """Stands in for mss.mss(): one virtual-screen monitor and one BGRA grab."""

    def __init__(self, left, top, width, height, scale=1):
        # The monitor rectangle is in logical units; `scale` > 1 makes the grab
        # come back in more pixels than that, as mss does on a Retina display.
        self.monitors = [{"left": left, "top": top, "width": width, "height": height}]
        # Encode the virtual (logical) coordinate of every pixel in its channels
        # so a test can tell exactly which part of the virtual screen it got.
        ys, xs = np.mgrid[0:height * scale, 0:width * scale]
        self.frame = np.zeros((height * scale, width * scale, 4), dtype=np.uint8)
        self.frame[:, :, 0] = ((xs // scale + left) // 16) % 256   # B: virtual x / 16
        self.frame[:, :, 1] = ((ys // scale + top) // 16) % 256    # G: virtual y / 16
        self.frame[:, :, 2] = 200
        self.frame[:, :, 3] = 255

    def grab(self, monitor):
        assert monitor is self.monitors[0]
        return self.frame

    def close(self):
        pass


def engine_with(fake):
    engine = VisionEngine()
    engine._mss_instance = fake
    engine._mss_failed_until = 0.0
    return engine


class VirtualScreenOriginTest(unittest.TestCase):
    def setUp(self):
        vision_module._last_frame = None
        vision_module._last_frame_ts = 0.0
        self.addCleanup(setattr, vision_module, "_last_frame", None)

    def test_a_monitor_left_of_the_primary_is_cut_off(self):
        fake = FakeMss(left=-1920, top=-2, width=3840, height=1082)
        frame = engine_with(fake)._grab_full_frame_uncached()
        self.assertEqual(frame.shape[:2], (1080, 1920))
        # Column 0 is virtual x = 0, not x = -1920.
        self.assertEqual(int(frame[0, 0, 0]), 0)
        self.assertEqual(int(frame[0, 1919, 0]), (1919 // 16) % 256)
        self.assertEqual(int(frame[0, 0, 1]), 0)

    def test_a_primary_at_the_virtual_origin_is_handed_out_whole(self):
        fake = FakeMss(left=0, top=0, width=3456, height=1080)
        frame = engine_with(fake)._grab_full_frame_uncached()
        self.assertEqual(frame.shape[:2], (1080, 3456))
        self.assertEqual(int(frame[0, 3455, 0]), (3455 // 16) % 256)

    def test_a_retina_grab_is_cut_in_its_own_pixels(self):
        """mss reports the monitors in points but grabs in pixels on macOS, so
        a monitor 1920 points to the left is 3840 pixels of the grab."""
        fake = FakeMss(left=-1920, top=0, width=3840, height=1080, scale=2)
        frame = engine_with(fake)._grab_full_frame_uncached()
        self.assertEqual(frame.shape[:2], (2160, 3840))
        self.assertEqual(int(frame[0, 0, 0]), 0)
        self.assertEqual(int(frame[0, 3839, 0]), (1919 // 16) % 256)

    def test_a_geometry_that_would_cut_everything_is_left_alone(self):
        arr = np.zeros((10, 10, 4), dtype=np.uint8)
        out = VisionEngine._cut_at_primary_origin(arr, {"left": -50, "top": 0, "width": 10, "height": 10})
        self.assertEqual(out.shape, arr.shape)

    def test_capture_of_a_region_returns_the_primary_monitor_pixels(self):
        """End to end through the shared-frame cache: the Arena region on the
        primary monitor comes back as the primary monitor, not the left one."""
        fake = FakeMss(left=-1920, top=-2, width=3840, height=1082)
        engine = engine_with(fake)
        engine.begin_tick()
        region = engine.capture((160, 48, 1600, 900))
        self.assertEqual(region.shape[:2], (900, 1600))
        self.assertEqual(int(region[0, 0, 0]), (160 // 16) % 256)
        self.assertEqual(int(region[0, 0, 1]), (48 // 16) % 256)

    def test_pixel_check_reads_screen_coordinates(self):
        fake = FakeMss(left=-1920, top=-2, width=3840, height=1082)
        engine = engine_with(fake)
        engine.begin_tick()
        # Screen (800, 400): B = 800 // 16 = 50, G = 400 // 16 = 25, R = 200.
        self.assertTrue(engine.pixel_check(800, 400, (200, 25, 50), tolerance=0))


if __name__ == "__main__":
    unittest.main()
