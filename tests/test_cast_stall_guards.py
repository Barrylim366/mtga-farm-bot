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
    """Cursor moves only as told and no hover ever arrives, so the hand scan
    exits via SCAN_STOPPED and _cast_once returns False without a real MTGA
    window."""

    def __init__(self):
        self._pos = _Pos(0, 0)
        self.moves = []

    def position(self):
        return self._pos

    def move_abs(self, x, y):
        self.moves.append((x, y))
        self._pos = _Pos(x, y)

    def move_rel(self, dx, dy):
        self._pos = _Pos(self._pos.x + dx, self._pos.y + dy)

    def left_click(self, n=1):
        pass


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c.input = _FakeInput()
    # A real span, not a degenerate point: with p1 == p2 the sweep exits on its
    # first bounds check without ever moving, so "the sweep saw nothing" would
    # be true for the wrong reason and no test would exercise the sweep at all.
    c._get_hand_scan_points_mapped = lambda **k: ((0, 0), (200, 0))
    c._ensure_options_overlay_closed = lambda **k: True
    c._write_hand_select_debug_bundle = lambda **k: None
    c.log_reader.has_new_line = lambda pattern: False
    c.log_reader.clear_new_line_flag = lambda pattern: None
    # Recovering from a blind sweep needs a real arena region and a real click;
    # neither exists here. Tests that care about it override this.
    c._reactivate_arena_window = lambda **k: False
    return c


def sweep_sees_hand(c, hover_ids=(111,)) -> None:
    """Let the fake sweep observe MTGA reporting these hovers, in order.

    A sweep that hovers SOMETHING -- even a card we were not looking for -- is
    the only kind that says anything about the hand's contents. Without this the
    fixture's sweeps are blind (MTGA never processed the mouse), which is a
    different failure and deliberately no longer earns a suppression."""
    queue: list[str] = []

    def start_of_sweep(pattern):
        # Every sweep crosses the hand row afresh, so every sweep sees these
        # hovers again. Hooked on the flag clear because the hand sweeps clear it
        # exactly once, up front -- note __select_object_in_region clears it per
        # grid cell instead, so this helper does not model that scan.
        queue[:] = [f'"objectId": {int(i)}' for i in hover_ids]

    c.log_reader.clear_new_line_flag = start_of_sweep
    c.log_reader.has_new_line = lambda pattern: bool(queue)
    c.log_reader.get_latest_line_containing_pattern = lambda pattern: queue.pop(0)


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
        sweep_sees_hand(self.c)
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            self.c.cast(477)
        self.assertTrue(self.c._is_cast_suppressed(477))
        self.update([{"instanceId": 477, "grpId": 93756}])
        self.assertFalse(self.c._is_cast_suppressed(477))


class CastSuppressionTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        # The suppression is evidence-based: it only applies to sweeps that
        # actually observed the hand. See BlindSweepTest for the other case.
        sweep_sees_hand(self.c)

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


class BlindSweepTest(unittest.TestCase):
    """A sweep that sees zero hovers is not evidence about the hand.

    The 2026-08-21 session: from 15:56:58 on, every cast sweep reported
    SCAN_STOPPED with not one hover event, because MTGA's window was no longer
    the active one and Unity only emits hover events for the active window. The
    bot swept the hand blind for ten minutes, passed priority every turn, and
    lost a match it was winning -- while marking each card 'unreachable' on
    evidence it never had."""

    def setUp(self):
        self.c = make_controller()
        self.reactivations = []
        self.c._reactivate_arena_window = lambda **k: (
            self.reactivations.append(k.get("context")) or False
        )

    def cast(self, card_id):
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            return self.c.cast(card_id)

    def test_a_blind_sweep_does_not_suppress_the_card(self):
        self.assertIs(self.cast(999), False)
        self.assertFalse(
            self.c._is_cast_suppressed(999),
            "a card was banned on the strength of a sweep that observed nothing",
        )

    def test_a_blind_sweep_tries_to_reactivate_the_window(self):
        """Sweeping harder cannot fix input never reaching the game."""
        self.cast(999)
        self.assertEqual(
            len(self.reactivations), 1,
            "recovery is once per cast: if the window did not come back the first "
            "time, clicking at it again only burns rope",
        )

    def test_the_sweep_evidence_says_blind(self):
        self.cast(999)
        self.assertEqual(self.c._last_sweep_evidence, "blind")

    def test_a_sweep_that_never_ran_is_not_called_blind(self):
        """The distinction that matters. An early exit -- a stuck options
        overlay, a missing arena region, another flow signalling the scan to
        stand down -- says nothing about whether MTGA is answering. Treating it
        as blind would fire the recovery CLICK into the prompt whose handler
        just asked the scan to get out of the way."""
        self.c._ensure_options_overlay_closed = lambda **k: False
        self.assertIs(self.cast(999), False)
        self.assertEqual(self.c._last_sweep_evidence, "unknown")
        self.assertEqual(self.reactivations, [], "clicked while a prompt was up")

    def test_an_aborted_sweep_is_not_called_blind(self):
        """__group_req_active_until is how the scry/modal handlers tell a running
        scan to stop moving the mouse."""
        import time as _time
        self.c._Controller__group_req_active_until = _time.time() + 6.0
        self.assertIs(self.cast(999), False)
        self.assertEqual(self.c._last_sweep_evidence, "unknown")
        self.assertEqual(self.reactivations, [])

    def test_a_concurrent_hover_scan_makes_the_verdict_inconclusive(self):
        """The hover queue is shared and the selection flows run their scans from
        timer threads. One draining the queue under our sweep must not be read as
        'MTGA is unresponsive'."""
        real = self.c.log_reader.has_new_line

        def has_new_line(pattern):
            self.c._note_hover_scan("OTHER")  # stand-in for a concurrent scan
            return real(pattern)

        self.c.log_reader.has_new_line = has_new_line
        self.cast(999)
        self.assertEqual(self.c._last_sweep_evidence, "unknown")
        self.assertEqual(self.reactivations, [])

    def test_a_sweep_that_saw_the_hand_is_left_alone(self):
        """The window is demonstrably live, so there is nothing to reactivate."""
        sweep_sees_hand(self.c)
        self.cast(999)
        self.assertEqual(self.reactivations, [])
        self.assertTrue(self.c._is_cast_suppressed(999))

    def test_reactivation_refuses_to_click_the_desktop(self):
        c = make_controller()
        c._ensure_arena_region = lambda **k: None
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            self.assertIs(Controller._reactivate_arena_window(c, context="test"), False)
        self.assertEqual(c.input.moves, [], "clicked outside the game window")


class StackScanMappingTest(unittest.TestCase):
    """The stack scan was the one hover scan handed its region unmapped: with
    MTGA windowed at x=1519 it drove the cursor to (1248, 190) and (672, 136),
    i.e. across whatever else was on the desktop, and could never hover a
    stack item."""

    def setUp(self):
        self.c = make_controller()
        self.c._arena_region = (1519, 128, 1920, 1080)
        self.c._ensure_arena_region = lambda **k: self.c._arena_region

    def test_the_scan_region_is_mapped_into_the_arena(self):
        p1, p2 = self.c._get_stack_scan_points_mapped(fallback=False)
        self.assertGreaterEqual(p1[0], 1519)
        self.assertGreaterEqual(p1[1], 128)
        self.assertLessEqual(p2[0], 1519 + 1920)
        self.assertLessEqual(p2[1], 128 + 1080)

    def test_the_fallback_region_is_mapped_too(self):
        p1, p2 = self.c._get_stack_scan_points_mapped(fallback=True)
        self.assertGreaterEqual(p1[0], 1519)
        self.assertLessEqual(p2[0], 1519 + 1920)

    def test_no_arena_means_no_scan(self):
        self.c._ensure_arena_region = lambda **k: None
        self.assertEqual(self.c._get_stack_scan_points_mapped(fallback=False), (None, None))
        self.assertIs(self.c.select_stack_item(999), False)
        self.assertEqual(self.c.input.moves, [], "the mouse was dragged across the desktop")

    def test_an_unmapped_region_is_refused_by_the_scan_itself(self):
        """Defence in depth: even if a caller forgets to map, the sweep must not
        leave the window."""
        moved = call_private(
            self.c,
            "select_object_in_region",
            999,
            (100, 200),
            (900, 600),
            80,
            1,
            "TEST_ITEM",
            None,
        )
        self.assertIs(moved, False)
        self.assertEqual(self.c.input.moves, [])


class EveryHoverScanRefusesDesktopTest(unittest.TestCase):
    """The hand scan has refused to run unmapped for a while; the other four
    hover scans captured the mapping source and then ignored it, so with no arena
    region they still swept raw 1920-space coordinates across the desktop -- the
    same defect, in four more places."""

    def setUp(self):
        self.c = make_controller()
        self.c._ensure_arena_region = lambda **k: None
        self.c._arena_region = None

    def assert_refused(self, call):
        self.assertIs(call(999), False)
        self.assertEqual(self.c.input.moves, [], "the mouse was moved without an arena")

    def test_stack(self):
        self.assert_refused(self.c.select_stack_item)

    def test_battlefield(self):
        self.assert_refused(self.c.select_battlefield_permanent)

    def test_opponent_battlefield(self):
        self.assert_refused(self.c.select_opponent_battlefield_permanent)

    def test_chooser(self):
        self.assert_refused(self.c.select_chooser_card)

    def test_the_mappers_all_report_the_failure(self):
        for name, kwargs in (
            ("_get_battlefield_scan_points_mapped", {}),
            ("_get_opponent_battlefield_scan_points_mapped", {}),
            ("_get_chooser_scan_points_mapped", {}),
            ("_get_stack_scan_points_mapped", {"fallback": False}),
            ("_get_stack_scan_points_mapped", {"fallback": True}),
            # The hand mapper is stubbed by make_controller; it has its own
            # coverage in HandScanRefusesDesktopTest.
        ):
            with self.subTest(mapper=name):
                self.assertEqual(getattr(self.c, name)(**kwargs), (None, None))


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
