"""Unit tests for smart selective attacking via CombatLogic."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.test_combat_shadow import make_controller, cleanup


class SelectiveAttackTests(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        self.addCleanup(cleanup, self.controller)

    def test_selective_attack_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.controller._Controller__combat_selective_attack_enabled())

    def test_selective_attack_enabled_by_env(self):
        with mock.patch.dict(os.environ, {"MTGA_SELECTIVE_ATTACK": "1"}):
            self.assertTrue(self.controller._Controller__combat_selective_attack_enabled())

    def test_selective_attack_holds_back_and_submits(self):
        with mock.patch.dict(os.environ, {"MTGA_SELECTIVE_ATTACK": "1"}):
            # Decision holds back creature 20, attacks with 10
            decision = {
                "attackers": [10],
                "hold_back": [20],
                "reason": "per_creature_safety",
            }
            self.controller._Controller__last_attack_decision = decision
            with mock.patch.object(self.controller, "select_battlefield_permanent", return_value=True) as mock_select:
                with mock.patch.object(self.controller, "_Controller__press_combat_button_verified", return_value=True) as mock_press:
                    with mock.patch.object(self.controller, "_map_abs_point_to_arena", return_value=((100, 200), "mock")):
                        ok = self.controller._all_attack_own()
                        self.assertTrue(ok)
                        mock_select.assert_called_once_with(10, 1)
                        self.assertEqual(mock_press.call_args[0][1], "ATTACK_SELECTED")

    def test_selective_attack_no_attackers_submits_no_attacks(self):
        with mock.patch.dict(os.environ, {"MTGA_SELECTIVE_ATTACK": "1"}):
            # Decision holds back all creatures
            decision = {
                "attackers": [],
                "hold_back": [10, 20],
                "reason": "life_guard",
            }
            self.controller._Controller__last_attack_decision = decision
            with mock.patch.object(self.controller, "_Controller__press_combat_button_verified", return_value=True) as mock_press:
                with mock.patch.object(self.controller, "_map_abs_point_to_arena", return_value=((100, 150), "mock")):
                    ok = self.controller._all_attack_own()
                    self.assertTrue(ok)
                    self.assertEqual(mock_press.call_args[0][1], "NO_ATTACKS")
