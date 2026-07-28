"""Unit tests for "which account am I logged into?".

Regression under test, from the 2026-07-27 run: the bot logged into TEUBAT at
12:11:54 and kept calling itself Affinity2004 for another hour. The identity was
resolved 3 times in six hours across ~24 real account switches, and since an
InventoryInfo balance carries no account name at all, every gold reading in
between was booked against whatever stale name happened to be current -- one row
inflated to 18700, its partner clamped to 0.

Cause: the bot threw away the one thing it was certain of (it had just typed that
account's credentials) and re-derived the identity from the log instead. The only
thing either log-side latch matches is `authenticateResponse.screenName`, which is
the MATCH-server handshake, not a login -- so right after a switch, before the
incoming account has played anything, the newest one in the tail still belongs to
the account we just left.

These tests drive the real file readers over a temp log; nothing touches MTGA.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import Controller.MTGAController.Controller as controller_module
from Controller.MTGAController.Controller import Controller

ACCOUNT_A = "Affinity2004"
ACCOUNT_B = "TEUBAT"


def handshake_line(screen_name: str) -> str:
    """A match-server AuthenticateResponse, the event both log-side latches read."""
    return (
        '[UnityCrossThreadLogger]<== Match to 12345: AuthenticateResponse '
        '{"authenticateResponse":{"clientId":"x","screenName":"%s"}}\n' % screen_name
    )


def inventory_line(gold: int) -> str:
    """A balance, exactly as MTGA writes it: with no account name anywhere in it."""
    return (
        '[UnityCrossThreadLogger]<== PlayerInventory.GetPlayerInventory '
        '{"InventoryInfo":{"SeqId":1,"Gold":%d,"Gems":0,"TotalVaultProgress":0}}\n' % gold
    )


class AccountIdentityTest(unittest.TestCase):
    def setUp(self):
        fd, self.log_path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))
        self.c = Controller(self.log_path)
        # The alias map is seeded from the real on-disk account config; blank it so
        # these tests describe only what they set up.
        self.c._screenname_to_alias = {}

    def append(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text)

    def log_size(self) -> int:
        return os.path.getsize(self.log_path)

    def begin_switch(self) -> None:
        """The one thing _perform_account_switch does before the logout that these
        tests depend on: mark where the log stood when the switch began."""
        self.c._quests_valid_from_offset = self.log_size()

    # --- the identity comes from the credentials we typed ------------------

    def test_identity_is_taken_from_the_account_we_logged_in(self):
        self.assertTrue(
            self.c._latch_identity_from_switch_target(
                {"name": ACCOUNT_B, "screen_name": ACCOUNT_B, "email": "b@x", "pw": "p"}
            )
        )
        self.assertEqual(self.c._current_account_screen_name, ACCOUNT_B)
        self.assertTrue(self.c._identity_from_config)

    def test_discriminator_is_stripped(self):
        """Users type the name as Arena shows it, digits and all; every other latch
        point stores the canonical form, and two spellings split the gold row."""
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": "TEUBAT#12345"}
        )
        self.assertEqual(self.c._current_account_screen_name, ACCOUNT_B)

    def test_a_row_without_an_arena_name_leaves_the_identity_unlatched(self):
        """Rows saved before the Arena name was a field. Claiming an identity we
        do not have would be worse than falling back to the log."""
        self.assertFalse(
            self.c._latch_identity_from_switch_target({"name": ACCOUNT_B, "email": "b@x"})
        )
        self.assertIsNone(self.c._current_account_screen_name)
        self.assertFalse(self.c._identity_from_config)

    def test_our_own_login_supersedes_a_manual_pin(self):
        """A pin says "this is who is logged in"; we just changed who that is."""
        self.c.set_current_account_manual(ACCOUNT_A)
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        self.assertFalse(self.c._current_account_pinned)
        self.assertEqual(self.c._current_account_screen_name, ACCOUNT_B)

    def test_the_account_gets_a_gold_row_immediately(self):
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        self.assertIn(ACCOUNT_B, self.c._gold_farmed_by_account)

    # --- the log must not take it back ------------------------------------

    def test_a_pre_switch_handshake_does_not_reclaim_the_identity(self):
        """The load-bearing case. A handshake written BEFORE the switch is by
        definition the account we left. Before the fix it silently won, because
        the tail this latch reads is ungated once an identity exists."""
        self.append(handshake_line(ACCOUNT_A))
        self.begin_switch()
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        # A tail read long after the switch still contains A's last match connect.
        self.c._latch_account_screen_name_from(handshake_line(ACCOUNT_A))
        self.assertEqual(self.c._current_account_screen_name, ACCOUNT_B)

    def test_a_post_switch_handshake_does_reclaim_the_identity(self):
        """The user can still change account in Arena by hand. A handshake written
        after the switch is real news and has to win, or the bot would be stuck
        with a name that is no longer true."""
        self.append(handshake_line(ACCOUNT_B))  # so the switch boundary is not 0
        self.begin_switch()
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        self.append(handshake_line(ACCOUNT_A))
        self.c._latch_account_screen_name_from(handshake_line(ACCOUNT_A))
        self.assertEqual(self.c._current_account_screen_name, ACCOUNT_A)
        self.assertFalse(
            self.c._identity_from_config,
            "the config-derived name was overruled, so it must stop being protected",
        )

    def test_pinning_an_account_by_hand_drops_the_protection(self):
        """A pin replaces the identity with the user's answer, so it is no longer
        the one our own login vouched for."""
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        self.c.set_current_account_manual(ACCOUNT_A)
        self.assertFalse(self.c._identity_from_config)

    def test_unpinning_restores_plain_auto_detection(self):
        """Unpinning asks for auto-detection back. Leaving the flag set would keep
        the log locked out by a protection the user just switched off."""
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        self.c.set_current_account_manual("")
        self.assertFalse(self.c._identity_from_config)

    def test_the_incoming_accounts_quests_are_read_from_after_the_switch(self):
        """The identity is now set at login time, which used to be what ended this
        gate. Without it the read drops to a plain tail that can still hold the
        OUTGOING account's quests block -- the new account would start on the old
        account's quests, and in quests mode immediately switch again."""
        self.append('{"quests":[{"questId":"OLD-ACCOUNT-QUEST"}]}\n')
        self.begin_switch()
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        self.assertIsNone(
            self.c._extract_latest_quests(),
            "the outgoing account's quests block was read for the incoming account",
        )
        self.append('{"quests":[{"questId":"NEW-ACCOUNT-QUEST"}]}\n')
        quests = self.c._extract_latest_quests()
        self.assertEqual([q["questId"] for q in quests], ["NEW-ACCOUNT-QUEST"])

    def test_the_protection_is_dropped_for_the_next_account(self):
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        self.c._reset_state_for_incoming_account()
        self.assertIsNone(self.c._current_account_screen_name)
        self.assertFalse(self.c._identity_from_config)

    # --- what it was all for: the gold lands on the right row -------------

    def test_the_2026_07_27_rotation_attributes_gold_correctly(self):
        """The exact shape that produced 'Affinity2004: 18450'. A logs in, plays,
        then B logs in and plays -- and crucially, A's last match handshake is
        still the newest one in the log while B is starting up."""
        self.c.begin_session()
        self.append(handshake_line(ACCOUNT_A))
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_A, "screen_name": ACCOUNT_A}
        )
        self.append(inventory_line(14150))
        self.c._update_gold_from_inventory()
        self.append(inventory_line(17000))
        self.c._update_gold_from_inventory()

        self.begin_switch()
        self.c._reset_state_for_incoming_account()
        self.c._latch_identity_from_switch_target(
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B}
        )
        # Nothing of B's is in the log yet -- the newest handshake is still A's,
        # and this is the poll that used to hand B's turn back to A.
        self.c._latch_account_screen_name_from(handshake_line(ACCOUNT_A))
        self.append(inventory_line(30500))
        self.c._update_gold_from_inventory()
        self.append(inventory_line(32600))
        self.c._update_gold_from_inventory()

        self.assertEqual(self.c._gold_farmed_by_account[ACCOUNT_A], 2850)
        self.assertEqual(self.c._gold_farmed_by_account[ACCOUNT_B], 2100)
        self.assertNotEqual(
            self.c._gold_farmed_by_account[ACCOUNT_A], 18450,
            "A was credited with B's balance again",
        )


class SwitchFlowIdentityTest(unittest.TestCase):
    """Drives the real _perform_account_switch with the screen/clicks stubbed out.

    Everything above calls the latch by hand, which leaves the one thing the whole
    change rests on untested: that the switch flow calls it AT ALL, and early
    enough that nothing reads the log first."""

    def setUp(self):
        fd, self.log_path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))
        self.c = Controller(self.log_path)
        self.c._screenname_to_alias = {}

        # The outgoing account, as the bot believed it before the switch.
        self.c._current_account_screen_name = ACCOUNT_A
        self.c._account_switch_enabled = True
        self.accounts = [
            {"name": ACCOUNT_A, "screen_name": ACCOUNT_A, "email": "a@x", "pw": "p", "folder": "a"},
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B, "email": "b@x", "pw": "p", "folder": "b"},
        ]
        self.c._load_accounts_from_dirs = lambda: list(self.accounts)

        # Screen, clicks and waits: none of them exist in a test process.
        self.c.input = _StubInput()
        self.c._get_state_from_log = lambda: None
        self.c._replay_recorded_logout = lambda: False
        self.c._run_mapped_logout_sequence = lambda: None
        self.c._wait_for_logout_to_reach_login_screen = lambda **kw: True
        self.c._run_post_login_routine = lambda account, all_accounts: True
        self.c.start_queueing = lambda: None
        self.c._persist_account_cycle_index = lambda: None
        self.c._login_delete_delay_sec = 0.0

        # The post-login step that reads the log. Records what the identity was
        # when it ran, which is the ordering this test exists to pin.
        self.identity_when_quests_read = "<never called>"

        def fake_refresh_quests_cache():
            self.identity_when_quests_read = self.c._current_account_screen_name

        self.c.refresh_quests_cache = fake_refresh_quests_cache

        real_sleep = controller_module.time.sleep
        controller_module.time.sleep = lambda _s: None
        self.addCleanup(lambda: setattr(controller_module.time, "sleep", real_sleep))

    def test_the_switch_flow_sets_the_identity_from_the_credentials_it_typed(self):
        self.c._perform_account_switch()
        self.assertEqual(self.c._current_account_screen_name, ACCOUNT_B)
        self.assertTrue(self.c._identity_from_config)

    def test_the_identity_is_set_before_anything_reads_the_log(self):
        """Ordering matters: refresh_quests_cache latches from the log and
        establishes the incoming account's gold baseline. If it ran first it would
        do both under the OUTGOING account's name."""
        self.c._perform_account_switch()
        self.assertEqual(self.identity_when_quests_read, ACCOUNT_B)

    def test_the_typed_credentials_are_the_ones_we_claim_the_identity_for(self):
        """Guards against the identity and the keystrokes drifting apart -- a
        confident wrong name is worse than no name."""
        self.c._perform_account_switch()
        self.assertIn("b@x", self.c.input.typed)
        self.assertEqual(self.c._current_account_screen_name, ACCOUNT_B)


class _StubInput:
    def __init__(self):
        self.typed = []

    def type_text(self, text):
        self.typed.append(text)

    def tap_delete(self):
        pass

    def tap_tab(self):
        pass

    def tap_enter(self):
        pass


class PersistedAliasTest(unittest.TestCase):
    """The learned screenName -> label file is an INFERENCE ("we aimed at row X,
    then saw screenName Y") and it was written to disk. One miss while the identity
    was stale therefore became permanent: TEUBAT was filed under the label of a
    different account, in every session from then on."""

    def setUp(self):
        fd, self.log_path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))

    def test_the_configured_name_beats_a_wrong_learned_one(self):
        """Both sources are fill-in-only, so which is consulted FIRST in
        Controller.__init__ decides. Constructs a real Controller against a real
        poisoned file -- asserting on the seeding helpers alone would pass no
        matter which order __init__ actually uses."""
        import json
        import Controller.MTGAController.Controller as controller_module

        alias_path = os.path.join(tempfile.mkdtemp(), "account_aliases.json")
        with open(alias_path, "w", encoding="utf-8") as f:
            # What a single stale-identity switch wrote, and reloaded forever after.
            json.dump({ACCOUNT_B: "SomeOtherRow"}, f)

        real_runtime_file = controller_module.runtime_file
        controller_module.runtime_file = (
            lambda *parts: alias_path if parts[-1] == "account_aliases.json"
            else real_runtime_file(*parts)
        )
        real_loader = Controller._load_accounts_from_dirs
        Controller._load_accounts_from_dirs = lambda self: [
            {"name": ACCOUNT_B, "screen_name": ACCOUNT_B, "email": "b@x", "pw": "p"}
        ]
        try:
            c = Controller(self.log_path)
        finally:
            controller_module.runtime_file = real_runtime_file
            Controller._load_accounts_from_dirs = real_loader

        self.assertEqual(
            c._screenname_to_alias.get(ACCOUNT_B), ACCOUNT_B,
            "the learned file overrode the account the user actually configured",
        )

    def test_a_guessed_alias_is_never_written_to_disk(self):
        c = Controller(self.log_path)
        c._screenname_to_alias = {}
        c._load_accounts_from_dirs = lambda: []
        persisted = []
        c._persist_aliases = lambda: persisted.append(dict(c._screenname_to_alias))
        c._current_account_screen_name = "SomeUnknownScreenName"
        c._pending_switch_alias = "TheRowWeAimedAt"
        c._register_current_account_for_gold()
        self.assertEqual(
            c._screenname_to_alias["SomeUnknownScreenName"], "TheRowWeAimedAt",
            "the guess is still usable for this session",
        )
        self.assertEqual(persisted, [], "but it must not outlive the session")

    def test_a_guess_does_not_ride_along_on_a_later_write(self):
        """_persist_aliases dumps the WHOLE map, so it is not enough that the guess
        doesn't trigger a write: it must be excluded when some other, legitimate
        mapping triggers one."""
        import json

        c = Controller(self.log_path)
        c._screenname_to_alias = {}
        c._load_accounts_from_dirs = lambda: []
        written = {}
        path = os.path.join(tempfile.mkdtemp(), "account_aliases.json")
        c._account_aliases_path = lambda: path

        c._current_account_screen_name = "SomeUnknownScreenName"
        c._pending_switch_alias = "TheRowWeAimedAt"
        c._register_current_account_for_gold()

        # Now a real, config-backed mapping is learned -> a write happens.
        c._load_accounts_from_dirs = lambda: [
            {"name": ACCOUNT_A, "screen_name": ACCOUNT_A, "email": "a@x", "pw": "p"}
        ]
        c._current_account_screen_name = ACCOUNT_A
        c._register_current_account_for_gold()

        with open(path, encoding="utf-8") as f:
            written = json.load(f)
        self.assertEqual(written, {ACCOUNT_A: ACCOUNT_A})
        self.assertNotIn(
            "SomeUnknownScreenName", written,
            "the guess was carried to disk by an unrelated write",
        )


if __name__ == "__main__":
    unittest.main()
