"""Starter selection reuse and post-match timing, with no screen or real input."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from Controller.MTGAController.Controller import Controller
from Game import Game
from state.state_machine import BotState


MODULE = "Controller.MTGAController.Controller"


class StarterSelectionMemoryTests(unittest.TestCase):
    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.addCleanup(lambda: os.unlink(log.name))
        self.c = Controller(log.name)
        self.c.input = Mock()
        self.c._get_state_from_log = Mock(return_value=BotState.HOME)
        self.c._current_account_screen_name = "Player#11111"
        self.c._choose_starter_deck_template = Mock(return_value="WB.PNG")
        self.c._open_starter_deck_chooser = Mock(return_value=True)
        self.c._detect_starter_screen = Mock(return_value=Controller._STARTER_SCREEN_PLAY)
        self.c._starter_event_page_confirmed = Mock(return_value=True)
        self.c._starter_template_visible = Mock(return_value=True)
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=(764, 699))
        self.c._click_image_in_scaled_arena_region = Mock(return_value=True)
        self.c._ensure_arena_region = Mock(return_value=(0, 0, 1920, 1080))
        self.c._click_abs = Mock()
        self.c._click_starter_back_arrow = Mock()
        self.c._start_popup_recovery = Mock()
        self.c._arm_quest_reroll = Mock()
        self.c._publish_account_switch_status = Mock()
        self.c._navigate_to_home = Mock(return_value=True)
        self.c.start_queueing = Mock()
        for target in (MODULE + ".time.sleep", MODULE + ".threading.Timer"):
            p = patch(target)
            p.start()
            self.addCleanup(p.stop)

    def select(self, deck="WB"):
        self.c._choose_starter_deck_template.return_value = deck + ".PNG"
        self.c._swap_starter_deck_for_quest(deck)

    def test_reuse_survives_match_reset_and_home_quest_refresh(self):
        self.select()
        self.assertEqual(self.c._starter_selected_deck, ("player#11111", "WB"))
        self.c.reset_for_new_game()
        # A changed quest response lets the existing Home refresh exit early.
        def refresh():
            self.c._cached_active_quest_id = "fresh"
        self.c.refresh_quests_cache = Mock(side_effect=refresh)
        self.assertTrue(self.c._refresh_quests_from_home())
        self.c._open_starter_deck_chooser.reset_mock()
        self.c._click_abs.reset_mock()
        self.c._click_image_in_scaled_arena_region.reset_mock()
        self.select()
        self.c._open_starter_deck_chooser.assert_not_called()
        self.c._click_abs.assert_not_called()
        self.c._click_image_in_scaled_arena_region.assert_not_called()
        # Reusing the deck still goes through the normal guarded Play action.
        self.c._click_starter_button = Mock(return_value=True)
        self.assertTrue(self.c._press_starter_event_play())
        self.c._starter_event_page_confirmed.assert_called_with("STARTER_EVENT_PLAY_GATE")

    def test_changed_target_selects_and_remembers_new_deck(self):
        self.select()
        self.select("RG")
        self.assertEqual(self.c._open_starter_deck_chooser.call_count, 2)
        self.assertEqual(self.c._starter_selected_deck, ("player#11111", "RG"))

    def test_failed_or_interrupted_selection_does_not_preserve_memory(self):
        for outcome in ("chooser", "unknown", "stopped", "matchmaking", "game"):
            with self.subTest(outcome=outcome):
                self.c._stop_requested = False
                self.c._get_state_from_log.return_value = BotState.HOME
                self.c._detect_starter_screen.side_effect = None
                self.c._detect_starter_screen.return_value = Controller._STARTER_SCREEN_PLAY
                self.select()
                if outcome in ("chooser", "unknown"):
                    self.c._detect_starter_screen.return_value = outcome
                else:
                    def interrupt(label):
                        if outcome == "stopped":
                            self.c._stop_requested = True
                        else:
                            self.c._get_state_from_log.return_value = (
                                BotState.FIND_MATCH if outcome == "matchmaking" else BotState.IN_GAME
                            )
                        return Controller._STARTER_SCREEN_PLAY
                    self.c._detect_starter_screen.side_effect = interrupt
                self.select("RG")
                self.assertIsNone(self.c._starter_selected_deck)

    def test_unconfirmed_event_or_missing_play_cannot_reuse(self):
        for missing in ("title", "play"):
            with self.subTest(missing=missing):
                self.c._starter_event_page_confirmed.return_value = True
                self.c._starter_template_visible.return_value = True
                self.select()
                self.c._starter_event_page_confirmed.return_value = missing != "title"
                self.c._starter_template_visible.return_value = missing != "play"
                self.c._open_starter_deck_chooser.reset_mock()
                self.c._open_starter_deck_chooser.return_value = False
                self.select()
                self.c._open_starter_deck_chooser.assert_called_once()
                self.assertIsNone(self.c._starter_selected_deck)
                self.c._open_starter_deck_chooser.return_value = True

    def test_no_identity_never_records_or_reuses_a_deck(self):
        self.c._current_account_screen_name = None
        self.select()
        self.select()
        self.assertIsNone(self.c._starter_selected_deck)
        self.assertEqual(self.c._open_starter_deck_chooser.call_count, 2)

    def test_account_discriminator_change_cannot_reuse(self):
        self.select()
        self.c._current_account_screen_name = "Player#22222"
        self.select()
        self.assertEqual(self.c._open_starter_deck_chooser.call_count, 2)
        self.assertEqual(self.c._starter_selected_deck, ("player#22222", "WB"))

    def test_session_start_and_confirmed_logout_clear_memory(self):
        self.select()
        self.c.begin_session()
        self.assertIsNone(self.c._starter_selected_deck)
        self.select()
        self.c._reset_state_for_incoming_account()
        self.assertIsNone(self.c._starter_selected_deck)

    def test_match_start_during_reuse_probe_prevents_selection_input(self):
        self.select()
        self.c._click_abs.reset_mock()
        self.c._open_starter_deck_chooser.reset_mock()
        def match_started(*args):
            self.c._get_state_from_log.return_value = BotState.IN_GAME
            return True
        self.c._starter_template_visible.side_effect = match_started
        self.select()
        self.c._open_starter_deck_chooser.assert_not_called()
        self.c._click_abs.assert_not_called()

    def test_twenty_second_hold_includes_ten_second_game_reset(self):
        self.assertEqual(self.c._post_match_delay_sec, 20)
        self.c._account_switch_due = Mock(return_value=False)
        with patch(MODULE + ".time.time", return_value=100):
            self.c._note_match_finished()
        game = Game(self.c, Mock())
        with patch("Game.threading.Timer") as timer, patch("Game.bot_logger"), patch(MODULE + ".time.time", return_value=100):
            game.on_match_end(False)
            self.assertEqual(timer.call_args.args[0], 10.0)
        with patch(MODULE + ".time.time", return_value=110):
            game._restart_game()
            self.c.start_queueing.assert_not_called()
        with patch(MODULE + ".time.time", return_value=119):
            self.c._maybe_post_match_action()
            self.c.start_queueing.assert_not_called()
        with patch(MODULE + ".time.time", return_value=120):
            self.c._maybe_post_match_action()
            self.c.start_queueing.assert_called_once()
