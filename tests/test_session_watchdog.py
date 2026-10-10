import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from state.state_machine import BotState
from tools.session_watchdog import (
    _Thresholds, _classify_line, _detect_stuck_reason, _write_match_record,
)


class SessionWatchdogStallTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = _Thresholds()

    def test_critical_inactivity_timer_is_reported_outside_home(self):
        status = {
            "bot_state": str(BotState.IN_GAME),
            "my_timer_type": "TimerType_Inactivity",
            "my_timer_critical_count": 1,
        }

        self.assertEqual(
            _detect_stuck_reason(status, self.thresholds),
            "repeated_own_timer_critical",
        )

    @patch("tools.session_watchdog.time.time", return_value=1000.0)
    def test_stalled_local_priority_is_reported(self, _time):
        status = {
            "bot_state": str(BotState.IN_GAME),
            "mode": "in_game",
            "my_timer_type": "TimerType_Inactivity",
            "my_timer_running": True,
            "my_timer_elapsed_sec": 60.0,
            "my_timer_remaining_sec": 10.0,
            "last_input_at_epoch": 900.0,
            "last_decision_at_epoch": 900.0,
            "last_playerlog_event_at_epoch": 900.0,
            "local_system_seat_id": 1,
            "turn_info": {"decisionPlayer": 1, "priorityPlayer": 1},
        }

        self.assertEqual(
            _detect_stuck_reason(status, self.thresholds),
            "own_inactivity_timer_stalled",
        )

    @patch("tools.session_watchdog.time.time", return_value=1000.0)
    def test_opponent_priority_is_not_a_local_stall(self, _time):
        status = {
            "bot_state": str(BotState.IN_GAME), "mode": "in_game",
            "my_timer_type": "TimerType_Inactivity", "my_timer_running": True,
            "my_timer_elapsed_sec": 60.0, "my_timer_remaining_sec": 10.0,
            "last_input_at_epoch": 900.0, "local_system_seat_id": 1,
            "turn_info": {"decisionPlayer": 1, "priorityPlayer": 2},
        }
        self.assertIsNone(_detect_stuck_reason(status, self.thresholds))

    def test_failure_signatures(self):
        examples = {
            "Combat recovery exhausted attempts.": "combat_recovery_failed",
            "COMBAT_RECOVERY_ATTEMPT: 1/2 forcing all_attack": None,
            "SUBMIT_SELECTION_FAILED: no submit control recognized (reason=spell)": "submit_failed",
            "SUBMIT_SELECTION_IMG: image not found within 1.5s": None,
            "PAY_COSTS_UNRESOLVED: handler failed": "pay_costs_unresolved",
            "CHOOSER_TARGET_UNCONFIRMED: selection not acknowledged": "chooser_unconfirmed",
            "UNSUPPORTED_CAST: skipping Undying Malice (grpId=123)": "unsupported_cast",
        }
        for line, expected in examples.items():
            with self.subTest(line=line):
                self.assertEqual(_classify_line(line), expected)

    def test_match_record_keeps_canonical_account(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = {"match": {"count": 0, "alerts": {}, "stalled": False, "session_dir": ""}}
            status = {
                "current_account": "Alias#12345",
                "account_aliases": {"Alias#12345": "Configured Account"},
            }
            with patch("tools.session_watchdog._records_session_dir", return_value=root), \
                    patch("tools.session_watchdog._append_text"), \
                    patch("tools.session_watchdog._timestamp", return_value="2026-01-01 00:00:00"), \
                    patch("tools.session_watchdog.time.time", return_value=1.0):
                _write_match_record(
                    state, "session", {"result": "won", "turns": 3, "duration": 10}, status
                )

            record = json.loads((root / "match-0001.json").read_text(encoding="utf-8"))
            self.assertEqual(record["account"], "Configured Account")
            self.assertEqual(record["schema_version"], 2)
            self.assertNotIn("account_attribution_missing", record["alerts"])

    def test_missing_match_account_remains_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = {"match": {"count": 0, "alerts": {}, "stalled": False, "session_dir": ""}}
            with patch("tools.session_watchdog._records_session_dir", return_value=root), \
                    patch("tools.session_watchdog._append_text"), \
                    patch("tools.session_watchdog._history_path", return_value=root / "history.log"), \
                    patch("tools.session_watchdog._timestamp", return_value="2026-01-01 00:00:00"), \
                    patch("tools.session_watchdog.time.time", return_value=1.0):
                _write_match_record(
                    state, "session", {"result": "lost", "turns": 3, "duration": 10}, {}
                )

            record = json.loads((root / "match-0001.json").read_text(encoding="utf-8"))
            self.assertIsNone(record["account"])
            self.assertEqual(record["alerts"], {})

    @patch("tools.session_watchdog.time.time", return_value=1000.0)
    def test_stall_is_ignored_without_local_priority(self, _time):
        status = {
            "bot_state": str(BotState.IN_GAME),
            "mode": "in_game",
            "my_timer_type": "TimerType_Inactivity",
            "my_timer_running": True,
            "my_timer_elapsed_sec": 60.0,
            "last_input_at_epoch": 900.0,
            "local_system_seat_id": 1,
            "turn_info": {"decisionPlayer": 2, "priorityPlayer": 2},
        }

        self.assertIsNone(_detect_stuck_reason(status, self.thresholds))


if __name__ == "__main__":
    unittest.main()
