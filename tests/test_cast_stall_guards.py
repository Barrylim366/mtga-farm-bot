"""Unit tests for the three defects behind the 2026-08-02 stalls.

Three matches that day were lost to `ResultReason_Timeout`: the bot spent the
whole 150s inactivity timer trying to cast a card that was never in its hand.
The chain was

  1. `__update_inst_id__grp_id_dict` was insert-only, and MTGA recycles
     instanceIds -- 477 was our Mountain in one match and the opponent's
     Inspiration from Beyond (a Sorcery) in the next. The frozen first sighting
     made the AI decide to play a Mountain that did not exist.
  2. `cast()` then swept the hand for it, three times, ~6.6s per sweep -- and
     when the arena region could not be resolved the sweep ran over raw desktop
     coordinates ((0,1050) -> (1920,1050)), i.e. outside the game entirely.
  3. `cast()` reported nothing back, so the decision loop re-drove the same pick
     forever. `STUCK_ACTION_RETRY_LIMIT` could not stop it: its counter is keyed
     on turn/phase/step and resets the moment the step advances.

Nothing here touches a real screen or the runtime directory: the input and
log-reader layers are faked and the controller reads a temp log file.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import Game as GameModule
from Controller.MTGAController.Controller import Controller


class _Pos:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _FakeInput:
    """Cursor never moves and no hover ever arrives, so the hand scan exits via
    SCAN_STOPPED and _cast_once returns False without a real MTGA window."""

    def __init__(self):
        self._pos = _Pos(0, 0)
        self.moves = []

    def position(self):
        return self._pos

    def move_abs(self, x, y):
        self.moves.append((x, y))
        self._pos = _Pos(x, y)

    def move_rel(self, dx, dy):
        self._pos = _Pos(self._pos.x, self._pos.y)

    def left_click(self, n=1):
        pass


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c.input = _FakeInput()
    c._get_hand_scan_points_mapped = lambda **k: ((0, 0), (0, 0))
    c._ensure_options_overlay_closed = lambda **k: True
    c._write_hand_select_debug_bundle = lambda **k: None
    c.log_reader.has_new_line = lambda pattern: False
    c.log_reader.clear_new_line_flag = lambda pattern: None
    return c


def call_private(controller, name, *args):
    """Reach a name-mangled private method without hard-coding the mangling at
    every call site."""
    return getattr(controller, f"_Controller__{name}")(*args)


class InstanceIdRemapTest(unittest.TestCase):
    """MTGA recycles instanceIds. The live state is the authority on what an id
    is NOW; this map only has to bridge the gaps between messages."""

    def setUp(self):
        self.c = make_controller()

    def update(self, objects):
        call_private(self.c, "update_inst_id__grp_id_dict", objects)

    def test_a_fresh_sighting_overwrites_a_recycled_id(self):
        """The exact regression: 477 was a Mountain, then became the opponent's
        Sorcery. Freezing the Mountain is what invented the phantom land."""
        self.update([{"instanceId": 477, "grpId": 95197}])
        self.update([{"instanceId": 477, "grpId": 93756}])
        self.assertEqual(self.c.get_inst_id_grp_id_dict()[477], 93756)

    def test_an_id_missing_from_a_diff_keeps_its_grp_id(self):
        """GameStateType_Diff carries only what changed, so absence is not
        evidence the object is gone."""
        self.update([{"instanceId": 477, "grpId": 95197}, {"instanceId": 480, "grpId": 93756}])
        self.update([{"instanceId": 480, "grpId": 93756}])
        self.assertEqual(self.c.get_inst_id_grp_id_dict()[477], 95197)

    def test_a_hidden_object_does_not_blank_a_known_card(self):
        """Face-down/hidden objects arrive with grpId 0; that is not a new
        identity, and letting it win would erase a card we can identify."""
        self.update([{"instanceId": 477, "grpId": 95197}])
        self.update([{"instanceId": 477, "grpId": 0}])
        self.assertEqual(self.c.get_inst_id_grp_id_dict()[477], 95197)

    def test_a_malformed_object_is_skipped_not_raised_on(self):
        self.update([{"grpId": 95197}, {"instanceId": 478}, None, {"instanceId": 479, "grpId": 1}])
        self.assertEqual(self.c.get_inst_id_grp_id_dict(), {479: 1})

    def test_a_remapped_id_is_no_longer_suppressed(self):
        """The suppression was earned by the OLD card. This id is a different
        card now, so making it serve the old card's sentence would skip a cast
        that is perfectly legal."""
        self.update([{"instanceId": 477, "grpId": 95197}])
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            self.c.cast(477)
        self.assertTrue(self.c._is_cast_suppressed(477))
        self.update([{"instanceId": 477, "grpId": 93756}])
        self.assertFalse(self.c._is_cast_suppressed(477))


class CastSuppressionTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()

    def cast(self, card_id):
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            return self.c.cast(card_id)

    def test_a_failed_cast_reports_false(self):
        """Silence is what let the decision loop wait for a state change that was
        never coming."""
        self.assertIs(self.cast(999), False)

    def test_a_second_attempt_does_not_sweep_the_hand_again(self):
        """Each sweep is ~6.6s of rope spent on a card the hand does not hold."""
        self.cast(999)
        with patch.object(self.c, "_cast_once") as once:
            self.assertIs(self.cast(999), False)
        once.assert_not_called()

    def test_the_suppression_expires(self):
        """A card really in hand, missed once while the window was busy, has to
        get another honest try -- this must not become a permanent ban."""
        import time as _time
        self.cast(999)
        self.assertTrue(self.c._is_cast_suppressed(999))
        with patch("time.time", return_value=_time.time() + 10_000):
            self.assertFalse(self.c._is_cast_suppressed(999))

    def test_hovering_the_card_clears_the_suppression(self):
        """Seeing it proves it is reachable, so the earlier give-up was wrong."""
        self.cast(999)
        self.c.clear_cast_suppression(999)
        self.assertFalse(self.c._is_cast_suppressed(999))

    def test_a_new_game_clears_every_suppression(self):
        self.cast(999)
        call_private(self.c, "reset_live_game_state", "test")
        self.assertFalse(self.c._is_cast_suppressed(999))

    def test_a_successful_cast_returns_true(self):
        with patch.object(self.c, "_cast_once", return_value=True), \
             patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            self.assertIs(self.c.cast(999), True)


class HandScanRefusesDesktopTest(unittest.TestCase):
    """Unmapped scan points are raw desktop coordinates, so the sweep runs
    outside the game window and cannot hover anything -- three guaranteed
    failures and ~20s of rope. The click paths already refuse this."""

    def setUp(self):
        self.c = make_controller()
        self.c._get_hand_scan_points_mapped = lambda **k: (None, None)

    def test_cast_aborts_instead_of_scanning_the_desktop(self):
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False):
            self.assertIs(self.c._cast_once(999), False)
        self.assertEqual(self.c.input.moves, [], "the mouse was dragged across the desktop")

    def test_select_hand_card_aborts_too(self):
        self.assertIs(self.c.select_hand_card(999), False)
        self.assertEqual(self.c.input.moves, [])

    def test_select_hand_card_offset_aborts_too(self):
        self.assertIs(self.c.select_hand_card_offset(999), False)
        self.assertEqual(self.c.input.moves, [])

    def test_mapped_points_are_still_returned(self):
        c = make_controller()
        c._arena_region = (429, 156, 1920, 1080)
        p1, p2 = c._get_hand_scan_points_mapped()
        self.assertIsNotNone(p1)
        self.assertIsNotNone(p2)


class _StubController:
    """Only what Game.decision_method touches for a cast move."""

    def __init__(self, cast_result):
        self._cast_result = cast_result
        self.calls = []

    def cast(self, inst_id):
        self.calls.append(("cast", inst_id))
        return self._cast_result

    def resolve(self):
        self.calls.append(("resolve", None))

    def get_inst_id_grp_id_dict(self):
        return {}


class GamePassesPriorityOnUncastableTest(unittest.TestCase):
    """The loop breaker. STUCK_ACTION_RETRY_LIMIT cannot cover this: its counter
    is keyed on turn/phase/step, so an advancing step rearms it and the bot goes
    right back to the same phantom card."""

    def game(self, cast_result):
        g = GameModule.Game.__new__(GameModule.Game)
        g.controller = _StubController(cast_result)
        g._last_move_signature = None
        g._last_move_repeat_count = 0
        g._debug = lambda *a, **k: None
        g._get_card_id_str = lambda inst_id: str(inst_id)
        return g

    def execute_cast(self, g, inst_id=477):
        """The cast branch of decision_method, calling the real fallback rather
        than a copy of it -- a copy would stay green if the fallback were
        deleted from Game."""
        if g.controller.cast(inst_id) is False:
            g._pass_priority_on_uncastable(inst_id, 1, "Phase_Main1", "Step_Draw", 2)

    def test_a_failed_cast_passes_priority(self):
        g = self.game(False)
        self.execute_cast(g)
        self.assertEqual(g.controller.calls, [("cast", 477), ("resolve", None)])

    def test_the_pass_is_what_the_breaker_records(self):
        """Leaving the failed cast's signature in place would let the breaker
        count a move that never actually ran."""
        g = self.game(False)
        self.execute_cast(g)
        self.assertEqual(g._last_move_signature[4], "resolve")
        self.assertEqual(g._last_move_repeat_count, 1)

    def test_a_successful_cast_does_not_pass_priority(self):
        g = self.game(True)
        self.execute_cast(g)
        self.assertEqual(g.controller.calls, [("cast", 477)])

    def test_a_controller_that_returns_none_keeps_the_old_behaviour(self):
        """`is False` and not falsiness: an older controller reporting nothing
        must not be read as a failure and made to pass priority."""
        g = self.game(None)
        self.execute_cast(g)
        self.assertEqual(g.controller.calls, [("cast", 477)])


if __name__ == "__main__":
    unittest.main()
