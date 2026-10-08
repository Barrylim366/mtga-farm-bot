"""Choosing which creatures attack.

    python -m unittest forge_tests.test_attack_hook -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge_bridge import attack_hook, forge_ai  # noqa: E402
from forge_bridge.carddb import CardDb  # noqa: E402
from forge_bridge.tracker import ArenaTracker  # noqa: E402
from forge_tests import gre  # noqa: E402

CARDS = CardDb({"cards": {str(grp): {"name": name} for grp, name in gre.NAMES.items()}, "counters": {}})
BEAR_A, BEAR_B, GIANT = 301, 302, 303


class Advisor:
    running = True

    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def decide(self, kind, state, timeout=10.0, **extra):
        self.calls += 1
        return {"decision": self.decision, "diag": {}}


class Controller:
    """What the hook needs from the bot's controller, recording what it was asked to do."""

    main_br_button_coordinates = (1755, 944)

    def __init__(self, unclickable=()):
        self.selected = []
        self.pressed = []
        self.clicks = []
        self.unclickable = set(unclickable)

    def select_battlefield_permanent(self, card_id, clicks=1):
        if card_id in self.unclickable:
            return False
        self.selected.append(card_id)
        return True

    def _map_abs_point_to_arena(self, point, **kwargs):
        return point, "test"

    def _Controller__press_combat_button_verified(self, target, label, **kwargs):
        self.pressed.append(label)
        return True

    def _click_abs(self, x, y, tag):
        self.clicks.append((x, y, tag))


class AttackHookTests(unittest.TestCase):
    def setUp(self):
        for name in ("MTGA_FORGE", "MTGA_FORGE_ATTACK"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)
        self.addCleanup(setattr, attack_hook, "NO_ATTACKS_POINT", attack_hook.NO_ATTACKS_POINT)
        forge_ai.FEED.clear()
        self.tracker = ArenaTracker()
        self.tracker.feed_message(gre.connect())
        self.tracker.feed_message(gre.state_message(
            phase="Phase_Combat", step="Step_DeclareAttack",
            battlefield=[gre.creature(BEAR_A, 1, 2, 2), gre.creature(BEAR_B, 1, 2, 2), gre.creature(GIANT, 2, 3, 3)],
        ))
        self.tracker.feed_message({
            "type": "GREMessageType_DeclareAttackersReq", "systemSeatIds": [gre.ME], "gameStateId": 10,
            "declareAttackersReq": {"attackers": [{"attackerInstanceId": i} for i in (BEAR_A, BEAR_B, GIANT)]},
        })
        self.all_attacks = 0

    def original(self, controller):
        self.all_attacks += 1
        return True

    def run_attack(self, picked, scores, controller=None):
        decision = {
            "attackers": [{"id": i} for i in picked], "names": [f"creature {i}" for i in picked], "scores": scores,
        }
        self.advisor = Advisor(decision)
        self.bridge = forge_ai.Bridge(client=self.advisor, cards=CARDS, tracker=self.tracker)  # type: ignore[arg-type]
        self.controller = controller or Controller()
        return attack_hook.wrap(self.original, self.bridge)(self.controller)

    def test_attacks_with_only_the_creatures_forge_picked(self):
        self.run_attack([GIANT], {"pick": 500, "all": 300, "none": 400})
        self.assertEqual(self.controller.selected, [GIANT])
        self.assertEqual(self.controller.pressed, ["ATTACK_SELECTED"])
        self.assertEqual(self.all_attacks, 0)
        self.assertIn("Holding back", forge_ai.FEED[-1][2])

    def test_everything_attacking_uses_the_bots_own_attack(self):
        self.run_attack([BEAR_A, BEAR_B, GIANT], {"pick": 500, "all": 500, "none": 100})
        self.assertEqual((self.all_attacks, self.controller.selected), (1, []))

    def test_playout_can_overrule_forges_pick_in_favour_of_everything(self):
        self.run_attack([GIANT], {"pick": 300, "all": 900, "none": 100})
        self.assertEqual((self.all_attacks, self.controller.selected), (1, []))

    def test_no_attack_presses_the_no_attacks_button_once_it_is_located(self):
        attack_hook.NO_ATTACKS_POINT = (1500, 944)
        self.run_attack([], {"pick": 400, "all": 100, "none": 400})
        self.assertEqual(self.controller.clicks, [(1500, 944, "NO_ATTACKS")])
        self.assertEqual(self.all_attacks, 0)

    def test_no_attack_without_a_located_button_falls_back_and_says_so(self):
        attack_hook.NO_ATTACKS_POINT = None
        self.run_attack([], {"pick": 400, "all": 100, "none": 400})
        self.assertEqual(self.all_attacks, 1)
        self.assertIn("cannot decline yet", forge_ai.FEED[-1][2])

    def test_a_chosen_attacker_that_cannot_be_clicked_stays_home(self):
        self.run_attack([BEAR_A, GIANT], {"pick": 500, "all": 300, "none": 400}, Controller(unclickable={BEAR_A}))
        self.assertEqual(self.controller.selected, [GIANT])
        self.assertEqual(self.controller.pressed, ["ATTACK_SELECTED"])

    def test_if_nothing_can_be_clicked_everything_attacks(self):
        self.run_attack([GIANT], {"pick": 500, "all": 300, "none": 400}, Controller(unclickable={GIANT}))
        self.assertEqual(self.all_attacks, 1)

    def test_the_plan_is_worked_out_once_per_turn(self):
        self.run_attack([GIANT], {"pick": 500, "all": 300, "none": 400})
        attack_hook.wrap(self.original, self.bridge)(self.controller)
        self.assertEqual(self.advisor.calls, 1)

    def test_shadow_mode_leaves_the_bots_attack_alone(self):
        os.environ["MTGA_FORGE_ATTACK"] = "shadow"
        self.run_attack([GIANT], {"pick": 500, "all": 300, "none": 400})
        self.assertEqual((self.all_attacks, self.controller.selected), (1, []))

    def test_an_advisor_failure_means_the_bots_attack(self):
        self.bridge = forge_ai.Bridge(client=None, cards=CARDS, tracker=self.tracker)
        controller = Controller()
        from unittest import mock

        with mock.patch.object(forge_ai.Bridge, "start_async"):
            attack_hook.wrap(self.original, self.bridge)(controller)
        self.assertEqual(self.all_attacks, 1)


if __name__ == "__main__":
    unittest.main()
