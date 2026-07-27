"""Unit tests for per-account farmed-gold attribution ("Gold farmed per account").

Regression under test, observed in the 2026-07-27 run: the Current Session panel
credited Affinity2004 with 18700 gold and its rotation partner with 0, when the
real figures were ~2950 and ~2350.

An InventoryInfo entry in Player.log carries a balance but never says WHICH
account it belongs to, so attribution is purely positional. The gate that made it
positional only applied while the incoming account's screenName was still
unlatched -- a window of seconds. For the rest of that account's turn the read
fell back to the whole log tail and returned whatever balance was newest,
including the other account's. One direction inflated a row, the other produced a
negative delta that the max(0, ...) clamp quietly rendered as 0.

These tests drive the real file readers over a temp log; nothing touches MTGA.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller

ACCOUNT_A = "Affinity2004"
ACCOUNT_B = "TEUBAT"


def inventory_line(gold: int) -> str:
    """An InventoryInfo event shaped like MTGA's, minus the fields we ignore.
    Note what is NOT in it: any hint of the account it describes."""
    return (
        '[UnityCrossThreadLogger]<== PlayerInventory.GetPlayerInventory '
        '{"InventoryInfo":{"SeqId":1,"Gold":%d,"Gems":0,"TotalVaultProgress":0}}\n' % gold
    )


class GoldAttributionTest(unittest.TestCase):
    def setUp(self):
        fd, self.log_path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))
        self.c = Controller(self.log_path)

    def append(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text)

    def log_size(self) -> int:
        return os.path.getsize(self.log_path)

    def switch_to(self, screen_name: str) -> None:
        """What an account switch does to the two things attribution depends on:
        the boundary offset (captured before the logout) and the identity."""
        self.c._quests_valid_from_offset = self.log_size()
        self.c._current_account_screen_name = screen_name

    # --- the session floor (the account we booted on) ---------------------

    def test_balance_from_before_the_session_is_not_read(self):
        """The tail still holds whatever accounts the LAST session rotated
        through; one of those must never become this account's baseline."""
        self.append(inventory_line(32800))  # previous session, another account
        self.c.begin_session()
        self.assertIsNone(self.c._read_latest_inventory_gold())

    def test_balance_written_after_the_session_start_is_read(self):
        self.c.begin_session()
        self.append(inventory_line(14150))
        self.assertEqual(self.c._read_latest_inventory_gold(), 14150)

    def test_stale_balance_cannot_become_the_baseline(self):
        """End to end for the startup account: a pre-session balance of 32800
        would have pinned the baseline above the real one and shown 0 farmed."""
        self.append(inventory_line(32800))
        self.c.begin_session()
        self.c._current_account_screen_name = ACCOUNT_A
        self.append(inventory_line(14150))
        self.c._update_gold_from_inventory()
        self.append(inventory_line(15650))
        self.c._update_gold_from_inventory()
        self.assertEqual(self.c._account_initial_gold[ACCOUNT_A], 14150)
        self.assertEqual(self.c._gold_farmed_by_account[ACCOUNT_A], 1500)

    # --- the switch boundary (the account we switched into) ---------------

    def test_previous_accounts_balance_is_not_read_after_a_switch(self):
        """The load-bearing case. Before the fix this returned 14150 -- the
        outgoing account's balance -- because the tail read ignored the boundary
        as soon as the incoming screenName was latched."""
        self.c.begin_session()
        self.append(inventory_line(14150))
        self.c._current_account_screen_name = ACCOUNT_A
        self.switch_to(ACCOUNT_B)
        self.assertIsNone(self.c._read_latest_inventory_gold())

    def test_incoming_accounts_own_balance_is_read_after_a_switch(self):
        self.c.begin_session()
        self.append(inventory_line(14150))
        self.switch_to(ACCOUNT_B)
        self.append(inventory_line(30500))
        self.assertEqual(self.c._read_latest_inventory_gold(), 30500)

    def test_a_full_rotation_keeps_both_accounts_correct(self):
        """The exact 2026-07-27 shape: two accounts, alternating, each writing
        its own balances. Before the fix this ended with 18450 on one row.

        The reads interleaved with the switches are the whole point -- MTGA does
        not write the incoming account's balance the instant it logs in, so the
        poll that runs in that gap is the one that used to grab the outgoing
        account's figure and pin it as the incoming account's baseline."""
        self.c.begin_session()
        self.c._current_account_screen_name = ACCOUNT_A
        self.append(inventory_line(14150))
        self.c._update_gold_from_inventory()

        self.switch_to(ACCOUNT_B)
        self.c._update_gold_from_inventory()  # B has written nothing yet
        self.assertNotIn(ACCOUNT_B, self.c._account_initial_gold,
                         "B must not be baselined from A's balance")
        self.append(inventory_line(30500))
        self.c._update_gold_from_inventory()
        self.append(inventory_line(32600))
        self.c._update_gold_from_inventory()

        self.switch_to(ACCOUNT_A)
        self.c._update_gold_from_inventory()  # A has not written again yet
        self.append(inventory_line(17100))
        self.c._update_gold_from_inventory()

        self.assertEqual(self.c._gold_farmed_by_account[ACCOUNT_A], 2950)
        self.assertEqual(self.c._gold_farmed_by_account[ACCOUNT_B], 2100)

    def test_switched_back_account_keeps_its_original_baseline(self):
        """Farmed gold is measured against the FIRST balance of the session, so
        coming back to an account must not re-baseline and zero its total."""
        self.c.begin_session()
        self.c._current_account_screen_name = ACCOUNT_A
        self.append(inventory_line(14150))
        self.c._update_gold_from_inventory()
        self.switch_to(ACCOUNT_B)
        self.append(inventory_line(30500))
        self.c._update_gold_from_inventory()
        self.switch_to(ACCOUNT_A)
        self.append(inventory_line(17100))
        self.c._update_gold_from_inventory()
        self.assertEqual(self.c._account_initial_gold[ACCOUNT_A], 14150)

    def test_unlatched_account_is_not_credited(self):
        self.c.begin_session()
        self.append(inventory_line(14150))
        self.c._update_gold_from_inventory()
        self.assertEqual(self.c._gold_farmed_by_account, {})

    # --- lifecycle hazards found in review --------------------------------

    def test_repeated_begin_session_keeps_the_first_floor(self):
        """The start path calls begin_session() twice around quest priming, and
        the priming Home dip is what makes MTGA write the startup account's
        balance. Re-arming the floor on the second call would discard it."""
        self.c.begin_session()
        self.append(inventory_line(14150))
        self.c.begin_session()
        self.assertEqual(self.c._read_latest_inventory_gold(), 14150)

    def test_log_rotation_does_not_freeze_the_gold_rows(self):
        """MTGA rotates Player.log on restart, so the file can shrink below a
        boundary captured earlier. Without a guard the read returns None forever
        and the panel silently stops updating."""
        self.append("x" * 5000)
        self.c.begin_session()
        self.assertGreater(self.c._gold_valid_from_offset, 0)
        with open(self.log_path, "w", encoding="utf-8") as f:  # rotated: fresh, shorter
            f.write(inventory_line(14150))
        self.assertEqual(self.c._read_latest_inventory_gold(), 14150)

    # --- the clamp no longer hides a bad read -----------------------------

    def test_balance_below_baseline_is_logged_and_clamped(self):
        """Spending gold explains this legitimately; a foreign balance does not.
        Either way it must not pass silently -- the clamp made the old bug look
        like an account that simply farmed nothing."""
        import bot_logger

        self.c.begin_session()
        self.c._current_account_screen_name = ACCOUNT_A
        self.append(inventory_line(14150))
        self.c._update_gold_from_inventory()

        logged = []
        original = bot_logger.log_info
        bot_logger.log_info = lambda msg: logged.append(msg)
        try:
            self.append(inventory_line(9000))
            self.c._update_gold_from_inventory()
        finally:
            bot_logger.log_info = original

        self.assertEqual(self.c._gold_farmed_by_account[ACCOUNT_A], 0)
        self.assertTrue(
            any("GOLD_BALANCE_BELOW_BASELINE" in m for m in logged),
            f"expected a below-baseline warning, got: {logged}",
        )

    def test_below_baseline_warning_does_not_repeat_per_poll(self):
        """An account can sit below its baseline for a whole rotation; the
        warning must not turn the log into a wall of identical lines."""
        import bot_logger

        self.c.begin_session()
        self.c._current_account_screen_name = ACCOUNT_A
        self.append(inventory_line(14150))
        self.c._update_gold_from_inventory()
        self.append(inventory_line(9000))

        logged = []
        original = bot_logger.log_info
        bot_logger.log_info = lambda msg: logged.append(msg)
        try:
            for _ in range(5):
                self.c._update_gold_from_inventory()
        finally:
            bot_logger.log_info = original

        self.assertEqual(
            sum("GOLD_BALANCE_BELOW_BASELINE" in m for m in logged), 1,
            f"expected exactly one warning across 5 polls, got: {logged}",
        )


if __name__ == "__main__":
    unittest.main()
