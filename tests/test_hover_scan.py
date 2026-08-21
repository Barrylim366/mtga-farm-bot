"""Unit tests for the hand-scan hover parsing and the cast sweep backoff.

Background (observed live, match 317f7a5d, 2026-08-19): the bot decided to play
a land on 9 consecutive decisions and executed none of them. Each cast attempt
swept the whole hand without MTGA emitting a single hover line, so every
decision ended in CAST_UNAVAILABLE and passed priority. It never played a land,
its board stayed empty from turn 2 to turn 6, and MTGA's 150s inactivity timer
expired twice.

Two defects came out of that post-mortem and are pinned here:

1. All three retries re-ran an identical 1000 px/s sweep (10px steps, 10ms
   dwell), each taking exactly 2.0s. Retrying identically cannot find what the
   first pass missed, and a hover is only logged after a client->server->log
   round trip, so a fast sweep can cross a card without one.

2. __parse_hover_id_line would mine an objectId out of a GRE line that
   describes no hover at all (a GameStateMessage is packed with ids): the
   seat-filtered pass fell through to a nested-dict walk and then a regex over
   the whole line. The scan could adopt an unrelated object as "the card under
   the cursor".

   Seat filtering is deliberately NOT attempted -- see the note in
   __parse_hover_id_line. Measured over a real 21MB Player.log: all 1382 bare
   `"objectId": N` fragments sit inside OUTGOING ClientToGREUIMessage blocks, so
   that shape (~97% of identifications) is our own hovers by construction. For
   the remaining compact incoming shape it could not be established which of
   `systemSeatIds` / `uiMessage.seatIds` marks the hoverer, and a filter with
   inverted polarity would drop our own hovers and keep only foreign ones.

The log lines below are verbatim from that session's Player.log.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller

# Verbatim from the session: a compact incoming GRE hover.
GRE_HOVER = (
    '[UnityCrossThreadLogger]==> { "transactionId": "cda48186-afef-4852-85a2-e158d69fe005", '
    '"requestId": 34, "timestamp": "1787131170146", "greToClientEvent": { "greToClientMessages": '
    '[ { "type": "GREMessageType_UIMessage", "systemSeatIds": [ 2 ], "uiMessage": '
    '{ "seatIds": [ 1 ], "onHover": { "objectId": 281 } } } ] } }'
)
# The mirrored seat combination; both occur and we do not distinguish them.
GRE_HOVER_MIRRORED = GRE_HOVER.replace('"seatIds": [ 1 ]', '"seatIds": [ 2 ]').replace(
    '"systemSeatIds": [ 2 ]', '"systemSeatIds": [ 1 ]'
).replace('"objectId": 281', '"objectId": 343')
# A fragment from an OUTGOING ClientToGREUIMessage block: our own hover.
BARE_FRAGMENT = '"objectId": 343'
# A GRE line stuffed with ids but describing no hover.
GAMESTATE_LINE = (
    '[UnityCrossThreadLogger]==> { "greToClientEvent": { "greToClientMessages": '
    '[ { "type": "GREMessageType_GameStateMessage", "systemSeatIds": [ 2 ], '
    '"gameStateMessage": { "annotations": [ { "objectId": 999 } ] } } ] } }'
)


def _controller():
    log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    log.close()
    return Controller(log.name)


def _parse(controller, line):
    # Name-mangled private helper.
    return controller._Controller__parse_hover_id_line(line)


def _set_seat(controller, seat):
    controller._Controller__system_seat_id = seat


class HoverLineParsingTests(unittest.TestCase):
    """Only a line that actually describes a hover may yield an id."""

    def setUp(self):
        self.c = _controller()
        _set_seat(self.c, 2)

    def test_gamestate_line_is_not_mined_for_an_object_id(self):
        """The regression. A GameStateMessage carries no hover but is packed with
        ids; the old code fell through to a nested-dict walk and then a regex over
        the whole line, so the scan could adopt an unrelated object as the card
        under the cursor -- and then clear_cast_suppression() for it."""
        self.assertIn('"objectId": 999', GAMESTATE_LINE)
        self.assertIsNone(_parse(self.c, GAMESTATE_LINE))

    def test_bare_fragment_is_returned(self):
        """~97% of identifications come from this shape, and all 1382 in a real
        Player.log sat inside an OUTGOING ClientToGREUIMessage block, i.e. they
        are our own hovers. Rejecting them would stop the bot playing entirely."""
        self.assertEqual(_parse(self.c, BARE_FRAGMENT), 343)

    def test_compact_gre_hover_is_returned_for_both_seat_combinations(self):
        """Deliberately seat-agnostic: which field marks the hoverer in the
        relayed shape is unproven, and a filter with inverted polarity would drop
        our own hovers and keep only foreign ones. Both combinations occur."""
        self.assertEqual(_parse(self.c, GRE_HOVER), 281)
        self.assertEqual(_parse(self.c, GRE_HOVER_MIRRORED), 343)

    def test_seat_is_not_consulted_at_all(self):
        """Pins the decision: the answer must not depend on our seat, so nobody
        reintroduces a polarity guess without new evidence."""
        results = []
        for seat in (1, 2, None):
            _set_seat(self.c, seat)
            results.append((
                _parse(self.c, GRE_HOVER),
                _parse(self.c, GRE_HOVER_MIRRORED),
                _parse(self.c, BARE_FRAGMENT),
            ))
        self.assertEqual(len(set(results)), 1, f"seat changed the outcome: {results}")

    def test_gre_line_without_a_hover_returns_none_even_with_a_ui_message(self):
        line = (
            '{ "greToClientEvent": { "greToClientMessages": [ { "uiMessage": '
            '{ "seatIds": [ 1 ], "onSelect": { "objectId": 77 } } } ] } }'
        )
        self.assertIsNone(_parse(self.c, line))

    def test_first_hover_wins_when_a_line_carries_several(self):
        line = (
            '{ "greToClientEvent": { "greToClientMessages": ['
            '{ "uiMessage": { "seatIds": [ 1 ], "onHover": { "objectId": 281 } } },'
            '{ "uiMessage": { "seatIds": [ 2 ], "onHover": { "objectId": 343 } } } ] } }'
        )
        self.assertEqual(_parse(self.c, line), 281)

    def test_empty_and_garbage_lines(self):
        for line in ("", None, "no json here", "{ broken"):
            with self.subTest(line=line):
                self.assertIsNone(_parse(self.c, line))

    def test_non_json_line_still_falls_back_to_the_regex(self):
        """Malformed JSON must not lose a hover that is plainly in the text."""
        self.assertEqual(_parse(self.c, 'garbage { "objectId": 512 '), 512)


class CastSweepBackoffTests(unittest.TestCase):
    """The sweep must get slower and finer each attempt, never repeat itself."""

    def test_pacing_is_strictly_gentler_each_attempt(self):
        pacing = Controller._CAST_SWEEP_PACING
        self.assertGreaterEqual(len(pacing), 2, "a single pacing is no backoff at all")
        # Strict, not sorted(): a non-strict check accepts
        # ((10,0.01),(10,0.02),...) where the step size never actually shrinks.
        for i in range(1, len(pacing)):
            self.assertLess(pacing[i][0], pacing[i - 1][0], f"step must shrink at {i}")
            self.assertGreater(pacing[i][1], pacing[i - 1][1], f"dwell must grow at {i}")

    def test_first_attempt_keeps_the_original_fast_pacing(self):
        """The healthy path must not get slower; only failures pay for the retry."""
        self.assertEqual(Controller._CAST_SWEEP_PACING[0], (10, 0.01))

    def test_each_attempt_sweeps_slower_in_wall_clock_terms(self):
        """Steps x dwell over a fixed width: the point is more dwell time per
        pixel crossed, which is what gives MTGA a chance to emit the hover."""
        width = 1920.0
        times = [
            (width / step) * dwell for step, dwell in Controller._CAST_SWEEP_PACING
        ]
        # Strictly increasing: `sorted()` alone would accept three identical
        # sweeps, which is exactly the bug being fixed.
        for i in range(1, len(times)):
            self.assertGreater(
                times[i], times[i - 1],
                f"attempt {i} is not slower than {i - 1}: {times}",
            )

    def test_worst_case_total_stays_well_inside_the_inactivity_timer(self):
        """MTGA's inactivity timer is 150s and expiring it is what lost the game
        in the post-mortem, so the whole of cast() must stay far from it.

        Sweep time alone understates the cost: each attempt also pays a fixed
        overhead (window focus, the reset settle, the "Are You Sure?" probe, the
        inter-attempt pause) that the code documents per attempt, plus the
        one-off inactive-window recovery. Budgeting only the sweeps would let the
        pacing grow while the real figure quietly doubled.
        """
        width = 1920.0
        sweeping = sum(
            (width / step) * dwell for step, dwell in Controller._CAST_SWEEP_PACING
        )
        fixed = (
            len(Controller._CAST_SWEEP_PACING) * Controller._CAST_ATTEMPT_FIXED_COST_SEC
            + Controller._CAST_REACTIVATION_COST_SEC
        )
        total = sweeping + fixed
        self.assertLess(
            total, 45.0,
            f"cast() worst case too large: {sweeping:.1f}s sweeping + {fixed:.1f}s fixed "
            f"= {total:.1f}s (inactivity timer is 150s)",
        )
        # And it must remain a small fraction of the timer, not merely under it.
        self.assertLess(total, 150.0 / 3)

    def test_cast_drives_every_pacing_step_in_order(self):
        c = _controller()
        c._buttons_dir = lambda: tempfile.mkdtemp()
        attempts: list[int] = []

        def cast_once(card_id, *, attempt=0):
            attempts.append(attempt)
            return False

        c._cast_once = cast_once
        c._dismiss_are_you_sure_if_present = lambda **kw: False

        import unittest.mock as mock

        # cast() arms a real threading.Timer on failure (__schedule_group_resume).
        # Patch the Timer factory itself rather than assigning over the mangled
        # private method: that assignment would silently create a new attribute if
        # the method were ever renamed, and the genuine one would then start a live
        # thread inside the test run.
        with mock.patch("Controller.MTGAController.Controller.time.sleep"),              mock.patch("Controller.MTGAController.Controller.threading.Timer") as timer:
            self.assertFalse(c.cast(343))

        self.assertTrue(timer.called, "expected the resume timer to be armed")
        self.assertTrue(
            timer.return_value.start.called, "the armed timer must be started"
        )

        self.assertEqual(attempts, list(range(len(Controller._CAST_SWEEP_PACING))))

    def test_cast_stops_at_the_first_successful_attempt(self):
        c = _controller()
        attempts: list[int] = []

        def cast_once(card_id, *, attempt=0):
            attempts.append(attempt)
            return attempt == 1

        c._cast_once = cast_once
        c._dismiss_are_you_sure_if_present = lambda **kw: False

        import unittest.mock as mock

        with mock.patch("Controller.MTGAController.Controller.time.sleep"):
            self.assertTrue(c.cast(343))

        self.assertEqual(attempts, [0, 1])

    def test_rejected_hovers_do_not_stall_the_sweep(self):
        """The scan does `if parsed is None: continue` inside its outer loop, so a
        parser that now returns None where it used to return an id could in
        principle spin without ever moving the mouse again.

        It cannot, and this pins why: has_new_line() reads the pending QUEUE and
        get_latest_line_containing_pattern() popleft()s from it, so every rejected
        line is consumed. Once the queue drains, the inner move loop runs again.
        A burst of hover-less GRE lines therefore costs iterations, never
        progress.
        """
        from collections import deque

        # Lines that parse to None: GRE traffic carrying no hover.
        queue = deque([GAMESTATE_LINE] * 5)
        consumed: list[str] = []

        class FakeLogReader:
            def has_new_line(self, pattern):
                return bool(queue)

            def get_latest_line_containing_pattern(self, pattern):
                line = queue.popleft()
                consumed.append(line)
                return line

            def clear_new_line_flag(self, pattern):
                queue.clear()

        c = _controller()
        _set_seat(c, 2)
        reader = FakeLogReader()

        # Drive the same shape the scan loop uses.
        current_hovered_id = None
        iterations = 0
        while current_hovered_id != 343 and iterations < 50:
            iterations += 1
            if not reader.has_new_line("objectId"):
                break  # stands in for "move the mouse and re-check"
            parsed = _parse(c, reader.get_latest_line_containing_pattern("objectId"))
            if parsed is None:
                continue
            current_hovered_id = parsed

        self.assertEqual(len(consumed), 5, "every rejected line must be consumed")
        self.assertFalse(queue, "queue must drain so the mouse moves again")
        self.assertLess(iterations, 50, "did not spin")

    def test_out_of_range_attempt_clamps_instead_of_raising(self):
        """_cast_once indexes the pacing table; a caller passing a stale attempt
        number must not crash the decision loop."""
        c = _controller()
        c._ensure_options_overlay_closed = lambda **kw: False  # bail right after pacing
        for attempt in (-5, 0, 99):
            with self.subTest(attempt=attempt):
                self.assertFalse(c._cast_once(343, attempt=attempt))


if __name__ == "__main__":
    unittest.main()
