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


if __name__ == "__main__":
    unittest.main()
