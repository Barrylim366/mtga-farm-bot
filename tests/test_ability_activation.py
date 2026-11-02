"""Activation handshake and offline visual regressions for the Marshal stall."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ForgeEngine import hooks as hooks_module
from ForgeEngine.bridge import Config
from ForgeEngine.hooks import ForgeHooks
from tests.test_forge_engine import ME, OPP, bridge_with, creature, state_message, tracker_with


class Clock:
    def __init__(self):
        self.now = 0.0
        self.tick = lambda: None

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.tick()


class AbilityHandshakeTest(unittest.TestCase):
    def setUp(self):
        self.tracker = tracker_with(state_message(
            battlefield=[creature(290, 30, 2, 2, ME)],
            actions=[{"actionType": "ActionType_Activate", "instanceId": 290, "abilityGrpId": 21730}],
        ))
        self.bridge = bridge_with(self.tracker)
        self.bridge.ability_result = Mock(wraps=self.bridge.ability_result)
        self.hooks = ForgeHooks(self.bridge, Config)
        self.clock = Clock()
        self.controller = SimpleNamespace(
            can_execute_game_action=Mock(return_value=True),
            select_battlefield_permanent=Mock(return_value=True),
            click_ability_control=Mock(return_value=False),
            write_ability_activation_debug=Mock(),
        )
        self.own = Mock()
        self.logs = []

    def request(self, kind="PayCostsReq", seat=ME, msg_id=100):
        self.tracker.feed_message({
            "type": "GREMessageType_" + kind, "systemSeatIds": [seat],
            "gameStateId": 10, "msgId": msg_id,
        })

    def stack(self, instance_id=500, parent=290):
        self.tracker.zones[27]["objectInstanceIds"].append(instance_id)
        self.tracker.objects[instance_id] = {
            "instanceId": instance_id, "parentId": parent, "type": "GameObjectType_Ability",
            "grpId": 21730, "controllerSeatId": ME,
        }

    def ability_messages(self, parent=290, grp=21730, seat=ME):
        # Minimal shape from Food's AbilityInstanceCreated GRE messages. The
        # following diff removes the ability before the next handshake check.
        return [{
            "type": "GREMessageType_GameStateMessage", "msgId": 101,
            "gameStateMessage": {
                "type": "GameStateType_Diff", "gameStateId": 11,
                "gameObjects": [{"instanceId": 500, "parentId": parent,
                                 "grpId": grp, "controllerSeatId": seat,
                                 "type": "GameObjectType_Ability"}],
                "annotations": [{"type": ["AnnotationType_AbilityInstanceCreated"],
                                 "affectorId": parent, "affectedIds": [500]}],
            },
        }, {
            "type": "GREMessageType_GameStateMessage", "msgId": 102,
            "gameStateMessage": {"type": "GameStateType_Diff", "gameStateId": 12,
                                 "diffDeletedInstanceIds": [500]},
        }]

    def feed_batch(self, messages):
        self.tracker.feed_line(json.dumps({"greToClientEvent": {"greToClientMessages": messages}}))

    def test_fast_food_activation_survives_creation_and_deletion_in_one_batch(self):
        def confirm(control, *, action_guard):
            self.assertTrue(action_guard())
            self.feed_batch(self.ability_messages())
            return True
        self.controller.click_ability_control.side_effect = confirm
        self.run_activation()
        self.assertNotIn(500, self.tracker.objects)
        self.bridge.ability_result.assert_called_once_with(290, True)
        self.controller.click_ability_control.assert_called_once()
        self.controller.select_battlefield_permanent.assert_called_once_with(290, 1)
        self.controller.write_ability_activation_debug.assert_not_called()
        self.assertIsNone(self.tracker._activation_observer)
        self.assertTrue(any('source_ability_created' in line for line in self.logs))

    def test_unrelated_batched_abilities_are_not_acknowledged(self):
        for kwargs in ({"parent": 999}, {"grp": 123}, {"seat": OPP}):
            with self.subTest(kwargs=kwargs):
                self.setUp()
                self.controller.select_battlefield_permanent.side_effect = (
                    lambda *args: self.feed_batch(self.ability_messages(**kwargs)) or True)
                self.run_activation()
                self.bridge.ability_result.assert_called_once_with(290, False)

    def test_evidence_during_failure_capture_prevents_recovery_call(self):
        self.controller.write_ability_activation_debug.side_effect = (
            lambda context: self.feed_batch(self.ability_messages()))
        self.run_activation()
        self.assertNotIn('cancel', [c.args[0] for c in self.controller.click_ability_control.call_args_list])
        self.bridge.ability_result.assert_called_once_with(290, True)

    def test_observer_cleanup_on_selection_exception(self):
        self.controller.select_battlefield_permanent.side_effect = RuntimeError('selection failed')
        with self.assertRaises(RuntimeError):
            self.run_activation()
        self.assertIsNone(self.tracker._activation_observer)
        self.assertFalse(self.hooks._ability_busy.locked())

    def test_observation_does_not_leak_into_next_attempt(self):
        self.controller.select_battlefield_permanent.side_effect = (
            lambda *args: self.feed_batch(self.ability_messages()) or True)
        self.run_activation()
        self.bridge.ability_result.reset_mock()
        self.controller.select_battlefield_permanent.side_effect = None
        self.run_activation()
        self.bridge.ability_result.assert_called_once_with(290, False)

    def run_activation(self):
        with patch.object(hooks_module, "time", self.clock), \
                patch.object(hooks_module, "log", side_effect=self.logs.append):
            self.hooks.activate_ability(self.controller, 290, 21730, self.own)

    def test_direct_start_needs_no_confirmation(self):
        self.controller.select_battlefield_permanent.side_effect = lambda *a: self.stack() or True
        self.run_activation()
        self.controller.click_ability_control.assert_not_called()
        self.bridge.ability_result.assert_called_once_with(290, True)
        self.assertTrue(any("ABILITY_ACTIVATION_ACKNOWLEDGED:" in line
                            and "confirmation_clicked=False" in line for line in self.logs))
        self.assertFalse(any("ABILITY_ACTIVATION_CLICKED:" in line for line in self.logs))

    def test_confirmation_leads_to_payment_or_targets(self):
        for kind in ("PayCostsReq", "SelectTargetsReq"):
            with self.subTest(kind=kind):
                self.setUp()
                def confirm(control, *, action_guard):
                    self.assertEqual(control, "activate")
                    self.assertTrue(action_guard())
                    self.request(kind)
                    return True
                self.controller.click_ability_control.side_effect = confirm
                self.run_activation()
                self.controller.click_ability_control.assert_called_once()
                self.bridge.ability_result.assert_called_once_with(290, True)
                self.assertEqual(sum("ABILITY_ACTIVATION_CLICKED:" in line for line in self.logs), 1)
                self.assertTrue(any("ABILITY_ACTIVATION_ACKNOWLEDGED:" in line
                                    and "confirmation_clicked=True" in line for line in self.logs))

    def test_delayed_acknowledgment_does_not_click_confirmation_twice(self):
        self.controller.click_ability_control.return_value = True
        self.clock.tick = lambda: self.request() if self.clock.now >= 2 else None
        self.run_activation()
        self.controller.click_ability_control.assert_called_once()
        self.bridge.ability_result.assert_called_once_with(290, True)

    def test_late_confirmation_gets_its_own_bounded_acknowledgment_window(self):
        confirmations = []
        def confirm(control, *, action_guard):
            if self.clock.now < 2.7:
                return False
            self.assertEqual(control, "activate")
            self.assertTrue(action_guard())
            confirmations.append(control)
            return True
        self.controller.click_ability_control.side_effect = confirm
        self.clock.tick = lambda: self.request() if self.clock.now >= 5.4 else None
        self.run_activation()
        self.assertEqual(confirmations, ["activate"])
        self.assertGreater(self.clock.now, 5)
        self.assertLess(self.clock.now, 6)
        self.bridge.ability_result.assert_called_once_with(290, True)

    def test_missing_confirmation_is_bounded_and_suppressed(self):
        self.run_activation()
        self.assertLess(self.clock.now, 3.3)
        self.assertEqual(self.controller.click_ability_control.call_args.args, ("cancel",))
        self.controller.write_ability_activation_debug.assert_called_once()
        self.assertIn(290, self.bridge._abilities_failed)

    def test_confirmed_but_unacknowledged_attempt_cancels_once(self):
        self.controller.click_ability_control.return_value = True
        self.run_activation()
        self.assertEqual([c.args[0] for c in self.controller.click_ability_control.call_args_list],
                         ["activate", "cancel"])
        self.assertLess(self.clock.now, 3.3)
        self.bridge.ability_result.assert_called_once_with(290, False)
        self.assertEqual(sum("ABILITY_ACTIVATION_CLICKED:" in line for line in self.logs), 1)
        self.assertEqual(sum("ABILITY_ACTIVATION_TIMEOUT:" in line for line in self.logs), 1)
        self.assertFalse(any("ABILITY_ACTIVATION_ACKNOWLEDGED:" in line for line in self.logs))

    def test_existing_prompt_or_stack_entry_is_not_success(self):
        self.request()
        self.stack()
        self.run_activation()
        self.bridge.ability_result.assert_called_once_with(290, False)

    def test_new_opponent_request_or_unrelated_stack_entry_is_not_success(self):
        def unrelated():
            self.request(seat=OPP)
            if 501 not in self.tracker.objects:
                self.stack(501, parent=999)
        self.clock.tick = unrelated
        self.run_activation()
        self.bridge.ability_result.assert_called_once_with(290, False)

    def test_new_but_obsolete_payment_request_is_not_success(self):
        def stale_request():
            self.request()
            self.tracker.game_state_id = 11
        self.clock.tick = stale_request
        self.run_activation()
        self.bridge.ability_result.assert_called_once_with(290, False)

    def test_match_or_turn_change_stops_without_recovery_click(self):
        for change in (lambda: self.tracker.game_info.update(matchID="m2"),
                       lambda: self.tracker.turn_info.update(turnNumber=4)):
            with self.subTest(change=change):
                self.tracker.game_info["matchID"] = "m1"
                self.tracker.turn_info["turnNumber"] = 3
                self.controller.click_ability_control.reset_mock()
                self.clock.tick = change
                self.run_activation()
                self.assertNotIn("cancel", [c.args[0] for c in self.controller.click_ability_control.call_args_list])
        self.controller.write_ability_activation_debug.assert_not_called()

    def test_stop_during_wait_stops_recovery(self):
        self.clock.tick = lambda: setattr(self.controller.can_execute_game_action, "return_value", False)
        self.run_activation()
        self.controller.write_ability_activation_debug.assert_not_called()
        self.bridge.ability_result.assert_not_called()

    def test_acknowledgment_during_failure_capture_prevents_cancel(self):
        self.controller.write_ability_activation_debug.side_effect = lambda context: self.request()
        def click(control, *, action_guard):
            if control == "cancel":
                self.assertFalse(action_guard())
            return False
        self.controller.click_ability_control.side_effect = click
        self.run_activation()
        self.bridge.ability_result.assert_called_once_with(290, True)

    def test_concurrent_call_does_nothing(self):
        self.hooks._ability_busy.acquire()
        try:
            self.run_activation()
            self.controller.select_battlefield_permanent.assert_not_called()
            self.own.assert_not_called()
        finally:
            self.hooks._ability_busy.release()

    def test_disabled_hooks_preserve_existing_fallback(self):
        self.hooks.config = lambda: Config(abilities=False)
        self.run_activation()
        self.own.assert_called_once()
        self.controller.select_battlefield_permanent.assert_not_called()


class ActivationObserverTest(unittest.TestCase):
    setUp = AbilityHandshakeTest.setUp
    stack = AbilityHandshakeTest.stack
    ability_messages = AbilityHandshakeTest.ability_messages
    feed_batch = AbilityHandshakeTest.feed_batch

    def test_split_annotation_and_object_acknowledge_only_matching_ability(self):
        for annotation_first in (True, False):
            with self.subTest(annotation_first=annotation_first):
                self.setUp()
                watch = self.tracker.begin_activation_observation({290}, 21730)
                creation = self.ability_messages()[0]
                state = creation['gameStateMessage']
                obj = state['gameObjects'][0]
                del obj['parentId']  # Source must come from the separate annotation.
                part = 'gameObjects' if annotation_first else 'annotations'
                deferred = state.pop(part)
                self.feed_batch([creation])
                self.assertIsNone(self.tracker.activation_evidence(watch))
                self.feed_batch([{
                    'type': 'GREMessageType_GameStateMessage', 'msgId': 102,
                    'gameStateMessage': {'type': 'GameStateType_Diff', 'gameStateId': 12,
                                         part: deferred},
                }])
                self.assertEqual(self.tracker.activation_evidence(watch)['source_id'], 290)

    def test_existing_ability_and_old_state_do_not_acknowledge(self):
        for existing in (True, False):
            with self.subTest(existing=existing):
                self.setUp()
                if existing:
                    self.stack()
                watch = self.tracker.begin_activation_observation({290}, 21730)
                messages = self.ability_messages()
                if not existing:
                    messages[0]['gameStateMessage']['gameStateId'] = self.tracker.game_state_id
                self.feed_batch(messages)
                self.assertIsNone(self.tracker.activation_evidence(watch))

    def test_match_change_invalidates_already_latched_evidence(self):
        watch = self.tracker.begin_activation_observation({290}, 21730)
        self.feed_batch(self.ability_messages())
        self.assertIsNotNone(self.tracker.activation_evidence(watch))
        self.tracker.feed_message({
            'type': 'GREMessageType_GameStateMessage',
            'gameStateMessage': {'type': 'GameStateType_Diff', 'gameStateId': 13,
                                 'gameInfo': {'matchID': 'different'}},
        })
        self.assertIsNone(self.tracker.activation_evidence(watch))
        self.assertIsNone(self.tracker._activation_observer)


class AbilityControlTest(unittest.TestCase):
    def make_controller(self):
        from Controller.MTGAController.Controller import Controller
        c = Controller.__new__(Controller)
        c._locate_image_center_in_scaled_arena_region = Mock(return_value=(640, 330))
        c._click_image_in_scaled_arena_region = Mock(return_value=False)
        c._click_abs = Mock()
        c.input = SimpleNamespace(input_transaction=lambda **kw: nullcontext(True))
        return c

    def test_guard_is_rechecked_after_recognition(self):
        c = self.make_controller()
        guard = Mock(side_effect=[True, True, False])
        self.assertFalse(c.click_ability_control("activate", action_guard=guard))
        c._click_abs.assert_not_called()

    def test_cancel_requires_activation_label_and_cancel_text(self):
        c = self.make_controller()
        for hits in ([None, None], [(640, 330), None]):
            c._locate_image_center_in_scaled_arena_region.side_effect = hits
            self.assertFalse(c.click_ability_control("cancel", action_guard=lambda: True))
        c._click_abs.assert_not_called()
        c._locate_image_center_in_scaled_arena_region.side_effect = [(640, 330), (1180, 635)]
        self.assertTrue(c.click_ability_control("cancel", action_guard=lambda: True))
        c._click_abs.assert_called_once_with(1180, 635, "ABILITY_CANCEL")

    def test_food_confirmation_and_cancel_use_food_label(self):
        c = self.make_controller()
        c._locate_image_center_in_scaled_arena_region.side_effect = [None, (1200, 565)]
        self.assertTrue(c.click_ability_control("activate", action_guard=lambda: True))
        c._click_abs.assert_called_once_with(1200, 565, "ABILITY_ACTIVATE_SACRIFICE_FOOD")
        c._click_abs.reset_mock()
        c._locate_image_center_in_scaled_arena_region.side_effect = [None, (1200, 565), (1480, 795)]
        self.assertTrue(c.click_ability_control("cancel", action_guard=lambda: True))
        c._click_abs.assert_called_once_with(1480, 795, "ABILITY_CANCEL")

    def test_busy_input_sends_no_click(self):
        c = self.make_controller()
        c.input.input_transaction = lambda **kw: nullcontext(False)
        self.assertFalse(c.click_ability_control("activate", action_guard=lambda: True))
        c._locate_image_center_in_scaled_arena_region.assert_not_called()

    def test_incident_templates_at_supported_window_sizes_without_screen_access(self):
        import cv2
        from Controller.MTGAController.Controller import Controller
        from vision.vision import VisionEngine
        fixtures = Path(__file__).parent / "fixtures"
        for filename, present in (("ability-activation.jpg", True),
                                  ("food-activation.png", True),
                                  ("ability-activation-dismissed.jpg", False)):
            original = cv2.imread(str(fixtures / filename))
            self.assertIsNotNone(original)
            for width, height in ((960, 540), (1280, 720), (1600, 900), (1920, 1080)):
                with self.subTest(filename=filename, size=(width, height)):
                    c = self.make_controller()
                    # Run the real matcher over an offline frame. Every capture
                    # comes from this fixture; no monitor/input backend is used.
                    c._vision = VisionEngine()
                    frame = cv2.resize(original, (width, height))
                    c._vision._grab_full_frame = lambda: frame
                    c._stop_requested = False
                    c._get_ui_action_arena_region = lambda **kw: (0, 0, width, height)
                    c._locate_image_center_in_scaled_arena_region = (
                        Controller._locate_image_center_in_scaled_arena_region.__get__(c)
                    )
                    for control in ("activate", "cancel"):
                        hit = c._locate_ability_control(control)
                        self.assertEqual(hit is not None, present, control)
                        if hit:
                            if filename == "food-activation.png":
                                expected = (979, 453) if control == "activate" else (1184, 634)
                            else:
                                expected = (427, 327) if control == "activate" else (1184, 634)
                            self.assertAlmostEqual(hit[0] / width * 1280, expected[0], delta=4)
                            self.assertAlmostEqual(hit[1] / height * 720, expected[1], delta=4)
                        # Exercise recognition through the guarded click path too:
                        # Cancel must accept Food's confirmation as activation UI.
                        c._click_abs.reset_mock()
                        self.assertEqual(c.click_ability_control(
                            control, action_guard=lambda: True), present)
                        if present:
                            label = ("ABILITY_ACTIVATE_SACRIFICE_FOOD"
                                     if filename == "food-activation.png" and control == "activate"
                                     else f"ABILITY_{control.upper()}")
                            c._click_abs.assert_called_once_with(*hit, label)
                        else:
                            c._click_abs.assert_not_called()
