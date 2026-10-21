"""Unit tests for preferred deck selection, only-grind mode, and direct re-queue bypass.

Verifies:
1. Starter deck normalization (color codes, deck names, combo labels, empty/None).
2. Starter target color resolution:
   - Active quest uses quest colors.
   - Finished quests automatically switch to preferred starter deck (or WB fallback).
   - No active quests uses preferred starter deck.
   - Only-grind mode forces preferred starter deck regardless of active quests.
3. Historic deck target resolution:
   - Active quest uses quest colors.
   - Finished quests switch to preferred historic deck (colors or forced file).
   - No active quests uses preferred historic deck.
   - Only-grind mode forces preferred historic deck regardless of active quests.
4. Deck chooser skip:
   - When target deck is already selected, _swap_starter_deck_for_quest does not open chooser.
   - Submitting a deck records it as selected.
   - _forget_selected_deck and account switch clear selected deck.
5. Post-match Home dip bypass:
   - No Home dip when all daily quests complete or only-grind mode is active.
   - Direct re-queue on event landing page.
6. ConfigManager getters, setters, and persistence.
"""
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
import run_bot
import ui


class StarterDeckNormalizationTests(unittest.TestCase):
    def test_direct_two_letter_codes(self):
        for code in ("WU", "WG", "UB", "UG", "WR", "BG", "RG", "BR", "WB", "UR"):
            self.assertEqual(Controller._normalize_starter_deck_code(code), code)
            self.assertEqual(Controller._normalize_starter_deck_code(code.lower()), code)

    def test_deck_names(self):
        cases = {
            "Arcane Aerialists": "WU",
            "Cat Attack": "WG",
            "Graveyard Gifts": "UB",
            "Learn from the Land": "UG",
            "Might of the Legion": "WR",
            "Morbid Machinations": "BG",
            "Path of Power": "RG",
            "Reckless Raid": "BR",
            "Vampiric Hunger": "WB",
            "Wondrous Wizardry": "UR",
        }
        for name, expected in cases.items():
            self.assertEqual(Controller._normalize_starter_deck_code(name), expected)
            self.assertEqual(Controller._normalize_starter_deck_code(name.lower()), expected)

    def test_combo_labels(self):
        self.assertEqual(Controller._normalize_starter_deck_code("RG - Path of Power"), "RG")
        self.assertEqual(Controller._normalize_starter_deck_code("Cat Attack (WG)"), "WG")

    def test_empty_and_invalid(self):
        self.assertEqual(Controller._normalize_starter_deck_code(""), "")
        self.assertEqual(Controller._normalize_starter_deck_code(None), "")
        self.assertEqual(Controller._normalize_starter_deck_code("None (Auto / WB Fallback)"), "")
        self.assertEqual(Controller._normalize_starter_deck_code("Unknown Deck XYZ"), "")


class _ThrowawayControllerBase(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        self.c = Controller(f.name)
        # Suppress physical actions and template matching
        self.c.input = Mock()
        self.c._click_abs = Mock()
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = Mock(return_value=False)
        self.c._dismiss_reward_popup = Mock(return_value=False)
        self.c._current_account_screen_name = "Player#11111"
        self.c._starter_event_page_confirmed = Mock(return_value=True)
        self.c._starter_template_visible = Mock(return_value=True)
        timer = patch("Controller.MTGAController.Controller.threading.Timer")
        timer.start()
        self.addCleanup(timer.stop)
        sleep = patch("Controller.MTGAController.Controller.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)


class StarterDeckResolutionTests(_ThrowawayControllerBase):
    def test_preferred_deck_after_configured_quest_target_with_other_quests_remaining(self):
        self.c._preferred_starter_deck = "RG"
        self.c._account_switch_mode = "quests"
        self.c._account_switch_main_quests = 1
        self.c._account_switch_daily_wins = 4
        self.c._last_valid_quest_active_incomplete = 2
        self.c._quest_count_confirmed_fresh = True
        self.c._cached_active_colors = "UB"
        self.assertEqual(self.c._resolve_starter_target_colors(), "RG")

    def test_default_mode_plays_quest_deck_when_quest_active(self):
        self.c._preferred_starter_deck = "RG"
        self.c._only_grind_preferred_deck = False
        self.c._cached_quests = [{"type": "guild", "guild": "Dimir", "goal": 20, "progress": 5}]
        self.c._cached_active_colors = "UB"
        self.c._last_valid_quest_active_incomplete = 1

        colors = self.c._resolve_starter_target_colors()
        self.assertEqual(colors, "UB", "Must play quest deck (UB) while quests are active")

    def test_default_mode_switches_to_preferred_deck_when_quests_complete(self):
        self.c._preferred_starter_deck = "RG"
        self.c._only_grind_preferred_deck = False
        self.c._last_valid_quest_active_incomplete = 0
        self.c._quest_count_confirmed_fresh = True

        colors = self.c._resolve_starter_target_colors()
        self.assertEqual(colors, "RG", "Must switch to preferred deck (RG) when quests complete")

    def test_default_mode_falls_back_to_wb_when_no_preferred_deck(self):
        self.c._preferred_starter_deck = ""
        self.c._only_grind_preferred_deck = False
        self.c._last_valid_quest_active_incomplete = 0
        self.c._quest_count_confirmed_fresh = True

        colors = self.c._resolve_starter_target_colors()
        self.assertEqual(colors, "WB", "Must fall back to WB when no preferred deck is set")

    def test_default_mode_uses_preferred_deck_when_no_active_quests(self):
        self.c._preferred_starter_deck = "BG"
        self.c._only_grind_preferred_deck = False
        self.c._cached_quests = []
        self.c._cached_active_colors = ""
        self.c._select_best_quest = lambda: None

        colors = self.c._resolve_starter_target_colors()
        self.assertEqual(colors, "BG", "Must use preferred deck (BG) when no active quests exist")

    def test_only_grind_mode_overrides_active_quests(self):
        self.c._preferred_starter_deck = "UR"
        self.c._only_grind_preferred_deck = True
        self.c._cached_quests = [{"type": "guild", "guild": "Golgari", "goal": 20, "progress": 0}]
        self.c._cached_active_colors = "BG"
        self.c._last_valid_quest_active_incomplete = 1

        colors = self.c._resolve_starter_target_colors()
        self.assertEqual(colors, "UR", "Only grind mode must play preferred deck (UR) ignoring quest (BG)")

    def test_only_grind_mode_with_blank_deck_falls_back_to_quest(self):
        self.c._preferred_starter_deck = ""
        self.c._only_grind_preferred_deck = True
        self.c._cached_quests = [{"type": "guild", "guild": "Azorius", "goal": 20, "progress": 0}]
        self.c._cached_active_colors = "WU"

        colors = self.c._resolve_starter_target_colors()
        self.assertEqual(colors, "WU", "Blank preferred deck in only-grind mode falls back cleanly")


class HistoricDeckResolutionTests(_ThrowawayControllerBase):
    def test_preferred_deck_when_750_policy_preserves_remaining_500_quests(self):
        self.c._preferred_historic_deck = "wins.png"
        self.c._account_switch_mode = "quests"
        self.c._account_switch_main_quests = 3
        self.c._account_switch_750_only = True
        self.c._last_valid_quest_active_incomplete = 2
        self.c._quest_count_confirmed_fresh = True
        self.c._cached_quests = [
            {"gold": 500, "goal": 20, "progress": 0},
            {"gold": 500, "goal": 30, "progress": 0},
        ]
        self.c._select_best_quest = Mock(side_effect=AssertionError("Quest target already met"))
        self.assertEqual(self.c._quest_deck_target(), ("", "wins.png"))

    def test_default_mode_plays_quest_deck_when_quest_active(self):
        self.c._preferred_historic_deck = "mono_red.png"
        self.c._only_grind_preferred_deck = False
        self.c._select_best_quest = lambda: {"type": "guild", "guild": "Boros", "gold": 500}
        self.c._last_valid_quest_active_incomplete = 1

        colors, forced = self.c._quest_deck_target()
        self.assertEqual(colors, "RW")
        self.assertIsNone(forced)

    def test_default_mode_switches_to_preferred_deck_when_quests_complete(self):
        self.c._preferred_historic_deck = "mono_red.png"
        self.c._only_grind_preferred_deck = False
        self.c._last_valid_quest_active_incomplete = 0
        self.c._quest_count_confirmed_fresh = True

        colors, forced = self.c._quest_deck_target()
        self.assertEqual(colors, "")
        self.assertEqual(forced, "mono_red.png")

    def test_default_mode_switches_to_preferred_color_deck_when_quests_complete(self):
        self.c._preferred_historic_deck = "R"
        self.c._only_grind_preferred_deck = False
        self.c._last_valid_quest_active_incomplete = 0
        self.c._quest_count_confirmed_fresh = True

        colors, forced = self.c._quest_deck_target()
        self.assertEqual(colors, "R")
        self.assertIsNone(forced)

    def test_only_grind_mode_overrides_active_quests_with_forced_file(self):
        self.c._preferred_historic_deck = "my_deck.png"
        self.c._only_grind_preferred_deck = True
        self.c._select_best_quest = lambda: {"type": "guild", "guild": "Dimir", "gold": 750}

        colors, forced = self.c._quest_deck_target()
        self.assertEqual(forced, "my_deck.png")
        self.assertEqual(colors, "")

    def test_only_grind_mode_overrides_active_quests_with_colors(self):
        self.c._preferred_historic_deck = "UB"
        self.c._only_grind_preferred_deck = True
        self.c._select_best_quest = lambda: {"type": "guild", "guild": "Boros", "gold": 500}

        colors, forced = self.c._quest_deck_target()
        self.assertEqual(colors, "UB")
        self.assertIsNone(forced)

    def test_stem_matches_forced_filename(self):
        self.assertTrue(
            Controller._deck_image_matches_target("/path/to/mono_red.png", "", "mono_red")
        )
        self.assertTrue(
            Controller._deck_image_matches_target("/path/to/mono_red.png", "", "mono_red.png")
        )


class StarterDeckChooserSkipTests(_ThrowawayControllerBase):
    def test_swap_skips_chooser_when_deck_already_selected(self):
        self.c._starter_selected_deck = ("player#11111", "RG")
        self.c._starter_navigation_may_act = lambda: True
        self.c._choose_starter_deck_template = lambda colors: "assets/assert/starter_decks/RG.PNG"
        self.c._detect_starter_screen = lambda label: Controller._STARTER_SCREEN_PLAY
        self.c._open_starter_deck_chooser = Mock()

        self.c._swap_starter_deck_for_quest("RG")
        self.c._open_starter_deck_chooser.assert_not_called()

    def test_swap_opens_chooser_when_deck_cached_but_not_on_play_screen(self):
        self.c._starter_selected_deck = ("player#11111", "RG")
        self.c._starter_event_page_confirmed.return_value = False
        self.c._starter_navigation_may_act = lambda: True
        self.c._choose_starter_deck_template = lambda colors: "assets/assert/starter_decks/RG.PNG"
        self.c._detect_starter_screen = lambda label: Controller._STARTER_SCREEN_START
        self.c._open_starter_deck_chooser = Mock(return_value=False)

        self.c._swap_starter_deck_for_quest("RG")
        self.c._open_starter_deck_chooser.assert_called_once()
        self.assertIsNone(self.c._starter_selected_deck)

    def test_swap_opens_chooser_when_different_deck_selected(self):
        self.c._starter_selected_deck = ("player#11111", "WB")
        self.c._starter_navigation_may_act = lambda: True
        self.c._choose_starter_deck_template = lambda colors: "assets/assert/starter_decks/RG.PNG"
        self.c._open_starter_deck_chooser = Mock(return_value=False)

        self.c._swap_starter_deck_for_quest("RG")
        self.c._open_starter_deck_chooser.assert_called_once()

    def test_submitting_deck_records_selection(self):
        self.c._starter_selected_deck = None
        self.c._starter_navigation_may_act = lambda: True
        self.c._choose_starter_deck_template = lambda colors: "assets/assert/starter_decks/RG.PNG"
        self.c._open_starter_deck_chooser = Mock(return_value=True)
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._map_abs_point_to_arena = lambda p, **kw: (p, "exact")
        self.c._detect_starter_screen = lambda label: Controller._STARTER_SCREEN_PLAY

        self.c._swap_starter_deck_for_quest("RG")
        self.assertEqual(self.c._starter_selected_deck, ("player#11111", "RG"))

    def test_forget_starter_deck_clears_selection(self):
        self.c._starter_selected_deck = ("player#11111", "RG")
        self.c._forget_starter_deck("account logout/switch")
        self.assertIsNone(self.c._starter_selected_deck)


class PostMatchHomeDipBypassTests(_ThrowawayControllerBase):
    def test_has_active_incomplete_quests_false_when_only_grind_enabled(self):
        self.c._only_grind_preferred_deck = True
        self.c._last_valid_quest_active_incomplete = 2
        self.assertFalse(self.c._has_active_incomplete_daily_quests())

    def test_has_active_incomplete_quests_true_when_only_grind_and_quest_switching_enabled(self):
        self.c._only_grind_preferred_deck = True
        self.c._account_switch_enabled = True
        self.c._account_switch_mode = "quests"
        self.c._account_switch_main_quests = 1
        self.c._last_valid_quest_active_incomplete = 1
        self.assertTrue(self.c._has_active_incomplete_daily_quests())

    def test_has_active_incomplete_quests_false_when_quests_complete(self):
        self.c._only_grind_preferred_deck = False
        self.c._last_valid_quest_active_incomplete = 0
        self.c._quest_count_confirmed_fresh = True
        self.assertFalse(self.c._has_active_incomplete_daily_quests())

    def test_has_active_incomplete_quests_true_when_incomplete_exist(self):
        self.c._only_grind_preferred_deck = False
        self.c._last_valid_quest_active_incomplete = 1
        self.assertTrue(self.c._has_active_incomplete_daily_quests())

    def test_start_game_does_not_dip_home_when_no_active_incomplete_quests(self):
        self.c._last_valid_quest_active_incomplete = 0
        self.c._quest_count_confirmed_fresh = True
        self.c._matches_since_quest_refresh = 5
        self.c._quest_refresh_every_n_matches = 1
        self.c._game_mode = "starter"

        self.c._refresh_quests_from_home = Mock()
        self.c._navigate_starter_deck = Mock()
        self.c.reroll_quest_on_landing = lambda: True

        self.c.start_game_from_home_screen()
        self.c._refresh_quests_from_home.assert_not_called()
        self.c._navigate_starter_deck.assert_called_once()

    def test_starter_nav_uses_fast_path_when_on_event_landing_page(self):
        self.c._foreign_event_landing_name = lambda: None
        self.c._on_starter_event_landing_page = Mock(return_value=True)
        self.c._queue_from_event_landing = Mock(return_value=True)
        self.c._resolve_starter_target_colors = lambda: "WB"
        self.c._dismiss_match_end_screen = lambda: False
        self.c._home_screen_confirmed = Mock()

        result = self.c._navigate_starter_deck()
        self.assertTrue(result)
        self.c._queue_from_event_landing.assert_called_once()
        self.c._home_screen_confirmed.assert_not_called()


class ConfigManagerDeckPreferencesTests(unittest.TestCase):
    def test_enhancement_settings_preserve_existing_controls_with_free_forge(self):
        window = types.SimpleNamespace(_config_manager=Mock())
        window._config_manager.get_free_forge_enabled.return_value = True
        notifications = Mock()
        notifications.get_config.return_value = {}
        low_power = Mock(enabled=True)

        def variable(*, value):
            return Mock(get=Mock(return_value=value))

        with patch("ui.tk.StringVar", side_effect=variable), \
             patch("ui.tk.BooleanVar", side_effect=variable), \
             patch("notifications.NotificationManager.get_instance", return_value=notifications), \
             patch("low_power.LowPowerManager.get_instance", return_value=low_power), \
             patch.dict(os.environ, {"MTGA_SELECTIVE_ATTACK": "0", "MTGA_FORGE_FREE": "off"}):
            ui.EnhancementsWindow._init_state(window)
            ui.EnhancementsWindow._save_config_now(window)

        saved = notifications.save_config.call_args.args[0]
        self.assertTrue(saved["free_forge"])
        self.assertTrue(saved["low_power_mode"])
        self.assertFalse(saved["selective_attack"])
        for method in ("_toggle_free_forge", "_toggle_low_power", "_toggle_selective_attack", "_open_session_stats"):
            self.assertTrue(callable(getattr(ui.EnhancementsWindow, method)))

    def test_defaults(self):
        with tempfile.TemporaryDirectory(prefix="deck-cfg-") as directory:
            cfg_path = pathlib.Path(directory) / "calibration_config.json"
            cfg = ui.ConfigManager(config_path=str(cfg_path))
            self.assertEqual(cfg.get_preferred_starter_deck(), "")
            self.assertEqual(cfg.get_preferred_historic_deck(), "")
            self.assertFalse(cfg.get_only_grind_preferred_deck())

    def test_set_and_reload(self):
        with tempfile.TemporaryDirectory(prefix="deck-cfg-") as directory:
            cfg_path = pathlib.Path(directory) / "calibration_config.json"
            cfg1 = ui.ConfigManager(config_path=str(cfg_path))
            cfg1.set_preferred_starter_deck("RG")
            cfg1.set_preferred_historic_deck("mono_red.png")
            cfg1.set_only_grind_preferred_deck(True)

            cfg2 = ui.ConfigManager(config_path=str(cfg_path))
            self.assertEqual(cfg2.get_preferred_starter_deck(), "RG")
            self.assertEqual(cfg2.get_preferred_historic_deck(), "mono_red.png")
            self.assertTrue(cfg2.get_only_grind_preferred_deck())

    def test_run_bot_deck_preferences_loader(self):
        with tempfile.TemporaryDirectory(prefix="cli-deck-cfg-") as directory:
            path = pathlib.Path(directory) / "calibration_config.json"
            path.write_text(
                json.dumps({
                    "preferred_starter_deck": "WB",
                    "preferred_historic_deck": "R",
                    "only_grind_preferred_deck": True,
                }),
                encoding="utf-8",
            )
            starter, historic, only_grind = run_bot._load_runtime_deck_preferences(path)
            self.assertEqual(starter, "WB")
            self.assertEqual(historic, "R")
            self.assertTrue(only_grind)


if __name__ == "__main__":
    unittest.main()
