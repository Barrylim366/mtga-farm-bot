"""The Forge engine layer, with the advisor process replaced by a stand-in.

No Java, no screen: the advisor is a fake that records requests and answers
what each test tells it to, and the controller is a fake with the handful of
steps the hooks call.
"""
import json
import threading
import unittest

from Controller.Utilities.GameState import GameState
from ForgeEngine import hooks as hooks_module
from ForgeEngine import snapshot
from ForgeEngine.bridge import Bridge, Config, Intent, filtered_state, probe_safely
from ForgeEngine.carddb import CardDb
from ForgeEngine.forge_ai import ForgeAI
from ForgeEngine.hooks import ForgeHooks, attack_difference, choose_target, group_requests
from ForgeEngine.tracker import ArenaTracker

ME, OPP = 1, 2


class FakeAdvisor:
    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.asked = []

    def running(self):
        return True

    def decide(self, kind, state, timeout_sec, extra=None):
        self.asked.append((kind, state, dict(extra or {})))
        answer = self.answers.get(kind)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(state, extra or {})
        return {"ok": True, "decision": answer if answer is not None else {"action": "none"}}

    def close(self):
        pass


CARDS = CardDb({
    "cards": {
        "10": {"name": "Forest", "types": ["Land"]},
        "20": {"name": "Llanowar Elves", "power": 1, "toughness": 1},
        "30": {"name": "Grizzly Bears", "power": 2, "toughness": 2},
        "40": {"name": "Shock"},
    },
    "counters": {"1": "+1/+1"},
})


def creature(instance_id, grp_id, power, toughness, controller, **extra):
    obj = {
        "instanceId": instance_id, "grpId": grp_id, "type": "GameObjectType_Card",
        "zoneId": 28, "ownerSeatId": controller, "controllerSeatId": controller,
        "cardTypes": ["CardType_Creature"], "power": {"value": power}, "toughness": {"value": toughness},
    }
    obj.update(extra)
    return obj


def state_message(*, state_id=10, full=True, turn=3, active=ME, phase="Phase_Main1", step=None,
                  hand=(), battlefield=(), actions=(), life=(20, 20), extra=None):
    turn_info = {"turnNumber": turn, "activePlayer": active, "priorityPlayer": active,
                 "decisionPlayer": active, "phase": phase}
    if step:
        turn_info["step"] = step
    hand = list(hand)
    battlefield = list(battlefield)
    gsm = {
        "type": "GameStateType_Full" if full else "GameStateType_Diff",
        "gameStateId": state_id,
        "gameInfo": {"matchID": "m1"},
        "turnInfo": turn_info,
        "players": [
            {"systemSeatNumber": ME, "lifeTotal": life[0]},
            {"systemSeatNumber": OPP, "lifeTotal": life[1]},
        ],
        "zones": [
            {"zoneId": 31, "type": "ZoneType_Hand", "ownerSeatId": ME,
             "objectInstanceIds": [o["instanceId"] for o in hand]},
            {"zoneId": 28, "type": "ZoneType_Battlefield",
             "objectInstanceIds": [o["instanceId"] for o in battlefield]},
            {"zoneId": 27, "type": "ZoneType_Stack", "objectInstanceIds": []},
            {"zoneId": 32, "type": "ZoneType_Library", "ownerSeatId": ME, "objectInstanceIds": list(range(900, 930))},
        ],
        "gameObjects": hand + battlefield,
        "actions": [{"seatId": ME, "action": a} for a in actions],
    }
    gsm.update(extra or {})
    return {"type": "GREMessageType_GameStateMessage", "systemSeatIds": [ME], "gameStateMessage": gsm}


def tracker_with(*messages):
    tracker = ArenaTracker()
    tracker.feed_message({"type": "GREMessageType_ConnectResp", "systemSeatIds": [ME]})
    for message in messages:
        tracker.feed_message(message)
    return tracker


def hand_card(instance_id, grp_id, card_types=("CardType_Creature",)):
    return {"instanceId": instance_id, "grpId": grp_id, "type": "GameObjectType_Card", "zoneId": 31,
            "ownerSeatId": ME, "controllerSeatId": ME, "cardTypes": list(card_types)}


def bridge_with(tracker, answers=None):
    bridge = Bridge(client=FakeAdvisor(answers), cards=CARDS, tracker=tracker, restarts=False)
    bridge.record = lambda entry: None
    return bridge


class TrackerTest(unittest.TestCase):
    def test_a_resent_object_replaces_the_old_one(self):
        tapped = state_message(battlefield=[creature(200, 30, 2, 2, ME, isTapped=True)])
        untapped = state_message(state_id=11, full=False, battlefield=[creature(200, 30, 2, 2, ME)])
        tracker = tracker_with(tapped)
        self.assertTrue(tracker.objects[200]["isTapped"])
        tracker.feed_message(untapped)
        self.assertNotIn("isTapped", tracker.objects[200])

    def test_a_request_is_current_only_until_the_next_state(self):
        tracker = tracker_with(state_message())
        tracker.feed_message({
            "type": "GREMessageType_DeclareAttackersReq", "systemSeatIds": [ME], "gameStateId": 10,
            "declareAttackersReq": {"attackers": [{"attackerInstanceId": 300}]},
        })
        self.assertTrue(tracker.pending_is_current("GREMessageType_DeclareAttackersReq"))
        self.assertEqual(snapshot.attack_request(tracker), [300])
        tracker.feed_message(state_message(state_id=11, full=False))
        self.assertFalse(tracker.pending_is_current("GREMessageType_DeclareAttackersReq"))

    def test_a_torn_line_is_ignored(self):
        tracker = ArenaTracker()
        line = "[UnityCrossThreadLogger] " + json.dumps({"greToClientEvent": {"greToClientMessages": [state_message()]}})
        self.assertEqual(tracker.feed_line(line[: len(line) // 2]), [])
        self.assertFalse(tracker.in_sync)
        self.assertEqual(len(tracker.feed_line(line)), 1)
        self.assertTrue(tracker.in_sync)

    def test_my_actions_ignores_stale_actions_available_req(self):
        tracker = tracker_with(state_message(state_id=10))
        tracker.feed_message({
            "type": "GREMessageType_ActionsAvailableReq", "systemSeatIds": [ME], "gameStateId": 10,
            "actionsAvailableReq": {"actions": [{"actionType": "ActionType_Cast", "instanceId": 500}]},
        })
        self.assertEqual(len(tracker.my_actions()), 1)
        self.assertEqual(tracker.my_actions()[0].get("instanceId"), 500)
        # Advance game state without a new action request
        tracker.feed_message(state_message(state_id=11, full=False))
        self.assertEqual(tracker.my_actions(), [])


class SnapshotTest(unittest.TestCase):
    def test_board_is_described_from_our_side(self):
        bears = creature(310, 30, 3, 3, ME, hasSummoningSickness=True,
                         uniqueAbilities=[{"id": 1, "grpId": 8}], color=["CardColor_Green"])
        attacker = creature(320, 20, 1, 1, OPP, damage=1)
        message = state_message(turn=6, active=OPP, life=(17, 9), hand=[hand_card(100, 20)],
                                battlefield=[bears, attacker],
                                extra={"persistentAnnotations": [{
                                    "id": 9, "type": ["AnnotationType_Counter"], "affectedIds": [310],
                                    "details": [{"key": "count", "valueInt32": [1]},
                                                {"key": "counter_type", "valueInt32": [1]}]}]})
        state = snapshot.build_state(tracker_with(message), CARDS)
        me, opp = state["players"]
        self.assertEqual((state["turn"], state["activePlayer"]), (6, 1))
        self.assertEqual((me["life"], opp["life"]), (17, 9))
        self.assertEqual(me["hand"], [{"id": 100, "name": "Llanowar Elves"}])
        self.assertEqual(me["librarySize"], 30)
        self.assertEqual(me["battlefield"][0], {
            "id": 310, "name": "Grizzly Bears", "sick": True, "types": ["Creature"], "colors": "G",
            "power": 3, "toughness": 3, "basePower": 2, "baseToughness": 2,
            "keywords": ["Flying"], "counters": {"+1/+1": 1},
        })
        self.assertEqual(opp["battlefield"][0]["damage"], 1)

    def test_land_drop_is_used_when_arena_stops_offering_one(self):
        hand = [hand_card(100, 10, ("CardType_Land",)), hand_card(101, 20)]
        offered = state_message(hand=hand, actions=[
            {"actionType": "ActionType_Play", "instanceId": 100},
            {"actionType": "ActionType_Cast", "instanceId": 101},
        ])
        not_offered = state_message(hand=hand, actions=[{"actionType": "ActionType_Cast", "instanceId": 101}])
        self.assertEqual(snapshot.build_state(tracker_with(offered), CARDS)["players"][0]["landsPlayed"], 0)
        self.assertEqual(snapshot.build_state(tracker_with(not_offered), CARDS)["players"][0]["landsPlayed"], 1)
        self.assertEqual(snapshot.main_request(tracker_with(offered)),
                         {"castable": [101], "playable": [100], "activatable": []})

    def test_an_ability_costs_plain_mana_or_is_left_uncompared(self):
        plain = {"manaCost": [{"color": ["ManaColor_Generic"], "count": 1}, {"color": ["ManaColor_Black"], "count": 1}]}
        self.assertEqual(snapshot.ability_cost(plain), {"cmc": 2, "colors": "B"})
        hybrid = {"manaCost": [{"color": ["ManaColor_Black", "ManaColor_Green"], "count": 1}]}
        self.assertEqual(snapshot.ability_cost(hybrid), {})

    def test_arenas_steps_have_forge_names(self):
        self.assertEqual(snapshot.forge_phase({"phase": "Phase_Main2", "step": "Step_End"}), "MAIN2")
        self.assertEqual(snapshot.forge_phase({"phase": "Phase_Combat", "step": "Step_DeclareBlock"}),
                         "COMBAT_DECLARE_BLOCKERS")
        self.assertIsNone(snapshot.forge_phase({"phase": "Phase_Beginning"}))


class FakeAI:
    """The bot's AI: answers with ``own``, and casts in a probe only what it may."""

    def __init__(self, own, may_cast=()):
        self.own = own
        self.may_cast = set(may_cast)
        self.memory = {"land_played": False}

    def generate_move(self, game_state, ids):
        castable = [
            w["action"]["instanceId"] for w in game_state.get_actions()
            if w["action"].get("actionType") in ("ActionType_Cast", "ActionType_Play")
        ]
        if len(castable) == 1:
            # a probe: one card left to answer about
            self.memory["land_played"] = True
            return {"cast": castable} if castable[0] in self.may_cast else {"resolve": []}
        return self.own

    def generate_keep(self, cards):
        return True


def bot_state(message):
    gsm = message["gameStateMessage"]
    return GameState({"turnInfo": gsm["turnInfo"], "actions": gsm["actions"], "gameObjects": gsm["gameObjects"]})


class MainPhaseTest(unittest.TestCase):
    def setUp(self):
        self.message = state_message(
            hand=[hand_card(100, 10, ("CardType_Land",)), hand_card(101, 20)],
            actions=[{"actionType": "ActionType_Play", "instanceId": 100},
                     {"actionType": "ActionType_Cast", "instanceId": 101}],
        )
        self.tracker = tracker_with(self.message)

    def choose(self, forge_answer, own, may_cast=()):
        bridge = bridge_with(self.tracker, {"main": forge_answer})
        ai = FakeAI(own, may_cast)
        move = bridge.choose_move(own, lambda s, i: probe_safely(ai, s, i), bot_state(self.message), {}, Config())
        return move, bridge, ai

    def test_forges_cast_is_made_when_the_bot_agrees(self):
        move, bridge, ai = self.choose({"action": "cast", "id": 101, "card": "Llanowar Elves"},
                                       {"cast": [100]}, may_cast={101})
        self.assertEqual(move, {"cast": [101]})
        kind, _state, extra = bridge.client.asked[0]
        self.assertEqual((kind, extra["castable"], extra["playable"]), ("main", [101], [100]))
        # The probe did not leave anything behind in the bot's memory.
        self.assertFalse(ai.memory["land_played"])

    def test_the_bots_move_stays_when_it_would_not_cast_forges_pick(self):
        move, _bridge, _ai = self.choose({"action": "cast", "id": 101}, {"cast": [100]}, may_cast=())
        self.assertEqual(move, {"cast": [100]})

    def test_free_forge_casts_when_legal_and_safe(self):
        bridge = bridge_with(self.tracker, {"main": {"action": "cast", "id": 101}})
        ai = FakeAI({"cast": [100]}, may_cast=())
        config = Config(free_forge=True)
        move = bridge.choose_move({"cast": [100]}, lambda s, i: probe_safely(ai, s, i),
                                  bot_state(self.message), {101: 20}, config)
        self.assertEqual(move, {"cast": [101]})

    def test_free_forge_fails_closed_when_card_id_missing_or_unsupported(self):
        bridge = bridge_with(self.tracker, {"main": {"action": "cast", "id": 101}})
        ai = FakeAI({"cast": [100]}, may_cast=())
        config = Config(free_forge=True)
        move = bridge.choose_move({"cast": [100]}, lambda s, i: probe_safely(ai, s, i),
                                  bot_state(self.message), {}, config)
        self.assertEqual(move, {"cast": [100]})

    def test_a_held_creature_is_cast_before_combat(self):
        answers = iter([{"action": "none"}, {"action": "cast", "id": 101}])
        move, bridge, _ai = self.choose(lambda state, extra: next(answers), {"resolve": []}, may_cast={101})
        self.assertEqual(move, {"cast": [101]})
        self.assertEqual([state["phase"] for _k, state, _e in bridge.client.asked], ["MAIN1", "MAIN2"])

    def test_shadow_mode_plays_the_bots_move(self):
        bridge = bridge_with(self.tracker, {"main": {"action": "cast", "id": 101}})
        ai = FakeAI({"cast": [100]}, may_cast={101})
        move = bridge.choose_move({"cast": [100]}, lambda s, i: probe_safely(ai, s, i),
                                  bot_state(self.message), {}, Config(mode="shadow"))
        self.assertEqual(move, {"cast": [100]})

    def test_forge_ai_falls_back_to_the_bot_on_any_failure(self):
        bridge = bridge_with(self.tracker, {"main": RuntimeError("advisor crashed")})
        ai = ForgeAI(FakeAI({"cast": [100]}), enabled=lambda: True, bridge=bridge, config=Config)
        self.assertEqual(ai.generate_move(bot_state(self.message), {}), {"cast": [100]})

    def test_forge_ai_is_the_bot_when_disabled(self):
        bridge = bridge_with(self.tracker, {"main": {"action": "cast", "id": 101}})
        ai = ForgeAI(FakeAI({"cast": [100]}, may_cast={101}), enabled=lambda: False, bridge=bridge, config=Config)
        self.assertEqual(ai.generate_move(bot_state(self.message), {}), {"cast": [100]})
        self.assertEqual(bridge.client.asked, [])

    def test_filtered_state_offers_one_card_as_our_main_phase(self):
        state = filtered_state(bot_state(self.message), 101, as_own_main_for=ME)
        kinds = [(w["action"]["actionType"], w["action"]["instanceId"]) for w in state.get_actions()]
        self.assertEqual(kinds, [("ActionType_Cast", 101)])
        self.assertEqual(state.get_turn_info()["phase"], "Phase_Main1")


class BlocksTest(unittest.TestCase):
    def setUp(self):
        attacker = creature(320, 30, 3, 3, OPP, attackState="AttackState_Attacking", attackInfo={"targetId": ME})
        message = state_message(active=OPP, phase="Phase_Combat", step="Step_DeclareBlock",
                                battlefield=[creature(310, 20, 1, 1, ME), attacker])
        self.tracker = tracker_with(message)
        self.legal = [{"blockerInstanceId": 310, "attackerInstanceIds": [320]}]

    def test_forges_blocks_replace_the_bots(self):
        bridge = bridge_with(self.tracker, {"block": {"blocks": [{"blocker": 310, "attacker": 320}], "names": ["x"]}})
        reviewed = ForgeHooks(bridge, Config).review_blocks(self.legal, {320}, {"assignments": [], "reason": "bot"})
        self.assertEqual(reviewed["assignments"], [[310, 320]])
        self.assertEqual(reviewed["reason"], "forge")
        _kind, _state, extra = bridge.client.asked[0]
        self.assertEqual(extra["legalBlocks"], {"310": [320]})
        self.assertEqual(extra["attackers"], [{"id": 320}])

    def test_the_bots_blocks_stay_when_forge_fails(self):
        bridge = bridge_with(self.tracker, {"block": RuntimeError("timeout")})
        own = {"assignments": [[310, 320]], "reason": "bot"}
        self.assertIs(ForgeHooks(bridge, Config).review_blocks(self.legal, {320}, own), own)

    def test_forge_empty_blocks_fallback_to_bot_assignments(self):
        bridge = bridge_with(self.tracker, {"block": {"blocks": [], "names": [], "notLegalInArena": ["X blocks Y"]}})
        own = {"assignments": [[310, 320]], "reason": "bot"}
        self.assertEqual(ForgeHooks(bridge, Config).review_blocks(self.legal, {320}, own), own)


class TargetsTest(unittest.TestCase):
    def intent(self, cards=(), players=()):
        return Intent("activate", 50, 7, list(cards), list(players), ["X"])

    def test_choice_maps_onto_the_bots_target_steps(self):
        self.assertEqual(choose_target(self.intent(cards=[5]), [5], [], False), ("creature", 5, False))
        self.assertEqual(choose_target(self.intent(cards=[6]), [5], [6], False), ("creature", 6, True))
        self.assertEqual(choose_target(self.intent(players=[1]), [], [], True), ("face",))
        self.assertIsNone(choose_target(self.intent(players=[1]), [], [], False))
        self.assertIsNone(choose_target(self.intent(cards=[9]), [5], [6], True))
        self.assertIsNone(choose_target(None, [5], [], True))

    def test_the_bot_picks_when_forge_has_no_intent(self):
        bridge = bridge_with(tracker_with(state_message()))
        calls = []
        ForgeHooks(bridge, Config).schedule_target(
            object(), lambda *a: calls.append(a), 44, "r", [5], True, [])
        self.assertEqual(calls, [(44, "r", [5], True, [])])


class FakeInput:
    def __init__(self):
        self.clicks = []

    def move_abs(self, x, y):
        self.pos = (x, y)

    def left_click(self, count=1):
        self.clicks.append(self.pos)


class FakeController:
    main_br_button_coordinates = (1776, 949)

    def __init__(self):
        self.input = FakeInput()
        self.selected = []
        self.submitted = 0

    def attack_target_required(self):
        return False

    def _map_abs_point_to_arena(self, point, **_kwargs):
        return point, "arena_relative_1920_direct"

    def select_battlefield_permanent(self, card_id, clicks=1):
        self.selected.append(card_id)
        return True

    def note_attack_submitted(self):
        self.submitted += 1

    def press_combat_button_verified(self, *a, **k):
        self.pressed = (a, k)
        return True


class AttackTest(unittest.TestCase):
    def setUp(self):
        battlefield = [creature(301, 20, 1, 1, ME), creature(302, 30, 2, 2, ME)]
        self.tracker = tracker_with(state_message(phase="Phase_Combat", step="Step_DeclareAttack",
                                                  battlefield=battlefield))
        self.tracker.feed_message({
            "type": "GREMessageType_DeclareAttackersReq", "systemSeatIds": [ME], "gameStateId": 10,
            "declareAttackersReq": {"attackers": [{"attackerInstanceId": 301}, {"attackerInstanceId": 302}]},
        })

    def run_attack(self, decision):
        bridge = bridge_with(self.tracker, {"attack": decision})
        bridge.sync = lambda: None
        controller = FakeController()
        own_calls = []
        answered = ForgeHooks(bridge, Config).all_attack(controller, lambda: own_calls.append(1) or True)
        return answered, controller, own_calls

    def test_everything_attacking_is_the_bots_own_attack(self):
        answered, controller, own = self.run_attack({
            "attackers": [{"id": 301}, {"id": 302}], "scores": {"pick": 5, "all": 5, "none": 1}})
        self.assertTrue(answered)
        self.assertEqual(own, [1])
        self.assertEqual(controller.selected, [])

    def test_nobody_attacking_presses_no_attacks_above_the_attack_button(self):
        answered, controller, own = self.run_attack({
            "attackers": [], "scores": {"pick": 9, "all": 1, "none": 9}})
        self.assertTrue(answered)
        self.assertEqual(own, [])
        (target, label), markers = controller.pressed
        self.assertEqual(target, (1776, 949 - hooks_module.NO_ATTACKS_ABOVE_ATTACK_BUTTON))
        self.assertEqual((label, markers["submitted_markers"]), ("FORGE_NO_ATTACKS", ["SubmitAttackersReq"]))
        self.assertEqual(controller.submitted, 1)

    def test_a_failed_plan_attacks_with_everything(self):
        answered, _controller, own = self.run_attack(RuntimeError("timeout"))
        self.assertEqual(own, [1])

    def test_a_second_concurrent_call_does_nothing(self):
        bridge = bridge_with(self.tracker)
        hooks = ForgeHooks(bridge, Config)
        hooks._attack_busy.acquire()
        try:
            own = []
            self.assertTrue(hooks.all_attack(FakeController(), lambda: own.append(1) or True))
            self.assertEqual(own, [])
        finally:
            hooks._attack_busy.release()

    def test_difference_counts_identical_creatures_by_kind(self):
        tracker = tracker_with(state_message(battlefield=[
            creature(1, 30, 2, 2, ME), creature(2, 30, 2, 2, ME), creature(3, 20, 1, 1, ME)]))
        # Arena marked the other bear of the same stack: that already counts.
        self.assertEqual(attack_difference(tracker, [1], [2]), ([], []))
        self.assertEqual(attack_difference(tracker, [1, 3], [2]), ([3], []))
        self.assertEqual(attack_difference(tracker, [3], [2]), ([3], [2]))


class GroupTest(unittest.TestCase):
    def test_scry_and_surveil_requests_are_read_from_the_line(self):
        message = {"type": "GREMessageType_GroupReq", "systemSeatIds": [ME],
                   "groupReq": {"context": "GroupingContext_Scry", "instanceIds": [7, 8]}}
        line = "x " + json.dumps({"greToClientEvent": {"greToClientMessages": [message]}})
        self.assertEqual(group_requests(line), [{"mode": "scry", "ids": [7, 8], "seats": [ME]}])

    def test_other_grouping_prompts_go_straight_to_the_bot(self):
        bridge = bridge_with(tracker_with(state_message()))
        seen = []
        message = {"type": "GREMessageType_GroupReq", "systemSeatIds": [ME],
                   "groupReq": {"context": "GroupingContext_Other", "instanceIds": [7]}}
        line = "x " + json.dumps({"greToClientEvent": {"greToClientMessages": [message]}})
        ForgeHooks(bridge, Config).handle_group_req(object(), line, seen.append)
        self.assertEqual(seen, [line])


class ControllerHookPointsTest(unittest.TestCase):
    """With no hooks set, the controller's steps are its own, unchanged."""

    def make(self):
        from Controller.MTGAController.Controller import Controller

        controller = Controller.__new__(Controller)
        return controller

    def test_all_attack_without_hooks_is_the_bots_own(self):
        controller = self.make()
        controller._all_attack_own = lambda: "own"
        self.assertEqual(controller.all_attack(), "own")

    def test_all_attack_with_hooks_goes_through_them(self):
        controller = self.make()
        controller._all_attack_own = lambda: "own"
        seen = []

        class Hooks:
            def all_attack(self, c, own):
                seen.append(c)
                return own()

        controller.decision_hooks = Hooks()
        self.assertEqual(controller.all_attack(), "own")
        self.assertEqual(seen, [controller])

    def test_activate_ability_with_hooks_goes_through_them(self):
        controller = self.make()
        calls = []
        controller._activate_ability_own = lambda card, ability: calls.append(("own", card, ability))

        class Hooks:
            def activate_ability(self, c, card, ability, own):
                calls.append(("hook", card, ability))
                own()

        controller.decision_hooks = Hooks()
        controller.activate_ability(5, 77)
        self.assertEqual(calls, [("hook", 5, 77), ("own", 5, 77)])


class ApplyTest(unittest.TestCase):
    def test_enabling_without_an_installed_engine_leaves_the_bot_alone(self):
        import ForgeEngine
        from ForgeEngine import paths

        class Controller:
            decision_hooks = "untouched"

        original = paths.installed
        paths.installed = lambda: False
        try:
            controller = Controller()
            self.assertFalse(ForgeEngine.apply(controller, True))
            self.assertIsNone(controller.decision_hooks)
        finally:
            paths.installed = original


if __name__ == "__main__":
    unittest.main()
