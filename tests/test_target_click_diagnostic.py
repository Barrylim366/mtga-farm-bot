"""A target click that had no effect must leave measurable evidence.

The hover scan proves the cursor was over the right object -- MTGA named it by
instanceId -- and the click is logged. Nothing in the log distinguishes "clicked
the card" from "clicked twenty pixels past its edge", and the difference is
expensive: while a target selection is pending, MTGA emits no hand hovers, so
every following cast sweep goes blind for reasons that look nothing like the
real cause.

Live on 2026-08-21: Scorching Dragonfire's only legal target was chosen
correctly (creature 285, `can_hit_face: False`), SELECT_OPP_BATTLEFIELD_ITEM
clicked at arena-relative (852, 314), and the prompt was still open 40 seconds
later; three blind hand sweeps followed and the bot came within seconds of an
emergency concede. select_opponent_battlefield_permanent's own docstring calls
its scan band "a first estimate [that] needs in-game calibration" -- this
bundle is what a calibration can be measured from.

These tests pin down that the check is diagnostic only: it clicks nothing, it
fires only on the failure, and an exception inside it cannot escape into the
selection flow.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._arena_region = (1519, 128, 1920, 1080)
    return c


class TargetClickCheckTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        self.bundles = []
        self.c._write_target_click_debug_bundle = lambda **k: self.bundles.append(k)
        # Run the timer body inline so the test is deterministic.
        self.fired = []

        class InlineTimer:
            def __init__(_s, delay, fn, *a, **k):
                _s.fn = fn

            def start(_s):
                self.fired.append(True)
                _s.fn()

        import Controller.MTGAController.Controller as mod

        self._orig = mod.threading.Timer
        mod.threading.Timer = InlineTimer
        self.addCleanup(lambda: setattr(mod.threading, "Timer", self._orig))

    def _set_pending(self, pending):
        self.c._Controller__pending_target_select = pending

    def test_a_pending_prompt_after_the_click_is_photographed(self):
        self._set_pending({"last_target": 285})
        self.c._schedule_target_click_check(card_id=285, label="OPP_BATTLEFIELD_ITEM")
        self.assertEqual(len(self.bundles), 1)
        self.assertEqual(self.bundles[0]["card_id"], 285)

    def test_a_click_that_worked_is_not_photographed(self):
        self._set_pending(None)
        self.c._schedule_target_click_check(card_id=285, label="OPP_BATTLEFIELD_ITEM")
        self.assertEqual(self.bundles, [], "screenshot taken for a click that worked")

    def test_nothing_runs_after_a_stop_request(self):
        self._set_pending({"last_target": 285})
        self.c._stop_requested = True
        self.c._schedule_target_click_check(card_id=285, label="OPP_BATTLEFIELD_ITEM")
        self.assertEqual(self.bundles, [])

    def test_nothing_runs_while_selections_are_suppressed(self):
        self._set_pending({"last_target": 285})
        self.c._suppress_selections = True
        self.c._schedule_target_click_check(card_id=285, label="OPP_BATTLEFIELD_ITEM")
        self.assertEqual(self.bundles, [])

    def test_a_failing_bundle_writer_cannot_break_the_caller(self):
        """This runs on a timer thread inside the selection flow; an exception
        escaping here would be a diagnostic that breaks targeting."""
        self._set_pending({"last_target": 285})

        def boom(**kwargs):
            raise RuntimeError("screenshot failed")

        self.c._write_target_click_debug_bundle = boom
        self.c._schedule_target_click_check(card_id=285, label="OPP_BATTLEFIELD_ITEM")

    def test_the_check_clicks_nothing(self):
        clicks = []
        self.c.input = type("I", (), {
            "left_click": lambda *a, **k: clicks.append(1),
            "move_abs": lambda *a, **k: None,
        })()
        self._set_pending({"last_target": 285})
        self.c._schedule_target_click_check(card_id=285, label="OPP_BATTLEFIELD_ITEM")
        self.assertEqual(clicks, [], "a diagnostic must never touch the board")


class SelectionClickRecordingTest(unittest.TestCase):
    """The written point is what makes the screenshot measurable.

    Asserts on the JSON the bundle actually writes. An earlier version of this
    test asserted `2371 - 1519 == 852`, which is arithmetic, not the payload --
    it would have passed with the field missing entirely.
    """

    def _write_bundle(self, *, click):
        import json

        import bot_logger
        import Controller.MTGAController.Controller as mod

        c = make_controller()
        c._last_selection_click = click
        c._vision = type("V", (), {
            "begin_tick": lambda *a: None,
            "capture": lambda *a, **k: object(),
            "save_image": lambda *a, **k: None,
        })()
        target = tempfile.mkdtemp(prefix="target-click-test-")
        real = bot_logger.ensure_debug_dir
        mod.bot_logger.ensure_debug_dir = lambda *a, **k: target
        try:
            c._write_target_click_debug_bundle(card_id=285, label="OPP_BATTLEFIELD_ITEM")
        finally:
            mod.bot_logger.ensure_debug_dir = real
        with open(os.path.join(target, "target_click_state.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_click_point_is_written_in_arena_relative_form(self):
        """The live numbers: screen (2371, 442) inside an arena at (1519, 128)
        is arena-relative (852, 314) -- directly comparable to the card in the
        screenshot next to it."""
        payload = self._write_bundle(click={
            "point": (2371, 442),
            "label": "OPP_BATTLEFIELD_ITEM",
            "card_id": 285,
            "arena": (1519, 128, 1920, 1080),
            "ts": 0.0,
        })
        self.assertEqual(payload["click_point_arena_relative"], [852, 314])
        self.assertEqual(payload["click_point_screen"], [2371, 442])
        self.assertEqual(payload["card_id"], 285)

    def test_a_missing_click_record_still_produces_a_bundle(self):
        """Never let the diagnostic be the thing that raises."""
        payload = self._write_bundle(click=None)
        self.assertIsNone(payload["click_point_screen"])
        self.assertEqual(payload["card_id"], 285)


if __name__ == "__main__":
    unittest.main()
