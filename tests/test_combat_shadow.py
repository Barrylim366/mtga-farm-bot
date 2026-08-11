"""Phase 1 combat runs in SHADOW MODE: it decides, logs, and changes nothing.

These tests pin that contract. The value of shadow mode is that it can be left
running during real farming sessions to collect evidence, so the two things that
must hold are:

  1. the decision is computed from the game's own request payload and recorded
     (in bot.log and in the decision snapshot's `extra`), and
  2. the executed behaviour is still exactly what it was -- all-attack and
     no-blocks -- so a bug in CombatLogic cannot cost a match.

Controller uses name-mangled double-underscore attributes; from outside the
class body they must be accessed as `_Controller__name`.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState

MY_SEAT = 1
OPP_SEAT = 2
BATTLEFIELD_ZONE = 28


def make_controller() -> Controller:
    handle = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    handle.close()
    controller = Controller(handle.name)
    controller._test_log_path = handle.name
    controller._Controller__system_seat_id = MY_SEAT
    return controller


def isolate_debug_captures(testcase, controller: Controller) -> str:
    """Keep a test's declare-block bundles out of the real runtime/debug.

    Two separate hazards, both of which this suite hit for real: the bundle
    writer creates directories under the bot's own debug root and buries the
    captures a live session produced, and it grabs the screen -- so an
    unisolated test photographs whatever the user happens to be doing. Returns
    the temporary root in case a test wants to inspect what was written.
    """
    controller._vision = None
    debug_root = tempfile.mkdtemp(prefix="declare-block-test-")
    testcase.addCleanup(shutil.rmtree, debug_root, True)

    def _fake_debug_dir(subdir=None):
        path = Path(debug_root) / (subdir or "")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    patcher = mock.patch(
        "Controller.MTGAController.Controller.bot_logger.ensure_debug_dir",
        side_effect=_fake_debug_dir,
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return debug_root


def cleanup(controller: Controller) -> None:
    for attr in (
        "_Controller__inactivity_timer",
        "_Controller__decision_execution_thread",
        "_Controller__decision_heartbeat_timer",
    ):
        timer = getattr(controller, attr, None)
        if timer is not None and hasattr(timer, "cancel"):
            timer.cancel()
    path = getattr(controller, "_test_log_path", None)
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def creature(instance_id, seat, power=2, toughness=2, *, attacking_seat=None):
    obj = {
        "instanceId": instance_id,
        "grpId": 93824,
        "zoneId": BATTLEFIELD_ZONE,
        "controllerSeatId": seat,
        "ownerSeatId": seat,
        "cardTypes": ["CardType_Creature"],
        "power": {"value": power},
        "toughness": {"value": toughness},
    }
    if attacking_seat is not None:
        obj["attackState"] = "AttackState_Attacking"
        obj["attackInfo"] = {"targetId": attacking_seat}
    return obj


def seed_board(controller, game_objects, *, my_life=20, opp_life=20, step="Step_DeclareBlock"):
    controller.updated_game_state = GameState({
        "turnInfo": {
            "turnNumber": 7,
            "phase": "Phase_Combat",
            "step": step,
            "activePlayer": OPP_SEAT,
            "priorityPlayer": MY_SEAT,
            "decisionPlayer": MY_SEAT,
        },
        "timers": [],
        "gameObjects": list(game_objects),
        "players": [
            {"systemSeatNumber": MY_SEAT, "lifeTotal": my_life},
            {"systemSeatNumber": OPP_SEAT, "lifeTotal": opp_life},
        ],
        "annotations": [],
        "actions": [],
        "zones": [{
            "zoneId": BATTLEFIELD_ZONE,
            "type": "ZoneType_Battlefield",
            "objectInstanceIds": [o["instanceId"] for o in game_objects],
        }],
    })


def blockers_req_line(*pairs, declared_attackers=()) -> str:
    """A Player.log line carrying a DeclareBlockersReq, as the handler sees it.

    The GRE bundles the game-state diffs that declared the combat into the same
    message, which is where the handler reads who is attacking right now;
    `declared_attackers` reproduces that.
    """
    messages = []
    if declared_attackers:
        messages.append({
            "type": "GREMessageType_GameStateMessage",
            "gameStateMessage": {
                "type": "GameStateType_Diff",
                "gameObjects": [
                    {"instanceId": instance_id, "attackState": "AttackState_Attacking"}
                    for instance_id in declared_attackers
                ],
            },
        })
    messages.append({
        "type": "GREMessageType_DeclareBlockersReq",
        "systemSeatIds": [MY_SEAT],
        "declareBlockersReq": {
            "blockers": [
                {
                    "blockerInstanceId": blocker,
                    "attackerInstanceIds": list(attackers),
                    "maxAttackers": 1,
                }
                for blocker, attackers in pairs
            ],
        },
    })
    payload = {"greToClientEvent": {"greToClientMessages": messages}}
    return "[UnityCrossThreadLogger] Match to X: GreToClientEvent " + json.dumps(payload)


class ShadowBlockDecisionTest(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        # The blocker handler declares no-blocks by clicking; nothing in this
        # test should ever reach the mouse.
        self.controller._suppress_selections = False
        isolate_debug_captures(self, self.controller)
        self.addCleanup(cleanup, self.controller)

    def test_the_decision_is_recorded_even_when_it_is_not_executed(self):
        """Blocking can be switched off (MTGA_COMBAT_BLOCKS=0) without losing the
        evidence: the block that *would* have been made is still logged, which is
        what makes the off state useful for diagnosis rather than just inert."""
        attacker = creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 6, 6)
        seed_board(self.controller, [attacker, blocker], my_life=20)

        with mock.patch.dict(os.environ, {"MTGA_COMBAT_BLOCKS": "0"}), \
                mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch("threading.Timer") as timer:
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )

        record.assert_called_once()
        args, kwargs = record.call_args
        self.assertEqual(args[0], "blockers")
        self.assertEqual(args[1], "no_blocks")
        timer.assert_called_once()

        shadow = kwargs["extra"]["combat_shadow"]
        self.assertEqual(shadow["assignments"], [(20, 10)])
        self.assertEqual(shadow["detail"][0]["outcome"], "kill_and_survive")
        self.assertEqual(shadow["my_life"], 20)

    def test_shadow_sees_lethal_pressure(self):
        attacker = creature(10, OPP_SEAT, 4, 4, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 1, 1)
        seed_board(self.controller, [attacker, blocker], my_life=3)

        with mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch("threading.Timer"):
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )

        shadow = record.call_args.kwargs["extra"]["combat_shadow"]
        self.assertTrue(shadow["lethal_without_blocks"])
        self.assertEqual(shadow["assignments"], [(20, 10)])  # chump to survive
        # grpId 93824 has trample: the 1/1 chump still leaks three damage.
        self.assertEqual(shadow["unblocked_damage"], 3)

    def test_unblockable_attacker_is_read_from_the_bundled_diff(self):
        """A flier nothing of ours can block is absent from the block graph, so
        the handler has to pick it up from the diffs shipped alongside the
        request or the lethal check misses it."""
        flier = creature(10, OPP_SEAT, 4, 4, attacking_seat=MY_SEAT)
        ground = creature(11, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 1, 1)
        seed_board(self.controller, [flier, ground, blocker], my_life=5)

        with mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch("threading.Timer"):
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [11]), declared_attackers=(10, 11))
            )

        shadow = record.call_args.kwargs["extra"]["combat_shadow"]
        self.assertEqual(shadow["incoming_damage"], 6)
        self.assertTrue(shadow["lethal_without_blocks"])
        self.assertEqual(shadow["assignments"], [(20, 11)])

    def test_a_broken_combat_decision_cannot_break_the_handler(self):
        """The whole point of shadow mode: CombatLogic is new code sitting on the
        rope. If it throws, the bot must still declare no blocks."""
        seed_board(self.controller, [creature(10, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)])

        with mock.patch(
            "AI.Utilities.CombatLogic.choose_blocks", side_effect=RuntimeError("boom")
        ), mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch("threading.Timer") as timer:
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )

        record.assert_called_once()
        self.assertEqual(record.call_args[0][1], "no_blocks")
        timer.assert_called_once()

    def test_shadow_can_be_switched_off(self):
        seed_board(self.controller, [creature(10, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)])
        with mock.patch.dict(os.environ, {"MTGA_COMBAT_SHADOW": "0"}), \
                mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch("threading.Timer"):
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )
        self.assertIsNone(record.call_args.kwargs["extra"])


class ShadowAttackDecisionTest(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        self.addCleanup(cleanup, self.controller)

    def test_shadow_attack_reads_the_games_legal_attacker_list(self):
        mine = creature(20, MY_SEAT, 2, 2)
        theirs = creature(10, OPP_SEAT, 4, 4)
        seed_board(self.controller, [mine, theirs], my_life=20, step="Step_DeclareAttack")

        legal = [{
            "attackerInstanceId": 20,
            "legalDamageRecipients": [
                {"type": "DamageRecType_Player", "playerSystemSeatId": OPP_SEAT}
            ],
        }]
        shadow = self.controller._Controller__shadow_attack_decision(legal)

        # A 2/2 into an untapped 4/4 just dies: shadow says hold it back, and
        # the live bot still swings with everything.
        self.assertEqual(shadow["attackers"], [])
        self.assertEqual(shadow["hold_back"], [20])

    def test_shadow_attack_survives_a_broken_decision(self):
        seed_board(self.controller, [creature(20, MY_SEAT, 2, 2)], step="Step_DeclareAttack")
        with mock.patch(
            "AI.Utilities.CombatLogic.choose_attackers", side_effect=RuntimeError("boom")
        ):
            self.assertIsNone(
                self.controller._Controller__shadow_attack_decision(
                    [{"attackerInstanceId": 20}]
                )
            )

    def test_no_seat_id_yet_is_not_an_error(self):
        self.controller._Controller__system_seat_id = None
        self.assertIsNone(self.controller._Controller__shadow_attack_decision([]))


if __name__ == "__main__":
    unittest.main()
