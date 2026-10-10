"""Deck attribution uses completed selections, account boundaries and source evidence."""
import csv
import json
import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / ".agents/skills/audit-logs/scripts/summarize.py"
spec = importlib.util.spec_from_file_location("audit_deck_summary", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditDeckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        (self.runtime / "analysis").mkdir()
        self.base = datetime(2026, 1, 1)
        self.configured = {"Alice#1", "Bob#2"}

    def epoch(self, seconds):
        return (self.base + timedelta(seconds=seconds)).timestamp()

    def line(self, seconds, text):
        stamp = (self.base + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
        return f"[{stamp}.000] {text}\n"

    def history(self, lines, rotated=False):
        name = "history.log.1" if rotated else "history.log"
        (self.runtime / "analysis" / name).write_text(
            "".join(self.line(seconds, text) for seconds, text in lines), encoding="utf-8")

    def record(self, index, started, account="Alice#1"):
        return {"session_id": "session", "match_index": index, "account": account,
                "ended_at_epoch": self.epoch(started + 10), "duration_sec": 10,
                "result": "lost", "_path": str(self.runtime / f"match-{index:04}.json")}

    def decks(self, records, start=0):
        return audit.match_decks(self.runtime, records,
                                 {r["_path"]: r["account"] for r in records},
                                 self.configured, self.epoch(start) if start is not None else None)

    def test_completed_selection_then_inheritance_and_account_switch(self):
        self.history([(1, "[INFO] Gold baseline for 'alice#1': 100"),
                      (2, "[INFO] Starter: deck UB submitted; event page ready to queue.")], rotated=True)
        self.history([(35, "[INFO] Switching account to 'Bob#2' (leaving 'Alice#1')"),
                      (49, "[INFO] Starter: deck WB submitted; event page ready to queue.")])
        matches = self.decks([self.record(1, 10), self.record(2, 25),
                             self.record(3, 38, "Bob#2"), self.record(4, 55, "Bob#2")])
        self.assertEqual([m["deck"] for m in matches], ["UB", "UB", None, "WB"])
        self.assertEqual([m["deck_status"] for m in matches],
                         ["logged_selection", "inferred", "unknown", "logged_selection"])
        self.assertTrue(matches[0]["deck_evidence"]["path"].endswith("history.log.1"))
        self.assertEqual(matches[0]["deck_evidence"]["line"], 2)

    def test_request_only_does_not_establish_deck_and_pick_invalidates_previous(self):
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[INFO] Starter: chose deck template WB.PNG for colors 'WB'."),
                      (15, "[INFO] Starter: deck UB submitted; event page ready to queue."),
                      (26, "[CLICK] (100, 200) - STARTER_DECK_PICK_WB")])
        matches = self.decks([self.record(1, 5), self.record(2, 20), self.record(3, 30)])
        self.assertEqual([m["deck"] for m in matches], [None, "UB", None])
        self.assertIsNotNone(matches[2]["deck_evidence"])

    def test_restart_and_return_to_account_do_not_reuse_previous_selection(self):
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[INFO] Starter: deck UB submitted; event page ready to queue."),
                      (21, "[INFO] Switching account to 'Bob#2'"),
                      (22, "[INFO] Switching account to 'Alice#1'"),
                      (35, "[INFO] Starter: deck WB submitted; event page ready to queue."),
                      (61, "=== MTGA Bot Session Started ==="),
                      (62, "[INFO] Gold baseline for 'Alice#1': 100")])
        matches = self.decks([self.record(1, 10), self.record(2, 25),
                             self.record(3, 40), self.record(4, 70)])
        self.assertEqual([m["deck"] for m in matches], ["UB", None, "WB", None])

    def test_historic_visible_memory_and_unnamed_fallback(self):
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[INFO] Historic: deck selected (My control deck.png) for quest target colors=UB."),
                      (21, "[INFO] Historic: deck Burn.png is already the selected deck for account 'Alice#1'; not clicking again."),
                      (41, "[INFO] Historic: deck Burn.png is not on the grid and was the last tile this session selected for account 'Alice#1'; treating it as still selected."),
                      (61, "[INFO] Historic: no quest target; selected the first deck in the list.")])
        matches = self.decks([self.record(i, s) for i, s in enumerate((10, 30, 50, 70), 1)])
        self.assertEqual([m["deck"] for m in matches], ["My control deck.png", "Burn.png", "Burn.png", None])
        self.assertEqual(matches[2]["deck_status"], "inferred")

    def test_missing_history_and_selection_for_wrong_account_remain_unknown(self):
        record = self.record(1, 10)
        self.assertIsNone(self.decks([record])[0]["deck"])
        self.history([(1, "[INFO] Gold baseline for 'Bob#2': 100"),
                      (2, "[INFO] Starter: deck WB submitted; event page ready to queue.")])
        self.assertIsNone(self.decks([record])[0]["deck"])

    def test_historical_session_header_includes_pregame_selection_but_excludes_old_run(self):
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[INFO] Starter: deck WB submitted; event page ready to queue."),
                      (3, "=== MTGA Bot Session Started ==="),
                      (4, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (5, "[INFO] Starter: deck UB submitted; event page ready to queue.")])
        matches = self.decks([self.record(1, 10)], start=None)
        self.assertEqual(matches[0]["deck"], "UB")
        self.assertEqual(matches[0]["deck_status"], "logged_selection")

    def test_summary_includes_each_game_and_deck_counts(self):
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[INFO] Starter: deck UB submitted; event page ready to queue.")])
        result = audit.summarize(self.runtime, [self.record(1, 10)],
                                 {"session_id": "session", "started_at_epoch": self.epoch(0),
                                  "updated_at_epoch": self.epoch(30), "mode": "stopped"}, self.runtime)
        self.assertEqual(result["deck_counts"], {"UB": 1})
        self.assertEqual(result["match_details"][0]["deck"], "UB")

    def test_saved_hand_inference_requires_starter_context_and_reliable_unique_cards(self):
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[INFO] Starter: Starter Deck Duel selected.")])
        directory = self.runtime / "debug" / "matches" / "snapshot-match"
        directory.mkdir(parents=True)
        path = directory / "snapshots.jsonl"
        snap = {"ts": datetime.fromtimestamp(self.epoch(11)).astimezone().isoformat(),
                "my_seat": 2, "hand": [{"name": name} for name in
                 ("Vengeful Bloodwitch", "Cat Collector", "Angelic Destiny", "Plains")]}
        path.write_text(json.dumps(snap) + "\n", encoding="utf-8")
        match = self.decks([self.record(1, 10)])[0]
        self.assertEqual(match["deck"], "WB")
        self.assertEqual(match["deck_status"], "inferred")
        self.assertEqual(match["deck_evidence"]["source"], "starter_hand_match")
        for change in ("unknown_seat", "few_cards", "wrong_format"):
            with self.subTest(change=change):
                modified = dict(snap)
                if change == "unknown_seat":
                    modified["seat_unknown"] = True
                elif change == "few_cards":
                    modified["hand"] = snap["hand"][:2]
                else:
                    self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100")])
                path.write_text(json.dumps(modified) + "\n", encoding="utf-8")
                self.assertIsNone(self.decks([self.record(1, 10)])[0]["deck"])

    def test_match_csv_upserts_and_preserves_evidence_only_when_logs_missing(self):
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[INFO] Starter: deck UB submitted; event page ready to queue.")])
        records = [self.record(1, 10)]
        path = self.runtime / "audit-matches.csv"
        audit.update_match_csv(path, {"session_id": "session", "match_details": self.decks(records)})
        (self.runtime / "analysis" / "history.log").unlink()
        audit.update_match_csv(path, {"session_id": "session", "match_details": self.decks(records)})
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["deck"], "UB")
        self.history([(1, "[INFO] Gold baseline for 'Alice#1': 100"),
                      (2, "[CLICK] (100, 200) - STARTER_DECK_PICK_WB")])
        audit.update_match_csv(path, {"session_id": "session", "match_details": self.decks(records)})
        with path.open(newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle))[0]["deck"], "")


if __name__ == "__main__":
    unittest.main()
