"""Tests for the temporary passive scry/surveil soak instrumentation."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState
from tools.analyze_group_soak import parse_events, summarize


class _FakeTimer:
    instances = []

    def __init__(self, delay, callback, args=None, kwargs=None):
        self.delay = delay
        self.callback = callback
        self.args = tuple(args or ())
        self.kwargs = dict(kwargs or {})
        self.cancelled = False
        self.daemon = False
        self.__class__.instances.append(self)

    def start(self):
        return None

    def cancel(self):
        self.cancelled = True

    def is_alive(self):
        return False


def make_controller() -> Controller:
    handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    handle.close()
    controller = Controller(handle.name)
    controller._Controller__system_seat_id = 1
    controller._Controller__live_match_id = "match-1"
    controller._Controller__last_seen_match_id = "match-1"
    # Mandatory screen-safety stubs: no unit test may inspect the monitor.
    controller._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
    controller._click_image_in_scaled_arena_region = lambda *a, **k: False
    controller._click_abs = lambda *a, **k: None
    return controller


def seed_state(controller: Controller, *, hand=(10,), cast_seat=1, local_sources=1, foreign_sources=0):
    actions = [{
        "seatId": cast_seat,
        "action": {
            "actionType": "ActionType_Cast",
            "instanceId": 10,
            "grpId": 93833,
            "manaCost": [{"color": ["ManaColor_Generic"], "count": 3}],
        },
    }]
    for index in range(local_sources):
        actions.append({
            "seatId": 1,
            "action": {
                "actionType": "ActionType_Activate_Mana",
                "instanceId": 100 + index,
                "abilityGrpId": 1001,
            },
        })
    for index in range(foreign_sources):
        actions.append({
            "seatId": 2,
            "action": {
                "actionType": "ActionType_Activate_Mana",
                "instanceId": 200 + index,
                "abilityGrpId": 1003,
            },
        })
    controller.updated_game_state = GameState({
        "gameStateId": 50,
        "turnInfo": {
            "turnNumber": 4, "phase": "Phase_Main1", "step": "Step_Main",
            "activePlayer": 1, "priorityPlayer": 1, "decisionPlayer": 1,
            "nextPhase": "Phase_Combat", "nextStep": "Step_BeginCombat",
        },
        "timers": [],
        "gameObjects": [{
            "instanceId": 10, "grpId": 93833, "zoneId": 31,
            "ownerSeatId": 1, "controllerSeatId": 1,
            "cardTypes": ["CardType_Creature"],
        }],
        "players": [{"systemSeatNumber": 1}, {"systemSeatNumber": 2}],
        "annotations": [],
        "actions": actions,
        "zones": [
            {"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": 1,
             "objectInstanceIds": list(hand)},
            {"zoneId": 27, "type": "ZoneType_Stack", "objectInstanceIds": []},
        ],
    })
    controller._Controller__inst_id_grp_id_dict = {10: 93833}
    controller._Controller__soak_group_context = {
        "prompt_seq": 1, "match_id": "match-1", "context": "GroupingContext_Scry",
        "prompt_id": 7, "source_id": 99, "started_monotonic": time.monotonic(),
        "start_state_id": 49, "decision_count": 0, "progress_logged": True,
        "bundle_written": False, "anomalies": [],
    }


class GroupSoakDecisionTest(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        self.events = []
        self.anomalies = []
        self.controller._Controller__soak_group_event = (
            lambda event, **details: self.events.append((event, details))
        )
        self.controller._Controller__soak_group_anomaly = (
            lambda anomaly, **details: self.anomalies.append((anomaly, details))
        )

    def test_detects_foreign_mana_making_cast_appear_affordable(self):
        seed_state(self.controller, local_sources=1, foreign_sources=2)

        self.controller.record_group_soak_decision("cast", [10])

        self.assertIn("affordable_only_with_foreign_mana", [name for name, _ in self.anomalies])
        cast = next(details for event, details in self.events if event == "post_group_cast_selected")
        self.assertFalse(cast["payable_with_local_sources"])
        self.assertTrue(cast["payable_with_all_sources"])
        self.assertEqual(cast["local_mana_source_count"], 1)
        self.assertEqual(cast["all_mana_source_count"], 3)

    def test_detects_cast_target_outside_authoritative_hand_membership(self):
        seed_state(self.controller, hand=(), local_sources=3)

        self.controller.record_group_soak_decision("cast", [10])

        self.assertIn("cast_not_in_hand", [name for name, _ in self.anomalies])

    def test_detects_cast_action_advertised_for_other_seat(self):
        seed_state(self.controller, cast_seat=2, local_sources=3)

        self.controller.record_group_soak_decision("cast", [10])

        self.assertIn("cast_owned_by_other_seat", [name for name, _ in self.anomalies])

    def test_old_match_prompt_does_not_observe_new_match_decision(self):
        seed_state(self.controller, local_sources=3)
        self.controller._Controller__live_match_id = "match-2"
        self.controller._Controller__last_seen_match_id = "match-2"

        self.controller.record_group_soak_decision("cast", [10])

        self.assertEqual(self.events, [])
        self.assertEqual(self.anomalies, [])


class GroupSoakLifecycleTest(unittest.TestCase):
    def setUp(self):
        _FakeTimer.instances = []
        self.controller = make_controller()
        self.events = []
        self.controller._Controller__soak_group_event = (
            lambda event, **details: self.events.append((event, details))
        )

    @staticmethod
    def group_line(context="GroupingContext_Scry", prompt_id=7, source_id=99):
        return json.dumps({
            "greToClientEvent": {"greToClientMessages": [{
                "type": "GREMessageType_GroupReq", "systemSeatIds": [1],
                "gameStateId": 50, "prompt": {"promptId": prompt_id},
                "groupReq": {
                    "context": context, "sourceId": source_id, "instanceIds": [10],
                    "groupSpecs": [{"zoneType": "ZoneType_Library"}],
                },
            }]},
        })

    def test_records_scry_prompt_and_distinct_rapid_prompt_suppression(self):
        seed_state(self.controller, local_sources=1)
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__handle_group_req(self.group_line())
            self.controller._Controller__handle_group_req(
                self.group_line("GroupingContext_Surveil", prompt_id=8, source_id=100)
            )

        self.assertEqual(self.controller._Controller__soak_group_seq, 1)
        self.assertEqual([event for event, _ in self.events], [
            "prompt_received", "duplicate_ignored",
        ])
        duplicate = self.events[-1][1]
        self.assertEqual(duplicate["incoming_context"], "GroupingContext_Surveil")
        self.assertEqual(duplicate["incoming_prompt_id"], 8)

    def test_raw_pending_count_and_state_progress_are_observed_only(self):
        seed_state(self.controller, local_sources=1)
        self.controller._Controller__soak_group_context["progress_logged"] = False
        self.controller._Controller__note_soak_group_raw_state({
            "greToClientEvent": {"greToClientMessages": [{
                "type": "GREMessageType_GameStateMessage",
                "gameStateMessage": {"gameStateId": 51, "pendingMessageCount": 2},
            }]},
        })

        self.assertEqual(self.controller._Controller__soak_group_raw_pending_count, 2)
        self.assertEqual(self.controller.updated_game_state.get_full_state()["gameStateId"], 50)
        self.assertEqual(self.events[-1][0], "state_progress")

    def test_group_resume_records_shadow_gate_but_does_not_block(self):
        seed_state(self.controller, local_sources=3)
        self.controller._Controller__has_mulled_keep = True
        self.controller._Controller__soak_group_raw_pending_count = 1
        self.controller._Controller__soak_group_raw_pending_state_id = 50
        self.controller._Controller__soak_group_anomaly = lambda *a, **k: None
        self.controller._Controller__decision_callback = (
            lambda state: self.controller.record_group_soak_decision("cast", [10])
        )

        self.controller._Controller__resume_decision_after_group_req(
            prompt_seq=1, match_id="match-1"
        )

        shadow = next(details for event, details in self.events if event == "resume_shadow_guard")
        self.assertEqual(shadow["mode"], "observe_only")
        self.assertEqual(shadow["recommendation"], "wait_for_current_raw_pending")
        cast = next(details for event, details in self.events if event == "post_group_cast_selected")
        self.assertEqual(cast["decision_origin"], "group_resume")

    def test_stale_group_resume_is_ignored(self):
        seed_state(self.controller, local_sources=3)
        calls = []
        self.controller._Controller__decision_callback = lambda state: calls.append(state)

        self.controller._Controller__resume_decision_after_group_req(
            prompt_seq=99, match_id="old-match"
        )

        self.assertEqual(calls, [])
        event, details = self.events[-1]
        self.assertEqual(event, "resume_rejected")
        self.assertEqual(details["reason"], "stale_prompt_or_match")

    def test_non_group_recovery_keeps_its_own_origin(self):
        seed_state(self.controller, local_sources=3)
        self.controller._Controller__has_mulled_keep = True
        self.controller._Controller__decision_callback = (
            lambda state: self.controller.record_group_soak_decision("cast", [10])
        )

        self.controller._Controller__resume_decision_after_recovery("cast_failure_recovery")

        self.assertNotIn("resume_timer_fired", [event for event, _ in self.events])
        cast = next(details for event, details in self.events if event == "post_group_cast_selected")
        self.assertEqual(cast["decision_origin"], "cast_failure_recovery")

    def test_non_group_recovery_does_not_cancel_group_resume_timer(self):
        seed_state(self.controller, local_sources=3)
        with mock.patch("Controller.MTGAController.Controller.threading.Timer", _FakeTimer):
            self.controller._Controller__schedule_group_resume(1.0)
            group_timer = self.controller._Controller__group_resume_timer
            self.controller._Controller__schedule_decision_recovery(1.0, "modal_recovery")
            recovery_timer = self.controller._Controller__decision_recovery_timer

        self.assertIsNot(group_timer, recovery_timer)
        self.assertFalse(group_timer.cancelled)
        self.assertEqual(group_timer.kwargs["prompt_seq"], 1)
        self.assertEqual(recovery_timer.kwargs["origin"], "modal_recovery")


class GroupSoakAnalyzerTest(unittest.TestCase):
    def test_deduplicates_history_copy_and_summarizes_anomaly(self):
        event1 = {
            "event": "prompt_received", "run_id": "run", "prompt_seq": 1,
            "context": "GroupingContext_Scry", "match_id": "match",
        }
        event2 = {
            "event": "anomaly", "anomaly": "cast_not_in_hand",
            "run_id": "run", "prompt_seq": 1,
            "context": "GroupingContext_Scry", "match_id": "match",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "bot.log"
            second = Path(temp_dir) / "history.log"
            text = "".join(
                f"[2026-09-19 12:00:0{index}.000] [INFO] [SOAK_GROUP_V1] {json.dumps(event)}\n"
                for index, event in enumerate((event1, event2))
            )
            first.write_text(text, encoding="utf-8")
            second.write_text(text, encoding="utf-8")
            events, errors = parse_events([first, second])

        report = summarize(events, errors)
        self.assertEqual(report["event_count"], 2)
        self.assertEqual(report["prompt_count"], 1)
        self.assertEqual(report["anomaly_counts"], {"cast_not_in_hand": 1})
        self.assertTrue(report["prompts"][0]["incomplete"])

    def test_keeps_unattributed_recovery_out_of_prompt_totals(self):
        events = [
            {"event": "resume_timer_fired", "run_id": "run", "prompt_seq": None},
            {
                "event": "post_group_cast_selected", "run_id": "run", "prompt_seq": 1,
                "context": "GroupingContext_Scry", "match_id": "match",
                "decision_origin": "cast_failure_recovery",
            },
            {
                "event": "resume_shadow_guard", "run_id": "run", "prompt_seq": 1,
                "context": "GroupingContext_Scry", "match_id": "match",
                "recommendation": "wait_for_current_raw_pending",
            },
        ]

        report = summarize(events)

        self.assertEqual(report["prompt_count"], 0)
        self.assertEqual(report["unattributed_event_counts"], {"resume_timer_fired": 1})
        self.assertEqual(report["decision_origin_counts"], {"cast_failure_recovery": 1})
        self.assertEqual(
            report["shadow_recommendation_counts"], {"wait_for_current_raw_pending": 1}
        )


if __name__ == "__main__":
    unittest.main()
