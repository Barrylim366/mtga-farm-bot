"""Single-card graveyard choices must be answered while the spell resolves."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState

module = importlib.import_module("Controller.MTGAController.Controller")


class GraveyardSelectNTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        log_path = os.path.join(self.directory.name, "Player.log")
        with open(log_path, "w", encoding="utf-8"):
            pass
        self.c = Controller(log_path)
        self.c._Controller__system_seat_id = 1
        self.c._Controller__live_match_id = "match-1"
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = Mock(return_value=False)
        self.c.can_execute_game_action = lambda match_id: (
            not self.c._stop_requested and match_id == self.c._Controller__live_match_id
        )
        self.c._Controller__record_decision = Mock()
        self.c._Controller__clear_target_wait_if_unblocked = Mock()
        self.c._Controller__write_target_debug_bundle = Mock()
        self.c.select_chooser_card = Mock(return_value=True)
        self.c._Controller__click_card_prompt_submit = Mock()
        self.state = {
            "gameStateId": 100, "pendingMessageCount": 1,
            "turnInfo": {"decisionPlayer": 1, "priorityPlayer": 1},
            "gameObjects": [
                {"instanceId": 10, "grpId": 93784, "zoneId": 33,
                 "cardTypes": ["CardType_Instant"]},
                {"instanceId": 11, "grpId": 12345, "zoneId": 33,
                 "cardTypes": ["CardType_Sorcery"]},
            ],
            "zones": [
                {"zoneId": 33, "type": "ZoneType_Graveyard", "ownerSeatId": 1,
                 "objectInstanceIds": [10, 11]},
                {"zoneId": 27, "type": "ZoneType_Stack", "objectInstanceIds": [99]},
                {"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": 1,
                 "objectInstanceIds": []},
            ],
        }
        self.c.updated_game_state = GameState(self.state)
        self.req = {"ids": [10, 11], "sourceId": 99, "minSel": 1, "maxSel": 1,
                    "context": "SelectionContext_Resolution", "idType": "IdType_InstanceId"}
        self.now = 1000.0
        self.tasks = []
        test = self

        class Timer:
            def __init__(self, delay, callback, args=()):
                self.delay, self.callback, self.args = delay, callback, args
            def start(self):
                test.tasks.append(self)

        for context in (
            patch.object(module.threading, "Timer", Timer),
            patch.object(module.time, "time", lambda: self.now),
            patch.object(module.CardInfo, "get_card_info_local", side_effect=lambda gid: {
                "manaCost": "{B}" if gid == 93784 else "{3}{U}"
            }),
        ):
            context.start()
            self.addCleanup(context.stop)

    def request(self, **message_fields):
        message = {"type": "GREMessageType_SelectNReq", "systemSeatIds": [1],
                   "selectNReq": self.req, **message_fields}
        self.c._Controller__handle_select_n_req(json.dumps({
            "greToClientEvent": {"greToClientMessages": [message]}
        }))

    def tick(self):
        timer = self.tasks.pop(0)
        self.now += timer.delay
        timer.callback(*timer.args)

    def test_resolution_choice_routes_to_chooser_and_ranks_without_card_id_special_case(self):
        self.request()
        self.tick()
        self.c.select_chooser_card.assert_called_once_with(11, clicks=1)
        self.tick()
        self.c._Controller__click_card_prompt_submit.assert_called_once()
        self.assertTrue(self.c._Controller__should_pause_for_select_n())
        self.state["gameStateId"] += 1
        self.state["zones"][0]["objectInstanceIds"].remove(11)
        self.tick()
        self.assertIsNone(self.c._Controller__pending_select_n)

    def test_duplicate_requests_do_not_deselect_or_start_another_worker(self):
        self.request()
        self.request()
        self.assertEqual(len(self.tasks), 1)
        self.tick()
        self.request()
        self.assertEqual(len(self.tasks), 1)
        self.c.select_chooser_card.assert_called_once()

    def test_unacknowledged_choice_keeps_decisions_paused_after_bounded_submits(self):
        self.request()
        for _ in range(45):
            if not self.tasks:
                break
            self.tick()
        self.assertFalse(self.tasks)
        self.c.select_chooser_card.assert_called_once()
        self.assertEqual(self.c._Controller__click_card_prompt_submit.call_count, 2)
        self.assertTrue(self.c._Controller__should_pause_for_select_n())
        self.c._Controller__write_target_debug_bundle.assert_called_once_with(
            "graveyard_choice_unconfirmed"
        )

    def test_missing_hover_never_submits_and_retries_only_once(self):
        self.c.select_chooser_card.return_value = False
        self.request()
        self.tick()
        self.tick()
        self.assertEqual(self.c.select_chooser_card.call_count, 2)
        self.c._Controller__click_card_prompt_submit.assert_not_called()
        self.assertTrue(self.c._Controller__should_pause_for_select_n())

    def test_retired_match_worker_cannot_click(self):
        self.request()
        self.c._Controller__live_match_id = "match-2"
        self.tick()
        self.c.select_chooser_card.assert_not_called()

    def test_stop_cancels_pending_submit(self):
        self.request()
        self.tick()
        self.c._stop_requested = True
        self.tick()
        self.c._Controller__click_card_prompt_submit.assert_not_called()

    def test_creature_choices_reuse_existing_creature_ranking(self):
        chosen = self.state["gameObjects"][0]
        chosen["cardTypes"] = ["CardType_Creature"]
        with patch.object(module.LifegainLogic, "best_creature", return_value=chosen):
            self.assertEqual(self.c._Controller__single_graveyard_choice(self.req), 10)

    def test_stale_submit_guard_rechecks_prompt_after_template_lookup(self):
        self.request()
        self.tick()
        pending = self.c._Controller__pending_select_n
        self.tick()
        guard = self.c._Controller__click_card_prompt_submit.call_args.kwargs["still_active"]
        self.assertTrue(guard())
        self.state["gameStateId"] += 1
        self.state["zones"][0]["objectInstanceIds"].remove(pending["target_id"])
        self.assertFalse(guard())

    def test_missing_zone_or_unrelated_state_update_is_not_acknowledgement(self):
        self.request()
        self.tick()
        self.state["gameStateId"] += 1
        self.assertTrue(self.c._Controller__should_pause_for_select_n())
        self.state["zones"] = []
        self.assertTrue(self.c._Controller__should_pause_for_select_n())

    def test_other_selection_types_are_not_claimed(self):
        variants = [
            {"minSel": 0}, {"maxSel": 2},
            {"context": "SelectionContext_Discard"},
            {"optionContext": "OptionContext_Sacrifice"},
            {"idType": "IdType_PromptParameterIndex"}, {"ids": [10, 999]},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertIsNone(self.c._Controller__single_graveyard_choice(
                    {**self.req, **changes}
                ))
        self.state["zones"][0]["ownerSeatId"] = 2
        self.assertIsNone(self.c._Controller__single_graveyard_choice(self.req))

    def test_informational_request_never_schedules_graveyard_click(self):
        self.request(informationalUseOnly=True)
        self.assertFalse(self.tasks)
        self.c.select_chooser_card.assert_not_called()


if __name__ == "__main__":
    unittest.main()
