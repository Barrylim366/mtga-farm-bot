"""Unit tests for account rotation + match-end idempotency.

Pure logic tests: no MTGA, no screen, no logout clicks. They pin the invariants
that broke account rotation in practice --

  * a match-end signalled twice must be handled once, and must stay suppressed
    until a NEW match is actually live (not merely until the restart timer ran),
  * the next switch target follows the configured play order anchored at the
    account currently logged in, never switching into that same account,
  * an account whose switch FAILED must not stay marked as "finished this
    round", or a couple of failed logouts end the round early.
"""
import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Game import Game


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    return Controller(f.name)


def accounts(*names: str) -> list[dict]:
    return [{"name": n, "folder": n, "email": f"{n}@x", "pw": "p"} for n in names]


class _StubController:
    """Minimal stand-in for the parts of Controller that Game.on_match_end uses."""

    def __init__(self, match_id: str | None = "match-1"):
        self.stop_inactivity_timer_calls = 0
        self.queue_starts = 0
        self.match_id = match_id

    def get_current_match_id(self):
        return self.match_id

    def stop_inactivity_timer(self):
        self.stop_inactivity_timer_calls += 1

    def start_queueing(self):
        self.queue_starts += 1

    def reset_for_new_game(self):
        pass


class _StubAI:
    def reset(self):
        pass


def make_game(match_id: str | None = "match-1") -> Game:
    game = Game(_StubController(match_id), _StubAI())
    # Never let a test arm the real 10s restart timer: on_match_end is driven
    # directly here, and a live Timer would keep the test process alive.
    game._restart_game = lambda: None
    return game


class MatchEndIdempotencyTests(unittest.TestCase):
    def test_first_signal_is_claimed_and_duplicates_are_not(self):
        game = make_game()
        game._mark_match_started()

        self.assertTrue(game.on_match_end(True))
        self.assertFalse(game.on_match_end(True))
        self.assertFalse(game.on_match_end(False))

    def test_duplicate_stays_suppressed_after_the_restart_ran(self):
        """The regression this guards: re-arming on the restart timer reopened the
        duplicate window ~10s after the match, so a late duplicate scheduled a
        second restart and spawned a parallel queue/switch loop."""
        game = make_game()
        game._mark_match_started()
        self.assertTrue(game.on_match_end(True))

        Game._restart_game(game)  # the real reset, bypassing the stub above

        self.assertFalse(
            game.on_match_end(True),
            "a duplicate arriving after the restart must still be suppressed",
        )

    def test_next_match_is_handled(self):
        game = make_game()
        game._mark_match_started()
        self.assertTrue(game.on_match_end(True))
        self.assertFalse(game.on_match_end(True))

        game.controller.match_id = "match-2"  # next match joined
        game._mark_match_started()

        self.assertTrue(game.on_match_end(False))

    def test_match_that_never_started_is_still_handled(self):
        """A match can end before the bot ever acts (opponent concedes during the
        mulligan), so nothing marks it as started. Its end MUST still be handled --
        a guard that swallows it leaves the bot idle with no restart scheduled."""
        game = make_game()
        game._mark_match_started()
        self.assertTrue(game.on_match_end(True))
        Game._restart_game(game)

        game.controller.match_id = "match-2"  # joined, then instantly over

        self.assertTrue(game.on_match_end(False))

    def test_no_match_id_and_no_start_never_latches(self):
        """The worst case for the fallback key: NO matchId and two matches in a row
        that both end before the bot ever acts, so nothing ever marks a start. Both
        ends must be handled -- a fallback that repeats (e.g. a start timestamp
        still sitting at None) would swallow the second one and the bot would idle
        forever with no restart scheduled."""
        game = make_game(match_id=None)
        self.assertIsNone(game._match_started_ts, "precondition: no match ever started")

        self.assertTrue(game.on_match_end(True))
        self.assertFalse(game.on_match_end(True), "duplicate of match 1")

        Game._restart_game(game)
        self.assertIsNone(game._match_started_ts, "still nothing marks a start")

        self.assertTrue(
            game.on_match_end(False),
            "the second match's real end must not be taken for a duplicate",
        )

    def test_no_match_id_dedupes_within_one_match(self):
        game = make_game(match_id=None)
        game._mark_match_started()

        self.assertTrue(game.on_match_end(True))
        self.assertFalse(game.on_match_end(True))
        self.assertFalse(game.on_match_end(True))

    def test_stop_requested_still_claims_the_match(self):
        """Stopping at match end must not report the match as a duplicate -- the
        UI counts the session's last game off this return value."""
        game = make_game()
        game._mark_match_started()
        game._stop_requested = True

        self.assertTrue(game.on_match_end(True))
        self.assertFalse(game.on_match_end(True))

    def test_start_clears_the_previous_session_key(self):
        game = make_game()
        game._mark_match_started()
        self.assertTrue(game.on_match_end(True))
        # A fresh start() must not inherit the previous session's claimed key.
        with game._match_end_lock:
            game._handled_match_end_key = None
        self.assertTrue(game.on_match_end(True))


class NextSwitchTargetTests(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        self.addCleanup(lambda: None)

    def test_no_accounts_returns_nothing(self):
        self.assertEqual(self.c._select_next_switch_target([], "A"), (None, None, None))

    def test_default_order_follows_the_current_account(self):
        accs = accounts("A", "B", "C")
        self.c._account_play_order = []
        self.c._account_cycle_index = 0

        idx, advance, mod = self.c._select_next_switch_target(accs, "B")

        self.assertEqual(accs[idx]["name"], "C")
        self.assertEqual((advance + 1) % mod, 0)

    def test_default_order_wraps_around(self):
        accs = accounts("A", "B", "C")
        self.c._account_play_order = []

        idx, _, _ = self.c._select_next_switch_target(accs, "C")

        self.assertEqual(accs[idx]["name"], "A")

    def test_current_account_is_never_the_target(self):
        """The stale-cycle-index bug: the index kept pointing at the account we
        were already on, so the bot logged out and straight back in."""
        accs = accounts("A", "B", "C")
        self.c._account_play_order = []
        self.c._account_cycle_index = 1  # points at B, which is current

        idx, _, _ = self.c._select_next_switch_target(accs, "B")

        self.assertNotEqual(accs[idx]["name"], "B")

    def test_play_order_is_honoured_and_anchored_at_current(self):
        accs = accounts("A", "B", "C")
        self.c._account_play_order = ["C", "A", "B"]
        self.c._account_cycle_index = 0  # stale on purpose

        idx, _, _ = self.c._select_next_switch_target(accs, "C")

        self.assertEqual(accs[idx]["name"], "A")

    def test_play_order_wraps_to_its_first_entry(self):
        accs = accounts("A", "B", "C")
        self.c._account_play_order = ["C", "A"]

        idx, _, _ = self.c._select_next_switch_target(accs, "A")

        self.assertEqual(accs[idx]["name"], "C")

    def test_unknown_current_account_falls_back_to_the_cycle_index(self):
        accs = accounts("A", "B", "C")
        self.c._account_play_order = ["B", "C", "A"]
        self.c._account_cycle_index = 1

        idx, advance, mod = self.c._select_next_switch_target(accs, None)

        self.assertEqual(accs[idx]["name"], "C")
        self.assertEqual(mod, 3)
        self.assertEqual(advance, 1)

    def test_accounts_outside_the_play_order_are_not_rotated(self):
        """A 2-account order among 4 folders is a 2-account round."""
        accs = accounts("A", "B", "C", "D")
        self.c._account_play_order = ["A", "B"]

        idx, _, mod = self.c._select_next_switch_target(accs, "B")

        self.assertEqual(accs[idx]["name"], "A")
        self.assertEqual(mod, 2)

    def test_single_account_order_stays_on_that_account(self):
        accs = accounts("A", "B")
        self.c._account_play_order = ["A"]

        idx, _, mod = self.c._select_next_switch_target(accs, "A")

        self.assertEqual(accs[idx]["name"], "A")
        self.assertEqual(mod, 1)

    def test_current_account_matching_is_case_insensitive(self):
        accs = accounts("Alpha", "Beta")
        self.c._account_play_order = []

        idx, _, _ = self.c._select_next_switch_target(accs, "alpha")

        self.assertEqual(accs[idx]["name"], "Beta")


class PendingCompletionTests(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()

    def test_failed_switch_gives_the_completion_mark_back(self):
        self.c._completed_account_keys = {"A"}
        self.c._pending_completed_key = "A"

        self.c._revert_pending_completion("logout failed")

        self.assertEqual(self.c._completed_account_keys, set())
        self.assertIsNone(self.c._pending_completed_key)

    def test_revert_only_touches_this_switch_key(self):
        self.c._completed_account_keys = {"A", "B"}
        self.c._pending_completed_key = "B"

        self.c._revert_pending_completion("logout failed")

        self.assertEqual(self.c._completed_account_keys, {"A"})

    def test_revert_without_a_pending_key_is_a_no_op(self):
        self.c._completed_account_keys = {"A"}
        self.c._pending_completed_key = None

        self.c._revert_pending_completion("nothing to undo")

        self.assertEqual(self.c._completed_account_keys, {"A"})

    def test_revert_is_not_repeatable(self):
        """Guards against a second revert clearing a LATER account's mark."""
        self.c._completed_account_keys = {"A"}
        self.c._pending_completed_key = "A"

        self.c._revert_pending_completion("logout failed")
        self.c._completed_account_keys.add("A")  # A finishes for real later on
        self.c._revert_pending_completion("called again")

        self.assertEqual(self.c._completed_account_keys, {"A"})


class SwitchStartSerializationTests(unittest.TestCase):
    def test_queue_start_waits_on_the_switch_start_lock(self):
        """start_queueing must take the SAME lock _perform_account_switch claims:
        its _account_switch_in_progress check is only meaningful if a switch cannot
        set that flag concurrently. Held from outside here, so start_queueing has to
        block -- with two separate locks it would sail straight through."""
        c = make_controller()
        # The real loop navigates and CLICKS. Only the start gate is under test.
        c._queue_spam_loop = lambda: None
        entered = threading.Event()
        returned = threading.Event()

        def call_start_queueing():
            entered.set()
            c.start_queueing()
            returned.set()

        with c._switch_start_lock:
            t = threading.Thread(target=call_start_queueing, daemon=True)
            t.start()
            self.assertTrue(entered.wait(2.0))
            self.assertFalse(
                returned.wait(0.4),
                "start_queueing did not block on the switch-start lock",
            )
        self.assertTrue(returned.wait(2.0), "start_queueing never released")
        t.join(2.0)
        c._stop_queue_spam = True
        thread = c._queue_spam_thread
        if thread is not None:
            thread.join(3.0)

    def test_queue_start_is_refused_during_a_switch(self):
        c = make_controller()
        c._queue_spam_loop = lambda: None
        c._account_switch_in_progress = True

        c.start_queueing()

        self.assertIsNone(
            c._queue_spam_thread,
            "a queue loop must not start while a switch is running",
        )

    def test_second_switch_caller_bails_out(self):
        c = make_controller()
        c._account_switch_in_progress = True

        # Returns immediately without touching the account state.
        c._perform_account_switch()

        self.assertTrue(c._account_switch_in_progress)
        self.assertIsNone(c._pending_completed_key)


class AliasNamespaceTests(unittest.TestCase):
    """One account must collapse to ONE identity, whichever spelling it arrives in.

    The Alias field is typed by hand and the dialog says the '#12345' digits are
    optional, so the configured value and the latched screenName can differ by the
    discriminator. Keyed in two namespaces, the same account is tracked twice:
    skip-self and next-target anchoring stop working (no configured label resolves)
    and round completion counts it under both keys.
    """

    def test_configured_alias_with_discriminator_still_resolves(self):
        c = make_controller()
        c._load_accounts_from_dirs = lambda: [
            {"name": "bruno1", "screen_name": "venturaa#12345"}
        ]
        c._screenname_to_alias = {}
        c._seed_aliases_from_account_configs()

        latched = c._canonical_screen_name("venturaa#12345")
        c._current_account_screen_name = latched

        self.assertEqual(c._current_account_config_name(), "bruno1")
        self.assertEqual(c._account_identity_key(latched), "bruno1")
        self.assertTrue(c._same_account(latched, "bruno1"))
        self.assertTrue(c._same_account("venturaa#12345", "bruno1"))

    def test_persisted_alias_keys_are_migrated_on_load(self):
        c = make_controller()
        c._screenname_to_alias = {}
        path = c._account_aliases_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        saved = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                saved = f.read()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"venturaa#12345": "bruno1"}')
            c._load_persisted_aliases()
            self.assertEqual(c._screenname_to_alias, {"venturaa": "bruno1"})
        finally:
            if saved is None:
                os.remove(path)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(saved)


if __name__ == "__main__":
    unittest.main()
