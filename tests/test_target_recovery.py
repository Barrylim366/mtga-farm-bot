import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.MTGAController.Controller import BotState


class _State:
    def get_annotations(self):
        return []


class TargetCastGuardTest(unittest.TestCase):
    def controller(self):
        c = Controller.__new__(Controller)
        c._Controller__live_match_id = "match"
        c._Controller__last_seen_match_id = "match"
        c._Controller__system_seat_id = 1
        c._get_state_from_log = lambda: BotState.IN_GAME
        c._Controller__pending_target_select = None
        c.updated_game_state = _State()
        return c

    def test_pending_target_transaction_blocks_casts(self):
        c = self.controller()
        c._Controller__pending_target_select = {
            "source_id": 42, "token": 1, "last_target": 99,
        }
        self.assertTrue(c.should_defer_cast_for_target_selection("match"))

    def test_no_target_transaction_allows_casts(self):
        self.assertFalse(self.controller().should_defer_cast_for_target_selection("match"))


if __name__ == "__main__":
    unittest.main()
