"""Unit tests for background Discord and Telegram notification manager."""
from __future__ import annotations

import unittest

from notifications import NotificationManager


class WebhooksTests(unittest.TestCase):
    def test_singleton_instance(self):
        inst1 = NotificationManager.get_instance()
        inst2 = NotificationManager.get_instance()
        self.assertIs(inst1, inst2)

    def test_disabled_notifications_do_not_enqueue(self):
        nm = NotificationManager.get_instance()
        nm._config["enabled"] = False
        initial_size = nm._queue.qsize()
        nm.send("Test", "Should not enqueue")
        self.assertEqual(nm._queue.qsize(), initial_size)
