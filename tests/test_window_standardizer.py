"""Unit tests for Windows DPI v2 awareness and window layout standardizer."""
from __future__ import annotations

import unittest
import os

from vision.window_locator import enable_per_monitor_dpi_v2, preprocess_for_matching


class WindowStandardizerTests(unittest.TestCase):
    def test_enable_dpi_v2_runs_safely(self):
        res = enable_per_monitor_dpi_v2()
        if os.name == "nt":
            self.assertIsInstance(res, bool)
        else:
            self.assertFalse(res)

    def test_preprocess_for_matching_none_inputs(self):
        img, tpl = preprocess_for_matching(None, None)
        self.assertIsNone(img)
        self.assertIsNone(tpl)
