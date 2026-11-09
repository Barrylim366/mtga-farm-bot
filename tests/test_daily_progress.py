"""The day's quests and wins, kept per account on disk, and the Skip button.

Reported 2026-11-06: restarting the bot mid-day made an account that had
already banked its 4 wins earn 4 more, because the counters lived in memory
only. These tests pin the store's rollover/increment rules and the Controller
wiring, plus the manual "skip to next account" request.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import daily_progress
from Controller.MTGAController.Controller import Controller


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "daily_progress.json")
        patcher = mock.patch.object(
            daily_progress, "runtime_file", side_effect=lambda *parts: _Path(self.path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_raw(self, text: str) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)


class _Path:
    """The tiny slice of Path the module uses."""

    def __init__(self, value):
        self._value = value

    def __fspath__(self):
        return self._value

    def __str__(self):
        return self._value

    @property
    def parent(self):
        return self

    def mkdir(self, *_args, **_kwargs):
        return None

    def with_suffix(self, suffix):
        return _Path(os.path.splitext(self._value)[0] + suffix)

    def replace(self, target):
        os.replace(self._value, str(target))


class ArenaDayKeyTest(unittest.TestCase):
    """The day boundary is Arena's 09:00 UTC reset, not this machine's midnight."""

    @staticmethod
    def _utc(*args):
        return datetime(*args, tzinfo=timezone.utc)

    def test_the_new_day_starts_at_nine_utc(self):
        self.assertEqual(daily_progress.day_key(self._utc(2026, 11, 6, 8, 59)), "2026-11-05")
        self.assertEqual(daily_progress.day_key(self._utc(2026, 11, 6, 9, 0)), "2026-11-06")

    def test_ten_pm_auckland_is_the_reset(self):
        """10pm NZDT (UTC+13) is 09:00 UTC, the moment the day rolls over."""
        auckland = timezone(timedelta(hours=13))
        self.assertEqual(
            daily_progress.day_key(datetime(2026, 11, 6, 21, 59, tzinfo=auckland)),
            "2026-11-05",
        )
        self.assertEqual(
            daily_progress.day_key(datetime(2026, 11, 6, 22, 0, tzinfo=auckland)),
            "2026-11-06",
        )

    def test_the_boundary_is_the_same_instant_for_every_zone(self):
        auckland = timezone(timedelta(hours=13))
        new_york = timezone(timedelta(hours=-5))
        moment_utc = self._utc(2026, 11, 6, 9, 0)
        self.assertEqual(
            daily_progress.day_key(moment_utc),
            daily_progress.day_key(moment_utc.astimezone(auckland)),
        )
        self.assertEqual(
            daily_progress.day_key(moment_utc),
            daily_progress.day_key(moment_utc.astimezone(new_york)),
        )

    def test_a_naive_datetime_is_read_as_utc(self):
        self.assertEqual(
            daily_progress.day_key(datetime(2026, 11, 6, 12, 0)),
            daily_progress.day_key(self._utc(2026, 11, 6, 12, 0)),
        )


class DailyWinsTest(_StoreCase):
    def test_a_win_is_counted_and_survives_a_fresh_read(self):
        self.assertEqual(daily_progress.wins("mycoides#47724"), 0)
        self.assertEqual(daily_progress.record_win("mycoides#47724"), 1)
        self.assertEqual(daily_progress.record_win("mycoides#47724"), 2)
        # A restart is just another read of the same file.
        self.assertEqual(daily_progress.wins("mycoides#47724"), 2)

    def test_accounts_are_counted_separately(self):
        daily_progress.record_win("alpha#1")
        daily_progress.record_win("bravo#2")
        daily_progress.record_win("bravo#2")
        self.assertEqual(daily_progress.wins("alpha#1"), 1)
        self.assertEqual(daily_progress.wins("bravo#2"), 2)

    def test_a_new_day_starts_from_zero(self):
        self.write_raw(
            '{"alpha#1": {"date": "2000-01-01", "wins": 4, "quests": 3, "open_quests": 0}}'
        )
        self.assertEqual(daily_progress.wins("alpha#1"), 0)
        self.assertEqual(daily_progress.quests_completed("alpha#1"), 0)
        self.assertEqual(daily_progress.record_win("alpha#1"), 1)

    def test_the_unknown_account_placeholder_is_never_persisted(self):
        self.assertEqual(daily_progress.record_win("(current account)"), 0)
        self.assertEqual(daily_progress.wins("(current account)"), 0)

    def test_an_unreadable_file_means_nothing_counted(self):
        self.write_raw("{not json")
        self.assertEqual(daily_progress.wins("alpha#1"), 0)
        self.assertEqual(daily_progress.record_win("alpha#1"), 1)


class DailyQuestsTest(_StoreCase):
    def test_the_first_look_only_sets_the_baseline(self):
        self.assertEqual(daily_progress.observe_open_quests("alpha#1", 3), 0)

    def test_a_quest_closing_counts_one(self):
        daily_progress.observe_open_quests("alpha#1", 3)
        self.assertEqual(daily_progress.observe_open_quests("alpha#1", 2), 1)
        self.assertEqual(daily_progress.quests_completed("alpha#1"), 1)

    def test_two_quests_closing_at_once_count_two(self):
        daily_progress.observe_open_quests("alpha#1", 3)
        self.assertEqual(daily_progress.observe_open_quests("alpha#1", 1), 2)

    def test_a_new_quest_arriving_is_not_a_completion(self):
        daily_progress.observe_open_quests("alpha#1", 1)
        self.assertEqual(daily_progress.observe_open_quests("alpha#1", 3), 0)
        self.assertEqual(daily_progress.observe_open_quests("alpha#1", 2), 1)

    def test_the_count_survives_a_restart(self):
        daily_progress.observe_open_quests("alpha#1", 3)
        daily_progress.observe_open_quests("alpha#1", 2)
        # Restart: the baseline is still on disk, so the next close still counts.
        self.assertEqual(daily_progress.observe_open_quests("alpha#1", 1), 2)

    def test_snapshot_only_lists_today(self):
        daily_progress.record_win("alpha#1")
        snapshot = daily_progress.snapshot()
        self.assertIn("alpha#1", snapshot)
        self.assertEqual(snapshot["alpha#1"]["wins"], 1)


class _ControllerCase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        self.c = Controller(handle.name)
        self.c.input = mock.Mock()
        self.c._click_abs = mock.Mock()
        self.c._locate_image_center_in_scaled_arena_region = mock.Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = mock.Mock(return_value=False)
        self.c._current_account_screen_name = "alpha#11111"


class WinsSeenFromDiskTest(_ControllerCase):
    def test_wins_seen_includes_todays_stored_wins(self):
        with mock.patch.object(daily_progress, "wins", return_value=3):
            self.assertEqual(self.c._wins_seen_for_current_account(), 3)
            self.c._daily_wins_this_account = 1        # this visit
            self.assertEqual(self.c._wins_seen_for_current_account(), 3)

    def test_a_restart_does_not_reset_the_target(self):
        """The reported bug: 4 wins before a restart, then another 4."""
        with mock.patch.object(daily_progress, "wins", return_value=4):
            self.c._account_switch_daily_wins = 4
            self.c._account_switch_main_quests = 1
            self.c._account_switch_mode = "quests"
            self.c._account_switch_enabled = True
            self.c._switch_phase = "wins"
            self.assertGreaterEqual(self.c._wins_seen_for_current_account(), 4)
            self.assertTrue(self.c._account_switch_due())


class SkipToNextAccountTest(_ControllerCase):
    def test_a_request_makes_the_switch_due_even_with_the_toggle_off(self):
        self.c._account_switch_enabled = False
        self.assertFalse(self.c._account_switch_due())
        self.assertTrue(self.c.request_skip_to_next_account())
        self.assertTrue(self.c._account_switch_due())

    def test_a_request_is_refused_while_a_switch_runs(self):
        self.c._account_switch_in_progress = True
        self.assertFalse(self.c.request_skip_to_next_account())
        self.assertFalse(self.c._skip_to_next_account_requested)

    def test_a_request_resets_the_give_up_guard(self):
        self.c._max_failed_switch_attempts = 3
        self.c._failed_switch_attempts = 3
        self.c._failed_switch_giveup_logged = True
        self.assertTrue(self.c.request_skip_to_next_account())
        self.assertEqual(self.c._failed_switch_attempts, 0)
        self.assertFalse(self.c._failed_switch_giveup_logged)
        self.assertTrue(self.c._account_switch_due())


class FinishedAccountFallbackTest(_ControllerCase):
    def _finished(self):
        self.c._account_switch_mode = "quests"
        self.c._quest_target_mode = "complete"
        self.c._account_switch_quests_to_complete = 1
        self.c._quest_count_confirmed_fresh = True
        self.c._last_valid_quest_active_incomplete = 0
        self.c._max_failed_switch_attempts = 3
        self.c._failed_switch_attempts = 3

    def test_a_finished_account_stops_when_switching_is_disabled(self):
        self._finished()
        self.assertTrue(self.c._should_stop_instead_of_queueing())

    def test_no_give_up_does_not_stop(self):
        self._finished()
        self.c._failed_switch_attempts = 0
        self.assertFalse(self.c._should_stop_instead_of_queueing())

    def test_an_unfinished_account_still_plays(self):
        self._finished()
        self.c._last_valid_quest_active_incomplete = 2
        self.assertFalse(self.c._should_stop_instead_of_queueing())


if __name__ == "__main__":
    unittest.main()
