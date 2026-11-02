import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from state.state_machine import BotState
from tools.session_watchdog import (
    _Thresholds, _classify_line, _detect_stuck_reason, _write_match_record, _process_new_lines,
)


class SessionWatchdogStallTests(unittest.TestCase):
    def test_match_identity_survives_later_ui_account_switch_and_is_consumed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = {}
            status = {"current_account": "ChristmasIsland#32386"}
            lines = '\n'.join([
                '[MATCH_IDENTITY] {"match_id":"game-1","account":"ulfara#70487","source":"login"}',
                '[MATCH_END] result=won turns=8 duration_sec=100.00',
                '[MATCH_IDENTITY] {"match_id":"game-2","account":null,"source":"unknown"}',
                '[MATCH_END] result=lost turns=3 duration_sec=50.00',
            ])
            with patch("tools.session_watchdog._records_session_dir", return_value=root), \
                    patch("tools.session_watchdog._append_text"):
                _process_new_lines(state, "session", lines, status)
            first = json.loads((root / "match-0001.json").read_text())
            second = json.loads((root / "match-0002.json").read_text())
            self.assertEqual(first["account"], "ulfara#70487")
            self.assertEqual(first["match_id"], "game-1")
            self.assertEqual(first["account_identity_source"], "login")
            self.assertEqual(first["schema_version"], 3)
            self.assertIsNone(second["account"])
            self.assertEqual(second["account_identity_source"], "unknown")
            self.assertNotIn("identity", state["match"])

    def test_match_end_writes_identity_and_result_in_one_log_operation(self):
        import bot_logger
        with patch.object(bot_logger, "_write_lines") as write:
            identity = {"match_id": "game", "account": "ulfara#70487", "source": "login"}
            bot_logger.log_match_end("won", 8, 100, identity=identity)
        write.assert_called_once()
        lines = write.call_args.args[1]
        self.assertEqual(json.loads(lines[0].split("[MATCH_IDENTITY] ")[1]), identity)
        self.assertIn("[MATCH_END] result=won", lines[1])

    def test_activation_timeout_counts_without_stall_or_concede(self):
        state = {}
        lines = "\n".join([
            "FORGE ABILITY_ACTIVATION_CLICKED: card=290 ability=21730",
            "FORGE ABILITY_ACTIVATION_ACKNOWLEDGED: card=290 ability=21730",
            "FORGE ABILITY_ACTIVATION_TIMEOUT: card=291 ability=21730",
            "FORGE ABILITY_ACTIVATION_TIMEOUT: card=292 ability=21730",
        ])
        with patch("tools.session_watchdog._append_text") as append:
            _process_new_lines(state, "session", lines, {})
        self.assertEqual(state["match"]["alerts"], {"ability_activation_timeout": 2})
        self.assertFalse(state["match"]["stalled"])
        self.assertEqual(append.call_count, 1)  # writes throttled, counts preserved

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
            self.assertEqual(record["account_identity_source"], "legacy_status")
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
