"""Unit tests for background Discord and Telegram notification manager."""
from __future__ import annotations

import unittest

from notifications import NotificationManager


class WebhooksTests(unittest.TestCase):
    def setUp(self):
        NotificationManager.reset_instance()

    def tearDown(self):
        NotificationManager.reset_instance()

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

    def test_default_config_is_disabled(self):
        nm = NotificationManager.get_instance()
        nm._config = {}  # Empty config should default enabled to False
        initial_size = nm._queue.qsize()
        nm.send("Test", "Should not enqueue when empty config")
        self.assertEqual(nm._queue.qsize(), initial_size)

    def test_enabled_notifications_enqueue(self):
        nm = NotificationManager.get_instance()
        nm._config = {
            "enabled": True,
            "discord_webhook_url": "https://discord.com/api/webhooks/test",
        }
        initial_size = nm._queue.qsize()
        nm.send("Test Title", "Test Description")
        self.assertEqual(nm._queue.qsize(), initial_size + 1)
        item = nm._queue.get_nowait()
        self.assertEqual(item["title"], "Test Title")

    def test_notify_match_result_rolling_win_rate(self):
        nm = NotificationManager.get_instance()
        nm._config = {
            "enabled": True,
            "discord_webhook_url": "https://discord.com/api/webhooks/test",
        }
        # Simulate 3 matches: Win, Loss, Win
        nm.notify_match_result(True, duration_sec=120)
        nm.notify_match_result(False, duration_sec=100)
        nm.notify_match_result(True, duration_sec=150)
        self.assertEqual(nm._queue.qsize(), 3)
        # Check last notification has rolling summary
        items = [nm._queue.get_nowait() for _ in range(3)]
        self.assertIn("Recent (last 3): **2W - 1L** (67% win rate)", items[2]["description"])
