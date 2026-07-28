"""Unit tests for the Manage Accounts rename that collapses two name fields into one.

An account used to carry two names: a free-text `name` the user picked, and a
`screen_name` that was the actual in-game Arena name. They could drift, and when
they did the Current Session panel showed a label that appears nowhere in Arena.
There is one field now, and it is the Arena name -- so opening a legacy row and
saving it RENAMES that account.

A rename is the dangerous part: the account is stored on disk in a folder whose
credentials.json is keyed by the old name, and the play order and the manual
current-account pin both reference accounts by name. Miss any of those and the
account silently drops out of the rotation -- which in quests mode makes the bot
decide the round is finished and stop itself.

These tests drive the real ConfigManager against a temp directory; no Tk window is
created (the dialog's own methods are exercised through a bare instance).
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ui


class _Var:
    def __init__(self):
        self.value = ""

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Rows:
    """The dialog's row list, without building a Tk window for it."""

    def __init__(self, config_manager, rows):
        self._config_manager = config_manager
        self._accounts_data = rows

    _collect_pending_renames = ui.SwitchAccountWindow._collect_pending_renames


class _Dialog(_Rows):
    """Enough of the dialog to run its real save/clear methods with no Tk window.

    Everything stubbed here is presentation (redraw, focus, message boxes); the
    methods under test are the real ones off the class."""

    def __init__(self, rows, config_manager=None):
        super().__init__(config_manager, rows)
        self._selected_account_idx = 0
        self.errors = []
        self.account_name_var = _Var()
        self.account_email_var = _Var()
        self.account_pw_var = _Var()

    def _refresh_accounts_table(self):
        pass

    def _refresh_order_choices(self):
        pass

    def _apply_details_to_selected(self, validate, show_error=False):
        return True

    def lift(self):
        pass

    def focus_force(self):
        pass

    _clear_selected_account_row = ui.SwitchAccountWindow._clear_selected_account_row
    _collect_accounts_for_save = ui.SwitchAccountWindow._collect_accounts_for_save
    _save_accounts = ui.SwitchAccountWindow._save_accounts


def row(name: str, *, stored: str | None = None, email: str = "a@x", pw: str = "p") -> dict:
    return {
        "name": name,
        "screen_name": name,
        "stored_name": name if stored is None else stored,
        "email": email,
        "pw": pw,
        "folder": "",
    }


class PendingRenameTest(unittest.TestCase):
    def rows(self, *rows_):
        return _Rows(None, list(rows_))

    def test_an_unchanged_row_is_not_a_rename(self):
        self.assertEqual(self.rows(row("Bob"))._collect_pending_renames(), {})

    def test_a_legacy_row_whose_arena_name_differs_is_a_rename(self):
        self.assertEqual(
            self.rows(row("RealArenaName", stored="MyLabel"))._collect_pending_renames(),
            {"MyLabel": "RealArenaName"},
        )

    def test_a_reused_slot_is_not_a_rename_of_the_account_deleted_from_it(self):
        """Delete an account, type a different one into the same row, save. If the
        deleted name were still attached to the slot, the new account would inherit
        its play-order position AND its manual pin -- so the bot would believe it is
        signed into an account it has never logged into."""
        dialog = _Dialog([row("OldAcct")])
        dialog._clear_selected_account_row()
        slot = dialog._accounts_data[0]
        slot.update({"name": "BrandNew", "screen_name": "BrandNew",
                     "email": "n@x", "pw": "p"})
        self.assertEqual(dialog._collect_pending_renames(), {})

    def test_case_only_changes_are_not_renames(self):
        """Every name comparison in the bot is casefolded, so re-keying on case
        would be churn -- and would rewrite the play order for nothing."""
        self.assertEqual(
            self.rows(row("bob", stored="Bob"))._collect_pending_renames(), {}
        )

    def test_a_cleared_row_is_a_deletion_not_a_rename(self):
        """Clearing a row deletes the account. Treating it as a rename would drag
        a dead name into the play order."""
        cleared = row("", stored="Bob", email="", pw="")
        self.assertEqual(self.rows(cleared)._collect_pending_renames(), {})


class ApplyRenameTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cm = ui.ConfigManager.__new__(ui.ConfigManager)
        self.cm.config = {
            "account_play_order": ["MyLabel", "Other"],
            "manual_current_account": "MyLabel",
        }
        self.cm._save_config = lambda: None

    def test_the_play_order_follows_the_rename(self):
        self.cm.apply_account_renames({"mylabel": "RealArenaName"})
        self.assertEqual(
            self.cm.config["account_play_order"], ["RealArenaName", "Other"]
        )

    def test_the_manual_pin_follows_the_rename(self):
        self.cm.apply_account_renames({"mylabel": "RealArenaName"})
        self.assertEqual(self.cm.config["manual_current_account"], "RealArenaName")

    def test_no_renames_changes_nothing(self):
        before = dict(self.cm.config)
        self.cm.apply_account_renames({})
        self.assertEqual(self.cm.config, before)


class RenameSurvivesSaveTest(unittest.TestCase):
    """End to end against the real save path and the real on-disk layout.

    This is the one that failed before: the rename was applied to the config but
    never to credentials.json, so the account kept its old name on disk while the
    play order had already moved on -- and the two never matched again."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cm = ui.ConfigManager.__new__(ui.ConfigManager)
        self.cm.config = {
            "account_play_order": ["MyLabel", "Other"],
            "manual_current_account": "",
        }
        self.cm._save_config = lambda: None
        self.cm._accounts_root = lambda: self.dir
        self.cm._account_scan_dirs = lambda: [self.dir]

    def stored_names(self) -> set:
        names = set()
        for entry in os.listdir(self.dir):
            path = os.path.join(self.dir, entry, "credentials.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    names |= set(json.load(f).keys())
        return names

    def test_disk_and_play_order_agree_after_a_rename(self):
        first = self.cm.save_managed_accounts([
            {"name": "MyLabel", "screen_name": "MyLabel", "email": "a@x", "pw": "p"},
            {"name": "Other", "screen_name": "Other", "email": "b@x", "pw": "p"},
        ])
        self.assertEqual(self.stored_names(), {"MyLabel", "Other"})
        folders = {acc["name"]: acc["folder"] for acc in first}

        # What Save Accounts now does, in that order. The rows carry the folder
        # they were loaded from, so a rename rewrites that folder's credentials.json
        # under the new key rather than creating a second folder for the account.
        self.cm.apply_account_renames({"mylabel": "RealArenaName"})
        saved = self.cm.save_managed_accounts([
            {"name": "RealArenaName", "screen_name": "RealArenaName", "email": "a@x",
             "pw": "p", "folder": folders["MyLabel"]},
            {"name": "Other", "screen_name": "Other", "email": "b@x", "pw": "p",
             "folder": folders["Other"]},
        ])

        self.assertEqual(self.stored_names(), {"RealArenaName", "Other"})
        self.assertEqual(
            self.cm.config["account_play_order"], ["RealArenaName", "Other"],
            "the renamed account fell out of the play order",
        )
        self.assertEqual(len(saved), 2, "an account was lost in the rename")
        self.assertEqual(
            saved[0]["folder"], folders["MyLabel"],
            "the rename moved the account to a new folder instead of rewriting it",
        )
        self.assertEqual(
            len([e for e in os.listdir(self.dir)
                 if os.path.isfile(os.path.join(self.dir, e, "credentials.json"))]),
            2,
            "the rename left an orphaned credentials file behind",
        )

    def test_renaming_before_saving_is_what_keeps_the_order(self):
        """Pins the ordering itself: save_managed_accounts filters the play order
        against the names it just wrote, so applying the rename afterwards empties
        it instead of carrying it over."""
        self.cm.save_managed_accounts([
            {"name": "MyLabel", "screen_name": "MyLabel", "email": "a@x", "pw": "p"},
        ])
        self.cm.config["account_play_order"] = ["MyLabel"]
        self.cm.save_managed_accounts([
            {"name": "RealArenaName", "screen_name": "RealArenaName", "email": "a@x", "pw": "p"},
        ])
        self.assertEqual(
            self.cm.config["account_play_order"], [],
            "if this ever stops being empty, the ordering constraint is gone and "
            "the comment in _save_accounts should be revisited",
        )


class SaveAccountsCallSiteTest(unittest.TestCase):
    """Drives the real _save_accounts. Without this, deleting the rename call from
    it leaves every other test in this file green -- the same regression as before,
    just relocated into the dialog."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cm = ui.ConfigManager.__new__(ui.ConfigManager)
        self.cm.config = {
            "account_play_order": ["MyLabel"],
            "manual_current_account": "MyLabel",
        }
        self.cm._save_config = lambda: None
        self.cm._accounts_root = lambda: self.dir
        self.cm._account_scan_dirs = lambda: [self.dir]
        self.cm.save_managed_accounts([
            {"name": "MyLabel", "screen_name": "MyLabel", "email": "a@x", "pw": "p"},
        ])
        self.cm.config["account_play_order"] = ["MyLabel"]
        self.cm.config["manual_current_account"] = "MyLabel"

        real_box = ui.messagebox
        ui.messagebox = _StubMessagebox()
        self.addCleanup(lambda: setattr(ui, "messagebox", real_box))
        self.box = ui.messagebox

    def dialog(self, rows):
        return _Dialog(rows, config_manager=self.cm)

    def test_saving_a_renamed_row_keeps_it_in_the_play_order(self):
        d = self.dialog([row("RealArenaName", stored="MyLabel")])
        d._save_accounts()
        self.assertEqual(self.box.errors, [])
        self.assertEqual(self.cm.config["account_play_order"], ["RealArenaName"])
        self.assertEqual(self.cm.config["manual_current_account"], "RealArenaName")

    def test_a_second_save_is_not_another_rename(self):
        d = self.dialog([row("RealArenaName", stored="MyLabel")])
        d._save_accounts()
        d._save_accounts()
        self.assertEqual(self.cm.config["account_play_order"], ["RealArenaName"])

    def test_a_failed_disk_write_does_not_leave_the_settings_ahead_of_disk(self):
        d = self.dialog([row("RealArenaName", stored="MyLabel")])
        self.cm.save_managed_accounts = lambda accounts: (_ for _ in ()).throw(
            OSError("disk full")
        )
        d._save_accounts()
        self.assertTrue(self.box.errors, "the user was not told the save failed")
        self.assertEqual(
            self.cm.config["account_play_order"], ["MyLabel"],
            "the play order was renamed even though nothing reached the disk",
        )
        self.assertEqual(self.cm.config["manual_current_account"], "MyLabel")

    def test_a_duplicate_arena_name_is_refused_before_anything_is_written(self):
        """Two legacy rows can collapse onto the same Arena name. That must be a
        visible error, not a silent dedupe that deletes an account."""
        d = self.dialog([
            row("SameName", stored="LabelOne"),
            row("SameName", stored="LabelTwo"),
        ])
        d._save_accounts()
        self.assertTrue(any("Duplicate" in e for e in self.box.errors), self.box.errors)
        self.assertEqual(self.cm.config["account_play_order"], ["MyLabel"])


class _StubMessagebox:
    def __init__(self):
        self.errors = []

    def showerror(self, title, message, **kw):
        self.errors.append(message)

    def showinfo(self, title, message, **kw):
        pass


if __name__ == "__main__":
    unittest.main()
