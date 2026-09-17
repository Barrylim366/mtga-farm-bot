"""Unit test for Finding 2: the "Are You Sure?" dialog probe in cast() must be
reactive (only run after a failed attempt), not speculative (run at the top of
every _cast_once, i.e. up to 3x per cast() call).

Drives Controller.cast()/_cast_once with the input/log-reader layers faked out
so no real screen interaction happens; only the call pattern around
_dismiss_are_you_sure_if_present is asserted.
"""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState


class _Pos:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _FakeInput:
    """Minimal stand-in for the real input controller: cursor never moves off
    the scan start point and no hover line ever arrives, so the hand scan
    exits quickly via SCAN_STOPPED / SCAN_FAILED and _cast_once returns False
    without needing a real MTGA window."""

    def __init__(self):
        self._pos = _Pos(0, 0)

    def position(self):
        return self._pos

    def move_abs(self, x, y):
        self._pos = _Pos(x, y)

    def move_rel(self, dx, dy):
        self._pos = _Pos(self._pos.x, self._pos.y)

    def left_click(self, n=1):
        pass


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    controller = Controller(f.name)
    controller._Controller__live_match_id = "test-match"
    controller._Controller__last_seen_match_id = "test-match"
    controller._get_state_from_log = lambda: BotState.IN_GAME
    return controller


class CastProbeIsReactiveTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        self.c.input = _FakeInput()
        # Hand scan bounds equal (p1 == p2): the scan loop's bounds check
        # trips immediately on the first iteration, so _cast_once returns
        # False fast without a real screen.
        self.c._get_hand_scan_points_mapped = lambda **k: ((0, 0), (0, 0))
        self.c._ensure_options_overlay_closed = lambda **k: True
        self.c._write_hand_select_debug_bundle = lambda **k: None
        self.c.log_reader.has_new_line = lambda pattern: False
        self.c.log_reader.clear_new_line_flag = lambda pattern: None

    def test_cast_once_does_not_probe_are_you_sure(self):
        with patch.object(self.c, "_dismiss_are_you_sure_if_present") as probe, \
             patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False):
            result = self.c._cast_once(999)
        self.assertFalse(result)
        probe.assert_not_called()

    def test_cast_probes_reactively_between_retries_not_up_front(self):
        with patch.object(self.c, "_dismiss_are_you_sure_if_present") as probe, \
             patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            self.c.cast(999)
        # 3 attempts -> 2 retries -> probe runs exactly twice (once after each
        # failed attempt, before the next retry), never before the 1st.
        self.assertEqual(probe.call_count, 2)


if __name__ == "__main__":
    unittest.main()
