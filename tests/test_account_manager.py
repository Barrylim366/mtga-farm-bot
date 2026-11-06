"""The Account Manager's account list, from the folders Manage Accounts writes.

No Tk here: these drive the real ConfigManager loader and the window's two pure
helpers, which is the part that can silently break. The drawing is checked by
looking at it.
"""
import json
import os
import tempfile
import unittest

import ui
from account_balances import read_balances, player_log_paths
from ui import AccountManagerWindow


class ManageAccountsFolderTest(unittest.TestCase):
    """A live instance (2026-11-05) holds Accounts/<label>/credentials.json and
    stores the BARE visible name (mycoides) where the log writes mycoides#47724."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = self.directory.name
        self.accounts_dir = os.path.join(root, "Accounts")
        os.makedirs(self.accounts_dir)
        self.log_path = os.path.join(root, "Player.log")
        with open(self.log_path, "w", encoding="utf-8") as handle:
            handle.writelines([
                "[Accounts - Login] Logged in successfully. Display Name: mycoides#47724\n",
                '{ "InventoryInfo": { "SeqId": 1, "Gems": 5000, "Gold": 44950 } }\n',
            ])
        self.balances = read_balances(player_log_paths(self.log_path))

    def write_account(self, folder, label, screen_name=None, pw="pw"):
        target = os.path.join(self.accounts_dir, folder)
        os.makedirs(target, exist_ok=True)
        details = {"email": f"{folder}@example.test", "pw": pw}
        if screen_name is not None:
            details["screen_name"] = screen_name
        with open(os.path.join(target, "credentials.json"), "w", encoding="utf-8") as handle:
            json.dump({label: details}, handle)

    def accounts(self):
        cm = ui.ConfigManager.__new__(ui.ConfigManager)
        cm.config = {}
        cm._save_config = lambda: None
        cm._accounts_root = lambda: self.accounts_dir
        cm._account_scan_dirs = lambda: [self.accounts_dir]
        return cm.get_managed_accounts()

    def test_the_folders_list_as_accounts(self):
        self.write_account("mycoides", "mycoides", screen_name="mycoides")
        self.write_account("luteus", "luteus")
        names = [AccountManagerWindow._account_label(row) for row in self.accounts()]
        self.assertEqual(sorted(names), ["luteus", "mycoides"])

    def test_a_bare_stored_name_still_finds_its_balance(self):
        self.write_account("mycoides", "mycoides", screen_name="mycoides")
        row = self.accounts()[0]
        self.assertEqual(AccountManagerWindow._balance_for(self.balances, row)["gold"], 44950)

    def test_a_row_without_a_screen_name_matches_by_its_label(self):
        self.write_account("mycoides", "mycoides")
        row = self.accounts()[0]
        self.assertEqual(AccountManagerWindow._balance_for(self.balances, row)["gold"], 44950)

    def test_a_folder_without_credentials_is_not_an_account(self):
        self.write_account("luteus", "luteus")
        os.makedirs(os.path.join(self.accounts_dir, "not_an_account"))
        self.assertEqual(len(self.accounts()), 1)

    def test_a_row_without_a_password_is_skipped(self):
        self.write_account("luteus", "luteus")
        self.write_account("halfwritten", "halfwritten", pw="")
        self.assertEqual(len(self.accounts()), 1)


class LabelTest(unittest.TestCase):
    def test_the_discriminator_never_reaches_the_table(self):
        self.assertEqual(
            AccountManagerWindow._account_label({"screen_name": "mycoides#47724"}), "mycoides"
        )

    def test_the_label_stands_in_for_a_row_without_an_arena_name(self):
        self.assertEqual(AccountManagerWindow._account_label({"name": "luteus"}), "luteus")

    def test_an_unknown_account_has_no_balance(self):
        self.assertIsNone(AccountManagerWindow._balance_for({}, {"screen_name": "nobody"}))


class SortTest(unittest.TestCase):
    """Clicking a header sorts by it: largest first, then smallest first."""

    ACCOUNTS = [
        {"screen_name": "alpha"},
        {"screen_name": "bravo"},
        {"screen_name": "charlie"},
        {"screen_name": "delta"},  # never signed in: no balance
    ]
    BALANCES = {
        "alpha": {"gold": 59550, "gems": 3300},
        "bravo": {"gold": 2975, "gems": 10235},
        "charlie": {"gold": 44950, "gems": 40},
    }

    def order(self, column, descending):
        return [
            account["screen_name"]
            for account in AccountManagerWindow._sort_accounts(
                self.ACCOUNTS,
                self.BALANCES,
                column,
                descending,
                AccountManagerWindow._account_label,
                lambda account: AccountManagerWindow._balance_for(self.BALANCES, account),
            )
        ]

    def test_without_a_click_the_list_is_alphabetical(self):
        self.assertEqual(self.order(None, True), ["alpha", "bravo", "charlie", "delta"])

    def test_the_first_click_sorts_largest_first(self):
        self.assertEqual(self.order("gold", True), ["alpha", "charlie", "bravo", "delta"])

    def test_the_second_click_reverses_it(self):
        self.assertEqual(self.order("gold", False), ["bravo", "charlie", "alpha", "delta"])

    def test_a_column_with_no_reading_stays_at_the_bottom(self):
        """delta has no balance: it is missing data, not the smallest figure."""
        self.assertEqual(self.order("gems", False)[-1], "delta")
        self.assertEqual(self.order("gems", True)[-1], "delta")

    def test_the_account_header_sorts_by_name_either_way(self):
        self.assertEqual(self.order("account", False), ["alpha", "bravo", "charlie", "delta"])
        self.assertEqual(self.order("account", True), ["delta", "charlie", "bravo", "alpha"])


if __name__ == "__main__":
    unittest.main()
