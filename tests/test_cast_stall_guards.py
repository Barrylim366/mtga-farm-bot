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
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import Game as GameModule
from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState


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
        self.clicks = []

    def position(self):
        return self._pos

    def move_abs(self, x, y):
        self.moves.append((x, y))
        self._pos = _Pos(x, y)

    def move_rel(self, dx, dy):
        self._pos = _Pos(self._pos.x, self._pos.y)

    def left_click(self, n=1):
        self.clicks.append(n)

    # _click_abs uses the down/up pair, not left_click. Missing them is how this
    # harness used to blow up with AttributeError instead of failing cleanly --
    # see the screen-isolation note in make_controller.
    def left_down(self):
        pass

    def left_up(self):
        pass

    def tap_enter(self):
        pass

    def tap_shift_enter(self):
        pass


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._Controller__live_match_id = "test-match"
    c._Controller__last_seen_match_id = "test-match"
    c._get_state_from_log = lambda: BotState.IN_GAME
    c.input = _FakeInput()
    c._get_hand_scan_points_mapped = lambda **k: ((0, 0), (0, 0))
    c._ensure_options_overlay_closed = lambda **k: True
    c._write_hand_select_debug_bundle = lambda **k: None
    c.log_reader.has_new_line = lambda pattern: False
    c.log_reader.clear_new_line_flag = lambda pattern: None
    # Keep the suite off the real screen. Without these, a recovery path runs
    # genuine template matching against whatever is on the monitor: when MTGA
    # happened to show a Done button, the match succeeded, _click_abs was called
    # for real and the harness died with AttributeError. That looked exactly
    # like a flaky test -- it failed only while the bot was running and passed
    # in five reruns after it stopped. A unit test must not see the screen.
    c._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
    c._click_image_in_scaled_arena_region = lambda *a, **k: False
    return c


def mtga_in_foreground(testcase):
    """Report MTGA as the foreground window for the rest of the test.

    Since the cast path checks the foreground before the hand sweep, a test
    that does not pin it reads the real desktop -- the terminal running the
    suite -- and aborts with foreground_recovery_failed before it reaches the
    sweep it means to exercise (and calls the real focus_mtga_window)."""
    patcher = patch(
        "Controller.MTGAController.Controller._describe_foreground_window",
        return_value={"hwnd": 1, "title": "MTGA", "is_mtga": True},
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)


def call_private(controller, name, *args):
    """Reach a name-mangled private method without hard-coding the mangling at
    every call site."""
    return getattr(controller, f"_Controller__{name}")(*args)


class InstanceIdRemapTest(unittest.TestCase):
    """MTGA recycles instanceIds. The live state is the authority on what an id
    is NOW; this map only has to bridge the gaps between messages."""

    def setUp(self):
        self.c = make_controller()
        mtga_in_foreground(self)

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
        mtga_in_foreground(self)

    def cast(self, card_id):
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             patch("time.sleep", return_value=None):
            return self.c.cast(card_id)

    def test_a_failed_cast_reports_false(self):
        """Silence is what let the decision loop wait for a state change that was
        never coming."""
        self.assertIs(self.cast(999), False)
        self.assertTrue(self.c._is_cast_suppressed(999))
        self.assertEqual(self.c.get_last_cast_abort_reason(), None)

    def test_a_failed_focus_recovery_aborts_without_suppression(self):
        """MTGA not owning the foreground says nothing about the hand, so the
        card must not be suppressed and no sweep or recovery probe may run."""
        probes = []
        recoveries = []
        self.c._dismiss_are_you_sure_if_present = lambda **_k: probes.append("confirm")
        self.c._dismiss_report_player_dialog = lambda **_k: probes.append("report")
        self.c._dismiss_stray_done_overlay = lambda **_k: probes.append("done")
        self.c._Controller__schedule_decision_recovery = lambda *a: recoveries.append(a)
        with patch("Controller.MTGAController.Controller._describe_foreground_window",
                   return_value={"hwnd": 2, "title": "Terminal", "is_mtga": False}), \
             patch("Controller.MTGAController.Controller.focus_mtga_window") as focus, \
             patch("time.sleep", return_value=None):
            self.assertIs(self.c.cast(999), False)
        focus.assert_called_once_with()
        self.assertEqual(self.c.get_last_cast_abort_reason(), "foreground_recovery_failed")
        self.assertFalse(self.c._is_cast_suppressed(999))
        self.assertEqual(self.c.input.moves, [])
        self.assertEqual(probes, [])
        self.assertEqual(recoveries, [(0.2, "cast_foreground_recovery")])

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

    def test_suppression_during_scan_stops_before_recovery_probes(self):
        probes = []

        def cancelled_scan(_card_id, **_kwargs):
            self.c._suppress_selections = True
            return False

        self.c._cast_once = cancelled_scan
        self.c._dismiss_are_you_sure_if_present = lambda **_kwargs: probes.append("confirm")
        self.c._dismiss_report_player_dialog = lambda **_kwargs: probes.append("report")
        self.c._dismiss_stray_done_overlay = lambda **_kwargs: probes.append("done")

        self.assertFalse(self.c.cast(999))
        self.assertEqual(probes, [])
        self.assertEqual(self.c.input.clicks, [])

    def test_target_prompt_mid_scan_defers_without_suppression_or_rescue(self):
        self.c._get_hand_scan_points_mapped = lambda **_k: ((0, 0), (30, 0))
        pending = [False]
        motions = []
        self.c.should_defer_cast_for_target_selection = lambda _match: pending[0]
        probes = []
        events = []
        self.c._Controller__cast_ack_event = lambda event, **details: events.append((event, details))
        self.c._dismiss_are_you_sure_if_present = lambda **_k: probes.append("confirm")
        self.c._dismiss_report_player_dialog = lambda **_k: probes.append("report")
        self.c._dismiss_stray_done_overlay = lambda **_k: probes.append("done")
        self.c._Controller__schedule_decision_recovery = lambda *_a: probes.append("recovery")

        def move_rel(dx, dy):
            motions.append((dx, dy))
            self.c.input._pos = _Pos(self.c.input._pos.x + dx, self.c.input._pos.y + dy)
            pending[0] = True

        self.c.input.move_rel = move_rel
        with patch("Controller.MTGAController.Controller._describe_foreground_window",
                   return_value={"is_mtga": True}), patch("time.sleep", return_value=None):
            self.assertFalse(self.c.cast(999))

        self.assertEqual(self.c.get_last_cast_abort_reason(), "target_selection_pending")
        self.assertFalse(self.c._is_cast_suppressed(999))
        self.assertEqual(probes, [])
        self.assertEqual(self.c.input.clicks, [])
        self.assertEqual(len(motions), 1)
        self.assertFalse(any(name == "cast_scan_failed" for name, _ in events))
        self.assertEqual([details["reason"] for name, details in events
                          if name == "cast_not_clicked"], ["target_selection_pending"])

    def test_target_prompt_before_first_click_defers_without_click(self):
        self.c._get_hand_scan_points_mapped = lambda **_k: ((0, 0), (30, 0))
        pending = [False]
        hover = [False]
        self.c.should_defer_cast_for_target_selection = lambda _match: pending[0]
        self.c.log_reader.has_new_line = lambda _pattern: hover[0]
        self.c.log_reader.get_latest_line_containing_pattern = lambda _pattern: "target"
        self.c._Controller__parse_hover_observation = lambda _line: (999, "local_fragment")
        original_move_abs = self.c.input.move_abs

        def move_abs(x, y):
            original_move_abs(x, y)
            if (x, y) == (0, 0):
                hover[0] = True

        self.c.input.move_abs = move_abs
        sleeps = [0]

        def prompt_on_click_pause(_seconds):
            sleeps[0] += 1
            if sleeps[0] == 2:
                pending[0] = True

        with patch("Controller.MTGAController.Controller._describe_foreground_window",
                   return_value={"is_mtga": True}), patch("time.sleep", side_effect=prompt_on_click_pause):
            self.assertFalse(self.c.cast(999))

        self.assertEqual(self.c.get_last_cast_abort_reason(), "target_selection_pending")
        self.assertFalse(self.c._is_cast_suppressed(999))
        self.assertEqual(sleeps[0], 2)
        self.assertEqual(self.c.input.clicks, [])


class GameActionCancellationTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()

    def test_resolve_refuses_input_after_concession_claim(self):
        self.c._Controller__concession_claimed = True
        with patch.object(self.c, "_map_abs_point_to_arena") as mapper:
            self.c.resolve()
        mapper.assert_not_called()

    def test_resolve_refuses_input_after_match_retirement(self):
        self.c._Controller__live_match_id = None
        self.c._Controller__last_seen_match_id = None
        self.c._Controller__retired_match_id = "test-match"
        with patch.object(self.c, "_map_abs_point_to_arena") as mapper:
            self.c.resolve()
        mapper.assert_not_called()

    def test_resolve_refuses_input_after_stop(self):
        self.c._stop_requested = True
        with patch.object(self.c, "_map_abs_point_to_arena") as mapper:
            self.c.resolve()
        mapper.assert_not_called()


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

    def test_wrong_sized_live_window_never_reuses_stale_hand_coordinates(self):
        """A moved/resized MTGA window must abort rather than sweep its old hand row."""
        c = make_controller()
        c._arena_region = None
        c._last_good_arena_region = (429, 156, 1920, 1080)
        c._last_good_arena_region_ts = 1.0
        c._should_reuse_cached_arena_region = lambda: True
        c._arena_region_provider = SimpleNamespace(
            reacquire=lambda: None,
            acquire=lambda: None,
            last_detection_result=SimpleNamespace(code="window_wrong_size"),
        )
        # make_controller uses fixed points for ordinary sweep tests; exercise
        # the real mapping path for this stale-geometry regression.
        c._get_hand_scan_points_mapped = Controller._get_hand_scan_points_mapped.__get__(c, Controller)
        self.assertIsNone(c._ensure_arena_region(force_reacquire=True))
        with patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False):
            self.assertFalse(c._cast_once(999))
        self.assertEqual(c.input.moves, [], "a geometry mismatch must not start a hand sweep")

    def test_anchor_miss_can_still_reuse_recent_region_in_game(self):
        """Only known geometry failures block the intentional in-game cache fallback."""
        c = make_controller()
        cached = (429, 156, 1920, 1080)
        c._arena_region = None
        c._last_good_arena_region = cached
        c._last_good_arena_region_ts = 1.0
        c._should_reuse_cached_arena_region = lambda: True
        c._arena_region_provider = SimpleNamespace(
            reacquire=lambda: None,
            acquire=lambda: None,
            last_detection_result=SimpleNamespace(code="anchor_not_found"),
        )
        self.assertEqual(c._ensure_arena_region(force_reacquire=True), cached)

    def test_cast_does_not_reactivate_an_already_foreground_mtga_window(self):
        """A redundant Win32 activation can make Unity drop injected hover events."""
        self.c._get_hand_scan_points_mapped = lambda **k: (None, None)
        with patch(
            "Controller.MTGAController.Controller._describe_foreground_window",
            return_value={"hwnd": 123, "title": "MTGA", "is_mtga": True},
        ), patch(
            "Controller.MTGAController.Controller.focus_mtga_window"
        ) as focus:
            self.assertFalse(self.c._cast_once(999))
        focus.assert_not_called()

    def test_title_only_mtga_window_still_triggers_verified_focus_recovery(self):
        self.c._get_hand_scan_points_mapped = lambda **k: (None, None)
        with patch(
            "Controller.MTGAController.Controller._describe_foreground_window",
            return_value={"hwnd": 456, "title": "MTGA issue - Browser", "is_mtga": False},
        ), patch(
            "Controller.MTGAController.Controller.focus_mtga_window"
        ) as focus:
            self.assertFalse(self.c._cast_once(999))
        focus.assert_called_once_with()

    def test_unknown_foreground_identity_still_triggers_focus_recovery(self):
        self.c._get_hand_scan_points_mapped = lambda **k: (None, None)
        with patch(
            "Controller.MTGAController.Controller._describe_foreground_window",
            return_value={"hwnd": 456, "title": "", "is_mtga": None},
        ), patch(
            "Controller.MTGAController.Controller.focus_mtga_window"
        ) as focus:
            self.assertFalse(self.c._cast_once(999))
        focus.assert_called_once_with()


class _StubController:
    """Only what Game.decision_method touches for a cast move."""

    def __init__(self, cast_result):
        self._cast_result = cast_result
        self.calls = []
        self.can_execute = True

    def can_execute_game_action(self, expected_match_id=None):
        return self.can_execute

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
        self.assertEqual(g._last_move_signature[6], "resolve")
        self.assertEqual(g._last_move_repeat_count, 1)

    def test_the_pass_records_the_same_match_and_state(self):
        g = self.game(False)
        g._pass_priority_on_uncastable(
            477, 1, "Phase_Main1", "Step_Draw", 2, "match-1", 50
        )
        self.assertEqual(
            g._last_move_signature,
            ("match-1", 50, 1, "Phase_Main1", "Step_Draw", 2, "resolve", ()),
        )

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

    def test_a_cancelled_cast_does_not_pass_priority(self):
        g = self.game(False)
        g.controller.can_execute = False
        self.execute_cast(g)
        self.assertEqual(g.controller.calls, [("cast", 477)])

    def test_pending_cast_ack_does_not_pass_priority(self):
        class State:
            def get_turn_info(inner):
                return {
                    "turnNumber": 3, "activePlayer": 2, "decisionPlayer": 2,
                    "priorityPlayer": 2, "phase": "Phase_Main1", "step": "Step_Draw",
                }

            def get_actions(inner):
                return [{"seatId": 2, "action": {
                    "actionType": "ActionType_Cast", "instanceId": 477,
                }}]

            def get_full_state(inner):
                return {
                    "gameStateId": 50,
                    "turnInfo": inner.get_turn_info(),
                    "actions": inner.get_actions(),
                }

        class SafetyController(_StubController):
            def __init__(inner):
                super().__init__(False)
                inner.last_abort = "cast_ack_pending"

            def get_current_match_id(inner):
                return "match-1"

            def reset_inactivity_timer(inner):
                return None

            def get_last_cast_abort_reason(inner):
                return inner.last_abort

            def get_inst_id_grp_id_dict(inner):
                return {}

            def should_defer_cast_for_target_selection(inner, _match):
                return False

            def cast(inner, inst_id, decision_context=None):
                inner.calls.append(("cast", inst_id))
                return False

        game = GameModule.Game.__new__(GameModule.Game)
        game._stop_requested = False
        game.controller = SafetyController()
        game.game_started = True
        game._last_action_delay_turn = 3
        game.last_logged_turn = 3
        game.starting_hand_logged = True
        game._last_move_signature = (
            "match-1", 50, 3, "Phase_Main1", "Step_Draw", 2, "cast", (477,),
        )
        game._last_move_repeat_count = 2
        game.ai = SimpleNamespace(generate_move=lambda *_args: {"cast": [477]})
        game._debug = lambda *_args, **_kwargs: None
        game._get_card_id_str = lambda _inst_id: "test card"
        game._recorder_seat = lambda: 2
        game._recorder_match_id = lambda: "match-1"
        fallback = []
        game._pass_priority_on_uncastable = lambda *_args: fallback.append(True)
        state = State()

        with patch.object(GameModule.runtime_status, "clear_intentional_wait"), \
             patch.object(GameModule.runtime_status, "set_mode"), \
             patch.object(GameModule.runtime_status, "touch_decision"), \
             patch.object(GameModule.bot_logger, "log_decision"), \
             patch.object(GameModule.debug_recorder, "capture", return_value="snapshot"), \
             patch.object(GameModule.debug_recorder, "attach_move"), \
             patch.object(GameModule.CardInfo, "get_card_info", return_value=None):
            game.decision_method(state)

        self.assertEqual(game.controller.calls, [("cast", 477)])
        self.assertEqual(fallback, [])
        self.assertEqual(game._last_move_repeat_count, 3)


class GameMoveRetryStateTest(unittest.TestCase):
    class State:
        def __init__(self, game_state_id):
            self.game_state_id = game_state_id

        def get_turn_info(self):
            return {
                "turnNumber": 3, "activePlayer": 2, "decisionPlayer": 2,
                "priorityPlayer": 2, "phase": "Phase_Main1", "step": "Step_Draw",
            }

        def get_actions(self):
            return [{"seatId": 2, "action": {
                "actionType": "ActionType_Cast", "instanceId": 477,
            }}]

        def get_full_state(self):
            return {
                "gameStateId": self.game_state_id,
                "turnInfo": self.get_turn_info(),
                "actions": self.get_actions(),
            }

    class Controller(_StubController):
        def __init__(self, cast_results):
            super().__init__(True)
            self.cast_results = iter(cast_results)
            self.last_abort = None

        def get_current_match_id(self):
            return "match-1"

        def reset_inactivity_timer(self):
            pass

        def get_last_cast_abort_reason(self):
            return self.last_abort

        def should_defer_cast_for_target_selection(self, _match):
            return False

        def cast(self, inst_id, decision_context=None):
            self.calls.append(("cast", inst_id))
            result, self.last_abort = next(self.cast_results)
            return result

    def make_game(self, cast_results):
        game = GameModule.Game.__new__(GameModule.Game)
        game._stop_requested = False
        game.controller = self.Controller(cast_results)
        game.game_started = True
        game._last_action_delay_turn = 3
        game.last_logged_turn = 3
        game.starting_hand_logged = True
        game._last_move_signature = (
            "match-1", 50, 3, "Phase_Main1", "Step_Draw", 2, "cast", (477,),
        )
        game._last_move_repeat_count = 2
        game.ai = SimpleNamespace(generate_move=lambda *_args: {"cast": [477]})
        game._debug = lambda *_args, **_kwargs: None
        game._get_card_id_str = lambda _inst_id: "test card"
        game._recorder_seat = lambda: 2
        game._recorder_match_id = lambda: "match-1"
        return game

    def decide(self, game, state):
        with patch.object(GameModule.runtime_status, "clear_intentional_wait"), \
             patch.object(GameModule.runtime_status, "set_mode"), \
             patch.object(GameModule.runtime_status, "touch_decision"), \
             patch.object(GameModule.bot_logger, "log_decision"), \
             patch.object(GameModule.bot_logger, "log_error"), \
             patch.object(GameModule.debug_recorder, "capture", return_value="snapshot"), \
             patch.object(GameModule.debug_recorder, "attach_move"), \
             patch.object(GameModule.CardInfo, "get_card_info", return_value=None):
            game.decision_method(state)

    def test_new_game_state_resets_cast_repeat_count(self):
        game = self.make_game([(True, None)])
        self.decide(game, self.State(51))
        self.assertEqual(game.controller.calls, [("cast", 477)])
        self.assertEqual(game._last_move_repeat_count, 1)
        self.assertEqual(game._last_move_signature[:2], ("match-1", 51))

    def test_third_cast_in_same_game_state_triggers_breaker(self):
        game = self.make_game([])
        self.decide(game, self.State(50))
        self.assertEqual(game.controller.calls, [("resolve", None)])
        self.assertEqual(game._last_move_signature,
                         ("match-1", 50, 3, "Phase_Main1", "Step_Draw", 2, "resolve", ()))
        self.assertEqual(game._last_move_repeat_count, 1)

    def test_stale_cast_is_reconsidered_from_new_state_without_pass(self):
        game = self.make_game([(False, "stale_decision_context"), (True, None)])
        game._last_move_repeat_count = 1
        self.decide(game, self.State(50))
        self.assertEqual(game.controller.calls, [("cast", 477)])
        self.assertEqual(game._last_move_repeat_count, 2)
        self.decide(game, self.State(51))
        self.assertEqual(game.controller.calls, [("cast", 477), ("cast", 477)])
        self.assertEqual(game._last_move_repeat_count, 1)

    def test_exhausted_cast_passes_priority_without_another_retry(self):
        game = self.make_game([(False, "cast_escape_retry_exhausted")])
        game._last_move_repeat_count = 0
        self.decide(game, self.State(50))
        self.assertEqual(game.controller.calls, [("cast", 477), ("resolve", None)])
        self.assertEqual(game._last_move_signature[-2:], ("resolve", ()))

    def test_recovery_can_choose_another_card(self):
        game = self.make_game([(True, None)])
        game._last_move_repeat_count = 0
        game.ai = SimpleNamespace(generate_move=lambda *_args: {"cast": [478]})
        self.decide(game, self.State(50))
        self.assertEqual(game.controller.calls, [("cast", 478)])

    def test_closed_target_prompt_still_defers_without_breaker_count(self):
        game = self.make_game([
            (False, "target_selection_pending"),
            (False, "target_selection_pending"),
        ])
        game._last_move_repeat_count = 1
        original_signature = game._last_move_signature
        self.decide(game, self.State(50))
        self.decide(game, self.State(50))
        self.assertEqual(game.controller.calls, [("cast", 477), ("cast", 477)])
        self.assertEqual(game._last_move_signature, original_signature)
        self.assertEqual(game._last_move_repeat_count, 1)


if __name__ == "__main__":
    unittest.main()
