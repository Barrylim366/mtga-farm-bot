"""Unit tests for low power and overnight throttling manager."""
from __future__ import annotations

import unittest

from low_power import LowPowerManager


class LowPowerTests(unittest.TestCase):
    def setUp(self):
        LowPowerManager.reset_instance()

    def tearDown(self):
        LowPowerManager.reset_instance()

    def test_low_power_manager_multiplier(self):
        lp = LowPowerManager.get_instance()
        lp.set_enabled(False)
        self.assertEqual(lp.sleep_multiplier, 1.0)
        lp.set_enabled(True)
        self.assertEqual(lp.sleep_multiplier, 1.35)
        lp.set_enabled(False)

    def test_singleton_thread_safety(self):
        import concurrent.futures
        instances = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(LowPowerManager.get_instance) for _ in range(16)]
            for f in futures:
                instances.append(f.result())
        first = instances[0]
        for inst in instances:
            self.assertIs(inst, first)
