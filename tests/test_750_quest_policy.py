"""Reward-aware switching uses isolated logs and never accesses the desktop."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import ui
from tests.test_quest_reroll import RerollCase, quest


class Quest750PolicyTests(RerollCase):
    def setUp(self):
        super().setUp()
        self.c._account_switch_mode = "quests"
        self.c._account_switch_main_quests = 2
        self.c._account_switch_daily_wins = 0
        self.c._account_switch_750_only = True

    def read(self, rewards):
        self.append([quest(str(i), gold) for i, gold in enumerate(rewards)])
        self.c.refresh_quests_cache()
        self.assertTrue(self.c._quest_count_confirmed_fresh)

    def test_examples_stop_at_expected_number_of_quests(self):
        for rewards, completions in [
            ([500, 500, 500], 1),
            ([750, 750, 750], 2),
            ([750, 500, 500], 1),
            ([750, 750], 1),
            ([500, 500], 0),
            ([750, 500], 1),
            ([750], 0),
            ([], 0),
        ]:
            with self.subTest(rewards=rewards):
                remaining = list(rewards)
                for completed in range(completions + 1):
                    self.read(remaining)
                    self.assertEqual(self.c._main_quest_target_met(), completed == completions)
                    self.assertEqual(self.c._account_switch_due(), completed == completions)
                    if completed < completions:
                        remaining.remove(max(remaining))

    def test_full_500_fallback_and_absolute_counting_for_all_targets(self):
        for target in (1, 2, 3):
            self.c._account_switch_main_quests = target
            for rewards in ([500, 500, 500], [750, 750, 750]):
                self.read(rewards)
                self.assertFalse(self.c._account_switch_due())
            self.read([500, 500])
            self.assertTrue(self.c._account_switch_due())
            for remaining in (0, 1, 2):
                self.read([750] * remaining)
                self.assertEqual(self.c._account_switch_due(), remaining <= 3 - target)

    def test_unfresh_and_missing_data_cannot_complete_the_quest_pass(self):
        self.assertFalse(self.c._main_quest_target_met())
        self.read([500, 500])
        self.c._quest_count_confirmed_fresh = False
        self.assertFalse(self.c._account_switch_due())
        self.assertIsNone(self.c._select_best_quest())
        self.c._quest_count_confirmed_fresh = True
        self.c._last_valid_quest_active_incomplete = None
        self.assertFalse(self.c._account_switch_due())

    def test_unknown_reward_is_not_treated_as_a_saved_500_quest(self):
        self.read([0, 500])
        self.assertFalse(self.c._account_switch_due())

    def test_completed_entries_and_simultaneous_completions(self):
        self.read([750, 500, 500])
        self.assertFalse(self.c._account_switch_due())
        done = quest("0", 750, progress=20)
        self.append([done, quest("1"), quest("2")])
        self.c.refresh_quests_cache()
        self.assertTrue(self.c._account_switch_due())
        self.assertEqual(self.c._cached_active_quest_id, "")
        self.read([])
        self.assertTrue(self.c._account_switch_due())

    def test_active_quest_starter_and_historic_share_reward_priority(self):
        low = quest("low", 500, guild="Golgari_Guildmage")
        high = quest("high", 750)
        self.append([low, high, quest("other")])
        self.c.refresh_quests_cache()
        self.assertEqual(self.c._cached_active_quest_id, "high")
        self.assertEqual(self.c._resolve_starter_target_colors(), "UG")
        self.assertEqual(self.c._quest_deck_target(), ("UG", None))
        self.append([low, quest("other")])
        self.c.refresh_quests_cache()
        self.c._account_switch_daily_wins = 4
        self.assertTrue(self.c._account_switch_due())
        self.assertEqual(self.c._cached_active_quest_id, "")
        self.assertEqual(self.c._resolve_starter_target_colors(), "WB")
        self.assertEqual(self.c._quest_deck_target(), ("", None))

    def test_non_color_750_quest_takes_priority_over_color_500(self):
        for kind, historic, starter in [
            ("Fatal_Push", ("", "B.png"), "B"),
            ("Raiding_Party", ("", "C.png"), "C"),
            ("Creature", ("C", None), ""),
        ]:
            with self.subTest(kind=kind):
                self.append([quest("low"), quest("high", 750, guild=kind), quest("other")])
                self.c.refresh_quests_cache()
                self.assertEqual(self.c._cached_active_quest_id, "high")
                self.assertEqual(self.c._quest_deck_target(), historic)
                self.assertEqual(self.c._resolve_starter_target_colors(), starter)

    def test_wins_pass_keeps_target_and_previously_earned_wins(self):
        self.read([500, 500])
        self.c._account_switch_daily_wins = 4
        self.c._current_account_screen_name = "player"
        self.c._daily_wins_this_account = 1
        self.assertTrue(self.c._account_switch_due())  # Quest pass ignores wins.
        self.c._switch_phase = "wins"
        self.assertFalse(self.c._account_switch_due())
        self.c._session_wins_by_account = {"player": 4}
        self.c._daily_wins_this_account = 0  # Reset when revisiting for the win pass.
        self.assertTrue(self.c._account_switch_due())

    def test_reward_priority_does_not_assume_unique_quest_ids(self):
        self.append([quest("same", 500), quest("same", 750, guild="Fatal_Push"), quest("other")])
        self.c.refresh_quests_cache()
        self.assertEqual(self.c._quest_deck_target(), ("", "B.png"))

    def test_saved_500_quests_still_allow_normal_round_completion(self):
        for wins in (0, 4):
            with self.subTest(wins=wins):
                self.read([500, 500])
                c = self.c
                c._account_switch_daily_wins = wins
                c._current_account_screen_name = "a"
                c._account_identity_key = lambda name: name
                c._current_account_config_name = lambda: "a"
                c._load_accounts_from_dirs = lambda: [{"name": "a"}, {"name": "b"}]
                c._resolve_account_play_order = lambda accounts: [0, 1]
                c._completed_account_keys = {"b"}
                c.start_queueing = Mock()
                c._request_stop_bot = Mock()
                c.log_out_btn_coors = None  # End safely before any logout UI.
                c.log_out_ok_btn_coors = None
                c._perform_account_switch()
                if wins:
                    self.assertEqual(c._switch_phase, "wins")
                    c._request_stop_bot.assert_not_called()
                else:
                    c._request_stop_bot.assert_called_once_with("all accounts completed this round")

    def test_ignored_in_time_mode_and_with_zero_quest_target(self):
        self.read([500, 500])
        self.c._account_switch_mode = "time"
        self.assertFalse(self.c._750_quest_policy_enabled())
        self.assertFalse(self.c._main_quest_target_met())
        self.c._account_switch_mode = "quests"
        self.c._account_switch_main_quests = 0
        self.assertFalse(self.c._750_quest_policy_enabled())
        self.assertTrue(self.c._main_quest_target_met())
        self.assertFalse(self.c._account_switch_due())  # Both thresholds zero.

    def test_disabled_option_retains_existing_target(self):
        self.c._account_switch_750_only = False
        self.read([500, 500])
        self.assertFalse(self.c._account_switch_due())
        self.assertEqual(self.c._cached_active_quest_id, "0")
        self.read([500])
        self.assertTrue(self.c._account_switch_due())

    def test_new_session_does_not_reuse_the_previous_accounts_decision(self):
        self.read([500, 500])
        self.assertTrue(self.c._account_switch_due())
        self.c._reset_quest_cache_for_new_session()
        self.assertFalse(self.c._account_switch_due())
        self.read([750, 750, 750])
        self.assertFalse(self.c._account_switch_due())

    def test_reroll_is_evaluated_before_skipping_saved_500_quests(self):
        low = quest("keep")
        self.append([quest(), low], canSwap=True)
        self.c.refresh_quests_cache()
        self.assertTrue(self.c._account_switch_due())
        self.c._credited_quest_ids = {"completed-earlier"}

        def click(x, y, tag):
            if tag == "QUEST_REROLL_CONFIRM":
                self.append([quest("replacement", 750), low], canSwap=False)

        self.c._click_abs.side_effect = click
        self.assertTrue(self.c.reroll_quest_on_landing())
        self.assertFalse(self.c._account_switch_due())
        self.assertEqual(self.c._cached_active_quest_id, "replacement")
        self.assertEqual(self.c._credited_quest_ids, {"completed-earlier"})


class Quest750ConfigTests(unittest.TestCase):
    def test_default_old_config_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"account_switch_main_quests": 2}), encoding="utf-8")
            config = ui.ConfigManager(config_path=path)
            self.assertFalse(config.get_account_switch_750_only())
            config.set_account_switch_750_only(True)
            self.assertTrue(ui.ConfigManager(config_path=path).get_account_switch_750_only())
            config.set_account_switch_750_only(False)
            self.assertFalse(ui.ConfigManager(config_path=path).get_account_switch_750_only())

    def test_configured_and_live_status_show_their_own_settings(self):
        config = Mock()
        config.get_account_switch_main_quests.return_value = 2
        config.get_account_switch_daily_wins.return_value = 4
        config.get_account_switch_750_only.return_value = True
        window = Mock(config_manager=config)
        self.assertIn("750 only", ui.MTGBotUI._quests_switch_label(window))
        live = Mock()
        live.get_account_switch_main_quests.return_value = 2
        live.get_account_switch_daily_wins.return_value = 4
        live.get_account_switch_750_only.return_value = False
        self.assertNotIn("750 only", ui.MTGBotUI._live_quests_switch_label(window, live))
