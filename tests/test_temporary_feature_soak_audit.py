"""The overnight audit must not borrow successes from an earlier run."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import temporary_feature_soak_audit as soak


class FeatureSoakAuditTests(unittest.TestCase):
    def test_changed_features_use_only_selected_tagged_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            analysis = runtime / "analysis"
            analysis.mkdir()
            (analysis / "history.log").write_text(
                "[2026-10-22 20:00:00] [ERROR] BLOCK_RECOVERY_FAILED: previous run\n"
                "[2026-10-22 21:00:00] [RAW] PILE_CHOICE_UNSUPPORTED: payload\n"
                "[2026-10-22 21:00:01] [INFO] BLOCK_RECOVERY_WARNING: insufficient blockers for [305]\n"
                "[2026-10-22 21:00:02] [INFO] BLOCK_RECOVERY: add_blocker {'blocker': 280, 'attacker': 305}\n"
                "[2026-10-22 21:00:03] [INFO] BLOCK_RECOVERY: cancel_blocks {'reason': 'budget'}\n"
                "[2026-10-22 21:00:04] [INFO] PILE_CHOICE_ARMED: source=468 groups=[]\n"
                "[2026-10-22 21:00:05] [INFO] PILE_CHOICE_CLICK: attempt=1\n"
                "[2026-10-22 21:00:06] [INFO] PILE_CHOICE_CLOSED: server advanced the pending decision.\n"
                "[2026-10-22 21:00:07] [INFO] SelectN stack cancelled: token=4 target sourceId=427.\n"
                "[2026-10-22 21:00:08] [INFO] STUCK_ACTION_RETRY_LIMIT\n"
                "[2026-10-22 21:00:09] [INFO] MY_TIMER_CRITICAL\n",
                encoding="utf-8",
            )
            report = soak.scan(runtime, soak.timestamp("[2026-10-22 21:00:00]"))
            checks = report["changed_feature_checks"]
            for feature in checks.values():
                self.assertEqual(feature["status"], "evidence observed; verify outcomes")
            self.assertEqual(checks["block_recovery"]["events"], {
                "block_warning": 1, "block_extra_assignment_attempt": 1, "block_cancel_attempt": 1,
            })
            self.assertEqual(checks["pile_choice"]["review_signals"]["pile_choice_unsupported"], 0)
            self.assertEqual(report["signals"]["stuck_action_retry_limit"]["count"], 1)
            self.assertEqual(report["signals"]["inactivity_timer_critical"]["count"], 1)

    def test_errors_need_review_even_without_an_attempt_and_closure_does_not_erase_them(self):
        counts = dict.fromkeys(soak.SIGNALS, 0)
        self.assertTrue(all(check["status"] == "not exercised in retained evidence"
                            for check in soak.changed_feature_checks(counts).values()))
        for signal in ("block_recovery_failed", "pile_choice_unsupported",
                       "group_recovery_unconfirmed", "group_recovery_failed", "stack_order_timeout"):
            with self.subTest(signal=signal):
                sample = counts | {signal: 1, "pile_choice_closed": 1}
                checks = soak.changed_feature_checks(sample)
                self.assertTrue(any(check["status"] == "needs review" for check in checks.values()))

    def test_boundary_rotation_and_raw_payload_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            analysis = runtime / "analysis"
            analysis.mkdir()
            (analysis / "history.log.1").write_text(
                "[2026-10-21 20:00:00.100] [INFO] ABILITY_ACTIVATION_ACKNOWLEDGED: old\n"
                "[2026-10-21 20:00:00.500] [INFO] ABILITY_ACTIVATION_CLICKED: new\n",
                encoding="utf-8",
            )
            (analysis / "history.log").write_text(
                "[2026-10-21 20:00:01.000] [RAW] ABILITY_ACTIVATION_ACKNOWLEDGED: payload\n"
                "ABILITY_ACTIVATION_ACKNOWLEDGED: continuation\n"
                "[2026-10-21 20:00:02.000] [INFO] ABILITY_ACTIVATION_TIMEOUT: new\n",
                encoding="utf-8",
            )
            since = soak.timestamp("[2026-10-21 20:00:00.500]")
            report = soak.scan(runtime, since)
            signals = report["signals"]
            self.assertEqual(signals["ability_activation_clicked"]["count"], 1)
            self.assertEqual(signals["ability_activation_timeout"]["count"], 1)
            self.assertEqual(signals["ability_activation_acknowledged"]["count"], 0)
            self.assertEqual(signals["ability_activation_clicked"]["examples"][0]["line"], 2)
            self.assertEqual(len(report["history_files"]), 2)
            self.assertFalse(report["history_starts_after_boundary"])

    def test_unconfirmed_dismissal_is_not_confirmation_and_deck_is_not_fixed_to_wb(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "analysis").mkdir()
            (runtime / "analysis/history.log").write_text(
                "[2026-10-21 20:00:01] [INFO] Match completed - dismissed end screen (unconfirmed; queue loop safety net will keep retrying)\n"
                "[2026-10-21 20:00:02] [INFO] Starter: configured quest target met; selecting RG win-farming deck.\n",
                encoding="utf-8",
            )
            signals = soak.scan(runtime)["signals"]
            self.assertEqual(signals["end_screen_dismissal_confirmed"]["count"], 0)
            self.assertEqual(signals["end_screen_dismissal_unconfirmed"]["count"], 1)
            self.assertEqual(signals["quest_target_met_win_deck"]["count"], 1)

    def test_missing_or_old_history_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            self.assertEqual(soak.scan(runtime)["coverage"], "unavailable")
            (runtime / "analysis").mkdir()
            (runtime / "analysis/history.log").write_text(
                "[2026-10-21 20:00:00] [INFO] FORGE advisor ready: old\n", encoding="utf-8",
            )
            report = soak.scan(runtime, soak.timestamp("[2026-10-21 21:00:00]"))
            self.assertEqual(report["coverage"], "unavailable")
            self.assertEqual(report["signals"]["forge_ready"]["count"], 0)

    def test_arm_and_default_audit_use_same_marker_without_changing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            argv = ["soak", "--runtime-dir", directory]
            with patch("sys.argv", argv + ["--arm"]), patch.object(soak, "revision", return_value={"branch": "test"}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(soak.main(), 0)
            marker = runtime / "analysis" / soak.ARM_FILE
            original = marker.read_bytes()
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(soak.main(), 2)
            report = json.loads(output.getvalue())
            self.assertEqual(report["since_epoch"], json.loads(original)["since_epoch"])
            self.assertEqual(marker.read_bytes(), original)

    def test_invalid_marker_requires_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            analysis = Path(directory) / "analysis"
            analysis.mkdir()
            (analysis / soak.ARM_FILE).write_text("[]", encoding="utf-8")
            argv = ["soak", "--runtime-dir", directory]
            with patch("sys.argv", argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                soak.main()
            self.assertEqual(raised.exception.code, 2)
            with patch("sys.argv", argv + ["--all-history"]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(soak.main(), 2)
