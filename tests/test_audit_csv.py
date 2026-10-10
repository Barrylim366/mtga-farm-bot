import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/audit-logs/scripts/summarize.py"
spec = importlib.util.spec_from_file_location("audit_csv_summary", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def summary(session="session-1", complete=True):
    return {
        "session_id": session,
        "window": {"started_at_epoch": 1000, "ended_at_epoch": 2000,
                   "first_match_start_estimate_epoch": 1100, "last_match_end_epoch": 1900,
                   "running": False},
        "matches": {"completed": 10, "won": 4, "lost": 6, "win_rate": .4},
        "concede": {"confirmed_matches": 2, "attempts": 2, "confirmed_rate": .2},
        "history_coverage": {"history": {"first_at": 900, "last_at": 2100}} if complete else {},
    }


class AuditHistoryTests(unittest.TestCase):
    def test_invalid_gold_values_are_skipped_without_losing_valid_balances(self):
        for invalid in ("invalid", None, [], {}):
            with self.subTest(invalid=invalid):
                result = audit.gold_summary(
                    [], {"gold_farmed": {"Alice#1": invalid, "Bob#2": "150", "Carol#3": 0}},
                    True, {"Alice#1", "Bob#2", "Carol#3"}, 1000, 4600, [])
                self.assertEqual(result["by_account"], {"Bob#2": 150, "Carol#3": 0})
                self.assertEqual(result["total"], 150)

    def test_invalid_gold_mapping_preserves_history_totals(self):
        events = [
            {"text": "Gold baseline for 'Alice#1': 100", "at": 1000, "path": "history.log", "line": 1},
            {"text": "Gold farmed (real): 'Alice#1' balance=250 farmed=150",
             "at": 4600, "path": "history.log", "line": 2},
        ]
        result = audit.gold_summary(
            events, {"gold_farmed": []}, True, {"Alice#1"}, 1000, 4600, [])
        self.assertEqual(result["source"], "history")
        self.assertEqual(result["by_account"], {"Alice#1": 150})

    def test_optional_target_history_remains_available_without_capture_bundles(self):
        lines = [
            "[2026-10-01 12:00:00.000] [INFO] FIERY_EQUIPMENT_ATTEMPT: target=287 found=True",
            "[2026-10-01 12:00:01.000] [INFO] SUBMIT_ZERO_ATTEMPT: source=303",
            "[2026-10-01 12:00:02.000] [INFO] SUBMIT_ZERO_ACKNOWLEDGED: prompt advanced",
        ]
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "analysis").mkdir()
            (runtime / "analysis/history.log").write_text("\n".join(lines), encoding="utf-8")
            events, coverage = audit.history_events(
                runtime, audit.timestamp(lines[0]), audit.timestamp(lines[-1]))
            self.assertEqual([event["kind"] for event in events], [
                "FIERY_EQUIPMENT_ATTEMPT", "SUBMIT_ZERO_ATTEMPT", "SUBMIT_ZERO_ACKNOWLEDGED"])
            self.assertEqual(events[-1]["line"], 3)
            self.assertTrue(coverage)


class AuditCSVTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "audit-history.csv"

    def rows(self):
        with self.path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_rates_counts_utc_and_repeated_audit_updates_single_row(self):
        data = summary()
        audit.update_audit_csv(self.path, data)
        data["matches"].update(completed=20, won=9, lost=11, win_rate=.45)
        data["window"]["running"] = True
        audit.update_audit_csv(self.path, data)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["completed_matches"], "20")
        self.assertEqual(rows[0]["win_rate"], "0.45")
        self.assertEqual(rows[0]["provisional"], "true")
        self.assertEqual(rows[0]["date_utc"], "1970-01-01")
        self.assertTrue(rows[0]["started_at_utc"].endswith("+00:00"))

    def test_preserves_other_sessions_and_known_rate_after_rotation(self):
        audit.update_audit_csv(self.path, summary())
        audit.update_audit_csv(self.path, summary("session-2"))
        data = summary(complete=False)
        data["concede"].update(confirmed_matches=0, attempts=0, confirmed_rate=0)
        audit.update_audit_csv(self.path, data)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["concede_rate"], "0.2")
        self.assertEqual(rows[0]["confirmed_concedes"], "2")

    def test_unknown_or_partial_history_and_unresolved_rates_are_blank(self):
        for case in ("missing", "partial", "unresolved", "no_matches"):
            with self.subTest(case=case):
                data = summary(case)
                if case == "missing":
                    data["history_coverage"] = {}
                elif case == "partial":
                    data["history_coverage"]["history"]["first_at"] = 1500
                else:
                    data["concede"]["confirmed_rate"] = None
                if case == "no_matches":
                    data["matches"].update(completed=0, won=0, lost=0, win_rate=None)
                audit.update_audit_csv(self.path, data)
        self.assertTrue(all(row["concede_rate"] == "" for row in self.rows()))
        self.assertTrue(all(row["concede_rate_status"] == "unavailable" for row in self.rows()))

    def test_different_denominator_does_not_reuse_old_rate(self):
        audit.update_audit_csv(self.path, summary())
        data = summary(complete=False)
        data["matches"]["completed"] = 11
        audit.update_audit_csv(self.path, data)
        self.assertEqual(self.rows()[0]["concede_rate"], "")

    def test_unexpected_csv_is_left_untouched(self):
        self.path.write_text("unrelated,columns\n1,2\n")
        with self.assertRaises(ValueError):
            audit.update_audit_csv(self.path, summary())
        self.assertEqual(self.path.read_text(), "unrelated,columns\n1,2\n")


if __name__ == "__main__":
    unittest.main()
