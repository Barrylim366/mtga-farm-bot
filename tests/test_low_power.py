"""Unit tests for low power and overnight throttling manager."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from low_power import LowPowerManager


class LowPowerTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._temp_config = Path(self._temp_dir.name) / "low_power.json"
        self._patcher = patch("low_power._get_config_path", return_value=self._temp_config)
        self._patcher.start()
        LowPowerManager.reset_instance()

    def tearDown(self):
        LowPowerManager.reset_instance()
        self._patcher.stop()
        self._temp_dir.cleanup()

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
