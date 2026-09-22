import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.MTGAController.Controller import BotState
from tools.analyze_target_recovery_soak import parse_events, summarize


class _State:
    def get_annotations(self):
        return []


class TargetCastGuardTest(unittest.TestCase):
    def controller(self):
        c = Controller.__new__(Controller)
        c._Controller__live_match_id = "match"
        c._Controller__last_seen_match_id = "match"
        c._Controller__system_seat_id = 1
        c._get_state_from_log = lambda: BotState.IN_GAME
        c._Controller__pending_target_select = None
        c._Controller__target_soak_last_event = None
        c.updated_game_state = _State()
        return c

    def test_pending_target_transaction_blocks_casts(self):
        c = self.controller()
        c._Controller__pending_target_select = {
            "source_id": 42, "token": 1, "last_target": 99,
        }
        self.assertTrue(c.should_defer_cast_for_target_selection("match"))

    def test_no_target_transaction_allows_casts(self):
        self.assertFalse(self.controller().should_defer_cast_for_target_selection("match"))


class TargetSoakAnalyzerTest(unittest.TestCase):
    def test_groups_retries_and_unresolved_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.log"
            rows = [
                {"event": "opened", "match_id": "m", "token": 1, "source_id": 42},
                {"event": "target_click", "match_id": "m", "token": 1, "target_id": 99},
                {"event": "retry_scheduled", "match_id": "m", "token": 1},
                {"event": "cast_deferred", "match_id": "m", "token": 1},
            ]
            path.write_text("".join("[2026-01-01] [INFO] [SOAK_TARGET_RECOVERY_V1] " + json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            events, errors = parse_events([path])
            result = summarize(events, errors)
        self.assertEqual(result["prompt_count"], 1)
        self.assertEqual(result["event_counts"]["retry_scheduled"], 1)
        self.assertEqual(len(result["unresolved_prompts"]), 1)
        self.assertEqual(result["unresolved_prompts"][0]["cast_deferred_count"], 1)


if __name__ == "__main__":
    unittest.main()
