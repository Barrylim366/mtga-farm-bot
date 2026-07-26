"""Unit tests for the start-of-session quest read.

On Start everything already in the player.log is ignored (the session floor), so
a quest the user re-rolled or finished by hand in MTGA can't be read as current;
the bot waits for MTGA to log a fresh block and only falls back to the old one
if none arrives within the margin.

Also pins down that a match-server handshake ("Match to <clientId>:
AuthenticateResponse", logged on every match connect) must NOT invalidate the
account's quests block -- gating on it would freeze quest data for the whole
match.

Pure log-parsing tests: they build a Controller against a throwaway log file and
never touch the screen, the network or the real runtime/status.json (the status
publisher is stubbed out).
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import runtime_status
from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState

GOLGARI = "Quests/Quest_Golgari_Guildmage"
SIMIC = "Quests/Quest_Simic_Manipulator"


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


def match_auth_block(screen_name: str) -> str:
    """The handshake MTGA logs on every match connect. It carries the local
    player's screenName, which is how identity is latched -- but it is NOT an
    account login, so it says nothing about the freshness of a quests block."""
    return "[UnityCrossThreadLogger]26/07/2026 15:36:31: Match to CLIENTID: AuthenticateResponse\n" + json.dumps(
        {"authenticateResponse": {"clientId": "CLIENTID", "sessionId": "s1", "screenName": screen_name}}
    ) + "\n"


class _QuestLogTestBase(unittest.TestCase):
    """Controller against a throwaway log; no screen, no network, no status.json."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.log_path = f.name
        self.controller = Controller(self.log_path)
        # Never write the user's real runtime/status.json from a test.
        self._real_status = (
            runtime_status.update_status,
            runtime_status.set_mode,
            runtime_status.clear_intentional_wait,
        )
        runtime_status.update_status = lambda **kwargs: None
        runtime_status.set_mode = lambda *a, **k: None
        runtime_status.clear_intentional_wait = lambda *a, **k: None

    def tearDown(self):
        (
            runtime_status.update_status,
            runtime_status.set_mode,
            runtime_status.clear_intentional_wait,
        ) = self._real_status
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    def append(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text)


class QuestReadGateTests(_QuestLogTestBase):
    """Which quests block the bot is allowed to believe."""

    def test_match_handshake_does_not_invalidate_the_quests_block(self):
        """Quests are logged on Home, the handshake on every match connect after
        it -- so the newest block is ALWAYS older than the newest handshake."""
        self.append(quests_block(GOLGARI))
        self.append(match_auth_block("venturaa"))
        quests = self.controller._extract_latest_quests()
        self.assertIsNotNone(quests)
        self.assertEqual([q["locKey"] for q in quests], [GOLGARI])

    def test_newest_block_wins(self):
        self.append(quests_block(SIMIC))
        self.append(match_auth_block("venturaa"))
        self.append(quests_block(GOLGARI, quest_id="q-2"))
        self.controller.refresh_quests_cache()
        self.assertEqual(self.controller._cached_active_colors, "BG")

    def test_session_floor_ignores_everything_logged_before_start(self):
        """What the user re-rolled by hand pre-Start must not be read as current."""
        self.append(match_auth_block("venturaa"))
        self.append(quests_block(SIMIC))
        self.controller._quests_session_floor_offset = os.path.getsize(self.log_path)
        self.assertIsNone(self.controller._extract_latest_quests())
        # ...until MTGA logs the refreshed list on Home.
        self.append(quests_block(GOLGARI, quest_id="q-2"))
        quests = self.controller._extract_latest_quests()
        self.assertIsNotNone(quests)
        self.assertEqual([q["locKey"] for q in quests], [GOLGARI])

    def test_reset_for_new_session_drops_the_previous_cache(self):
        self.append(match_auth_block("venturaa"))
        self.append(quests_block(SIMIC))
        self.controller.refresh_quests_cache()
        self.assertEqual(self.controller._cached_active_colors, "UG")
        self.controller._reset_quest_cache_for_new_session()
        self.assertEqual(self.controller._cached_quests, [])
        self.assertEqual(self.controller._cached_active_colors, "")
        self.assertIsNone(self.controller._last_valid_quest_active_incomplete)
        self.assertFalse(self.controller._home_quest_check_done)

    def test_prime_times_out_and_falls_back_to_the_newest_block(self):
        """No fresh block arrives -> the floor is dropped, the bot is not blind."""
        self.append(match_auth_block("venturaa"))
        self.append(quests_block(SIMIC))
        self.controller._QUESTS_PRIME_TIMEOUT = 0.0
        self.controller._ensure_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
        # Keep the test off the real screen (this one does template matching).
        self.controller._dismiss_reward_popup = lambda: False
        self.controller._navigate_to_home = lambda: False
        self.assertFalse(self.controller.prime_quests_for_new_session())
        self.assertEqual(self.controller._quests_session_floor_offset, 0)
        self.assertEqual(self.controller._cached_active_colors, "UG")

    def test_prime_reads_the_block_logged_after_start(self):
        self.append(match_auth_block("venturaa"))
        self.append(quests_block(SIMIC))

        def fake_home() -> bool:
            # Stands in for MTGA logging QuestGetQuests when Home (re)loads.
            self.append(quests_block(GOLGARI, quest_id="q-2"))
            return True

        self.controller._ensure_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
        # Keep the test off the real screen (this one does template matching).
        self.controller._dismiss_reward_popup = lambda: False
        self.controller._navigate_to_home = fake_home
        self.assertTrue(self.controller.prime_quests_for_new_session())
        self.assertEqual(self.controller._cached_active_colors, "BG")
        self.assertEqual(self.controller._quests_session_floor_offset, 0)


class QuestTargetSelectionTests(_QuestLogTestBase):
    """The colors the starter-deck swap is driven with."""

    def test_completed_guild_quest_is_not_selected_while_another_is_open(self):
        """The live parse (used whenever the cache is empty) must not keep
        farming a quest that is already at its goal."""
        done = json.loads(quests_block(SIMIC).split(" ", 2)[2])["quests"][0]
        done["endingProgress"] = 20
        open_quest = json.loads(quests_block(GOLGARI, quest_id="q-2").split(" ", 2)[2])["quests"][0]
        # The finished one pays more, so gold alone would pick it.
        done["chestDescription"]["locParams"]["number1"] = 750
        self.append("<== QuestGetQuests " + json.dumps({"quests": [done, open_quest]}) + "\n")
        best = self.controller._select_best_quest()
        self.assertEqual(best.get("guild"), "golgari")

    def test_requeue_uses_the_colors_read_after_the_refresh(self):
        """The post-match re-queue refreshes quests; the swap must use the NEW
        colors, not the ones resolved before that refresh."""
        self.append(quests_block(SIMIC))
        self.controller.refresh_quests_cache()
        stale_colors = self.controller._resolve_starter_target_colors()
        self.assertEqual(stale_colors, "UG")

        swapped_with = []
        self.controller._on_starter_event_landing_page = lambda label: True
        self.controller._click_image_in_scaled_arena_region = (
            lambda *a, **k: True
        )
        self.controller._swap_starter_deck_for_quest = swapped_with.append
        # The quest changed (re-rolled/completed) since the colors above were read.
        self.append(quests_block(GOLGARI, quest_id="q-2"))

        self.assertTrue(self.controller._queue_from_event_landing(stale_colors))
        self.assertEqual(swapped_with, ["BG"])


class AccountSwitchTimingTests(_QuestLogTestBase):
    """WHEN the switch (and the end-of-round stop) is allowed to happen."""

    def _arm_round_complete(self) -> list:
        """Every configured account already finished -> the next switch attempt
        is the end of the round, i.e. the one that stops the bot."""
        c = self.controller
        c._account_switch_mode = "quests"
        c._load_accounts_from_dirs = lambda: [{"name": "a"}, {"name": "b"}]
        c._resolve_account_play_order = lambda accounts: [0, 1]
        c._completed_account_keys = {"a", "b"}
        c._current_account_screen_name = "a"
        stops: list = []
        c.set_stop_bot_callback(stops.append)
        return stops

    def test_switch_is_deferred_while_a_match_is_running(self):
        """The bug seen live: the loop queued a match and a stray post-match
        trigger stopped the bot mid-game."""
        stops = self._arm_round_complete()
        self.controller._get_state_from_log = lambda: BotState.IN_GAME
        self.controller._perform_account_switch()
        self.assertEqual(stops, [])
        self.assertTrue(self.controller._account_switch_pending)
        # Released, so the deferred switch can run again after the match.
        self.assertFalse(self.controller._account_switch_in_progress)
        self.assertFalse(self.controller._stop_requested)

    def test_switch_is_deferred_while_matchmaking(self):
        stops = self._arm_round_complete()
        self.controller._get_state_from_log = lambda: BotState.FIND_MATCH
        self.controller._perform_account_switch()
        self.assertEqual(stops, [])
        self.assertTrue(self.controller._account_switch_pending)

    def test_round_complete_stops_the_bot_once_the_match_is_over(self):
        stops = self._arm_round_complete()
        self.controller._get_state_from_log = lambda: BotState.HOME
        self.controller._perform_account_switch()
        self.assertEqual(len(stops), 1)
        self.assertTrue(self.controller._stop_requested)


if __name__ == "__main__":
    unittest.main()
