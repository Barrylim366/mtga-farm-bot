"""Arena's "Report a Player" dialog must be cancelled, never submitted.

The dialog is not a game prompt and no game message announces it, so the bot is
blind to it: while it is up, every screen probe simply fails. It happened three
times over 2026-08-21/22. Twice the trigger was a click that could be identified
afterwards; the third time, on 2026-08-22, there was no logged click for six
minutes beforehand, so the trigger is unknown and guarding another click path
would not have helped. Each time the bot then failed every probe until the rope
expired -- the last one for 28 minutes, with a won match sitting unclaimed
behind the dialog.

The stall alone would be survivable. What makes this worth a dedicated net is
the Submit Report button sitting next to Cancel, pointed at a real player: the
tests below pin down that Submit can never be the thing that gets clicked.

The net hangs off the queue loop only. Its original second hook, in
_dismiss_blocking_overlay, went away with the blind-sweep rescue in the 1.2.1
click-path revert (1eb512d); if that path ever comes back, the in-match hook has
to come back with its own test.
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
    # A unit test must never search the real monitor: unstubbed, these two go
    # looking at whatever MTGA is doing right now and can click for real.
    c._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
    c._click_image_in_scaled_arena_region = lambda *a, **k: False
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


class StuckQueueProbeTest(unittest.TestCase):
    """The net has to reach the place the bot actually gets stuck.

    On 2026-08-22 the dialog opened on TEUBAT's match-end screen: the queue loop
    then clicked Play into it for 28 minutes, logging screen probes that could
    not match. There was no logged click for six minutes beforehand, so guarding
    another click path would not have helped -- hence a probe keyed on "the
    queue is getting nowhere".
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

    def test_only_the_title_gated_dialog_is_ever_touched(self):
        """This runs on Home, where a false-positive match on any other template
        would click into the live UI. Nothing but the report dialog is probed."""
        c = make_controller()
        c._get_state_from_log = lambda: "HOME"
        searched = []
        c._locate_image_center_in_scaled_arena_region = lambda path, label, **k: (
            searched.append(os.path.basename(path)) or None
        )
        clicked = []
        c._click_image_in_scaled_arena_region = lambda path, label, **k: (
            clicked.append(os.path.basename(path)) or False
        )
        c._queue_progress_ts = time.time() - 200
        c._probe_report_dialog_if_queue_is_stuck()
        self.assertEqual(searched, ["report_player_title.png"])
        self.assertEqual(clicked, [], "clicked a template before the title matched")

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
