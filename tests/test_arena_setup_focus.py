"""The Arena setup check raises the Arena window before it looks at the screen.

Measured 2026-10-15 on a three-monitor desk: four consecutive Start presses
failed with `anchor_not_found` (best score 0.58 against a 0.78 threshold)
while the Arena sat on the Home screen in English. The debug bundles showed
why: the check captures the *screen* at the window's rectangle, and that
rectangle held a browser, later a chat app, lying on top of the Arena.
Capturing the window itself (PrintWindow) matched the same anchors at 0.98.

The Controller already calls focus_mtga_window() before every cast for the
same reason (Unity delivers no hover to a covered window). The setup check
did not, so a user who presses Start from the bot's own always-on-top window
while the Arena is behind something else gets a failure that looks like a
wrong screen or a wrong language. These tests pin the order: focus, settle,
then detect.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vision import window_locator as wl


class FakeProvider:
    def __init__(self, calls, *args, **kwargs):
        self.calls = calls
        self.kwargs = kwargs

    def detect(self, write_debug_on_fail=True):
        self.calls.append(("detect", write_debug_on_fail))
        return "detection-result"


class SetupCheckFocusTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        saved = (wl.ArenaRegionProvider, wl.focus_mtga_window, wl.VisionEngine)
        wl.ArenaRegionProvider = lambda *a, **kw: FakeProvider(self.calls, *a, **kw)
        wl.focus_mtga_window = lambda expected_size=(1920, 1080): self.calls.append(("focus", expected_size)) or True
        wl.VisionEngine = lambda: None
        self.addCleanup(
            lambda: setattr(wl, "ArenaRegionProvider", saved[0])
            or setattr(wl, "focus_mtga_window", saved[1])
            or setattr(wl, "VisionEngine", saved[2])
        )

    def test_the_window_is_raised_before_the_screen_is_read(self):
        result = wl.run_arena_setup_check(assets_dir="x", expected_size=(1920, 1080), settle_sec=0)
        self.assertEqual(result, "detection-result")
        self.assertEqual(self.calls, [("focus", (1920, 1080)), ("detect", True)])

    def test_the_debug_flag_still_reaches_the_detector(self):
        wl.run_arena_setup_check(assets_dir="x", write_debug_on_fail=False, settle_sec=0)
        self.assertIn(("detect", False), self.calls)

    def test_focusing_can_be_switched_off(self):
        """Callers that already own the foreground (or tests of the detector
        itself) must be able to skip the focus step."""
        wl.run_arena_setup_check(assets_dir="x", focus=False, settle_sec=0)
        self.assertEqual(self.calls, [("detect", True)])


if __name__ == "__main__":
    unittest.main()
