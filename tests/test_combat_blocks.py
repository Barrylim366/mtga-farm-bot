"""Phase 1b: the bot actually declares blocks.

The contract these pin, in order of how much they matter:

  1. every failure path still presses the button. A block that cannot be
     assigned costs us a blocker; a combat that never submits costs the rope and
     then the match;
  2. the blocker is clicked on our row and the attacker on the opponent's, and
     the button submits afterwards;
  3. MTGA_COMBAT_BLOCKS=0 still restores the old no-blocks behaviour exactly --
     blocking is on by default now, so the escape hatch is what has to work if a
     live session misbehaves.

Controller uses name-mangled double-underscore attributes; from outside the
class body they must be accessed as `_Controller__name`.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.test_combat_shadow import (
    MY_SEAT,
    OPP_SEAT,
    blockers_req_line,
    cleanup,
    creature,
    isolate_debug_captures,
    make_controller,
    seed_board,
)

BLOCKS_ON = {"MTGA_COMBAT_BLOCKS": "1"}


class DeclareBlocksTest(unittest.TestCase):
    def setUp(self):
        self.controller = make_controller()
        self.controller._suppress_selections = False
        self.addCleanup(cleanup, self.controller)
        # Screen captures are the one part of this handler that touches the
        # outside world on import; the calibration bundle has its own test.
        patcher = mock.patch.object(self.controller, "_write_declare_block_debug_bundle")
        self.capture = patcher.start()
        self.addCleanup(patcher.stop)

    def _good_block_board(self):
        """A 6/6 of ours that kills an attacking 5/5 and lives."""
        seed_board(
            self.controller,
            [creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT), creature(20, MY_SEAT, 6, 6)],
            my_life=20,
        )

    def test_blocks_are_on_by_default(self):
        self._good_block_board()
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch("threading.Timer") as timer:
            os.environ.pop("MTGA_COMBAT_BLOCKS", None)
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )

        self.assertEqual(record.call_args[0][1], "declare_blocks")
        self.assertEqual(timer.call_args.kwargs["args"], ([(20, 10)],))

    def test_blocks_can_be_switched_back_off(self):
        """The escape hatch has to work: if a session stalls, MTGA_COMBAT_BLOCKS=0
        must restore the old no-blocks behaviour without a code change."""
        self._good_block_board()
        with mock.patch.dict(os.environ, {"MTGA_COMBAT_BLOCKS": "0"}), \
                mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch.object(self.controller, "select_battlefield_permanent") as select, \
                mock.patch("threading.Timer") as timer:
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )

        self.assertEqual(record.call_args[0][1], "no_blocks")
        select.assert_not_called()
        self.assertEqual(timer.call_args.kwargs["args"], ("NO_BLOCKS",))

    def test_a_block_is_clicked_out_blocker_first_then_attacker(self):
        self._good_block_board()
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch.object(
                    self.controller, "select_battlefield_permanent", return_value=True
                ) as blocker_click, \
                mock.patch.object(
                    self.controller, "select_attacking_creature", return_value=True
                ) as attacker_click, \
                mock.patch.object(
                    self.controller, "_Controller__click_combat_submit_button"
                ) as submit:
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )
            # The handler defers the clicking to a timer; run it inline.
            self.controller._Controller__execute_blocks([(20, 10)])

        self.assertEqual(record.call_args[0][1], "declare_blocks")
        self.assertEqual(record.call_args[0][2], {"assignments": [[20, 10]]})
        blocker_click.assert_called_once_with(20)
        attacker_click.assert_called_once_with(10)
        submit.assert_called_once_with("SUBMIT_BLOCKS")

    def test_an_attacker_we_cannot_find_does_not_strand_a_half_assigned_blocker(self):
        """The blocker click arms an assignment; leaving it armed eats the submit
        click and the turn ropes out. Escape has to clear it."""
        self._good_block_board()
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(self.controller, "select_battlefield_permanent", return_value=True), \
                mock.patch.object(self.controller, "select_attacking_creature", return_value=False), \
                mock.patch.object(self.controller.input, "tap_escape") as escape, \
                mock.patch.object(
                    self.controller, "_Controller__click_combat_submit_button"
                ) as submit:
            self.controller._Controller__execute_blocks([(20, 10)])

        escape.assert_called_once()
        submit.assert_called_once_with("NO_BLOCKS")

    def test_a_blocker_we_cannot_find_is_skipped_and_the_rest_still_run(self):
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(
                    self.controller, "select_battlefield_permanent", side_effect=[False, True]
                ) as blocker_click, \
                mock.patch.object(
                    self.controller, "select_attacking_creature", return_value=True
                ) as attacker_click, \
                mock.patch.object(
                    self.controller, "_Controller__click_combat_submit_button"
                ) as submit:
            self.controller._Controller__execute_blocks([(20, 10), (21, 11)])

        self.assertEqual(blocker_click.call_count, 2)
        attacker_click.assert_called_once_with(11)
        submit.assert_called_once_with("SUBMIT_BLOCKS")

    def test_a_selection_that_throws_still_submits(self):
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(
                    self.controller, "select_battlefield_permanent", side_effect=RuntimeError("boom")
                ), mock.patch.object(
                    self.controller, "_Controller__click_combat_submit_button"
                ) as submit:
            self.controller._Controller__execute_blocks([(20, 10)])

        submit.assert_called_once_with("NO_BLOCKS")

    def test_the_time_budget_stops_scanning_and_submits_what_is_assigned(self):
        """Each hover-scan can run seconds. Blocking must never spend the whole
        rope looking for creatures."""
        self.controller._Controller__combat_blocks_budget_sec = 0.0
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(self.controller, "select_battlefield_permanent") as blocker_click, \
                mock.patch.object(
                    self.controller, "_Controller__click_combat_submit_button"
                ) as submit:
            self.controller._Controller__execute_blocks([(20, 10)])

        blocker_click.assert_not_called()
        submit.assert_called_once_with("NO_BLOCKS")

    def test_no_block_worth_making_takes_the_no_blocks_path_even_when_enabled(self):
        """Enabling blocks does not mean blocking every time: a 1/1 into a 5/5
        with life to spare is CombatLogic declining, not a failure."""
        seed_board(
            self.controller,
            [creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT), creature(20, MY_SEAT, 1, 1)],
            my_life=20,
        )
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(self.controller, "_Controller__record_decision") as record, \
                mock.patch("threading.Timer") as timer:
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )

        self.assertEqual(record.call_args[0][1], "no_blocks")
        self.assertEqual(timer.call_args.kwargs["args"], ("NO_BLOCKS",))


class ResolveRaceTest(unittest.TestCase):
    """resolve() and the block submit are the same button.

    The decision loop wakes up during Step_DeclareBlock, sees it is not our
    priority, and returns `resolve` -- which clicks the bottom-right button. In
    that step the button reads "No Blocks", so a resolve() that lands while
    __execute_blocks is still hunting for a creature submits an empty block step
    and combat damage resolves. Measured on 2026-07-30: 7 of 7 blocks on ordinary
    turns went through, 6 of 7 on lethal turns did not, and the traces show the
    RESOLVE click a fraction of a second before Step_CombatDamage.
    """

    def setUp(self):
        self.controller = make_controller()
        self.controller._suppress_selections = False
        self.addCleanup(cleanup, self.controller)
        patcher = mock.patch.object(self.controller, "_write_declare_block_debug_bundle")
        patcher.start()
        self.addCleanup(patcher.stop)
        seed_board(
            self.controller,
            [creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT), creature(20, MY_SEAT, 6, 6)],
            my_life=20,
        )

    def _resolve_clicked(self) -> bool:
        """True if resolve() got as far as reaching for the button."""
        with mock.patch.object(
            self.controller, "_map_abs_point_to_arena",
            return_value=((0, 0), "absolute_no_arena"),
        ) as mapper:
            self.controller.resolve()
        return mapper.called

    def test_resolve_still_works_when_no_block_is_being_declared(self):
        self.assertTrue(self._resolve_clicked())

    def test_deciding_to_block_mutes_resolve_before_the_timer_hands_off(self):
        """The pause must be armed synchronously: the decision loop can fire
        inside the 0.8s hand-off, and a flag set inside the timer is set too
        late to stop it."""
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(self.controller, "_Controller__record_decision"), \
                mock.patch("threading.Timer"):
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )
        self.assertFalse(self._resolve_clicked())

    def test_declaring_no_blocks_does_not_mute_resolve(self):
        """Nothing is being clicked out, so there is no race to protect against
        -- muting resolve() here would only slow the turn down."""
        seed_board(
            self.controller,
            [creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT), creature(20, MY_SEAT, 1, 1)],
            my_life=20,
        )
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(self.controller, "_Controller__record_decision"), \
                mock.patch("threading.Timer"):
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )
        self.assertTrue(self._resolve_clicked())

    def test_resolve_comes_back_once_the_blocks_are_submitted(self):
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(
                    self.controller, "select_battlefield_permanent", return_value=True
                ), \
                mock.patch.object(
                    self.controller, "select_attacking_creature", return_value=True
                ), \
                mock.patch.object(
                    self.controller, "_Controller__click_combat_submit_button"
                ):
            self.controller._Controller__begin_declare_blocks_pause()
            self.assertFalse(self._resolve_clicked())
            self.controller._Controller__execute_blocks([(20, 10)])
        self.assertTrue(
            self._resolve_clicked(),
            "leaving resolve() muted after the submit would stall every later "
            "priority window of the match",
        )

    def test_the_pause_expires_on_its_own(self):
        """If the executor thread dies mid-assignment the pause has to lapse, or
        resolve() is muted for the rest of the match -- a far worse stall than
        the missed block it was protecting."""
        self.controller._Controller__begin_declare_blocks_pause()
        self.assertFalse(self._resolve_clicked())
        self.controller._Controller__declaring_blocks_until = time.time() - 0.01
        self.assertTrue(self._resolve_clicked())

    def test_a_failed_assignment_still_releases_resolve(self):
        with mock.patch.dict(os.environ, BLOCKS_ON), \
                mock.patch.object(
                    self.controller, "select_battlefield_permanent", return_value=False
                ), \
                mock.patch.object(
                    self.controller, "_Controller__click_combat_submit_button"
                ):
            self.controller._Controller__begin_declare_blocks_pause()
            self.controller._Controller__execute_blocks([(20, 10)])
        self.assertTrue(self._resolve_clicked())


class CalibrationCaptureTest(unittest.TestCase):
    """The capture is what makes the board layout measurable, so it has to run on
    an ordinary blocks-off session -- not only when blocking is switched on."""

    def setUp(self):
        self.controller = make_controller()
        self.controller._suppress_selections = False
        self.addCleanup(cleanup, self.controller)
        self.debug_root = isolate_debug_captures(self, self.controller)

    def test_capture_runs_even_when_blocking_is_switched_off(self):
        """Someone who turns blocking off still gets the board evidence, which is
        what makes a bad session diagnosable."""
        seed_board(
            self.controller,
            [creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT), creature(20, MY_SEAT, 6, 6)],
        )
        with mock.patch.dict(os.environ, {"MTGA_COMBAT_BLOCKS": "0"}), \
                mock.patch.object(self.controller, "_Controller__record_decision"), \
                mock.patch("threading.Timer"), \
                mock.patch.object(
                    self.controller, "_write_declare_block_debug_bundle"
                ) as capture:
            self.controller._Controller__handle_declare_blockers_req(
                blockers_req_line((20, [10]))
            )

        capture.assert_called_once()
        self.assertFalse(capture.call_args.kwargs["executing"])
        self.assertEqual(capture.call_args.kwargs["shadow"]["assignments"], [(20, 10)])

    def test_capture_is_bounded_per_session(self):
        self.controller._Controller__declare_block_capture_limit = 2
        for _ in range(5):
            self.controller._write_declare_block_debug_bundle(shadow=None, executing=False)
        self.assertEqual(self.controller._Controller__declare_block_captures, 2)

    def test_capture_can_be_switched_off(self):
        with mock.patch.dict(os.environ, {"MTGA_COMBAT_BLOCK_CAPTURE": "0"}):
            self.controller._write_declare_block_debug_bundle(shadow=None, executing=False)
        self.assertEqual(self.controller._Controller__declare_block_captures, 0)


if __name__ == "__main__":
    unittest.main()
