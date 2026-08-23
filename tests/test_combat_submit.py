"""The combat button is pressed on evidence, not on a stopwatch.

MTGA needs two presses of the same bottom-right button -- declare, then submit --
and swallows anything clicked while the creatures animate in. The old code slept
a fixed 0.6s/1.0s and pressed again blind, which is invisible when it fails: the
click is in our log, the game never saw it. Measured cost on 2026-08-23: 26
logged DeclareBlockersReq against 2 SubmitBlockersReq, the blocker timer at
0.0s combat after combat, three matches lost -- one of them while ahead on life.

So these pin the press *count* per outcome, because that is the whole bug:

  1. declared, then acknowledged -> exactly two presses;
  2. already submitted by the first press ("No Blocks") -> exactly one, so we
     do not click on into the next step;
  3. nothing confirmed at all -> still two, the old never-freeze fallback: an
     unanswered combat costs the rope and then the match;
  4. declared but the submit is never acknowledged -> retried, bounded, and
     loudly logged rather than silently dropped;
  5. a *parallel* sequence presses nothing at all. That one is the actual root
     cause: a second all_attack() ran 0.46-0.48s behind the first, straight into
     the animation, and the post-mortem read it as a designed retry;
  6. the worst case stays under Game.py's 8s DECISION_HEARTBEAT, which would
     otherwise re-enter this path and rebuild the concurrency.

The helper is called directly with a target point, so no template matching and
no screen capture is involved (see CLAUDE.md on tests that reach the screen).
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.test_combat_shadow import cleanup, make_controller

TARGET = (2515, 1137)
BLOCK_DECLARED = ["BlockState_Declared", "BlockState_Blocking"]
BLOCK_SUBMITTED = ["SubmitBlockersReq"]


class CombatSubmitTest(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        self.controller._suppress_selections = False
        self.addCleanup(cleanup, self.controller)
        self.controller.input = mock.MagicMock()
        # Real sleeps would make this suite wait out the animation budget.
        patcher = mock.patch("Controller.MTGAController.Controller.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        mock.patch.object(self.controller, "_get_log_size", return_value=0).start()
        self.addCleanup(mock.patch.stopall)

    def _arm_markers(self, *, declared: bool, submitted_after: int | None):
        """Script Player.log: was a declaration seen, and from which press on is
        a submit acknowledged? `submitted_after=1` means the first press already
        submitted; None means never.
        """
        self.presses = 0
        real_click = self.controller.input.left_click

        def count_click(*a, **k):
            self.presses += 1
            return real_click(*a, **k)

        self.controller.input.left_click = count_click

        def fake_wait(markers, *, start_offset, timeout_sec, label):
            asks_submit = any(m in BLOCK_SUBMITTED for m in markers)
            asks_declare = any(m in BLOCK_DECLARED for m in markers)
            if asks_submit and submitted_after is not None and self.presses >= submitted_after:
                return True
            if asks_declare and declared:
                return True
            return False

        mock.patch.object(
            self.controller, "_wait_for_playerlog_marker", side_effect=fake_wait
        ).start()

    def _press(self):
        return self.controller._Controller__press_combat_button_verified(
            TARGET,
            "SUBMIT_BLOCKS",
            declared_markers=BLOCK_DECLARED,
            submitted_markers=BLOCK_SUBMITTED,
        )

    def test_a_declaration_is_submitted_with_exactly_one_more_press(self):
        self._arm_markers(declared=True, submitted_after=2)
        self.assertTrue(self._press())
        self.assertEqual(self.presses, 2, "declare + submit is two presses, no more")

    def test_a_single_press_that_already_submitted_is_not_followed_by_another(self):
        """The No Blocks case: pressing on would land in the next step."""
        self._arm_markers(declared=False, submitted_after=1)
        self.assertTrue(self._press())
        self.assertEqual(self.presses, 1)

    def test_an_unconfirmed_press_still_presses_again(self):
        """Never freeze: losing beats standing in an unanswered combat step."""
        self._arm_markers(declared=False, submitted_after=None)
        self.assertFalse(self._press())
        self.assertEqual(self.presses, 2)

    def test_an_unacknowledged_submit_is_retried_and_bounded(self):
        self._arm_markers(declared=True, submitted_after=None)
        with mock.patch(
            "Controller.MTGAController.Controller.bot_logger.log_error"
        ) as log_error:
            self.assertFalse(self._press())
        attempts = self.controller._Controller__COMBAT_SUBMIT_ATTEMPTS
        self.assertEqual(self.presses, 1 + attempts, "retries must stay bounded")
        self.assertTrue(
            any("COMBAT_SUBMIT_UNACKNOWLEDGED" in str(c) for c in log_error.call_args_list),
            "a swallowed submit must be visible in the log, not silent",
        )

    def test_the_animation_is_waited_out_before_submitting(self):
        """A submit inside the animation is exactly what got swallowed."""
        self._arm_markers(declared=True, submitted_after=2)
        self._press()
        waited = [c.args[0] for c in self.sleep.call_args_list if c.args]
        self.assertIn(
            self.controller._Controller__COMBAT_ANIMATION_SEC,
            waited,
            "no animation wait between declaring and submitting",
        )

    def test_a_stop_request_ends_the_sequence(self):
        self._arm_markers(declared=True, submitted_after=None)
        self.controller._stop_requested = True
        self.assertFalse(self._press())
        self.assertEqual(self.presses, 0, "pressed on after Stop was requested")

    def test_a_parallel_sequence_is_skipped_rather_than_pressing_too(self):
        """The 0.46s double press this whole fix is about.

        A second all_attack() ran concurrently and pressed while the first
        sequence was mid-animation -- measured twice, on 2026-08-22 (+0.46s) and
        again on 2026-08-23 (+0.48s). The lock is what makes the second one a
        no-op instead of a swallowed click.
        """
        self._arm_markers(declared=True, submitted_after=2)
        self.controller._Controller__combat_submit_lock.acquire()
        self.addCleanup(self.controller._Controller__combat_submit_lock.release)
        self.assertFalse(self._press())
        self.assertEqual(self.presses, 0, "pressed while another sequence was in flight")

    def test_the_sequence_stays_under_the_decision_heartbeat(self):
        """Game.py re-drives a decision idle for 8s; overrunning that re-enters
        this path and re-creates the concurrency it just fixed."""
        c = self.controller
        worst = (
            c._Controller__COMBAT_CONFIRM_SEC
            + 0.3
            + c._Controller__COMBAT_SUBMIT_ATTEMPTS
            * (c._Controller__COMBAT_ANIMATION_SEC + c._Controller__COMBAT_CONFIRM_SEC)
        )
        self.assertLess(worst, 7.0, f"worst case {worst:.1f}s is too close to the 8s heartbeat")


    def test_every_press_is_logged_and_a_skipped_sequence_logs_none(self):
        """The click log must match reality.

        It used to be written once by the caller, before the lock: a sequence the
        lock skipped still produced a click line, and the extra presses produced
        none. That is what left a 20s window on 2026-08-23 with MTGA's library
        viewer on screen and no click anywhere to explain it.
        """
        self._arm_markers(declared=True, submitted_after=2)
        with mock.patch(
            "Controller.MTGAController.Controller.bot_logger.log_click"
        ) as log_click:
            self._press()
        self.assertEqual(
            log_click.call_count, self.presses, "logged clicks != presses made"
        )
        labels = [c.args[2] for c in log_click.call_args_list]
        self.assertEqual(labels[0], "SUBMIT_BLOCKS", "first press keeps the plain label")
        self.assertTrue(
            labels[1].startswith("SUBMIT_BLOCKS_RETRY"),
            f"a retry must be distinguishable, got {labels[1]!r}",
        )

        # ... and a sequence the lock turns away logs nothing at all.
        self._arm_markers(declared=True, submitted_after=2)
        self.controller._Controller__combat_submit_lock.acquire()
        self.addCleanup(self.controller._Controller__combat_submit_lock.release)
        with mock.patch(
            "Controller.MTGAController.Controller.bot_logger.log_click"
        ) as log_click:
            self.assertFalse(self._press())
        log_click.assert_not_called()

    def test_the_blind_fallback_press_is_marked_as_such(self):
        self._arm_markers(declared=False, submitted_after=None)
        with mock.patch(
            "Controller.MTGAController.Controller.bot_logger.log_click"
        ) as log_click:
            self._press()
        labels = [c.args[2] for c in log_click.call_args_list]
        self.assertIn("SUBMIT_BLOCKS_BLIND", labels)


if __name__ == "__main__":
    unittest.main()
