"""Unit tests for low power and overnight throttling manager."""
from __future__ import annotations

import unittest

from low_power import LowPowerManager


class LowPowerTests(unittest.TestCase):
    def test_low_power_manager_multiplier(self):
        lp = LowPowerManager.get_instance()
        lp.set_enabled(False)
        self.assertEqual(lp.sleep_multiplier, 1.0)
        lp.set_enabled(True)
        self.assertEqual(lp.sleep_multiplier, 1.35)
        lp.set_enabled(False)
