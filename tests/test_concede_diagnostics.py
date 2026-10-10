import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import concede_diagnostics as diagnostics
from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState


class ConcedeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"MTGA_RUNTIME_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        status = patch.object(diagnostics.runtime_status, "read_status", return_value={
            "session_id": "session-1", "account": "private-player-name"})
        status.start()
        self.addCleanup(status.stop)
        self.addCleanup(diagnostics._ACTIVE.clear)

    def incident(self):
        return diagnostics.ConcedeIncident.create("STALL_CONCEDE", "match-1")

    def capture(self, incident, size=(1920, 1080), ok=True):
        w, h = size
        image = np.full((h, w, 3), 255, dtype=np.uint8)
        vision = Mock()
        vision.capture.return_value = image
        provider = Mock()
        provider.detect.return_value = SimpleNamespace(ok=ok, region=(30, 40, w, h))
        incident.start_attempt(1)
        incident.capture(vision, provider)
        return image, vision

    def test_window_only_masked_jpeg_minimal_metadata(self):
        incident = self.incident()
        original, vision = self.capture(incident)
        incident.finish("match_completion_observed")
        vision.capture.assert_called_once_with((30, 40, 1920, 1080))
        image = diagnostics.cv2.imread(str(incident.path / "attempt-1.jpg"))
        self.assertEqual(image.shape[:2], (720, 1280))
        self.assertLess(image[10:45, 10:290].max(), 5)
        self.assertLess(image[675:710, 10:290].max(), 5)
        self.assertGreater(image[300:400, 600:700].min(), 250)
        self.assertTrue(np.all(original == 255))
        data = json.loads((incident.path / "incident.json").read_text())
        self.assertEqual(data["outcome"], "match_completion_observed")
        self.assertEqual(data["session_id"], "session-1")
        self.assertNotIn("private-player-name", json.dumps(data))
        self.assertEqual({p.name for p in incident.path.iterdir()}, {"incident.json", "attempt-1.jpg"})

    def test_smaller_images_not_enlarged_and_scaled_masks(self):
        image = np.full((360, 640, 3), 255, dtype=np.uint8)
        result = diagnostics.prepare_image(image)
        self.assertEqual(result.shape, image.shape)
        self.assertEqual(result[0:30, 0:160].max(), 0)
        self.assertEqual(result[334:360, 0:160].max(), 0)
        self.assertEqual(result[50, 50, 0], 255)

    def test_failures_never_save_unmasked_fallback(self):
        for size, ok, expected in [((1920, 1080), False, "window_unavailable"),
                                   ((1000, 1000), True, "unsupported_layout")]:
            with self.subTest(expected=expected):
                incident = self.incident()
                _, vision = self.capture(incident, size, ok)
                self.assertEqual(incident.payload["attempts"][0]["capture_status"], expected)
                self.assertFalse(list(incident.path.glob("*.jpg")))
                if not ok:
                    vision.capture.assert_not_called()
                incident.finish("cancelled")

    def test_capture_exception_and_write_failure_are_recorded(self):
        for failure in ("capture", "encode"):
            with self.subTest(failure=failure):
                incident = self.incident()
                incident.start_attempt(1)
                vision = Mock()
                vision.capture.return_value = np.ones((1080, 1920, 3), dtype=np.uint8)
                if failure == "capture":
                    vision.capture.side_effect = RuntimeError("private-player-name")
                provider = Mock()
                provider.detect.return_value = SimpleNamespace(ok=True, region=(0, 0, 1920, 1080))
                with patch.object(diagnostics.cv2, "imwrite", return_value=False):
                    incident.capture(vision, provider)
                self.assertIn(incident.payload["attempts"][0]["capture_status"], ("capture_failed", "write_failed"))
                self.assertNotIn("private-player-name", (incident.path / "incident.json").read_text())
                incident.finish("cancelled")

    def test_incomplete_crop_is_rejected(self):
        incident = self.incident()
        incident.start_attempt(1)
        vision = Mock()
        vision.capture.return_value = np.ones((720, 1279, 3), dtype=np.uint8)
        provider = Mock()
        provider.detect.return_value = SimpleNamespace(ok=True, region=(0, 0, 1280, 720))
        incident.capture(vision, provider)
        self.assertEqual(incident.payload["attempts"][0]["capture_status"], "incomplete_window")

    def test_retention_protects_active_and_unrelated_folders(self):
        active = self.incident()
        unrelated = active.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "incident.json").write_text("{}")
        outside = Path(self.temp.name) / "other"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        for _ in range(32):
            incident = self.incident()
            incident.finish("attempts_exhausted")
        self.assertTrue(active.path.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue((outside / "keep.txt").exists())
        active.finish("cancelled")
        self.assertEqual(len(list(active.root.glob("*/incident.json"))), 31)

    def test_directory_and_metadata_write_failures_are_nonfatal(self):
        with patch.object(diagnostics, "ensure_runtime_subdir", side_effect=OSError):
            self.assertIsNone(self.incident())
        incident = self.incident()
        with patch.object(Path, "write_text", side_effect=OSError):
            incident.start_attempt(1)
            incident.finish("cancelled")
        self.assertNotIn(incident.path, diagnostics._ACTIVE)

    def test_audit_indexes_failed_capture_and_events_without_claiming_confirmation(self):
        script = Path(__file__).resolve().parents[1] / ".agents/skills/audit-logs/scripts/summarize.py"
        spec = importlib.util.spec_from_file_location("concede_audit", script)
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        incident = self.incident()
        self.capture(incident, ok=False)
        incident.finish("cancelled")
        found = audit.concede_incidents(Path(self.temp.name), "session-1")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["attempts"][0]["capture_status"], "window_unavailable")
        self.assertEqual(audit.concede_incidents(Path(self.temp.name), "another-session"), [])
        history = Path(self.temp.name) / "analysis"
        history.mkdir(exist_ok=True)
        (history / "history.log").write_text(
            "[2026-10-01 12:00:00] CONCEDE_INCIDENT_CREATED: path=example\n"
            "[2026-10-01 12:00:01] CONCEDE_INCIDENT_FINISHED: outcome=cancelled path=example\n")
        events, _ = audit.history_events(Path(self.temp.name), 0, float("inf"))
        self.assertEqual([e["kind"] for e in events],
                         ["CONCEDE_INCIDENT_CREATED", "CONCEDE_INCIDENT_FINISHED"])


class ConcedeIntegrationTests(unittest.TestCase):
    def controller(self):
        c = Controller.__new__(Controller)
        c._stop_requested = False
        c._Controller__live_match_id = "match-1"
        c._Controller__last_seen_match_id = "match-1"
        c._Controller__concede_outcome = None
        c._Controller__concede_completed_event = Mock()
        c._Controller__concede_completed_event.is_set.return_value = False
        c._Controller__concede_completed_event.wait.return_value = False
        c._get_state_from_log = lambda: BotState.IN_GAME
        c._loaded_click_targets = {}
        c._map_abs_point_to_arena = lambda *a, **kw: ((100, 200), "arena")
        c._vision = Mock()
        c._arena_region_provider = Mock()
        c.input = Mock()
        c._Controller__click_concede_and_confirm = Mock()
        return c

    def test_both_reasons_capture_after_focus_before_escape_and_group_retries(self):
        for reason in ("STALL_CONCEDE", "EMERGENCY_CONCEDE"):
            with self.subTest(reason=reason):
                c = self.controller()
                incident = Mock()
                order = []
                c.input.tap_escape.side_effect = lambda: order.append("escape")
                incident.capture.side_effect = lambda *a: order.append("capture")
                with patch("Controller.MTGAController.Controller.ConcedeIncident.create", return_value=incident) as create, \
                     patch("Controller.MTGAController.Controller.focus_mtga_window", side_effect=lambda: order.append("focus") or True), \
                     patch("Controller.MTGAController.Controller.time.sleep"), \
                     patch("Controller.MTGAController.Controller.runtime_status"):
                    c._Controller__run_claimed_concede_sequence(reason)
                self.assertEqual(order, ["focus", "capture", "escape"] * 2)
                create.assert_called_once_with(reason, "match-1")
                self.assertEqual(incident.start_attempt.call_count, 2)
                incident.finish.assert_called_once_with("attempts_exhausted")
                c.input.release_exclusive.assert_called_once()

    def test_stop_and_match_change_finalize_without_capture(self):
        for stop in (False, True):
            c = self.controller()
            c._stop_requested = stop
            if not stop:
                c._Controller__live_match_id = "match-2"
            incident = Mock()
            with patch("Controller.MTGAController.Controller.ConcedeIncident.create", return_value=incident):
                c._Controller__run_claimed_concede_sequence("STALL_CONCEDE", expected_match_id="match-1")
            incident.capture.assert_not_called()
            incident.finish.assert_called_once_with("stop_requested" if stop else "cancelled")

    def test_match_change_during_capture_skips_escape(self):
        c = self.controller()
        incident = Mock()
        c._Controller__active_concede_incident = incident
        incident.capture.side_effect = lambda *a: setattr(c, "_Controller__live_match_id", "match-2")
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True), \
             patch("Controller.MTGAController.Controller.time.sleep"), \
             patch("Controller.MTGAController.Controller.runtime_status"):
            c._Controller__perform_concede("STALL_CONCEDE_1", "match-1")
        c.input.tap_escape.assert_not_called()


if __name__ == "__main__":
    unittest.main()
