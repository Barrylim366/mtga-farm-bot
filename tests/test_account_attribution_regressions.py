"""Replay bare/tagged Gold identities and stale startup pins without Arena I/O."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from Controller.MTGAController.Controller import Controller
from tests.test_account_identity import handshake_line, inventory_line


class AccountAttributionRegressions(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log = Path(self.directory.name) / "Player.log"
        self.log.write_text("", encoding="utf-8")
        self.accounts = [
            {"name": "ulfara#70487", "screen_name": "ulfara#70487"},
            {"name": "ChristmasIsland#32386", "screen_name": "ChristmasIsland#32386"},
        ]
        # Configuration and all visual/input side effects are synthetic.
        with patch.object(Controller, "_load_accounts_from_dirs", return_value=self.accounts), \
                patch.object(Controller, "_persist_aliases"):
            self.c = Controller(str(self.log))
        self.c._load_accounts_from_dirs = lambda: self.accounts
        self.c._screenname_to_alias = {}
        for name in ("_publish_gold_farmed", "_publish_account_switch_status", "_persist_aliases",
                     "_start_popup_recovery", "_forget_selected_deck", "_forget_starter_deck",
                     "_locate_image_center_in_scaled_arena_region", "_click_image_in_scaled_arena_region"):
            setattr(self.c, name, Mock(return_value=None))

    def append(self, line):
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def balance(self, value):
        self.append(inventory_line(value))
        self.c._update_gold_from_inventory()

    def test_bare_name_becoming_tagged_preserves_first_baseline(self):
        self.c.begin_session()
        self.c._current_account_screen_name = "ulfara"
        self.balance(2000)
        self.balance(2750)
        self.c._current_account_screen_name = "ulfara#70487"
        self.c._register_current_account_for_gold()
        self.balance(3650)
        self.assertEqual(self.c._account_initial_gold, {"ulfara#70487": 2000})
        self.assertEqual(self.c._gold_farmed_by_account, {"ulfara#70487": 1650})

    def test_existing_split_rows_use_first_observation_not_lowest_balance_or_sum(self):
        self.c._current_account_screen_name = "ulfara#70487"
        self.c._account_initial_gold = {"ulfara": 4000, "ulfara#70487": 2750}
        self.c._gold_farmed_by_account = {"ulfara": 750, "ulfara#70487": 900}
        self.balance(3650)  # spending: the later lower baseline must not win
        self.assertEqual(self.c._account_initial_gold, {"ulfara#70487": 4000})
        self.assertEqual(self.c._gold_farmed_by_account, {"ulfara#70487": 0})

    def test_split_rows_recalculate_1650_from_fresh_balance(self):
        self.c._current_account_screen_name = "ulfara#70487"
        self.c._account_initial_gold = {"ulfara": 2000, "ulfara#70487": 2750}
        self.c._gold_farmed_by_account = {"ulfara": 750, "ulfara#70487": 900}
        self.balance(3650)
        self.assertEqual(self.c._gold_farmed_by_account, {"ulfara#70487": 1650})

    def test_case_and_return_visit_share_baseline_but_other_tags_never_merge(self):
        self.c._current_account_screen_name = "ULFARA"
        self.balance(2000)
        self.c._current_account_screen_name = "ulfara#99999"
        self.balance(7000)
        self.c._current_account_screen_name = "ULFARA#70487"
        self.balance(3650)
        self.assertEqual(self.c._gold_farmed_by_account,
                         {"ulfara#70487": 1650, "ulfara#99999": 0})

    def test_legacy_bare_configuration_can_upgrade_to_observed_full_identity(self):
        self.accounts[0] = {"name": "ulfara", "screen_name": "ulfara"}
        self.c._current_account_screen_name = "ulfara"
        self.balance(2000)
        self.c._current_account_screen_name = "ulfara#70487"
        self.balance(3650)
        self.assertEqual(self.c._account_initial_gold, {"ulfara#70487": 2000})
        self.assertEqual(self.c._gold_farmed_by_account, {"ulfara#70487": 1650})

    def test_duplicate_visible_names_remain_unresolved_without_tag(self):
        self.accounts.append({"name": "ulfara#99999", "screen_name": "ulfara#99999"})
        self.c._current_account_screen_name = "ulfara"
        self.balance(2000)
        self.assertEqual(self.c._account_initial_gold, {})
        self.c._current_account_screen_name = "ulfara#70487"
        self.balance(2000)
        self.c._current_account_screen_name = "ulfara#99999"
        self.balance(8000)
        self.assertEqual(len(self.c._account_initial_gold), 2)

    def test_stale_seeded_pin_corrected_before_first_game_without_quests(self):
        self.c.set_current_account_manual("ChristmasIsland#32386", seeded=True)
        self.append(handshake_line("ulfara"))
        self.c.begin_session()
        identity = self.c.capture_match_account("first-game")
        self.assertEqual(identity, {"match_id": "first-game", "account": "ulfara#70487", "source": "login"})
        self.balance(2000)
        self.assertNotIn("ChristmasIsland#32386", self.c._account_initial_gold)

    def test_fresh_manual_pin_is_not_overridden_by_older_handshake(self):
        self.append(handshake_line("ulfara"))
        self.c.set_current_account_manual("ChristmasIsland#32386")
        self.c.begin_session()
        self.assertEqual(self.c.capture_match_account("first")["account"], "ChristmasIsland#32386")

    def test_seeded_pin_with_no_identity_evidence_does_not_credit_gold(self):
        self.c.set_current_account_manual("ChristmasIsland#32386", seeded=True)
        self.c.begin_session()
        self.balance(2000)
        self.assertEqual(self.c._account_initial_gold, {})
        self.assertIsNone(self.c.capture_match_account("first")["account"])

    def test_known_match_account_survives_later_account_switch(self):
        self.append(handshake_line("ulfara"))
        before = self.c.capture_match_account("first")
        self.c._quests_valid_from_offset = self.log.stat().st_size
        self.c._latch_identity_from_switch_target(self.accounts[1])
        self.assertEqual(self.c.capture_match_account("first"), before)

    def test_unknown_match_is_not_attributed_to_a_later_switch(self):
        self.assertIsNone(self.c.capture_match_account("first")["account"])
        self.append("switch boundary\n")
        self.c._quests_valid_from_offset = self.log.stat().st_size
        self.c._latch_identity_from_switch_target(self.accounts[1])
        self.assertIsNone(self.c.capture_match_account("first")["account"])

    def test_unknown_match_can_acquire_fresh_authentication_on_same_visit(self):
        self.assertIsNone(self.c.capture_match_account("first")["account"])
        self.append(handshake_line("ulfara"))
        self.assertEqual(self.c.capture_match_account("first")["account"], "ulfara#70487")
