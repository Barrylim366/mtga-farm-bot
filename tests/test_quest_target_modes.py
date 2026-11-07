"""The two quest-target modes: "complete N per day" and "leave N open".

The original rule was absolute: leave at most `3 - N` quests open, so a slot
emptied on an earlier day counted as a completion today. The new "complete"
mode counts the quests closed since the bot first saw the account today, which
is what makes it guarantee one completion every day. Both are configuration
plus pure decision logic, so these tests build a Controller against a throwaway
log and never touch the screen.
"""
import json
import os
import tempfile
import unittest

import ui
from Controller.MTGAController.Controller import Controller


class QuestTargetConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "calibration_config.json")
        # An install that predates the mode: only the legacy number is stored.
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"account_switch_main_quests": 1}, handle)
        self.config = ui.ConfigManager(self.path)

    def test_an_existing_install_defaults_to_leave_open(self):
        self.assertEqual(self.config.get_account_switch_quest_target(), "leave")
        # Legacy "complete 1" is "leave 2 open", exactly today's behaviour.
        self.assertEqual(self.config.get_account_switch_quests_to_leave(), 2)

    def test_the_complete_count_starts_from_the_legacy_number(self):
        self.assertEqual(self.config.get_account_switch_quests_to_complete(), 1)

    def test_each_mode_keeps_its_own_number(self):
        self.config.set_account_switch_quest_target("complete")
        self.config.set_account_switch_quests_to_complete(2)
        self.config.set_account_switch_quest_target("leave")
        self.config.set_account_switch_quests_to_leave(1)
        self.assertEqual(self.config.get_account_switch_quests_to_complete(), 2)
        self.assertEqual(self.config.get_account_switch_quests_to_leave(), 1)
        # Leave 1 is the legacy "complete 2" (3 - 1).
        self.assertEqual(self.config.get_account_switch_main_quests(), 2)

    def test_leaving_all_three_is_the_old_disabled_number(self):
        self.config.set_account_switch_quests_to_leave(3)
        self.assertEqual(self.config.get_account_switch_main_quests(), 0)
        self.assertEqual(self.config.get_account_switch_quests_to_leave(), 3)

    def test_the_mode_and_numbers_survive_a_restart(self):
        self.config.set_account_switch_quest_target("complete")
        self.config.set_account_switch_quests_to_complete(2)
        reloaded = ui.ConfigManager(self.path)
        self.assertEqual(reloaded.get_account_switch_quest_target(), "complete")
        self.assertEqual(reloaded.get_account_switch_quests_to_complete(), 2)

    def test_an_unknown_mode_falls_back_to_leave(self):
        self.config.config["account_switch_quest_target"] = "nonsense"
        self.assertEqual(self.config.get_account_switch_quest_target(), "leave")


class _QuestTargetCase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        handle.close()
        self.log_path = handle.name
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))
        self.c = Controller(self.log_path)
        self.c._account_switch_mode = "quests"
        self.c._account_switch_daily_wins = 0
        # The day's tally is disk-backed; these tests are about the comparison.
        self.c._quests_completed_today = lambda: 0

    def make_complete(self, count):
        self.c._quest_target_mode = "complete"
        self.c._account_switch_quests_to_complete = count
        self.c._account_switch_main_quests = count
        self.c._last_valid_quest_active_incomplete = 2
        self.c._quest_count_confirmed_fresh = True

    def make_leave(self, legacy):
        self.c._quest_target_mode = "leave"
        self.c._account_switch_main_quests = legacy
        self.c._last_valid_quest_active_incomplete = 2
        self.c._quest_count_confirmed_fresh = True


class QuestTargetDecisionTests(_QuestTargetCase):
    def test_leave_mode_keeps_the_absolute_rule(self):
        self.make_leave(1)
        self.assertTrue(self.c._main_quest_target_met())   # 2 open <= 2
        self.c._last_valid_quest_active_incomplete = 3
        self.assertFalse(self.c._main_quest_target_met())

    def test_leave_mode_is_met_when_fewer_quests_are_open_than_the_allowance(self):
        # "Leave 2 open" with only one quest open: nothing to play, straight to
        # the wins.
        self.make_leave(1)  # legacy complete 1 == leave 2 open
        self.c._last_valid_quest_active_incomplete = 1
        self.assertTrue(self.c._main_quest_target_met())

    def test_complete_mode_does_not_credit_a_carried_empty_slot(self):
        # Two quests open is the old "target met" (one completion's worth), but
        # nothing has been completed today, so the new mode keeps playing.
        self.make_complete(1)
        self.assertFalse(self.c._main_quest_target_met())
        self.c._quests_completed_today = lambda: 1
        self.assertTrue(self.c._main_quest_target_met())

    def test_complete_mode_is_met_when_there_is_nothing_left_to_do(self):
        self.make_complete(1)
        self.c._last_valid_quest_active_incomplete = 0
        self.assertTrue(self.c._main_quest_target_met())

    def test_complete_mode_stops_once_the_open_quests_are_done(self):
        # "Complete 2" with only one quest open: play it, then the quota is
        # unreachable today, so the quest side is done and the wins can follow.
        self.make_complete(2)
        self.c._last_valid_quest_active_incomplete = 1
        self.assertFalse(self.c._main_quest_target_met())
        self.c._last_valid_quest_active_incomplete = 0
        self.assertTrue(self.c._main_quest_target_met())

    def test_a_set_aside_quest_waives_the_quota_in_both_modes(self):
        # No deck for the quest: the quest aspect is considered finished, so the
        # account can play for its wins and move on.
        self.make_complete(2)
        self.assertFalse(self.c._main_quest_target_met())
        self.c._quest_target_unavailable = True
        self.assertTrue(self.c._main_quest_target_met())
        self.make_leave(2)
        self.c._quest_target_unavailable = False
        self.assertFalse(self.c._main_quest_target_met())
        self.c._quest_target_unavailable = True
        self.assertTrue(self.c._main_quest_target_met())

    def test_complete_mode_still_needs_a_fresh_read(self):
        self.make_complete(1)
        self.c._quest_count_confirmed_fresh = False
        self.assertFalse(self.c._main_quest_target_met())

    def test_complete_mode_switches_only_after_its_own_count(self):
        self.make_complete(2)
        self.c._quests_completed_today = lambda: 1
        self.assertFalse(self.c._account_switch_due())
        self.c._quests_completed_today = lambda: 2
        self.assertTrue(self.c._account_switch_due())

    def test_a_complete_target_counts_as_configured_for_the_round(self):
        self.make_complete(1)
        self.assertTrue(self.c._quest_target_active())
        self.make_leave(0)  # leave all three = no quest requirement
        self.assertFalse(self.c._quest_target_active())

    def test_the_750_only_early_finish_belongs_to_the_leave_mode(self):
        # Two 500-gold quests and a free slot: the 750-only policy calls that
        # done in leave-open mode (keep the 500s for tomorrow's reroll), but
        # "complete 2" has not completed anything yet.
        self.c._account_switch_750_only = True
        self.c._cached_quests = [{"gold": 500}, {"gold": 500}]
        self.make_leave(2)   # leave 1 open; 2 open does not meet it on its own
        self.assertTrue(self.c._main_quest_target_met())
        self.make_complete(2)
        self.assertFalse(self.c._main_quest_target_met())
        self.c._quests_completed_today = lambda: 2
        self.assertTrue(self.c._main_quest_target_met())


class QuestSetAsideDeckTests(_QuestTargetCase):
    """No deck for the quest: play the win deck instead of retrying forever."""

    def test_the_historic_target_falls_back_to_the_preferred_deck(self):
        self.c._preferred_historic_deck = "My Win Deck.png"
        self.c._only_grind_preferred_deck = False
        self.c._last_valid_quest_active_incomplete = 2
        self.c._quest_count_confirmed_fresh = True
        self.c._quest_target_unavailable = True
        self.assertEqual(self.c._quest_deck_target("Test"), ("", "My Win Deck.png"))

    def test_the_starter_target_is_the_win_deck(self):
        self.c._preferred_starter_deck = ""
        self.c._quest_target_unavailable = True
        self.assertEqual(self.c._resolve_starter_target_colors(), "WB")

    def _select_with(self, deck_image):
        c = self.c
        c._historic_deck_candidate_accounts = lambda planned=None: [
            {"name": "acct", "folder": "acct"}
        ]
        c._choose_deck_image = lambda _account, _colors, _forced: deck_image
        c._selected_deck_variant = lambda _image: None
        c._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
        c._click_image = lambda *a, **k: False
        c._deck_is_known_selected = lambda *_a: False
        return c._select_historic_deck_for_quest("UB", None)

    def test_no_configured_thumbnail_sets_the_quest_aside_at_once(self):
        self.assertIsNone(self._select_with(os.path.join("C:/decks", "RG.png")))
        self.assertTrue(self.c._quest_target_unavailable)

    def test_a_thumbnail_that_never_appears_is_set_aside_after_two_misses(self):
        # The image exists but the grid never shows it (a stale capture after a
        # rename): one miss is retried, two in a row set the quest aside.
        self.assertIsNone(self._select_with(os.path.join("C:/decks", "UB.png")))
        self.assertFalse(self.c._quest_target_unavailable)
        self.c._note_historic_deck_selection_failed("UB", None)
        self.assertFalse(self.c._quest_target_unavailable)
        self.c._note_historic_deck_selection_failed("UB", None)
        self.assertTrue(self.c._quest_target_unavailable)

    def test_no_target_never_sets_the_quest_aside(self):
        self.c._note_historic_deck_selection_failed("", None)
        self.c._note_historic_deck_selection_failed("", None)
        self.assertFalse(self.c._quest_target_unavailable)

    def test_a_missing_preferred_win_deck_falls_back_to_the_first_tile(self):
        # The quest is already set aside, and now the preferred win deck has no
        # thumbnail either: any deck will do, so the first tile is used rather
        # than failing on a target the account does not own.
        self.c._quest_target_unavailable = True
        self.c._click_first_deck_slot = lambda: True
        selection = self._select_with(os.path.join("C:/decks", "RG.png"))
        self.assertEqual(selection, (self.c._FIRST_DECK_SENTINEL, "acct"))


if __name__ == "__main__":
    unittest.main()
