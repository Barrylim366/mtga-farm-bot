"""Unit tests for the quest-aware Historic format/deck selection.

The bug these pin down: the Historic re-queue used to be a bare Play click,
which re-enters *whatever MTGA last had selected*. When the client was last on a
Starter Duel event with a Golgari deck, an RW (Boros) quest got farmed with that
Golgari Starter deck and never progressed -- the exact sequence in the user's
bot.log of 2026-09-14 (quest colors RW, Home, Play, then a `DualColorPrecons`
match).

So the queue path must now navigate to the Historic deck screen, pick the deck
matching the quest, verify it got there -- and, when any of that fails, NOT
queue, because queueing is what re-enters the stale selection.

Pure logic tests: the Controller is built against a throwaway player.log, every
screen-touching call is stubbed (see CLAUDE.md -- a Controller that is allowed to
template-match really searches the monitor), and runtime/status.json is never
written.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import runtime_status
from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState

BOROS = "Quests/Quest_Boros_Recruit"          # RW
GOLGARI = "Quests/Quest_Golgari_Guildmage"    # BG
FATAL_PUSH = "Quests/Quest_Fatal_Push"        # forced deck file B.png
CREATURE = "Quests/Quest_Creature_Cast"


def quests_block(loc_key: str, quest_id: str = "q-1", gold: int = 500) -> str:
    payload = {
        "quests": [{
            "questId": quest_id,
            "locKey": loc_key,
            "goal": 20,
            "endingProgress": 0,
            "chestDescription": {"locParams": {"number1": gold}},
        }]
    }
    return "<== QuestGetQuests " + json.dumps(payload) + "\n"


class _FakeInput:
    """Records the physical input the queue click would issue."""

    def __init__(self):
        self.calls = []

    def move_abs(self, x, y):
        self.calls.append(("move_abs", x, y))

    def left_down(self):
        self.calls.append(("left_down",))

    def left_up(self):
        self.calls.append(("left_up",))

    def left_click(self, n=1):
        self.calls.append(("left_click", n))

    def tap_escape(self):
        self.calls.append(("tap_escape",))

    @property
    def clicked(self) -> bool:
        return any(c[0] in ("left_down", "left_click") for c in self.calls)


class _HistoricTestBase(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.log_path = f.name
        self.account_dir = tempfile.mkdtemp(prefix="acct_")
        for name in ("RW.png", "BG.png", "B.png"):
            with open(os.path.join(self.account_dir, name), "wb") as fh:
                fh.write(b"")

        self._real_status = (
            runtime_status.update_status,
            runtime_status.set_mode,
            runtime_status.clear_intentional_wait,
            runtime_status.touch_input,
        )
        runtime_status.update_status = lambda **kwargs: None
        runtime_status.set_mode = lambda *a, **k: None
        runtime_status.clear_intentional_wait = lambda *a, **k: None
        runtime_status.touch_input = lambda *a, **k: None
        sleep_patch = mock.patch("time.sleep", lambda *a, **k: None)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

        c = Controller(self.log_path)
        # Never let a test see the screen (CLAUDE.md).
        c._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
        c._click_image_in_scaled_arena_region = lambda *a, **k: False
        c._ensure_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
        c._map_abs_point_to_arena = lambda p, **k: (tuple(p), "arena_mapped")
        c._map_base_point_into_arena = lambda arena, p: tuple(p)
        c.input = _FakeInput()
        # One configured account, its thumbnails in a temp folder.
        self.account = {
            "name": "AccountA", "folder": "AccountA",
            "email": "a@b.c", "pw": "x", "screen_name": "AccountA#11111",
        }
        c._load_accounts_from_dirs = lambda: [self.account]
        c._resolve_account_dir = lambda acc: self.account_dir
        c._current_account_screen_name = "AccountA#11111"
        # Deck thumbnails that "match on screen".
        self.on_screen = {"RW.png", "BG.png", "B.png"}
        self.clicked_decks = []

        def click_image(path, label):
            base = os.path.basename(path)
            if base in self.on_screen:
                self.clicked_decks.append(base)
                return True
            return False

        c._click_image = click_image
        c._click_first_deck_slot = lambda: False
        self.controller = c

    def tearDown(self):
        (
            runtime_status.update_status,
            runtime_status.set_mode,
            runtime_status.clear_intentional_wait,
            runtime_status.touch_input,
        ) = self._real_status
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    def append(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(text)

    def arm_navigation(self, *, home=True, nav=True, state=BotState.MY_DECKS, anchor=True):
        """Stub the navigation the selection drives, and where it lands.

        ``anchor`` is what the final on-screen verification sees; ``state`` is
        what the player.log claims. They are separate on purpose -- the log state
        is substring-matched over a 250 KB tail and goes stale."""
        self.nav_calls = []
        self.controller._navigate_to_home = lambda: home
        def oob():
            self.nav_calls.append("oob")
            return nav
        self.controller._run_post_login_navigation_oob = oob
        self.controller._get_state_from_log = lambda: state
        self.controller._locate_image_center_in_scaled_arena_region = (
            lambda *a, **k: (100, 100) if anchor else None
        )


class QuestDeckTargetTests(_HistoricTestBase):
    """The one place both Historic paths read the quest's deck target from."""

    def test_guild_quest_maps_to_its_colors(self):
        self.append(quests_block(BOROS))
        self.assertEqual(self.controller._quest_deck_target("Historic"), ("RW", None))

    def test_forced_file_quest_returns_the_file_not_colors(self):
        self.append(quests_block(FATAL_PUSH))
        self.assertEqual(self.controller._quest_deck_target("Historic"), ("", "B.png"))

    def test_creature_quest_maps_to_colorless(self):
        self.append(quests_block(CREATURE))
        self.assertEqual(self.controller._quest_deck_target("Historic"), ("C", None))

    def test_no_quest_falls_back_to_the_cached_colors(self):
        """The live parse is empty between matches; the cache is this account's
        own Home read, so it must win over giving up on the quest."""
        self.controller._cached_active_colors = "RW"
        self.assertEqual(self.controller._quest_deck_target("Historic"), ("RW", None))

    def test_no_quest_and_no_cache_has_no_target(self):
        self.assertEqual(self.controller._quest_deck_target("Historic"), ("", None))


class HistoricSelectionTests(_HistoricTestBase):
    """Navigating + selecting the quest's deck before the queue click."""

    def test_quest_colors_drive_the_deck_not_the_last_selection(self):
        """The reported bug: RW quest, Golgari deck left selected by MTGA."""
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertEqual(self.clicked_decks, ["RW.png"])
        self.assertEqual(self.nav_calls, ["oob"])

    def test_forced_file_quest_selects_that_deck(self):
        self.append(quests_block(FATAL_PUSH))
        self.arm_navigation()
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertEqual(self.clicked_decks, ["B.png"])

    def test_failed_navigation_does_not_verify_a_selection(self):
        self.append(quests_block(BOROS))
        self.arm_navigation(nav=False)
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(self.clicked_decks, [])
        self.assertIsNone(self.controller._historic_selection_key)

    def test_no_selectable_deck_does_not_verify_a_selection(self):
        """The quest's thumbnail is configured but does not match on screen (a
        stale screenshot): keeping the stale deck is what must not be queued."""
        self.append(quests_block(BOROS))
        self.on_screen = set()
        self.arm_navigation()
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertIsNone(self.controller._historic_selection_key)

    def test_target_quest_never_falls_back_to_the_first_deck_tile(self):
        """The first tile's colors are unknown, so it cannot satisfy a quest that
        named its colors -- farming RW with an arbitrary deck is the same bug as
        farming it with the stale Starter deck, just quieter."""
        self.append(quests_block(BOROS))
        self.on_screen = set()
        slot_calls = []
        self.controller._click_first_deck_slot = lambda: slot_calls.append("slot") or True
        self.arm_navigation()
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(slot_calls, [])
        self.assertIsNone(self.controller._historic_selection_key)

    def test_no_matching_thumbnail_configured_is_not_substituted(self):
        """_choose_deck_image falls back to the first/random image in the folder;
        with an explicit target that fallback must be rejected, not clicked."""
        for name in ("RW.png", "B.png"):
            os.unlink(os.path.join(self.account_dir, name))   # only BG.png left
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(self.clicked_decks, [])

    def test_first_deck_fallback_only_without_a_quest_target(self):
        """No readable quest: no colors to miss, so getting off whatever was left
        selected is still worth doing."""
        self.on_screen = set()
        slot_calls = []
        self.controller._click_first_deck_slot = lambda: slot_calls.append("slot") or True
        self.arm_navigation()
        self.assertEqual(self.controller._quest_deck_target("Historic"), ("", None))
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertEqual(slot_calls, ["slot"])

    def test_other_accounts_thumbnails_are_not_matched_when_identity_is_known(self):
        """Two accounts' RW decks are routinely the same precon art, so matching
        a foreign folder against this grid selects a deck nothing pointed at."""
        other_dir = tempfile.mkdtemp(prefix="acct_other_")
        with open(os.path.join(other_dir, "RW.png"), "wb") as fh:
            fh.write(b"")
        other = {
            "name": "AccountB", "folder": "AccountB",
            "email": "b@b.c", "pw": "x", "screen_name": "AccountB#22222",
        }
        dirs = {"AccountA": self.account_dir, "AccountB": other_dir}
        self.controller._load_accounts_from_dirs = lambda: [self.account, other]
        self.controller._resolve_account_dir = lambda acc: dirs[acc["folder"]]
        os.unlink(os.path.join(self.account_dir, "RW.png"))    # A has no RW deck
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(self.clicked_decks, [])

    def test_unknown_identity_may_still_match_any_configured_folder(self):
        """Degraded path: before the screenName is latched there is nothing to
        scope the search to, and no deck at all is worse."""
        self.controller._current_account_screen_name = ""
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertEqual(self.clicked_decks, ["RW.png"])

    def test_wrong_screen_after_navigation_is_not_verified(self):
        """Navigation reported success but we are somewhere else (e.g. back on
        the event page) -- the last gate before the queue click."""
        self.append(quests_block(BOROS))
        self.arm_navigation(state=BotState.HOME, anchor=False)
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertIsNone(self.controller._historic_selection_key)

    def test_stale_log_state_alone_does_not_verify_the_screen(self):
        """The log state is substring-matched over a 250 KB tail, so the words
        from an earlier My Decks navigation still read as My Decks long after the
        client moved on. Only a live anchor match may pass this gate."""
        self.append(quests_block(BOROS))
        self.arm_navigation(state=BotState.MY_DECKS, anchor=False)
        self.assertFalse(self.controller._historic_selection_screen_verified())
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertIsNone(self.controller._historic_selection_key)

    def test_failures_back_off_instead_of_renavigating_every_tick(self):
        """The queue loop ticks every ~3s. A target this install cannot satisfy
        fails every time, so retrying it per tick would walk the whole navigation
        (and log it) three times a minute, forever."""
        self.append(quests_block(BOROS))
        self.on_screen = set()
        self.arm_navigation()
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(self.controller._historic_selection_failures, 1)
        self.assertGreater(self.controller._historic_selection_retry_ts, 0.0)
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(self.nav_calls, ["oob"])          # no second navigation
        self.assertEqual(self.controller._historic_selection_failures, 1)

    def test_backoff_expiry_retries_and_success_clears_it(self):
        self.append(quests_block(BOROS))
        self.on_screen = set()
        self.arm_navigation()
        self.assertFalse(self.controller._ensure_historic_selection())
        self.controller._historic_selection_retry_ts = 0.0   # backoff elapsed
        self.on_screen = {"RW.png"}
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertEqual(self.nav_calls, ["oob", "oob"])
        self.assertEqual(self.controller._historic_selection_failures, 0)
        self.assertEqual(self.controller._historic_selection_retry_ts, 0.0)

    def test_anchor_match_verifies_even_with_an_unknown_log_state(self):
        self.arm_navigation(state=BotState.UNKNOWN, anchor=True)
        self.assertTrue(self.controller._historic_selection_screen_verified())

    def test_verified_selection_is_not_renavigated_for_the_same_quest(self):
        """MTGA keeps the selection between matches; re-navigating every queue
        would add ~10s of clicking per match for nothing."""
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertEqual(self.nav_calls, ["oob"])
        self.assertEqual(self.clicked_decks, ["RW.png"])

    def test_changed_quest_colors_reselect_the_deck(self):
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertTrue(self.controller._ensure_historic_selection())
        self.append(quests_block(GOLGARI, quest_id="q-2"))
        self.assertTrue(self.controller._ensure_historic_selection())
        self.assertEqual(self.clicked_decks, ["RW.png", "BG.png"])
        self.assertEqual(self.nav_calls, ["oob", "oob"])

    def test_match_in_progress_defers_instead_of_navigating(self):
        self.append(quests_block(BOROS))
        self.arm_navigation(state=BotState.IN_GAME)
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(self.nav_calls, [])

    def test_account_switch_owns_the_screen(self):
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.controller._account_switch_in_progress = True
        self.assertFalse(self.controller._ensure_historic_selection())
        self.assertEqual(self.nav_calls, [])

    def test_incoming_account_must_reselect(self):
        """Another account has its own decks, and MTGA re-applies whatever IT
        last had selected -- the outgoing account's verification says nothing."""
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertTrue(self.controller._ensure_historic_selection())
        self.controller._reset_state_for_incoming_account()
        self.assertIsNone(self.controller._historic_selection_key)

    def test_new_session_must_reselect(self):
        self.append(quests_block(BOROS))
        self.arm_navigation()
        self.assertTrue(self.controller._ensure_historic_selection())
        self.controller.begin_session()
        self.assertIsNone(self.controller._historic_selection_key)

    def test_current_account_is_resolved_from_the_logged_in_screen_name(self):
        self.assertEqual(self.controller._current_account_config(), self.account)
        self.controller._current_account_screen_name = "SomeoneElse#99999"
        self.assertIsNone(self.controller._current_account_config())


class _PostLoginTestBase(_HistoricTestBase):
    """Harness for the post-login routine: no screen, every click recorded."""

    def setUp(self):
        super().setUp()
        c = self.controller
        c._game_mode = "historic"
        c.reroll_quest_on_landing = lambda: True
        c._account_switch_due = lambda: False
        self.play_clicks = []
        c._click_image_in_scaled_arena_region = (
            lambda path, label, **k: self.play_clicks.append(label) or True
        )
        c._my_decks_grid_open = lambda: True
        self.nav_ok = True
        c._run_post_login_navigation_oob = lambda: self.nav_ok
        self.append(quests_block(BOROS))

    def run_routine(self, accounts=None):
        accounts = accounts if accounts is not None else [self.account]
        return self.controller._run_post_login_routine(self.account, accounts)

    @property
    def play_pressed(self) -> bool:
        return "POST_LOGIN_PLAY_CONFIRM" in self.play_clicks

    def drop_quests(self) -> None:
        """Leave the routine with no readable quest, i.e. no deck target."""
        with open(self.log_path, "w", encoding="utf-8"):
            pass


class PostLoginDeckSelectionTests(_PostLoginTestBase):
    """The first queue after a login or account switch obeys the same strict rule.

    This is the path that opens an account's session. It used to click whatever
    `_choose_deck_image` fell back to -- or the first tile -- and then press Play,
    so an account's opening matches could farm an RW quest with the wrong deck
    while the between-matches re-queue was strict about exactly that."""

    def test_matching_thumbnail_is_selected_and_play_pressed(self):
        self.assertTrue(self.run_routine())
        self.assertEqual(self.clicked_decks, ["RW.png"])
        self.assertTrue(self.play_pressed)

    def test_no_matching_thumbnail_configured_does_not_press_play(self):
        """Only BG.png left: the quest is RW, so there is nothing to select and
        the routine must not queue an RW quest onto a Golgari deck."""
        for name in ("RW.png", "B.png"):
            os.unlink(os.path.join(self.account_dir, name))
        self.assertFalse(self.run_routine())
        self.assertEqual(self.clicked_decks, [])
        self.assertFalse(self.play_pressed)
        self.assertIsNone(self.controller._historic_selection_key)

    def test_thumbnail_that_does_not_match_on_screen_does_not_press_play(self):
        """RW.png is configured but no longer matches the client (stale capture)."""
        self.on_screen = set()
        self.assertFalse(self.run_routine())
        self.assertFalse(self.play_pressed)

    def test_target_quest_never_falls_back_to_the_first_deck_tile(self):
        self.on_screen = set()
        slot_calls = []
        self.controller._click_first_deck_slot = lambda: slot_calls.append("slot") or True
        self.assertFalse(self.run_routine())
        self.assertEqual(slot_calls, [])
        self.assertFalse(self.play_pressed)

    def test_another_accounts_thumbnail_is_not_used(self):
        """The planned account has no RW deck; another account's RW.png must not
        stand in for it, even though it would match the artwork on screen."""
        other_dir = tempfile.mkdtemp(prefix="acct_other_")
        with open(os.path.join(other_dir, "RW.png"), "wb") as fh:
            fh.write(b"")
        other = {
            "name": "AccountB", "folder": "AccountB",
            "email": "b@b.c", "pw": "x", "screen_name": "AccountB#22222",
        }
        dirs = {"AccountA": self.account_dir, "AccountB": other_dir}
        self.controller._resolve_account_dir = lambda acc: dirs[acc["folder"]]
        os.unlink(os.path.join(self.account_dir, "RW.png"))
        self.assertFalse(self.run_routine([self.account, other]))
        self.assertEqual(self.clicked_decks, [])
        self.assertFalse(self.play_pressed)

    def test_no_quest_target_still_uses_the_first_deck_tile(self):
        """Nothing to miss without a target, so getting off whatever was left
        selected is still worth doing -- and this path still queues."""
        self.drop_quests()
        self.on_screen = set()
        self.controller._click_first_deck_slot = lambda: True
        self.assertEqual(self.controller._quest_deck_target("Post-login"), ("", None))
        self.assertTrue(self.run_routine())
        self.assertTrue(self.play_pressed)


class PostLoginSelectionBookkeepingTests(_PostLoginTestBase):
    """When the post-login routine may declare the Historic selection verified.

    It cannot re-check the screen afterwards (it has already pressed Play and is
    matchmaking), so it may only vouch for a selection every step of which it
    confirmed. Anything less must stay unverified -- the re-queue then navigates
    and checks for itself, which is cheap next to farming the wrong deck."""

    def test_confirmed_navigation_and_matching_deck_is_verified(self):
        self.assertTrue(self.run_routine())
        self.assertEqual(
            self.controller._historic_selection_key,
            self.controller._historic_selection_key_for("RW", None),
        )

    def test_legacy_unverified_navigation_leaves_it_unverified(self):
        """The legacy image-only fallback asserts no screen it passes through."""
        self.nav_ok = False
        self.assertTrue(self.run_routine())
        self.assertEqual(self.clicked_decks, ["RW.png"])
        self.assertTrue(self.play_pressed)
        self.assertIsNone(self.controller._historic_selection_key)

    def test_first_deck_fallback_leaves_it_unverified(self):
        """The first tile's colors are unknown, so it cannot vouch for a quest --
        and without a target there is no key worth keeping either."""
        self.drop_quests()
        self.on_screen = set()
        self.controller._click_first_deck_slot = lambda: True
        self.assertTrue(self.run_routine())
        self.assertIsNone(self.controller._historic_selection_key)


class HistoricQueueGateTests(_HistoricTestBase):
    """start_game_from_home_screen must not click Play on an unverified selection."""

    def setUp(self):
        super().setUp()
        c = self.controller
        c._game_mode = "historic"
        c.reroll_quest_on_landing = lambda: True
        c._account_switch_enabled = False
        c._account_switch_in_progress = False
        c._account_switch_due = lambda: False
        c._refresh_quests_from_home = lambda: True
        c._get_state_from_log = lambda: BotState.HOME

    def test_unverified_selection_blocks_the_queue_click(self):
        self.controller._ensure_historic_selection = lambda: False
        self.controller.start_game_from_home_screen()
        self.assertFalse(self.controller.input.clicked)

    def test_verified_selection_queues(self):
        self.controller._ensure_historic_selection = lambda: True
        self.controller.start_game_from_home_screen()
        self.assertTrue(self.controller.input.clicked)

    def test_starter_mode_is_untouched(self):
        """Starter keeps its own Events > In Progress navigation and never goes
        through the Historic selection."""
        calls = []
        self.controller._game_mode = "starter"
        self.controller._navigate_starter_deck = lambda: calls.append("starter") or True
        self.controller._ensure_historic_selection = lambda: calls.append("historic") or True
        self.controller.start_game_from_home_screen()
        self.assertEqual(calls, ["starter"])
        self.assertFalse(self.controller.input.clicked)


if __name__ == "__main__":
    unittest.main()
