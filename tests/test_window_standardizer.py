"""Unit tests for window layout standardizer."""
from __future__ import annotations

import unittest
from vision.window_locator import snap_arena_window_16x9


class WindowStandardizerTests(unittest.TestCase):
    def test_snap_arena_window_zero_hwnd_safe(self):
        res = snap_arena_window_16x9(0)
        self.assertFalse(res)
