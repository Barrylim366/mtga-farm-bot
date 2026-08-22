"""Arena's "Report a Player" dialog must be cancelled, never submitted.

The dialog is not a game prompt and no game message announces it, so the bot is
blind to it: while it is up, every screen probe simply fails. It happened twice
on 2026-08-21. The first time the trigger was found and blocked -- a blind
Home-tab click at (104, 39) landing on the opponent's name on the match-end
screen (see test_navigate_home_guard). The second time, at 19:53, there was no
logged click at all between the last DISMISS_END_SCREEN and the open dialog, so
the trigger is unknown and guarding another click path would not have helped.
Both times the bot then failed every probe until the rope expired.

The stall alone would be survivable. What makes this worth a dedicated net is
the Submit Report button sitting next to Cancel, pointed at a real player: the
tests below pin down that Submit can never be the thing that gets clicked.
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState

# Measured from the live capture of the dialog (1920x1080 game frame).
CANCEL_CENTRE = (797, 875)
SUBMIT_CENTRE = (1122, 875)


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._arena_region = (0, 0, 1920, 1080)
    c._ensure_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
    c._map_base_point_into_arena = lambda arena, point: point
    return c


class ReportDialogDismissalTest(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        self.clicks = []
        self.image_clicks = []
        self.c._click_abs = lambda x, y, label: self.clicks.append((x, y, label))

    def _wire(self, *, title_found, cancel_click_ok):
        def locate(path, label, **kwargs):
            if "report_player_title" in path:
                return (960, 205) if title_found else None
            return None

        def click_image(path, label, **kwargs):
            self.image_clicks.append((os.path.basename(path), kwargs.get("rel_region")))
            if "report_player_cancel" in path:
                return cancel_click_ok
            return False

        self.c._locate_image_center_in_scaled_arena_region = locate
        self.c._click_image_in_scaled_arena_region = click_image

    def test_the_dialog_is_cancelled_via_the_button_template(self):
        self._wire(title_found=True, cancel_click_ok=True)
        self.assertTrue(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(
            [name for name, _ in self.image_clicks], ["report_player_cancel.png"]
        )
        self.assertEqual(self.clicks, [], "fell back to a fixed click unnecessarily")

    def test_the_search_band_cannot_contain_the_submit_button(self):
        """The one assertion that matters. The Cancel template is 285px wide, so
        a match must fit entirely inside the search band; Submit Report starts
        around x=988 and could only match from 988 to 1273."""
        self._wire(title_found=True, cancel_click_ok=True)
        self.c._dismiss_report_player_dialog(context="TEST")
        _, rel_region = self.image_clicks[0]
        x, _, w, _ = rel_region
        self.assertLessEqual(
            x + w, 1000,
            "the search band reaches into the Submit Report button",
        )
        self.assertLess(x + w, SUBMIT_CENTRE[0], "band overlaps Submit's centre")

    def test_the_fallback_click_is_cancel_and_nothing_else(self):
        self._wire(title_found=True, cancel_click_ok=False)
        self.assertTrue(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(len(self.clicks), 1)
        x, y, _ = self.clicks[0]
        self.assertEqual((x, y), CANCEL_CENTRE)
        self.assertNotEqual((x, y), SUBMIT_CENTRE)

    def test_nothing_is_clicked_when_the_dialog_is_not_there(self):
        self._wire(title_found=False, cancel_click_ok=True)
        self.assertFalse(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.image_clicks, [], "hunted for Cancel with no dialog up")

    def test_no_absolute_click_without_an_arena_region(self):
        """A fixed-point fallback with no arena region would be a desktop click."""
        self._wire(title_found=True, cancel_click_ok=False)
        self.c._ensure_arena_region = lambda *a, **k: None
        self.assertFalse(self.c._dismiss_report_player_dialog(context="TEST"))
        self.assertEqual(self.clicks, [])


class BlockingOverlayChecksTheDialogFirstTest(unittest.TestCase):
    """The report dialog is checked before the card-viewer templates: it is the
    only thing covering the hand that can do damage outside the game."""

    def setUp(self):
        self.c = make_controller()
        self.order = []
        self.c._dismiss_report_player_dialog = lambda **k: (
            self.order.append("report") or self.found
        )
        self.c._click_image_in_scaled_arena_region = lambda *a, **k: (
            self.order.append("done_button") or False
        )
        self.found = False

    def test_the_report_dialog_is_probed_before_the_done_templates(self):
        self.c._dismiss_blocking_overlay(context="TEST")
        self.assertEqual(self.order[0], "report")

    def test_a_dismissed_report_dialog_short_circuits(self):
        self.found = True
        self.assertTrue(self.c._dismiss_blocking_overlay(context="TEST"))
        self.assertEqual(self.order, ["report"], "kept clicking after the fix")

    def test_a_stop_request_prevents_every_probe(self):
        self.c._stop_requested = True
        self.assertFalse(self.c._dismiss_blocking_overlay(context="TEST"))
        self.assertEqual(self.order, [])


class StuckQueueProbeTest(unittest.TestCase):
    """The net has to reach the place the bot actually gets stuck.

    _dismiss_blocking_overlay only runs after a blind HAND SWEEP, i.e. during a
    match. On 2026-08-22 the dialog opened on TEUBAT's match-end screen instead:
    the queue loop then clicked Play into it for 28 minutes, logging screen
    probes that could not match, and the net never ran because no hand sweep
    ever happened. Unlike the two earlier occurrences there was no logged click
    for six minutes beforehand, so guarding another click path would not have
    helped -- hence a probe keyed on "the queue is getting nowhere".
    """

    def setUp(self):
        self.c = make_controller()
        self.probes = []
        self.c._dismiss_report_player_dialog = lambda **k: (
            self.probes.append(k.get("context")) or True
        )
        self.c._get_state_from_log = lambda: "HOME"
        self.c._queue_progress_ts = 0.0

    def _tick(self, *, seconds_ago):
        self.c._queue_progress_ts = time.time() - seconds_ago
        self.c._probe_report_dialog_if_queue_is_stuck()

    def test_a_short_wait_between_matches_is_not_probed(self):
        self._tick(seconds_ago=10)
        self.assertEqual(self.probes, [], "probed during a normal queue wait")

    def test_a_long_stall_is_probed(self):
        self._tick(seconds_ago=200)
        self.assertEqual(self.probes, ["QUEUE_STALL"])

    def test_a_started_match_resets_the_clock(self):
        self.c._get_state_from_log = lambda: BotState.IN_GAME
        self._tick(seconds_ago=200)
        self.assertEqual(self.probes, [], "probed while a match was running")
        self.assertGreater(self.c._queue_progress_ts, time.time() - 5)

    def test_the_probe_does_not_repeat_every_tick(self):
        """One screen search per interval, not one per 3-second tick."""
        self._tick(seconds_ago=200)
        self.c._probe_report_dialog_if_queue_is_stuck()
        self.c._probe_report_dialog_if_queue_is_stuck()
        self.assertEqual(len(self.probes), 1)

    def test_the_first_tick_only_starts_the_clock(self):
        self.c._queue_progress_ts = 0.0
        self.c._probe_report_dialog_if_queue_is_stuck()
        self.assertEqual(self.probes, [])
        self.assertGreater(self.c._queue_progress_ts, 0.0)

    def test_a_stop_request_silences_the_probe(self):
        self.c._stop_requested = True
        self._tick(seconds_ago=200)
        self.assertEqual(self.probes, [])

    def test_it_never_reaches_the_done_button_templates(self):
        """On Home a false-positive Done match would click into the live UI, so
        this path probes the title-gated report dialog and nothing else."""
        overlay_calls = []
        self.c._dismiss_blocking_overlay = lambda **k: overlay_calls.append(k)
        self._tick(seconds_ago=200)
        self.assertEqual(overlay_calls, [])

    def test_a_throwing_probe_cannot_kill_the_queue_loop(self):
        def boom(**kwargs):
            raise RuntimeError("screen search exploded")

        self.c._dismiss_report_player_dialog = boom
        self._tick(seconds_ago=200)  # must not raise

    def test_the_queue_loop_calls_the_probe(self):
        """Pins the wiring, not just the helper -- the helper existing while
        nothing calls it is exactly the bug this fixes."""
        import inspect

        src = inspect.getsource(Controller._queue_spam_loop)
        self.assertIn("_probe_report_dialog_if_queue_is_stuck", src)


class TemplateAssetsExistTest(unittest.TestCase):
    """The dismissal is a no-op without its templates, and a silent no-op is
    exactly how this failure hid in the first place."""

    def test_both_templates_are_installed(self):
        for name in ("report_player_title.png", "report_player_cancel.png"):
            path = os.path.join(ROOT, "assets", "assert", name)
            with self.subTest(asset=name):
                self.assertTrue(os.path.exists(path), f"missing template: {path}")
                self.assertGreater(os.path.getsize(path), 500, "template looks empty")


if __name__ == "__main__":
    unittest.main()
