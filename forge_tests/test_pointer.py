"""The battlefield cursor sweep, against a simulated board whose hover reports arrive late.

    python -m unittest forge_tests.test_pointer -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge_bridge import pointer  # noqa: E402


class FakeBoard:
    """Cards as x-ranges on one row. Moving onto a card logs its id -- optionally 0.12 s later."""

    DELAY = 0.12

    def __init__(self, cards: dict[int, tuple[int, int]], late: bool = False) -> None:
        self.cards = cards
        self.late = late
        self.position = (0, 0)
        self.hovering = None
        self.pending: list[list] = []  # [seconds still to wait, id] for reports on their way
        self.lines: list[int] = []
        self.clicks: list[tuple[int, int]] = []
        self.moves: list[int] = []

    # --- the parts of the controller the sweep uses
    patterns = {"hover_id": "objectId"}
    screen_bounds = ((0, 0), (1920, 1080))
    _Controller__live_match_id = "m"

    @property
    def log_reader(self):
        return self

    @property
    def input(self):
        return self

    def can_execute_game_action(self, match_id=None) -> bool:
        return True

    def _Controller__parse_hover_id_line(self, line):
        return line

    def clear_new_line_flag(self, pattern) -> None:
        self.lines.clear()

    def has_new_line(self, pattern) -> bool:
        return bool(self.lines)

    def get_latest_line_containing_pattern(self, pattern):
        return self.lines.pop(0)

    def sleep(self, seconds) -> None:
        for report in self.pending:
            report[0] -= seconds
        self.lines.extend(report[1] for report in self.pending if report[0] <= 1e-9)
        self.pending = [report for report in self.pending if report[0] > 1e-9]

    def move_abs(self, x, y) -> None:
        self.position = (x, y)
        self.moves.append(x)
        under = next((cid for cid, (lo, hi) in self.cards.items() if lo <= x <= hi and 300 <= y <= 400), None)
        if under != self.hovering:
            self.hovering = under
            if under is not None:
                if self.late:
                    self.pending.append([self.DELAY, under])
                else:
                    self.lines.append(under)

    def left_click(self, count=1) -> None:
        self.clicks.append(self.position)

    def clicked_card(self):
        x, _ = self.clicks[-1]
        return next((cid for cid, (lo, hi) in self.cards.items() if lo <= x <= hi), None)


def sweep(board: FakeBoard, card_id: int) -> bool:
    with mock.patch.object(pointer.time, "sleep", side_effect=board.sleep):
        return pointer.find_and_click(
            board, card_id, (190, 300), (1760, 400), 55, 1, "OPP_BATTLEFIELD_ITEM", None, lambda message: None
        )


class PointerTests(unittest.TestCase):
    def test_centre_out_order(self):
        self.assertEqual(pointer.centre_out(0, 40, 10), [20, 10, 30, 0, 40])

    def test_clicks_the_wanted_card(self):
        board = FakeBoard({7: (900, 1000), 8: (1010, 1110)})
        self.assertTrue(sweep(board, 8))
        self.assertEqual(board.clicked_card(), 8)

    def test_a_late_report_does_not_put_the_click_on_the_neighbour(self):
        # every hover line shows up after the cursor has moved on, which is what made the old sweep
        # click a step too far
        board = FakeBoard({7: (900, 1000), 8: (1010, 1110), 9: (1120, 1220)}, late=True)
        for wanted in (7, 8, 9):
            board.clicks.clear()
            self.assertTrue(sweep(board, wanted), wanted)
            self.assertEqual(board.clicked_card(), wanted)

    def test_a_card_near_the_middle_is_found_in_fewer_moves_than_from_the_left(self):
        board = FakeBoard({8: (930, 1030)})
        self.assertTrue(sweep(board, 8))
        left_to_right = len(range(190, 930, 55))
        self.assertLess(len(board.moves), left_to_right)

    def test_missing_card_reports_failure_without_clicking(self):
        board = FakeBoard({7: (900, 1000)})
        self.assertFalse(sweep(board, 99))
        self.assertEqual(board.clicks, [])

    def test_only_battlefield_sweeps_are_replaced(self):
        calls = []
        wrapped = pointer.wrap(lambda self, **kwargs: calls.append(kwargs["label"]) or True)
        self.assertTrue(wrapped(FakeBoard({}), 1, (0, 0), (10, 10), 10, 1, "STACK_ITEM"))
        self.assertEqual(calls, ["STACK_ITEM"])

    def test_switched_off(self):
        os.environ["MTGA_FORGE_POINTER"] = "off"
        self.addCleanup(os.environ.pop, "MTGA_FORGE_POINTER", None)
        calls = []
        wrapped = pointer.wrap(lambda self, **kwargs: calls.append(kwargs["label"]) or True)
        wrapped(FakeBoard({}), 1, (0, 0), (10, 10), 10, 1, "OPP_BATTLEFIELD_ITEM")
        self.assertEqual(calls, ["OPP_BATTLEFIELD_ITEM"])


if __name__ == "__main__":
    unittest.main()
