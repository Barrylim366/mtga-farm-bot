import os
import sys
import threading
import unittest
from unittest.mock import patch

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


class CreatureTargetRetryTest(unittest.TestCase):
    def setUp(self):
        self.callbacks = []
        self.clicks = []
        self.submissions = []
        self.bundles = []
        self.controller = Controller.__new__(Controller)
        c = self.controller
        c._suppress_selections = False
        c._stop_requested = False
        c._Controller__live_match_id = "match"
        c._Controller__pending_target_select = None
        c._Controller__target_select_token_counter = 0
        c.can_execute_game_action = lambda expected_match_id=None: True
        c._Controller__record_decision = lambda *args, **kwargs: None
        c._Controller__get_delay_timer_remaining = lambda: 0
        c._Controller__write_target_debug_bundle = self.bundles.append
        c.select_battlefield_permanent = lambda card_id, clicks=1: self.clicks.append(card_id) or True
        c.submit_selection = lambda **kwargs: self.submissions.append(kwargs) or True
        c._dismiss_are_you_sure_if_present = lambda **kwargs: None
        c._Controller__update_pending_target_select(42, min_t=1, selected=0)

    def _timer(self, delay, callback, args=(), kwargs=None):
        callbacks = self.callbacks

        class Timer:
            def start(self):
                callbacks.append(lambda: callback(*args, **(kwargs or {})))

        return Timer()

    def _run_next(self):
        self.assertTrue(self.callbacks)
        self.callbacks.pop(0)()

    def test_delayed_acknowledgement_does_not_click_target_again(self):
        with patch.object(threading, "Timer", self._timer):
            self.controller._Controller__schedule_creature_target_selection(
                42, 99, "test", friendly=True,
            )
            # A repeated prompt must not start an independent click sequence.
            self.controller._Controller__schedule_creature_target_selection(
                42, 99, "duplicate", friendly=True,
            )
            self.assertEqual(len(self.callbacks), 1)
            self._run_next()  # First target click.
            for _ in range(2):
                self._run_next()  # No new Arena target update yet.
            self.controller._Controller__update_pending_target_select(42, selected=None)
            for _ in range(2):
                self._run_next()  # An update without a count is also inconclusive.
            self.assertEqual(self.clicks, [99])
            self.assertEqual(self.submissions, [])

            self.controller._Controller__update_pending_target_select(42, selected=1)
            self._run_next()
            self.assertEqual(self.clicks, [99])
            self.assertEqual(len(self.submissions), 1)

    def test_new_zero_selected_update_allows_one_retry(self):
        with patch.object(threading, "Timer", self._timer):
            self.controller._Controller__schedule_creature_target_selection(
                42, 99, "test", friendly=True,
            )
            self._run_next()  # First target click.
            self.controller._Controller__update_pending_target_select(42, selected=0)
            self._run_next()  # The new update confirms no target was selected.
            self._run_next()  # Retry click.
            self.assertEqual(self.clicks, [99, 99])

    def test_silent_uncancellable_target_click_retries_after_ack_window(self):
        with patch.object(threading, "Timer", self._timer), patch("time.monotonic") as clock:
            clock.return_value = 100.0
            self.controller._Controller__schedule_creature_target_selection(
                42, 99, "test", friendly=True,
            )
            self._run_next()  # First click; no selection update follows.
            clock.return_value = 104.0
            self._run_next()  # Acknowledgement window expired.
            self.assertEqual(self.clicks, [99])
            self._run_next()  # Bounded retry.
            clock.return_value = 108.0
            self._run_next()
            self._run_next()
            clock.return_value = 112.0
            self._run_next()  # Final timeout releases the duplicate-request guard.

        self.assertEqual(self.clicks, [99, 99, 99])
        self.assertEqual(self.submissions, [])
        self.assertFalse(self.controller._Controller__pending_target_select["creature_target_flow_active"])
        self.assertEqual(self.callbacks, [])

    def test_late_target_ack_before_retry_submits_without_second_click(self):
        with patch.object(threading, "Timer", self._timer), patch("time.monotonic") as clock:
            clock.return_value = 100.0
            self.controller._Controller__schedule_creature_target_selection(
                42, 99, "test", friendly=True,
            )
            self._run_next()
            clock.return_value = 104.0
            self._run_next()  # Timeout arms a retry.
            self.controller._Controller__update_pending_target_select(42, selected=1)
            self._run_next()  # The retry sees the late acknowledgement.

        self.assertEqual(self.clicks, [99])
        self.assertEqual(len(self.submissions), 1)


if __name__ == "__main__":
    unittest.main()
