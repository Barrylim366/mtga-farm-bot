"""Unit tests for the Account Play Order box in the Manage Accounts window.

The play order is stored -- and resolved by the controller -- under the account's
credentials name (the key in credentials.json). The dialog, however, shows each
row under its Arena name, which for a row saved before the two names were merged
is a DIFFERENT string.

That mismatch used to empty the box twice over: opening the window blanked every
slot whose saved entry was spelled the on-disk way, and picking names out of the
dropdowns saved nothing, because the values offered were Arena names and the
config filtered them against credentials names. Either way the user ended up with
`account_play_order: []` and the bot fell back to alphabetical rotation.

No Tk window is built here; the dialog's real methods run against stub vars.
"""
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ui


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combo:
    def __init__(self):
        self.values = []

    def configure(self, values=None, **kw):
        if values is not None:
            self.values = list(values)


class _OrderBox:
    """The play-order half of the dialog, with the real methods under test."""

    def __init__(self, config_manager, rows, slot_values):
        self._config_manager = config_manager
        self._accounts_data = rows
        self._order_vars = [_Var(v) for v in slot_values]
        self._order_combos = [_Combo() for _ in slot_values]
        self._parent = None

    def lift(self):
        pass

    def focus_force(self):
        pass

    _refresh_order_choices = ui.SwitchAccountWindow._refresh_order_choices
    _save_account_play_order = ui.SwitchAccountWindow._save_account_play_order

    @property
    def slots(self):
        return [v.get() for v in self._order_vars]


def row(arena: str, stored: str | None = None) -> dict:
    """A dialog row. `stored` is what the account is still called on disk."""
    return {
        "name": arena,
        "screen_name": arena,
        "stored_name": arena if stored is None else stored,
        "email": "a@x",
        "pw": "p",
        "folder": "",
    }


class _CMTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cm = ui.ConfigManager.__new__(ui.ConfigManager)
        self.cm.config = {"account_play_order": []}
        self.cm._save_config = lambda: None
        self.cm._accounts_root = lambda: self.dir
        self.cm._account_scan_dirs = lambda: [self.dir]

    def write_accounts(self, pairs):
        """pairs: [(credentials name, Arena name)] -- differing where a row has
        not been re-saved since the two names were merged."""
        self.cm.save_managed_accounts([
            {"name": name, "screen_name": arena, "email": f"{name}@x", "pw": "p"}
            for name, arena in pairs
        ])
        self.cm.config["account_play_order"] = []


class SetPlayOrderTest(_CMTest):
    def test_arena_names_from_the_dropdowns_are_accepted(self):
        """The regression: the combos offer Arena names, so this is exactly what
        Save Order hands over. Filtering it against credentials names wrote []."""
        self.write_accounts([("bruno1", "venturaa"), ("milo", "milobasas")])
        self.cm.set_account_play_order(["milobasas", "venturaa"])
        self.assertEqual(self.cm.config["account_play_order"], ["milo", "bruno1"])

    def test_credentials_names_are_still_accepted(self):
        self.write_accounts([("bruno1", "venturaa"), ("milo", "milobasas")])
        self.cm.set_account_play_order(["milo", "bruno1"])
        self.assertEqual(self.cm.config["account_play_order"], ["milo", "bruno1"])

    def test_the_stored_order_is_the_name_the_controller_resolves(self):
        """Controller._resolve_account_play_order matches against acc["name"], so
        an order kept in Arena spelling would resolve to nothing at runtime."""
        self.write_accounts([("bruno1", "venturaa")])
        self.cm.set_account_play_order(["venturaa"])
        names = {a["name"] for a in self.cm.get_managed_accounts()}
        self.assertTrue(set(self.cm.config["account_play_order"]) <= names)

    def test_matching_is_case_insensitive(self):
        self.write_accounts([("bruno1", "venturaa")])
        self.cm.set_account_play_order(["VENTURAA"])
        self.assertEqual(self.cm.config["account_play_order"], ["bruno1"])

    def test_the_same_account_under_both_names_is_one_entry(self):
        self.write_accounts([("bruno1", "venturaa")])
        self.cm.set_account_play_order(["venturaa", "bruno1"])
        self.assertEqual(self.cm.config["account_play_order"], ["bruno1"])

    def test_an_unknown_name_is_still_dropped(self):
        self.write_accounts([("bruno1", "venturaa")])
        self.cm.set_account_play_order(["venturaa", "deleted_account"])
        self.assertEqual(self.cm.config["account_play_order"], ["bruno1"])


class RefreshOrderChoicesTest(_CMTest):
    def box(self, rows, slots):
        return _OrderBox(self.cm, rows, slots)

    def test_an_order_saved_under_the_on_disk_name_still_shows(self):
        """What the user reported: the order is set in the config but every slot
        renders empty, because the row is labelled with its Arena name."""
        b = self.box([row("venturaa", "bruno1"), row("milobasas", "milo")],
                     ["milo", "bruno1", ""])
        b._refresh_order_choices()
        self.assertEqual(b.slots, ["milobasas", "venturaa", ""])

    def test_the_dropdowns_offer_a_blank_plus_every_named_row(self):
        b = self.box([row("venturaa", "bruno1"), row("milobasas", "milo"), row("")],
                     ["", ""])
        b._refresh_order_choices()
        self.assertEqual(b._order_combos[0].values, ["", "venturaa", "milobasas"])

    def test_a_deleted_account_is_cleared_from_its_slot(self):
        b = self.box([row("venturaa", "bruno1")], ["venturaa", "gone"])
        b._refresh_order_choices()
        self.assertEqual(b.slots, ["venturaa", ""])

    def test_a_case_only_difference_is_not_treated_as_a_deletion(self):
        b = self.box([row("venturaa", "bruno1")], ["VenturaA"])
        b._refresh_order_choices()
        self.assertEqual(b.slots, ["venturaa"])

    def test_the_same_account_twice_keeps_only_the_first_slot(self):
        """set_account_play_order dedupes on save, so showing the duplicate would
        promise a rotation the config cannot hold."""
        b = self.box([row("venturaa", "bruno1"), row("milobasas", "milo")],
                     ["venturaa", "milobasas", "venturaa"])
        b._refresh_order_choices()
        self.assertEqual(b.slots, ["venturaa", "milobasas", ""])


class SaveOrderRoundTripTest(_CMTest):
    def test_setting_an_order_survives_closing_and_reopening_the_window(self):
        """End to end over the two halves that disagreed: pick Arena names, save,
        then rebuild the box from the config the way __init__ does."""
        self.write_accounts([("bruno1", "venturaa"), ("milo", "milobasas")])
        rows = [row("venturaa", "bruno1"), row("milobasas", "milo")]

        picking = _OrderBox(self.cm, rows, ["milobasas", "venturaa"])
        picking._save_account_play_order()
        self.assertEqual(self.cm.config["account_play_order"], ["milo", "bruno1"])

        saved = self.cm.get_account_play_order()
        reopened = _OrderBox(self.cm, rows, saved + [""] * (3 - len(saved)))
        reopened._refresh_order_choices()
        self.assertEqual(reopened.slots, ["milobasas", "venturaa", ""])

    def test_the_cycle_index_is_reset_so_rotation_starts_at_slot_one(self):
        self.write_accounts([("bruno1", "venturaa"), ("milo", "milobasas")])
        self.cm.config["account_cycle_index"] = 1
        box = _OrderBox(self.cm, [row("venturaa", "bruno1")], ["venturaa"])
        box._save_account_play_order()
        self.assertEqual(self.cm.config["account_cycle_index"], 0)

    def test_running_controller_receives_the_canonical_order(self):
        self.write_accounts([("bruno1", "venturaa")])
        calls = []
        controller = SimpleNamespace(
            set_account_play_order=lambda order: calls.append(list(order)),
            set_account_cycle_index=lambda _index: None,
        )
        parent = SimpleNamespace(bot_running=True, _controller=controller)
        box = _OrderBox(self.cm, [row("venturaa", "bruno1")], ["venturaa"])
        box._parent = SimpleNamespace(master=parent)
        box._save_account_play_order()
        self.assertEqual(calls, [["bruno1"]])


class SaveAccountsKeepsOrderTest(_CMTest):
    def test_cross_account_name_alias_collision_is_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            self.cm.save_managed_accounts([
                {"name": "alpha", "screen_name": "arena-one", "email": "a@x", "pw": "p"},
                {"name": "arena-one", "screen_name": "arena-two", "email": "b@x", "pw": "p"},
            ])
        self.assertEqual(os.listdir(self.dir), [])

    def test_saving_accounts_does_not_drop_an_order_in_arena_spelling(self):
        """save_managed_accounts re-filters the order against what it just wrote.
        An entry already canonical stays; one in the other spelling is rewritten
        rather than discarded."""
        self.write_accounts([("bruno1", "venturaa"), ("milo", "milobasas")])
        self.cm.config["account_play_order"] = ["milobasas", "bruno1"]
        self.cm.save_managed_accounts([
            {"name": "bruno1", "screen_name": "venturaa", "email": "bruno1@x", "pw": "p"},
            {"name": "milo", "screen_name": "milobasas", "email": "milo@x", "pw": "p"},
        ])
        self.assertEqual(self.cm.config["account_play_order"], ["milo", "bruno1"])


if __name__ == "__main__":
    unittest.main()
