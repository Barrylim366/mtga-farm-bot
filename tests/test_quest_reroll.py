"""Quest rerolls: isolated log fixtures, fake input, and no desktop access."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from Controller.MTGAController.Controller import Controller
from Controller.MTGAController.quest_reroll import is_eligible, replacement_verified
from state.state_machine import BotState
from vision.vision import cv2


def quest(qid="old", gold=500, goal=20, progress=0, guild="Simic_Manipulator"):
    return {"questId": qid, "locKey": "Quests/Quest_" + guild,
            "goal": goal, "endingProgress": progress,
            "chestDescription": {"locParams": {"number1": gold}}}


class RerollCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "Player.log"
        self.path.touch()
        self.c = Controller(str(self.path), input_backend="null")
        c = self.c
        # Every route that could reach a real monitor/mouse is replaced here.
        c.input = Mock()
        c._vision = Mock()
        c._ensure_arena_region = Mock(return_value=(0, 0, 1920, 1080))
        c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        c._click_image_in_scaled_arena_region = Mock(return_value=False)
        c._click_abs = Mock()
        c._navigate_to_home = Mock(return_value=True)
        c._get_state_from_log = Mock(return_value=BotState.HOME)
        c._quest_reroll_home_visible = Mock(return_value=True)
        c._quest_reroll_templates_ready = Mock(return_value=True)
        c._find_500_gold_quest_tile = Mock(return_value=(825, 905))
        c._quest_reroll_dialog_visible = Mock(return_value=True)
        c._quest_reroll_confirm_point = Mock(return_value=(1118, 624))
        c._close_quest_reroll_dialog = Mock(return_value=True)
        c._freshen_quest_reroll_snapshot = Mock(return_value=None)
        c._refresh_identity_from_login = Mock()
        c._latch_account_screen_name_from = Mock()
        c._update_gold_from_inventory = Mock()
        c._publish_account_switch_status = Mock()
        c._QUEST_REROLL_TIMEOUT = 0.04
        self.sleep = patch("Controller.MTGAController.quest_reroll.time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        status = patch("Controller.MTGAController.Controller.runtime_status.update_status")
        status.start()
        self.addCleanup(status.stop)

    def append(self, quests=None, **fields):
        payload = {"quests": [quest()] if quests is None else quests, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write("<== QuestGetQuests " + json.dumps(payload) + "\n")
        return payload

    def tags(self):
        return [call.args[2] for call in self.c._click_abs.call_args_list]


class SnapshotTests(RerollCase):
    def test_boolean_availability_and_compatibility(self):
        for value in (True, False, None, "true", 1, {}, []):
            with self.subTest(value=value):
                payload = self.append(canSwap=value)
                result = self.c._extract_latest_quest_snapshot()
                self.assertEqual(result["canSwap"], value if type(value) is bool else None)
                self.assertEqual(self.c._extract_latest_quests(), payload["quests"])
        self.append()
        self.assertFalse(self.c._extract_latest_quest_snapshot()["canSwap"])

    def test_malformed_list_or_record_is_rejected(self):
        for value in ("bad", [None], [1], [{} , "bad"]):
            self.append(value, canSwap=True)
            self.assertIsNone(self.c._extract_latest_quest_snapshot())

    def test_fresh_floor_excludes_previous_account_and_pre_start_data(self):
        self.append(canSwap=True)
        self.c._arm_quest_reroll()
        self.assertIsNone(self.c._extract_latest_quest_snapshot(min_offset=self.c._quest_reroll_floor))
        self.append([quest("new", 750)], canSwap=False)
        self.assertFalse(self.c._extract_latest_quest_snapshot(min_offset=self.c._quest_reroll_floor)["canSwap"])

    def test_truncation_fails_closed_for_reroll(self):
        self.append(canSwap=True)
        self.c._arm_quest_reroll()
        self.path.write_text("", encoding="utf-8")
        self.append([], canSwap=True)
        self.assertIsNone(self.c._extract_latest_quest_snapshot(min_offset=self.c._quest_reroll_floor))

    def test_log_rotation_drops_stale_ordinary_reroll_floor(self):
        self.c._quest_reroll_data_floor = 10_000
        self.append([quest("new")], canSwap=True)

        snapshot = self.c._extract_latest_quest_snapshot()

        self.assertIsNotNone(snapshot)
        self.assertIsNone(self.c._quest_reroll_data_floor)
        self.assertEqual(snapshot["quests"][0]["questId"], "new")


class EligibilityTests(unittest.TestCase):
    def test_only_known_incomplete_500_gold_quests(self):
        self.assertTrue(is_eligible(quest()))
        self.assertTrue(is_eligible(quest(progress=19)))
        for q in (quest(gold=750), quest(gold=250), quest(progress=20),
                  quest(goal=0), quest(goal=None), quest(progress=-1), {},
                  quest(gold="not gold")):
            self.assertFalse(is_eligible(q), q)

    def test_verified_replacement_is_not_quest_completion(self):
        before = {"quests": [quest(), quest("keep", 750)], "canSwap": True}
        for gold in (500, 750):
            after = {"quests": [quest("replacement", gold), quest("keep", 750)], "canSwap": False}
            self.assertTrue(replacement_verified(before, after))
        for after in (None, {**before, "canSwap": False},
                      {"quests": [quest("keep", 750)], "canSwap": False},
                      {"quests": [quest("new")], "canSwap": True}):
            self.assertFalse(replacement_verified(before, after))
        # Reordering is not replacement, and losing the 750 quest is not ours.
        self.assertFalse(replacement_verified(before, {"quests": [quest("keep", 750), quest()], "canSwap": False}))
        self.assertFalse(replacement_verified(before, {"quests": [quest(), quest("new")], "canSwap": False}))


class LandingTests(RerollCase):
    def test_skip_reasons_do_not_touch_home_or_mouse(self):
        for quests, swap in (([], True), ([quest(gold=750)], True),
                             ([quest(progress=20)], True), ([quest()], False), ([quest()], None)):
            self.c._arm_quest_reroll()
            self.append(quests, canSwap=swap)
            self.assertTrue(self.c.reroll_quest_on_landing())
        self.c._arm_quest_reroll()  # Now the last response is stale.
        self.assertTrue(self.c.reroll_quest_on_landing())
        self.c._click_abs.assert_not_called()
        self.c._navigate_to_home.assert_not_called()

    def test_replacement_updates_deck_and_preserves_counts_for_both_rewards(self):
        for gold in (500, 750):
            with self.subTest(gold=gold):
                c = self.c
                c._quest_reroll_dialog_open = False
                c._arm_quest_reroll()
                self.append(canSwap=True)
                c.refresh_quests_cache()
                self.assertEqual(c._cached_active_colors, "UG")
                c._credited_quest_ids = {"completed-earlier"}
                c._gold_farmed_by_account = {"account": 250}
                def click(x, y, tag):
                    if tag == "QUEST_REROLL_CONFIRM":
                        self.append([quest("replacement", gold, guild="Golgari_Guildmage")], canSwap=False)
                c._click_abs.side_effect = click
                self.assertTrue(c.reroll_quest_on_landing())
                self.assertEqual(c._cached_active_colors, "BG")
                self.assertEqual(c._cached_active_quest_id, "replacement")
                self.assertEqual(c._last_valid_quest_active_incomplete, 1)
                self.assertEqual(c._credited_quest_ids, {"completed-earlier"})
                self.assertEqual(c._gold_farmed_by_account, {"account": 250})
                self.assertIsNone(c._quest_reroll_unverified_before)

    def test_timeout_no_duplicate_and_late_response_refresh(self):
        c = self.c
        self.append(canSwap=True)
        # A transient incomplete response during the swap isn't completion.
        c._click_abs.side_effect = lambda x, y, tag: self.append([], canSwap=False) if tag == "QUEST_REROLL_CONFIRM" else None
        self.assertTrue(c.reroll_quest_on_landing())
        c._quest_reroll_dialog_open = False  # Stubbed cleanup has closed it.
        for _ in range(3):
            c.reroll_quest_on_landing()
            c.refresh_quests_cache()
        self.assertEqual(self.tags().count("QUEST_REROLL_CONFIRM"), 1)
        self.assertEqual(c._cached_active_colors, "")
        c.refresh_quests_cache()
        self.assertFalse(c._quest_count_confirmed_fresh)
        self.append([quest("new", 750)], canSwap=False)
        c.refresh_quests_cache()
        self.assertEqual(c._cached_active_quest_id, "new")

    def test_failed_confirmation_does_not_freeze_future_quest_completion(self):
        c = self.c
        self.append(canSwap=True)
        # The click failed: Arena returns the same still-rerollable quest list.
        c._click_abs.side_effect = lambda x, y, tag: self.append(canSwap=True) if tag == "QUEST_REROLL_CONFIRM" else None
        c.reroll_quest_on_landing()
        self.assertEqual(c._cached_active_quest_id, "old")
        self.assertIsNone(c._quest_reroll_unverified_before)
        self.append([], canSwap=True)  # Completed during the subsequent match.
        c.refresh_quests_cache()
        self.assertEqual(c._last_valid_quest_active_incomplete, 0)
        self.assertTrue(c._quest_count_confirmed_fresh)

    def test_live_shape_omitted_false_flag_and_no_response_until_home_reentry(self):
        c = self.c
        self.append(canSwap=True)
        c._QUEST_REROLL_TIMEOUT = 10
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        def freshen(**kwargs):
            # Measured live: no swap response, then QuestGetQuests only on a
            # Home re-entry, with false canSwap and zero progress OMITTED.
            new = quest("new", 750)
            del new["endingProgress"]
            self.append([new])
            return c._extract_latest_quest_snapshot(min_offset=c._quest_reroll_floor)
        c._freshen_quest_reroll_snapshot.side_effect = freshen
        with patch("Controller.MTGAController.quest_reroll.time.monotonic", side_effect=lambda: now[0]), \
                patch("Controller.MTGAController.quest_reroll.time.sleep", side_effect=sleep):
            self.assertTrue(c.reroll_quest_on_landing())
        self.assertEqual(self.tags().count("QUEST_REROLL_CONFIRM"), 1)
        c._freshen_quest_reroll_snapshot.assert_called_once_with(deadline=10)
        self.assertEqual(c._cached_active_quest_id, "new")
        self.assertEqual(c._last_valid_quest_active_incomplete, 1)
        self.assertIsNone(c._quest_reroll_unverified_before)

    def test_profile_reentry_waits_for_loading_and_does_not_relax_floor(self):
        c = self.c
        self.append(canSwap=True)
        c._arm_quest_reroll()
        floor = c._quest_reroll_floor
        c._reroll_match = Mock(return_value=(215, 40))
        c._navigate_to_home.side_effect = [False, True]
        def click(x, y, tag):
            self.append(canSwap=False)
        c._click_abs.side_effect = click
        result = Controller._freshen_quest_reroll_snapshot(c)
        self.assertFalse(result["canSwap"])
        self.assertEqual(c._quest_reroll_floor, floor)
        self.assertEqual(c._navigate_to_home.call_count, 2)
        self.assertEqual(self.tags(), ["QUEST_REROLL_REFRESH_PROFILE"])

    def test_failed_recognition_and_unavailable_confirmation(self):
        for attr, value, expected, may_proceed in (
            ("_quest_reroll_templates_ready", False, [], True),
            ("_quest_reroll_dialog_visible", False, ["QUEST_REROLL_OPEN"], True),
            ("_quest_reroll_confirm_point", None, ["QUEST_REROLL_OPEN"], True),
        ):
            with self.subTest(attr=attr):
                self.c._quest_reroll_dialog_open = False
                self.c._arm_quest_reroll()
                self.c._click_abs.reset_mock()
                self.append(canSwap=True)
                with patch.object(self.c, attr, return_value=value):
                    result = self.c.reroll_quest_on_landing()
                self.assertEqual(self.tags(), expected)
                self.assertEqual(result, may_proceed)

    def test_unrecognized_tile_continues_historic_deck_selection_after_home_recheck(self):
        """A visual miss must not skip the post-login Historic deck path on Home."""
        c = self.c
        c._arm_quest_reroll()
        self.append(canSwap=True)
        c._find_500_gold_quest_tile.return_value = None
        c._run_post_login_navigation_oob = Mock(return_value=True)
        c._select_best_quest = Mock(return_value=None)
        c._choose_deck_image = Mock(return_value="historic-deck.png")
        c._click_image = Mock(return_value=True)

        self.assertTrue(c._run_post_login_routine({"name": "incoming"}, []))

        self.assertEqual(c._quest_reroll_home_visible.call_count, 2)
        c._run_post_login_navigation_oob.assert_called_once()
        c._choose_deck_image.assert_called_once_with({"name": "incoming"}, "", None)
        self.assertEqual(
            [call.args[1] for call in c._click_image.call_args_list],
            ["POST_LOGIN_DECK", "POST_LOGIN_PLAY_CONFIRM"],
        )

    def test_unrecognized_tile_keeps_reroll_pending_until_home_is_rechecked(self):
        c = self.c
        c._arm_quest_reroll()
        self.append(canSwap=True)
        c._find_500_gold_quest_tile.return_value = None
        c._quest_reroll_home_visible.side_effect = [True, False, True, True]

        self.assertFalse(c.reroll_quest_on_landing())
        self.assertTrue(c._quest_reroll_pending)
        self.assertTrue(c.reroll_quest_on_landing())
        self.assertFalse(c._quest_reroll_pending)

    def test_dialog_cleanup_exception_keeps_flag_and_next_landing_resumes(self):
        c = self.c
        c._quest_reroll_dialog_open = True
        c._quest_reroll_pending = False

        attempts = []

        def close(*_args):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("temporary vision error")
            c._quest_reroll_dialog_open = False
            return True

        c._close_quest_reroll_dialog.side_effect = close
        self.assertFalse(c.reroll_quest_on_landing())
        self.assertTrue(c._quest_reroll_dialog_open)

        c._run_post_login_navigation_oob = Mock(return_value=True)
        c._select_best_quest = Mock(return_value=None)
        c._choose_deck_image = Mock(return_value="historic-deck.png")
        c._click_image = Mock(return_value=True)
        self.assertTrue(c._run_post_login_routine({"name": "incoming"}, []))
        self.assertFalse(c._quest_reroll_dialog_open)
        self.assertEqual(c._close_quest_reroll_dialog.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in c._click_image.call_args_list],
            ["POST_LOGIN_DECK", "POST_LOGIN_PLAY_CONFIRM"],
        )

    def test_stop_before_confirmation_never_submits(self):
        self.append(canSwap=True)
        self.c._click_abs.side_effect = lambda *a: setattr(self.c, "_stop_requested", True)
        self.c.reroll_quest_on_landing()
        self.assertEqual(self.tags(), ["QUEST_REROLL_OPEN"])

    def test_stopped_or_mid_match_check_stays_pending(self):
        for state in (BotState.IN_GAME, BotState.FIND_MATCH):
            self.c._get_state_from_log.return_value = state
            self.assertFalse(self.c.reroll_quest_on_landing())
            self.assertTrue(self.c._quest_reroll_pending)
        self.c._get_state_from_log.return_value = BotState.HOME
        self.append(canSwap=False)
        self.assertTrue(self.c.reroll_quest_on_landing())
        self.assertFalse(self.c._quest_reroll_pending)
        self.c._click_abs.assert_not_called()

    def test_other_switch_owner_defers(self):
        self.c._account_switch_in_progress = True
        self.c._switch_owner_ident = threading.get_ident() + 1
        self.assertFalse(self.c.reroll_quest_on_landing())
        self.assertTrue(self.c._quest_reroll_pending)

    def test_ordinary_cache_refresh_does_not_reroll(self):
        self.append(canSwap=True)
        self.c.refresh_quests_cache()
        self.c.refresh_quests_cache()
        self.c._click_abs.assert_not_called()
        self.assertTrue(self.c._quest_reroll_pending)

    def test_account_login_rearms_and_rejects_outgoing_response(self):
        self.append(canSwap=False)
        self.c.reroll_quest_on_landing()
        self.c._reset_state_for_incoming_account()
        self.assertTrue(self.c._quest_reroll_pending)
        self.assertIsNone(self.c._extract_latest_quest_snapshot(min_offset=self.c._quest_reroll_floor))

    def test_unresolved_dialog_blocks_queue_and_post_login_selection(self):
        c = self.c
        c._quest_reroll_dialog_open = True
        c._close_quest_reroll_dialog.return_value = False
        c._navigate_starter_deck = Mock()
        c._select_best_quest = Mock()
        c.start_game_from_home_screen()
        self.assertFalse(c._run_post_login_routine({}, []))
        c._navigate_starter_deck.assert_not_called()
        c._select_best_quest.assert_not_called()
        c._click_abs.assert_not_called()

    def test_recoverable_reroll_failure_still_selects_a_deck(self):
        # The post-login routine runs once per login/switch, so a self-healing
        # reroll failure must not cost the account its deck selection.
        c = self.c
        c._game_mode = "starter"
        c._run_starter_deck_routine = Mock(return_value=True)
        c.reroll_quest_on_landing = Mock(return_value=False)
        self.assertTrue(c._run_post_login_routine({}, []))
        c._run_starter_deck_routine.assert_called_once()

    def test_post_login_skips_deck_and_queue_when_switch_is_due(self):
        c = self.c
        c._game_mode = "starter"
        c.reroll_quest_on_landing = Mock(return_value=True)
        c._account_switch_due = Mock(return_value=True)
        c._run_starter_deck_routine = Mock(return_value=True)
        self.assertTrue(c._run_post_login_routine({}, []))
        c._run_starter_deck_routine.assert_not_called()

    def test_match_or_stop_still_skips_deck_selection(self):
        c = self.c
        c._game_mode = "starter"
        c._run_starter_deck_routine = Mock(return_value=True)
        c.reroll_quest_on_landing = Mock(return_value=False)
        c._get_state_from_log.return_value = BotState.IN_GAME
        self.assertFalse(c._run_post_login_routine({}, []))
        c._run_starter_deck_routine.assert_not_called()

    def test_raising_screen_probe_cannot_kill_the_queue_thread(self):
        c = self.c
        c._get_state_from_log.side_effect = OSError("screen gone")
        self.assertTrue(c.reroll_quest_on_landing())
        c._quest_reroll_dialog_open = True
        self.assertFalse(c.reroll_quest_on_landing())
        c._get_state_from_log.side_effect = None
        c._close_quest_reroll_dialog.side_effect = OSError("capture failed")
        self.assertFalse(c.reroll_quest_on_landing())
        self.assertTrue(c._quest_reroll_dialog_open)

    def test_post_login_check_precedes_deck_selection(self):
        c = self.c
        order = []
        c.reroll_quest_on_landing = Mock(side_effect=lambda: order.append("reroll") or True)
        c._game_mode = "starter"
        c._run_starter_deck_routine = Mock(side_effect=lambda: order.append("deck") or True)
        c._run_post_login_routine({}, [])
        self.assertEqual(order, ["reroll", "deck"])

    def test_deferred_check_runs_before_first_home_queue(self):
        c = self.c
        c._get_state_from_log.return_value = BotState.IN_GAME
        c.start_game_from_home_screen()
        self.assertTrue(c._quest_reroll_pending)
        c._get_state_from_log.return_value = BotState.HOME
        self.append(canSwap=False)
        c._game_mode = "starter"
        pending_at_queue = []
        c._navigate_starter_deck = Mock(side_effect=lambda: pending_at_queue.append(c._quest_reroll_pending))
        c.start_game_from_home_screen()
        self.assertEqual(pending_at_queue, [False])

    def test_switch_requests_do_not_queue_duplicate_logouts_on_navigation_lock(self):
        c = self.c
        started, finish = threading.Event(), threading.Event()
        def owned():
            started.set()
            finish.wait(2)
        c._perform_owned_account_switch = Mock(side_effect=owned)
        first = threading.Thread(target=c._perform_account_switch)
        first.start()
        try:
            self.assertTrue(started.wait(1))
            # A second request must return immediately, not wait on the UI lock
            # and then log the newly arrived account straight back out.
            second = threading.Thread(target=c._perform_account_switch)
            second.start()
            second.join(1)
            self.assertFalse(second.is_alive())
            self.assertEqual(c._perform_owned_account_switch.call_count, 1)
        finally:
            finish.set()
            first.join(2)
        self.assertFalse(c._account_switch_in_progress)


class VisionGuardTests(RerollCase):
    def test_leftmost_visual_tile_not_log_order(self):
        c = self.c
        c._reroll_match = Mock(side_effect=[None, (540, 900), (830, 900)])
        self.assertEqual(Controller._find_500_gold_quest_tile(c), (540, 900))
        self.assertEqual([call.args[1][0] for call in c._reroll_match.call_args_list], [150, 450])
        self.assertTrue(all(call.kwargs["confidence"] == 0.82
                            for call in c._reroll_match.call_args_list))

    @unittest.skipIf(cv2 is None, "OpenCV not installed")
    def test_dimmed_control_is_rejected_despite_template_match(self):
        c = self.c
        path = c._app_path("assets", "assert", "quest_reroll", "confirm.png")
        template = cv2.imread(path)
        self.assertIsNotNone(template, "real Arena template must be shipped")
        h, w = template.shape[:2]
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        x, y = 1000, 600
        c._locate_image_center_in_scaled_arena_region.return_value = (x+w//2, y+h//2)
        c._vision.capture.return_value = frame
        frame[y:y+h, x:x+w] = template
        self.assertIsNotNone(c._reroll_match("confirm", (960, 570, 340, 110)))
        frame[y:y+h, x:x+w] = (template * 0.25).astype(np.uint8)
        self.assertIsNone(c._reroll_match("confirm", (960, 570, 340, 110)))

    def test_cleanup_only_cancels_recognized_dialog_and_verifies_home(self):
        c = self.c
        c._quest_reroll_dialog_open = True
        c._quest_reroll_dialog_visible.side_effect = [True, False]
        c._reroll_dialog_base_point = Mock(return_value=(800, 624))
        self.assertTrue(Controller._close_quest_reroll_dialog(c))
        self.assertEqual(self.tags(), ["QUEST_REROLL_CANCEL"])
        self.assertFalse(c._quest_reroll_dialog_open)

    def test_confirm_and_cancel_use_fixed_measured_points_after_dialog_check(self):
        c = self.c
        c._get_ui_action_arena_region = Mock(return_value=(100, 200, 1920, 1080))
        self.assertEqual(Controller._quest_reroll_confirm_point(c), (1220, 825))
        c._quest_reroll_dialog_open = True
        c._quest_reroll_dialog_visible.side_effect = [True, False]
        c._quest_reroll_home_visible.return_value = True
        self.assertTrue(Controller._close_quest_reroll_dialog(c))
        self.assertEqual(self.tags(), ["QUEST_REROLL_CANCEL"])


class GameStartupTests(unittest.TestCase):
    def test_startup_orders_prime_reroll_then_queue_and_respects_stop(self):
        from Game import Game
        for stop in (False, True):
            with self.subTest(stop=stop):
                c = Mock()
                c._stop_requested = False
                c._quest_reroll_dialog_open = False
                order = []
                c.prime_quests_for_new_session.side_effect = lambda: order.append("prime")
                def reroll():
                    order.append("reroll")
                    c._stop_requested = stop
                    return not stop
                c.reroll_quest_on_landing.side_effect = reroll
                c.start_game.side_effect = lambda: order.append("queue")
                g = Game(c, Mock())
                g._refresh_card_data = Mock()
                g._debug = Mock()
                with patch("Game.debug_recorder.start_session"), \
                        patch("Game.click_recorder.start_session"), \
                        patch("Game.runtime_status.set_mode"), \
                        patch("Game.CardInfo.warm_up_starter_data"), \
                        patch("Game.CardInfo.refresh_missing_cards"):
                    g.start()
                self.assertEqual(order, ["prime", "reroll"] + ([] if stop else ["queue"]))

    def test_unresolved_reroll_does_not_start_queueing(self):
        from Game import Game
        c = Mock()
        c._stop_requested = False
        c._quest_reroll_dialog_open = True
        c.reroll_quest_on_landing.return_value = False
        g = Game(c, Mock())
        g._refresh_card_data = Mock()
        g._debug = Mock()
        with patch("Game.debug_recorder.start_session"), \
                patch("Game.click_recorder.start_session"), \
                patch("Game.runtime_status.set_mode"), \
                patch("Game.runtime_status.set_startup_phase"), \
                patch("Game.CardInfo.warm_up_starter_data"), \
                patch("Game.CardInfo.refresh_missing_cards"):
            g.start()
        c.start_game.assert_not_called()

    def test_deferred_reroll_allows_startup_to_continue(self):
        from Game import Game
        c = Mock()
        c._stop_requested = False
        c._quest_reroll_dialog_open = False
        c.reroll_quest_on_landing.return_value = False
        g = Game(c, Mock())
        g._refresh_card_data = Mock()
        g._debug = Mock()
        with patch("Game.debug_recorder.start_session"), \
                patch("Game.click_recorder.start_session"), \
                patch("Game.runtime_status.set_mode"), \
                patch("Game.runtime_status.set_startup_phase"), \
                patch("Game.CardInfo.warm_up_starter_data"), \
                patch("Game.CardInfo.refresh_missing_cards"):
            g.start()
        c.start_game.assert_called_once()


if __name__ == "__main__":
    unittest.main()
