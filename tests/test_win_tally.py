"""Today's wins read from Player.log, and the daily-reset setting they use.

Reported 2026-11-06: an account whose four wins were won by hand before the bot
started looked untouched, so the bot farmed it for four more. The log carries
every completed match (players, winning team, timestamp), so the tally can be
seeded at startup. "Today" is Arena's day: the reset is a fixed UTC instant
(09:00 by default), not local midnight.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import daily_progress
import win_tally


def match_line(players, winning_team, timestamp_ms, transaction="t-1", scope="MatchScope_Match"):
    """One `MatchCompleted` log line as Arena writes it."""
    payload = {
        "transactionId": transaction,
        "timestamp": str(timestamp_ms),
        "matchGameRoomStateChangedEvent": {
            "gameRoomInfo": {
                "gameRoomConfig": {
                    "matchId": "m-1",
                    "reservedPlayers": [
                        {"playerName": name, "teamId": team} for name, team in players
                    ],
                },
                "stateType": "MatchGameRoomStateType_MatchCompleted",
                "finalMatchResult": {
                    "matchCompletedReason": "MatchCompletedReasonType_GameCompleted",
                    "resultList": [
                        {"result": "ResultType_WinLoss", "winningTeamId": winning_team, "scope": scope}
                    ],
                },
            }
        },
    }
    return "[UnityCrossThreadLogger]" + json.dumps(payload)


class MatchOutcomeTest(unittest.TestCase):
    PLAYERS = [("mycoides", 1), ("Opponent", 2)]

    def test_a_win_is_read_from_the_result(self):
        line = match_line(self.PLAYERS, 1, 1000)
        self.assertEqual(win_tally.match_outcome(line, "mycoides"), (1000, True))

    def test_a_loss_is_read_too(self):
        line = match_line(self.PLAYERS, 2, 1000)
        self.assertEqual(win_tally.match_outcome(line, "mycoides"), (1000, False))

    def test_the_discriminator_does_not_have_to_match(self):
        line = match_line(self.PLAYERS, 1, 1000)
        self.assertEqual(win_tally.match_outcome(line, "Mycoides#47724"), (1000, True))

    def test_another_account_is_not_counted(self):
        line = match_line(self.PLAYERS, 1, 1000)
        self.assertIsNone(win_tally.match_outcome(line, "luteus"))

    def test_a_line_that_is_not_a_completed_match_is_ignored(self):
        self.assertIsNone(win_tally.match_outcome('{"timestamp": "1"}', "mycoides"))


class WinsFromLinesTest(unittest.TestCase):
    PLAYERS = [("mycoides", 1), ("Opponent", 2)]

    def test_only_wins_after_the_cutoff(self):
        lines = [
            match_line(self.PLAYERS, 1, 500, transaction="old"),
            match_line(self.PLAYERS, 1, 2000, transaction="new"),
            match_line(self.PLAYERS, 2, 2500, transaction="loss"),
        ]
        self.assertEqual(win_tally.wins_from_lines(lines, "mycoides", since_epoch_ms=1000), 1)

    def test_the_same_match_logged_twice_counts_once(self):
        line = match_line(self.PLAYERS, 1, 2000, transaction="same")
        self.assertEqual(win_tally.wins_from_lines([line, line], "mycoides", 0), 1)

    def test_four_hand_won_games_are_four_wins(self):
        lines = [
            match_line(self.PLAYERS, 1, 2000 + index * 10, transaction=f"m{index}")
            for index in range(4)
        ]
        self.assertEqual(win_tally.wins_from_lines(lines, "mycoides", 0), 4)


class WinsTodayByAccountTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log_path = os.path.join(self.directory.name, "Player.log")

    def write(self, *lines):
        with open(self.log_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def test_it_counts_per_account_and_respects_the_reset(self):
        now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        reset = daily_progress.day_start_epoch(now)          # 09:00 UTC that day
        before = int((reset - 60) * 1000)
        after = int((reset + 60) * 1000)
        self.write(
            match_line([("mycoides", 1), ("Opponent", 2)], 1, before, transaction="yesterday"),
            match_line([("mycoides", 1), ("Opponent", 2)], 1, after, transaction="m1"),
            match_line([("mycoides", 1), ("Opponent", 2)], 1, after + 10, transaction="m2"),
            match_line([("luteus", 1), ("Opponent", 2)], 2, after + 20, transaction="m3"),
        )
        with mock.patch.object(daily_progress, "day_start_epoch", return_value=reset):
            counts = win_tally.wins_today_by_account(self.log_path, ["mycoides", "luteus"])
        self.assertEqual(counts, {"mycoides": 2})

    def test_the_rotated_log_is_read_as_well(self):
        now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
        reset = daily_progress.day_start_epoch(now)
        after = int((reset + 60) * 1000)
        previous = os.path.join(self.directory.name, "Player-prev.log")
        with open(previous, "w", encoding="utf-8") as handle:
            handle.write(match_line([("mycoides", 1), ("Opponent", 2)], 1, after, transaction="p1") + "\n")
        self.write(match_line([("mycoides", 1), ("Opponent", 2)], 1, after + 10, transaction="c1"))
        with mock.patch.object(daily_progress, "day_start_epoch", return_value=reset):
            counts = win_tally.wins_today_by_account(self.log_path, ["mycoides"])
        self.assertEqual(counts, {"mycoides": 2})

    def test_a_missing_log_is_not_an_error(self):
        counts = win_tally.wins_today_by_account(
            os.path.join(self.directory.name, "nope.log"), ["mycoides"]
        )
        self.assertEqual(counts, {})


class DailyProgressConfigureTest(unittest.TestCase):
    def tearDown(self):
        daily_progress.configure(9)

    def test_the_reset_hour_can_be_changed_and_is_clamped(self):
        self.assertEqual(daily_progress.configure(10), 10)
        self.assertEqual(daily_progress.day_key(datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)), "2026-11-05")
        self.assertEqual(daily_progress.configure(30), 10)     # out of range: kept
        self.assertEqual(daily_progress.configure("x"), 10)
        self.assertEqual(daily_progress.configure(None), 10)
        self.assertEqual(daily_progress.configure(9), 9)

    def test_the_day_starts_at_the_configured_hour(self):
        daily_progress.configure(9)
        moment = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
        start = datetime.fromtimestamp(daily_progress.day_start_epoch(moment), tz=timezone.utc)
        self.assertEqual(start, datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc))

    def test_before_the_reset_the_day_started_yesterday(self):
        daily_progress.configure(9)
        moment = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
        start = datetime.fromtimestamp(daily_progress.day_start_epoch(moment), tz=timezone.utc)
        self.assertEqual(start, datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
