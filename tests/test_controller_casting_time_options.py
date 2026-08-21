"""Unit tests for the casting-time "Choose One" overlay handling (issue #41).

Regression under test: casting Apothecary Stomper opened the modal ETB dialog,
the single blind plate click did not register, and the decision loop then
dispatched the next move (a land play) into the still-open overlay -- the hand
row is hidden behind it, so the hover scan swept the whole row, failed with
SCAN_STOPPED, and retried until the match was lost.

Two behaviours are asserted here:
  * while the dialog is up, decisions pause instead of clicking into it, and
  * the plate click is retried until the game visibly moves on.

Nothing clicks a real mouse: Controller.input and the arena mapping are stubbed,
and threading.Timer is replaced with a fake the test fires by hand.

Controller uses name-mangled double-underscore attributes; from outside the
class body they must be accessed as `_Controller__name`.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import Controller.MTGAController.Controller as controller_module
from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState

APOTHECARY_STOMPER_ABILITY_GRP_ID = 175864


class FakeTimer:
    """Records the callback instead of running it, so a test can step the retry
    chain deterministically."""

    def __init__(self, delay, fn, *args, **kwargs):
        self.delay = delay
        self.fn = fn
        self.cancelled = False
        self.started = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def is_alive(self):
        return self.started and not self.cancelled


class FakeInput:
    def __init__(self):
        self.moves = []
        self.clicks = 0

    def move_abs(self, x, y):
        self.moves.append((x, y))

    def left_click(self, n=1):
        self.clicks += 1


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._Controller__system_seat_id = 1
    c.input = FakeInput()
    c._map_abs_point_to_arena = lambda point, label=None: ((1000, 500), "test")
    c._Controller__record_decision = lambda *a, **k: None
    return c


def seed_state(c: Controller, *, stack_object_ids=(379,), turn_number=9,
               phase="Phase_Main1", step="Step_Draw", decision_player=1,
               game_state_id=217):
    c.updated_game_state = GameState({
        "turnInfo": {
            "turnNumber": turn_number,
            "phase": phase,
            "step": step,
            "activePlayer": decision_player,
            "priorityPlayer": decision_player,
            "decisionPlayer": decision_player,
            "nextPhase": "Phase_Combat",
            "nextStep": "Step_BeginCombat",
        },
        "timers": [],
        "gameObjects": [],
        "players": [{"systemSeatNumber": 1}, {"systemSeatNumber": 2}],
        "annotations": [],
        "actions": [],
        "zones": [{
            "zoneId": 27,
            "type": "ZoneType_Stack",
            "visibility": "Visibility_Public",
            "objectInstanceIds": list(stack_object_ids),
        }],
        "gameStateId": game_state_id,
    })


def apply_resolution_diff(c: Controller, *, game_state_id=218):
    """Feed the controller a diff shaped the way MTGA really reports "the modal's
    ability resolved off the stack".

    This is the shape that killed the first version of this fix: MTGA omits
    `objectInstanceIds` entirely instead of sending an empty list, and
    GameState.update merges zone entries field-by-field -- so after this diff the
    merged stack STILL reads [379]. Any "did the dialog close?" test built on the
    stack contents therefore passes in a hand-built fixture and fails in the real
    game. gameStateId is what actually moves."""
    c.updated_game_state.update(GameState({
        "type": "GameStateType_Diff",
        "gameStateId": game_state_id,
        "prevGameStateId": game_state_id - 1,
        "zones": [{
            "zoneId": 27,
            "type": "ZoneType_Stack",
            "visibility": "Visibility_Public",
        }],
    }))


def timer_state_line(*, game_state_id=226) -> str:
    """A TimerStateMessage, the shape that exposed the stray-retry bug.

    It carries a gameStateId but no gameStateMessage, so nothing of it survives
    into the merged state. In the 2026-07-27 capture this was the ONLY carrier of
    the state advance between answering Apothecary Stomper's modal (224) and the
    follow-up SelectTargetsReq 2.7s later (227)."""
    return "[Message] " + json.dumps({
        "greToClientEvent": {
            "greToClientMessages": [{
                "type": "GREMessageType_TimerStateMessage",
                "systemSeatIds": [1, 2],
                "msgId": 290,
                "gameStateId": game_state_id,
                "timerStateMessage": {
                    "seatId": 1,
                    "timers": [{"timerId": 3, "type": "TimerType_ActivePlayer",
                                "durationSec": 68, "running": True}],
                },
            }]
        }
    })


def casting_time_options_line(
    *, option_type="CastingTimeOptionType_Modal",
    grp_id=APOTHECARY_STOMPER_ABILITY_GRP_ID, seat_ids=(1,),
) -> str:
    return "[Message] " + json.dumps({
        "greToClientEvent": {
            "greToClientMessages": [{
                "type": "GREMessageType_CastingTimeOptionsReq",
                "systemSeatIds": list(seat_ids),
                "castingTimeOptionsReq": {
                    "castingTimeOptionReq": [{
                        "castingTimeOptionType": option_type,
                        "ctoId": 2,
                        "grpId": grp_id,
                        "isRequired": True,
                    }],
                },
            }]
        }
    })


class CastingTimeOptionsPauseTest(unittest.TestCase):
    """The overlay must pause decisions -- this is what stops the hand scan."""

    def setUp(self):
        self.c = make_controller()
        seed_state(self.c)
        self.timer_patch = mock.patch.object(controller_module.threading, "Timer", FakeTimer)
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)

    def arm(self):
        self.c._Controller__handle_casting_time_options_req(casting_time_options_line())

    def still_open(self) -> bool:
        return self.c._Controller__should_pause_for_casting_time_options()

    def test_modal_request_opens_the_pause_window(self):
        self.arm()
        self.assertTrue(self.still_open())

    def test_no_pause_before_any_dialog(self):
        self.assertFalse(self.still_open())

    def test_decision_redrive_is_blocked_while_the_dialog_is_open(self):
        """The heartbeat must not re-drive a decision into the overlay either."""
        self.c._Controller__has_mulled_keep = True
        self.assertTrue(
            self.c._Controller__safe_to_redrive_decision(),
            "precondition: nothing else may be blocking, or this test proves nothing",
        )
        self.arm()
        self.assertFalse(self.c._Controller__safe_to_redrive_decision())

    def test_game_state_advance_closes_the_window(self):
        """The load-bearing tell: a mid-main-phase modal is answered without turn,
        phase, step or decisionPlayer moving at all -- only gameStateId moves."""
        self.arm()
        self.assertTrue(self.still_open())
        apply_resolution_diff(self.c)
        self.assertFalse(self.still_open())

    def test_real_resolution_diff_leaves_the_stack_unchanged(self):
        """Pins WHY the signal is gameStateId and not the stack: after the diff
        that resolves the ability, the merged stack still reads [379]."""
        self.arm()
        apply_resolution_diff(self.c)
        stack = self.c.updated_game_state.get_zone("ZoneType_Stack") or {}
        self.assertEqual(stack.get("objectInstanceIds"), [379])

    def test_following_target_request_closes_the_window(self):
        """Picking "put two +1/+1 counters on target creature" makes MTGA ask for
        the target next -- proof the mode was accepted."""
        self.arm()
        self.c._Controller__last_target_select_ts = time.time() + 0.1
        self.assertFalse(self.still_open())

    def test_following_pay_costs_request_closes_the_window(self):
        self.arm()
        self.c._Controller__pending_pay_costs_ts = time.time() + 0.1
        self.assertFalse(self.still_open())

    def test_turn_advance_closes_the_window(self):
        self.arm()
        seed_state(self.c, turn_number=10)
        self.assertFalse(self.still_open())

    def test_window_expires_so_an_unseen_dialog_cannot_freeze_the_bot(self):
        self.arm()
        self.c._Controller__casting_time_options_until = time.time() - 0.01
        self.assertFalse(self.still_open())

    def test_request_for_another_seat_does_not_pause_us(self):
        self.c._Controller__handle_casting_time_options_req(
            casting_time_options_line(seat_ids=(2,))
        )
        self.assertFalse(self.still_open())


class WireGameStateIdTest(unittest.TestCase):
    """Regression for the stray retry clicks seen in the 2026-07-27 run.

    The fix worked -- three Apothecary Stomper modals were answered and no match
    stalled -- but Player.log recorded 8 responses for 8 dialogs against 11
    clicks. The 3 extra ones were the Modal retries: the first click had already
    been accepted, and the only evidence of that reaching the client was a timer
    message, which the merged state throws away.
    """

    def setUp(self):
        self.c = make_controller()
        seed_state(self.c, game_state_id=224)
        self.timer_patch = mock.patch.object(controller_module.threading, "Timer", FakeTimer)
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)

    def note(self, line: str) -> None:
        self.c._Controller__note_gre_state_id(json.loads(line.split("[Message] ", 1)[1]))

    def test_timer_message_state_id_is_picked_up(self):
        self.note(timer_state_line(game_state_id=226))
        self.assertEqual(self.c._Controller__read_game_state_id(), 226)

    def test_merged_state_is_the_fallback_when_nothing_seen_on_the_wire(self):
        self.assertEqual(self.c._Controller__read_game_state_id(), 224)

    def test_timer_message_alone_closes_the_dialog_window(self):
        """The load-bearing case: no diff reaches the merged state at all, yet the
        pause must lift so the retry does not click onto the board."""
        self.c._Controller__handle_casting_time_options_req(casting_time_options_line())
        self.assertTrue(self.c._Controller__should_pause_for_casting_time_options())
        self.note(timer_state_line(game_state_id=226))
        self.assertEqual(
            self.c.updated_game_state.get_full_state().get("gameStateId"), 224,
            "precondition: the merged state must NOT have learned 226, or this "
            "test would pass without the fix",
        )
        self.assertFalse(self.c._Controller__should_pause_for_casting_time_options())

    def test_unchanged_state_id_keeps_the_window_open(self):
        self.c._Controller__handle_casting_time_options_req(casting_time_options_line())
        self.note(timer_state_line(game_state_id=224))
        self.assertTrue(self.c._Controller__should_pause_for_casting_time_options())

    def test_state_id_does_not_survive_into_the_next_match(self):
        """gameStateId restarts low each game; a stale high one would make every
        fresh dialog look already-answered."""
        self.note(timer_state_line(game_state_id=226))
        self.c._Controller__reset_live_game_state("test")
        self.assertIsNone(self.c._Controller__latest_gre_state_id)


class CastingTimeOptionsRetryTest(unittest.TestCase):
    """A plate click that does not register must be repeated -- and must stop the
    moment the game moves on, so no retry ever lands on the battlefield."""

    def setUp(self):
        self.c = make_controller()
        seed_state(self.c)
        self.timers = []

        outer = self

        class RecordingTimer(FakeTimer):
            def __init__(self, delay, fn, *args, **kwargs):
                super().__init__(delay, fn, *args, **kwargs)
                outer.timers.append(self)

        self.timer_patch = mock.patch.object(controller_module.threading, "Timer", RecordingTimer)
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)
        self.c._Controller__handle_casting_time_options_req(casting_time_options_line())

    def fire_next(self):
        """Run the most recently armed, still-live timer callback."""
        for timer in reversed(self.timers):
            if timer.started and not timer.cancelled and not getattr(timer, "fired", False):
                timer.fired = True
                timer.fn()
                return True
        return False

    def test_modal_plate_is_clicked_and_retried_while_the_dialog_stays_open(self):
        self.assertEqual(self.c.input.clicks, 0, "click is deferred until the overlay animates in")
        self.fire_next()
        self.assertEqual(self.c.input.clicks, 1)
        self.fire_next()
        self.assertEqual(self.c.input.clicks, 2, "unregistered plate click must be retried")

    def test_retries_stop_once_the_dialog_has_resolved(self):
        self.fire_next()
        self.assertEqual(self.c.input.clicks, 1)
        apply_resolution_diff(self.c)  # the answered ability resolved
        self.fire_next()
        self.assertEqual(self.c.input.clicks, 1, "no stray click onto the board behind the overlay")

    def test_a_dialog_that_closes_mid_retry_never_gets_the_click(self):
        """The plate sits over the battlefield, so the settle sleep between the
        move and the click is its own window for a stray click."""
        self.fire_next()  # attempt 0
        original_move_abs = self.c.input.move_abs

        def closing_move(x, y):
            original_move_abs(x, y)
            apply_resolution_diff(self.c)  # dialog closes during the settle sleep

        self.c.input.move_abs = closing_move
        self.fire_next()  # attempt 1
        self.assertEqual(self.c.input.clicks, 1, "the retry must abort after the settle sleep")

    def test_retries_are_bounded_and_release_the_pause(self):
        while self.fire_next():
            pass
        self.assertLessEqual(
            self.c.input.clicks,
            1 + Controller._Controller__CASTING_TIME_OPTION_MAX_RETRIES,
        )
        self.assertFalse(
            self.c._Controller__should_pause_for_casting_time_options(),
            "a dialog we cannot resolve must not pause decisions forever",
        )

    def test_stop_request_aborts_the_retry_chain(self):
        self.c._stop_requested = True
        self.fire_next()
        self.assertEqual(self.c.input.clicks, 0)


EATEN_ALIVE_GRP_ID = 93885


class ChooseOrCostCandidateSweepTest(unittest.TestCase):
    """A ChooseOrCost dialog must not be answered by the same point three times.

    Live on 2026-08-21, turn 19: the AI picked Eaten Alive ("As an additional
    cost to cast this spell, sacrifice a creature or pay {3}{B}. Exile target
    creature or planeswalker."), chose its target, and the handler clicked
    (1775, 978) three times 1.6s apart. Every click was logged, the dialog
    stayed open through all of them ("Deferring decision; casting-time Choose
    One dialog still open"), the retries ran out and the spell was simply lost.
    Repeating one point can only rescue a swallowed click, never a point that is
    not on the button -- so the retries walk the candidate list instead.
    """

    def setUp(self):
        self.c = make_controller()
        seed_state(self.c)
        self.timers = []
        self.points = []
        outer = self

        class RecordingTimer(FakeTimer):
            def __init__(self, delay, fn, *args, **kwargs):
                super().__init__(delay, fn, *args, **kwargs)
                outer.timers.append(self)

        self.timer_patch = mock.patch.object(controller_module.threading, "Timer", RecordingTimer)
        self.timer_patch.start()
        self.addCleanup(self.timer_patch.stop)
        # Record the 1920-frame point each attempt asks for.
        self.c._map_abs_point_to_arena = lambda point, label=None: (
            outer.points.append(tuple(point)) or (1000, 500),
            "test",
        )
        self.c._write_casting_option_debug_bundle = lambda **k: outer.bundles.append(k)
        self.bundles = []
        self.c._Controller__handle_casting_time_options_req(
            casting_time_options_line(
                option_type="CastingTimeOptionType_ChooseOrCost",
                grp_id=EATEN_ALIVE_GRP_ID,
            )
        )

    def fire_next(self):
        for timer in reversed(self.timers):
            if timer.started and not timer.cancelled and not getattr(timer, "fired", False):
                timer.fired = True
                timer.fn()
                return True
        return False

    def test_each_attempt_tries_a_different_point(self):
        while self.fire_next():
            pass
        self.assertEqual(
            self.points,
            list(Controller._CHOOSE_OR_COST_CANDIDATES),
            "the retries repeated a point instead of trying the next candidate",
        )

    def test_the_first_candidate_is_the_previously_shipped_point(self):
        """Ordering matters: the point that was there before goes first, so a
        card this already worked for keeps working."""
        self.fire_next()
        self.assertEqual(self.points, [(1775, 978)])

    def test_the_sweep_stops_as_soon_as_the_dialog_closes(self):
        self.fire_next()
        self.assertEqual(self.c.input.clicks, 1)
        apply_resolution_diff(self.c)
        while self.fire_next():
            pass
        self.assertEqual(
            self.c.input.clicks, 1,
            "a later candidate fired after the dialog was answered -- that click "
            "lands on the battlefield",
        )

    def test_exhausting_every_candidate_photographs_the_dialog(self):
        """The failure has no other visible symptom: the clicks are all logged
        and the spell just never gets cast, so the screenshot is the only way to
        measure where the button really is."""
        while self.fire_next():
            pass
        self.assertEqual(len(self.bundles), 1, "no debug bundle for an unanswered dialog")
        self.assertEqual(self.bundles[0]["reason"], "retries_exhausted_dialog_still_open")
        self.assertEqual(
            list(self.bundles[0]["tried"]), list(Controller._CHOOSE_OR_COST_CANDIDATES)
        )

    def test_a_dialog_that_resolves_is_not_photographed(self):
        self.fire_next()
        apply_resolution_diff(self.c)
        while self.fire_next():
            pass
        self.assertEqual(self.bundles, [], "screenshot taken for a dialog that worked")

    def test_the_pause_is_released_even_after_every_candidate_missed(self):
        while self.fire_next():
            pass
        self.assertFalse(
            self.c._Controller__should_pause_for_casting_time_options(),
            "a dialog we cannot answer must not pause decisions forever",
        )

    def test_a_modal_dialog_still_uses_its_single_measured_plate(self):
        """The candidate sweep is for ChooseOrCost only; the modal plates are
        measured and must not start wandering."""
        c = make_controller()
        seed_state(c)
        points = []
        c._map_abs_point_to_arena = lambda point, label=None: (
            points.append(tuple(point)) or (1000, 500), "test",
        )
        timers = []

        class T(FakeTimer):
            def __init__(self, delay, fn, *a, **k):
                super().__init__(delay, fn, *a, **k)
                timers.append(self)

        with mock.patch.object(controller_module.threading, "Timer", T):
            c._Controller__handle_casting_time_options_req(casting_time_options_line())
            for timer in list(timers):
                if timer.started and not timer.cancelled:
                    timer.fn()
        self.assertTrue(points)
        self.assertEqual(set(points), {(750, 505)}, "modal plate point drifted")


if __name__ == "__main__":
    unittest.main()
