"""Mixed display DPI must be rejected before anchors or cached clicks are used."""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from vision import window_locator as wl
from vision.vision import VisionEngine


class MixedScalingQueryTest(unittest.TestCase):
    def setUp(self):
        self.user32 = MagicMock()
        self.shcore = MagicMock()
        self.user32.GetThreadDpiAwarenessContext.return_value = 123
        self.user32.GetAwarenessFromDpiAwarenessContext.return_value = 1
        self.user32.MonitorFromWindow.return_value = 2
        self.user32.MonitorFromPoint.return_value = 1
        self.user32.SetThreadDpiAwarenessContext.return_value = 123
        self.dpis = {1: 168, 2: 288}

        def get_dpi(monitor, kind, x, y):
            x._obj.value = y._obj.value = self.dpis[monitor]
            return 0

        self.shcore.GetDpiForMonitor.side_effect = get_dpi
        for target, name, value in (
            (wl.os, "name", "nt"),
            (wl, "ctypes", ctypes),
            (wl, "wintypes", wintypes),
            (ctypes, "windll", SimpleNamespace(user32=self.user32, shcore=self.shcore)),
        ):
            patcher = patch.object(target, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_system_aware_thread_with_different_monitor_dpi_is_unsafe(self):
        risk = wl._get_windows_mixed_scaling_risk(42)
        self.assertEqual(risk, {
            "thread_dpi_awareness": 1,
            "primary_scaling_percent": 175,
            "arena_scaling_percent": 300,
        })
        self.assertEqual(self.user32.SetThreadDpiAwarenessContext.call_count, 2)
        self.user32.SetThreadDpiAwarenessContext.assert_called_with(123)

    def test_unaware_thread_is_also_rejected(self):
        self.user32.GetAwarenessFromDpiAwarenessContext.return_value = 0
        self.assertIsNotNone(wl._get_windows_mixed_scaling_risk(42))

    def test_per_monitor_aware_thread_is_allowed(self):
        self.user32.GetAwarenessFromDpiAwarenessContext.return_value = 2
        self.assertIsNone(wl._get_windows_mixed_scaling_risk(42))
        self.user32.SetThreadDpiAwarenessContext.assert_not_called()

    def test_primary_monitor_is_allowed(self):
        self.user32.MonitorFromWindow.return_value = 1
        self.assertIsNone(wl._get_windows_mixed_scaling_risk(42))
        self.shcore.GetDpiForMonitor.assert_not_called()

    def test_secondary_with_equal_scaling_is_allowed(self):
        self.dpis[2] = 168
        self.assertIsNone(wl._get_windows_mixed_scaling_risk(42))

    def test_failed_or_unavailable_dpi_query_does_not_invent_a_mismatch(self):
        self.shcore.GetDpiForMonitor.side_effect = OSError("unavailable")
        self.assertIsNone(wl._get_windows_mixed_scaling_risk(42))
        self.user32.SetThreadDpiAwarenessContext.assert_called_with(123)

    def test_failed_hresult_does_not_invent_a_mismatch(self):
        self.shcore.GetDpiForMonitor.side_effect = None
        self.shcore.GetDpiForMonitor.return_value = -1
        self.assertIsNone(wl._get_windows_mixed_scaling_risk(42))


class MixedScalingDetectionTest(unittest.TestCase):
    def test_setup_rejects_unsafe_scaling_before_reading_screen(self):
        provider = wl.ArenaRegionProvider(vision=VisionEngine(), assets_dir="x")
        provider._verify_region_with_any_anchor = MagicMock()
        rect = wl.WindowRect(4622, 477, 1599, 900)
        candidate = {"hwnd": 42, "client_rect": rect, "window_rect": rect}
        risk = {"thread_dpi_awareness": 1,
                "primary_scaling_percent": 175, "arena_scaling_percent": 300}
        with patch.object(wl, "_list_mtga_window_rects_windows", return_value=[candidate]), \
             patch.object(wl, "_visible_screen_bounds_physical", return_value=(0, 0, 7280, 2160)), \
             patch.object(wl, "_get_windows_display_scaling_percent", return_value=175), \
             patch.object(wl, "_get_windows_mixed_scaling_risk", return_value=risk) as query:
            result = provider._detect_windows()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "window_mixed_scaling")
        self.assertIn("300%", result.message)
        self.assertIn("175%", result.message)
        self.assertIn("Move Arena to the primary monitor", result.message)
        self.assertEqual(result.diagnostics["mixed_display_scaling"], risk)
        query.assert_called_once_with(42)
        provider._verify_region_with_any_anchor.assert_not_called()

    def test_rejected_scaling_cannot_reuse_cached_gameplay_or_ui_coordinates(self):
        from tests.test_cast_stall_guards import make_controller

        controller = make_controller()
        controller._arena_region = None
        controller._last_good_arena_region = (429, 156, 1920, 1080)
        controller._should_reuse_cached_arena_region = lambda: True
        controller._arena_region_provider = SimpleNamespace(
            reacquire=lambda: None, acquire=lambda: None,
            last_detection_result=SimpleNamespace(code="window_mixed_scaling"),
        )
        self.assertIsNone(controller._ensure_arena_region(force_reacquire=True))
        self.assertIsNone(controller._get_ui_action_arena_region())
        controller._dismiss_reward_popup = lambda: False
        self.assertFalse(controller._navigate_to_home())
        self.assertEqual(controller.input.moves, [])


if __name__ == "__main__":
    unittest.main()
