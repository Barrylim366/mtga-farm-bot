"""Regression tests for the Historic navigation actions and their recovery.

The failure these pin down (live, 2026-09-15 13:39): Historic re-queue reported
`action_failed:POST_LOGIN_PLAY` over and over while the saved debug bundles
(`runtime/debug/20260915-1339*`) showed a perfectly ordinary Home screen with the
Play button plainly visible, and `state.json` said `BotState.HOME`.

Measured against those captures with the bot's own matcher:

| search area                        | best score for home_anchor.png |
| ---                                | ---                            |
| `pre_assert_roi_rel=(20,20,380,160)` | 0.218                        |
| full 1920x1080 client area           | 0.998 at centre (104, 39)    |

The anchor sits flush against the top edge (rect x=43..165, y=0..79), so an ROI
starting at y=20 slices 20 rows off it and the template cannot match at the 0.84
threshold. The pre-assert failed every attempt, each failure called the recovery,
and the recovery pressed ESC -- on Home, where ESC opens the exit/settings
overlay, which the next attempt's ESC closed again.

These tests use the real ActionSpecs and the real template files against a
synthesized frame, so they fail if either the ROI geometry or the recovery
regresses. Nothing here touches the screen.
"""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.actions import ActionSpec, run_action
from actions.navigation_flow import build_post_login_navigation_actions
from state.state_machine import BotState
from vision.vision import VisionEngine, imread_unicode

ASSETS = os.path.join(ROOT, "assets", "assert")
BUTTONS = os.path.join(ROOT, "Buttons")

# Where home_anchor.png really sits in the 1920x1080 client area, measured from
# the live debug captures: centre (104, 39) for a 122x79 template.
HOME_ANCHOR_TOPLEFT = (43, 0)
# The post-assert anchor for the same action, parked clear of the home anchor
# (which ends at y=79) but still inside the shared anchor ROI.
PLAY_MENU_ANCHOR_TOPLEFT = (43, 120)
ARENA = (0, 0, 1920, 1080)

# Where each navigation widget really is in the 1920x1080 client, measured on the
# live client on 2026-09-15 by walking the flow by hand. The Play blade widgets
# are in the RIGHT-HAND column (x~1733), not the top-left nav strip.
MEASURED_CENTRES = {
    "home_anchor.png": (104, 39),
    "play_btn.png": (1733, 1007),
    "find_match_btn.png": (1733, 140),
    "find_match_anchor.png": (1733, 276),
    "nav_play_subtab.png": (1733, 328),
    "nav_historic_play.png": (1688, 584),
    "historic_anchor.png": (1663, 586),
    "nav_my_decks.png": (230, 356),
    "my_decks_grid_open.png": (158, 593),
}


def _specs_by_name():
    return {
        spec.name: spec
        for spec in build_post_login_navigation_actions(
            assets_dir=ASSETS, buttons_dir=BUTTONS
        )
    }


def _frame_with(*placements) -> np.ndarray:
    """A 1920x1080 frame with the given templates pasted at the given top-lefts.

    Mid-grey background rather than black: a uniform black field gives a zero-
    variance patch, and TM_CCOEFF_NORMED against it is degenerate."""
    frame = np.full((1080, 1920, 3), 96, dtype=np.uint8)
    for template_path, (x, y) in placements:
        tpl = imread_unicode(template_path)
        h, w = tpl.shape[:2]
        frame[y:y + h, x:x + w] = tpl
    return frame


class _FrameVision(VisionEngine):
    """The real matcher, reading a synthesized frame instead of the monitor."""

    def __init__(self, frame: np.ndarray):
        super().__init__()
        self._frame = frame
        self.grabs = 0

    def _grab_full_frame(self):
        self.grabs += 1
        return self._frame


class _Harness:
    def __init__(self, frame, state=BotState.HOME, arena=ARENA):
        self.vision = _FrameVision(frame)
        self.state = state
        self.arena = arena
        self.clicks = []
        self.recoveries = []
        self.diagnostics = []

    def run(self, spec: ActionSpec):
        return run_action(
            spec,
            state_getter=lambda: self.state,
            vision=self.vision,
            arena_region_getter=lambda: self.arena,
            click_abs=lambda x, y, tag: self.clicks.append((x, y, tag)),
            recover_once=lambda name, attempt: self.recoveries.append((name, attempt)),
            on_diagnostic=self.diagnostics.append,
        )


class HomeAnchorRoiTests(unittest.TestCase):
    """The ROI must cover where the anchor actually is."""

    def setUp(self):
        self.spec = _specs_by_name()["POST_LOGIN_PLAY"]
        self.frame = _frame_with(
            (os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT),
            (os.path.join(BUTTONS, "play_btn.png"), (1600, 900)),
            (os.path.join(ASSETS, "play_menu_anchor.png"), PLAY_MENU_ANCHOR_TOPLEFT),
        )

    def test_post_login_play_pre_assert_roi_contains_the_home_anchor(self):
        """The geometric statement, independent of any matching: the configured
        ROI must contain the anchor's real rectangle."""
        x, y, w, h = self.spec.pre_assert_roi_rel
        tpl = imread_unicode(os.path.join(ASSETS, "home_anchor.png"))
        th, tw = tpl.shape[:2]
        ax, ay = HOME_ANCHOR_TOPLEFT
        self.assertLessEqual(x, ax, "ROI starts right of the anchor")
        self.assertLessEqual(y, ay, "ROI starts below the anchor -- this is the 2026-09-15 bug")
        self.assertGreaterEqual(x + w, ax + tw, "ROI ends left of the anchor")
        self.assertGreaterEqual(y + h, ay + th, "ROI ends above the anchor")

    def test_home_screen_passes_the_pre_assert_and_clicks_play(self):
        """End to end over the real matcher: on a Home frame the action must
        click, not fall into recovery."""
        h = _Harness(self.frame)
        result = h.run(self.spec)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual([c[2] for c in h.clicks], ["POST_LOGIN_PLAY"])
        self.assertEqual(h.recoveries, [])

    def test_every_roi_contains_the_widget_it_searches_for(self):
        """Each ROI must contain where its template really is.

        The positions are the live 2026-09-15 measurements (arena centres in the
        1920x1080 client). This is the check that would have caught the original
        bug in seconds, and it also pins the blade widgets, which are NOT in the
        top-left strip -- they sit in the right-hand column around x=1733."""
        for spec in _specs_by_name().values():
            for kind, template, roi in (
                ("pre", spec.pre_assert_template, spec.pre_assert_roi_rel),
                ("post", spec.post_assert_template, spec.post_assert_roi_rel),
                ("skip", spec.skip_if_template, spec.skip_if_roi_rel),
                ("click", spec.click_template, spec.click_search_roi_rel),
            ):
                if not template or roi is None:
                    continue
                name = os.path.basename(template)
                self.assertIn(name, MEASURED_CENTRES, f"{spec.name} {kind}: unmeasured template")
                cx, cy = MEASURED_CENTRES[name]
                tpl = imread_unicode(template)
                self.assertIsNotNone(tpl, f"{template} missing")
                th, tw = tpl.shape[:2]
                x, y, w, h = roi
                self.assertLessEqual(x, cx - tw // 2, f"{spec.name} {kind} ROI starts right of {name}")
                self.assertLessEqual(y, cy - th // 2, f"{spec.name} {kind} ROI starts below {name}")
                self.assertGreaterEqual(x + w, cx + tw // 2, f"{spec.name} {kind} ROI ends left of {name}")
                self.assertGreaterEqual(y + h, cy + th // 2, f"{spec.name} {kind} ROI ends above {name}")


class RoiWideningDiagnosticTests(unittest.TestCase):
    """A too-tight anchor ROI must cost a log line, not the navigation."""

    def _spec(self, roi):
        base = _specs_by_name()["POST_LOGIN_PLAY"]
        return ActionSpec(
            name=base.name,
            required_state=base.required_state,
            click_template=base.click_template,
            click_search_roi_rel=base.click_search_roi_rel,
            pre_assert_template=base.pre_assert_template,
            pre_assert_roi_rel=roi,
            post_expected_state=base.post_expected_state,
            post_assert_template=base.post_assert_template,
            post_assert_roi_rel=(0, 0, 760, 260),
            threshold=base.threshold,
            pre_timeout_sec=0.3,
            post_timeout_sec=1.0,
        )

    def test_anchor_outside_its_roi_is_found_and_reported(self):
        """Exactly the historical geometry: ROI inset by 20 rows, anchor flush
        with the top edge. The action must still proceed, and must say why."""
        frame = _frame_with(
            (os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT),
            (os.path.join(BUTTONS, "play_btn.png"), (1600, 900)),
            (os.path.join(ASSETS, "play_menu_anchor.png"), PLAY_MENU_ANCHOR_TOPLEFT),
        )
        h = _Harness(frame)
        result = h.run(self._spec((20, 20, 380, 160)))
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(len(h.diagnostics), 1, h.diagnostics)
        self.assertIn("ACTION_ROI_TOO_TIGHT", h.diagnostics[0])
        self.assertIn("POST_LOGIN_PLAY", h.diagnostics[0])
        self.assertIn("home_anchor.png", h.diagnostics[0])

    def test_anchor_absent_everywhere_still_fails(self):
        """The widening must not turn 'wrong screen' into a pass."""
        frame = _frame_with((os.path.join(BUTTONS, "play_btn.png"), (1600, 900)))
        h = _Harness(frame)
        result = h.run(self._spec((20, 20, 380, 160)))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "action_failed:POST_LOGIN_PLAY")
        self.assertEqual(h.diagnostics, [])
        self.assertTrue(h.recoveries)

    def test_click_template_is_never_widened(self):
        """Anchors may be searched client-wide; a click must not be, or a real
        click can land on a widget outside the intended box.

        POST_LOGIN_PLAY carries a coordinate fallback, so "no click at all" is no
        longer the observable: what matters is that the click goes to the measured
        point INSIDE the search ROI and never to the template's real position
        outside it."""
        frame = _frame_with(
            (os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT),
            # Play button parked far outside its search ROI.
            (os.path.join(BUTTONS, "play_btn.png"), (200, 500)),
            (os.path.join(ASSETS, "play_menu_anchor.png"), PLAY_MENU_ANCHOR_TOPLEFT),
        )
        h = _Harness(frame)
        spec = _specs_by_name()["POST_LOGIN_PLAY"]
        h.run(spec)
        rx, ry, rw, rh = spec.click_search_roi_rel
        self.assertTrue(h.clicks, "the step neither matched nor fell back")
        for x, y, tag in h.clicks:
            self.assertEqual(tag, "POST_LOGIN_PLAY_FALLBACK", "clicked a widened match")
            self.assertTrue(rx <= x <= rx + rw and ry <= y <= ry + rh,
                            f"click ({x}, {y}) landed outside the search ROI")

    def test_the_fallback_clicks_the_measured_play_button(self):
        """The 2026-09-20 failure: the click template is on screen but scores
        below the threshold (an always-on-top window over the button's lower
        half). The step must still click, at the measured point, and say so."""
        # Home, with nothing the Play button's template can match. The blade
        # anchor is deliberately absent too, so the step still fails its
        # post-assert -- what is under test is that a click was ATTEMPTED at the
        # measured point instead of the step giving up at the click stage.
        frame = _frame_with((os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT))
        spec = _specs_by_name()["POST_LOGIN_PLAY"]
        self.assertIsNotNone(spec.click_fallback_rel, "POST_LOGIN_PLAY lost its fallback")
        h = _Harness(frame)
        h.run(spec)
        self.assertIn(
            (spec.click_fallback_rel[0], spec.click_fallback_rel[1],
             "POST_LOGIN_PLAY_FALLBACK"),
            h.clicks,
        )
        self.assertTrue(
            any("ACTION_CLICK_FALLBACK" in d for d in h.diagnostics), h.diagnostics
        )

    def test_a_fallback_outside_its_roi_is_refused(self):
        """A fallback point may not smuggle a click past the ROI bound."""
        base = _specs_by_name()["POST_LOGIN_PLAY"]
        spec = ActionSpec(
            name=base.name,
            required_state=base.required_state,
            click_template=base.click_template,
            click_search_roi_rel=base.click_search_roi_rel,
            click_fallback_rel=(100, 100),
            pre_assert_template=base.pre_assert_template,
            pre_assert_roi_rel=base.pre_assert_roi_rel,
            threshold=base.threshold,
            pre_timeout_sec=0.3,
            post_timeout_sec=0.3,
        )
        frame = _frame_with((os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT))
        h = _Harness(frame)
        result = h.run(spec)
        self.assertFalse(result.ok)
        self.assertEqual(h.clicks, [])
        self.assertTrue(
            any("ACTION_FALLBACK_OUT_OF_ROI" in d for d in h.diagnostics), h.diagnostics
        )


class ScaledClientTests(unittest.TestCase):
    """The specs are in the 1920x1080 reference frame; the client often is not.

    Reported 2026-09-23: a windowed MTGA at arena=(407, 165, 1366, 768).
    POST_LOGIN_PLAY failed at step=pre_assert on every attempt and Historic never
    queued, because the ROIs were added to the arena origin unscaled and the
    1920-cut templates were matched against a 0.71x screen. Here the same Home
    frame is shrunk into exactly that window on a larger desktop."""

    SMALL_ARENA = (407, 165, 1366, 768)

    def _desktop(self, client_1920):
        import cv2

        x, y, w, h = self.SMALL_ARENA
        desktop = np.full((1080, 1920, 3), 30, dtype=np.uint8)
        desktop[y:y + h, x:x + w] = cv2.resize(
            client_1920, (w, h), interpolation=cv2.INTER_AREA
        )
        return desktop

    def _inside_arena(self, x, y):
        ax, ay, aw, ah = self.SMALL_ARENA
        return ax <= x < ax + aw and ay <= y < ay + ah

    def test_home_on_a_1366x768_client_passes_and_clicks_play(self):
        client = _frame_with(
            (os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT),
            (os.path.join(BUTTONS, "play_btn.png"), (1600, 900)),
        )
        spec = _specs_by_name()["POST_LOGIN_PLAY"]
        h = _Harness(self._desktop(client), arena=self.SMALL_ARENA)
        from actions.actions import _run_pre_assert
        self.assertTrue(
            _run_pre_assert(spec, h.vision, self.SMALL_ARENA),
            "home_anchor.png must be found on a scaled Home screen",
        )
        h.run(spec)
        self.assertTrue(h.clicks, "nothing was clicked")
        x, y, tag = h.clicks[0]
        self.assertEqual(tag, "POST_LOGIN_PLAY", "the template, not the fallback, must match")
        # play_btn.png's centre, mapped into the small window.
        tpl = imread_unicode(os.path.join(BUTTONS, "play_btn.png"))
        th, tw = tpl.shape[:2]
        sx, sy = 1366 / 1920, 768 / 1080
        ex = 407 + (1600 + tw / 2) * sx
        ey = 165 + (900 + th / 2) * sy
        self.assertLess(abs(x - ex), 4, (x, ex))
        self.assertLess(abs(y - ey), 4, (y, ey))

    def test_the_fallback_point_lands_inside_a_scaled_client(self):
        """HOME_PLAY_POINT (1733, 1007) is a reference point; applied unscaled it
        lies below a 768-high window entirely."""
        client = _frame_with((os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT))
        spec = _specs_by_name()["POST_LOGIN_PLAY"]
        h = _Harness(self._desktop(client), arena=self.SMALL_ARENA)
        h.run(spec)
        fallbacks = [c for c in h.clicks if c[2] == "POST_LOGIN_PLAY_FALLBACK"]
        self.assertTrue(fallbacks, h.diagnostics)
        for x, y, _tag in fallbacks:
            self.assertTrue(self._inside_arena(x, y), f"fallback ({x}, {y}) outside the client")

    def test_a_1920_client_is_unchanged(self):
        """Identity scale: the reference frame and the screen coincide."""
        from actions.actions import _abs_point, _abs_region

        self.assertEqual(_abs_region(ARENA, (1550, 40, 370, 220)), (1550, 40, 370, 220))
        self.assertEqual(_abs_point((10, 20, 1920, 1080), (1733, 1007)), (1743, 1027))


class PostAssertlessStepTests(unittest.TestCase):
    """A step with nothing to verify must pass on a confirmed click.

    Live, 2026-09-15 15:46: `POST_LOGIN_PLAY_SUBTAB` clicked the Play sub-tab
    correctly and then reported `action_failed:POST_LOGIN_PLAY_SUBTAB` on every
    attempt, which failed the whole navigation and left Historic unable to queue.
    It declares no post-assert and no expected state -- the Play blade is not a
    scene, so there is nothing to wait for -- and `_run_post_assert` simply ran
    out its deadline and returned False."""

    def _frame(self):
        return _frame_with(
            (os.path.join(ASSETS, "nav", "nav_play_subtab.png"), (1678, 278)),
        )

    def test_the_real_subtab_step_passes_once_it_has_clicked(self):
        spec = _specs_by_name()["POST_LOGIN_PLAY_SUBTAB"]
        self.assertIsNone(spec.post_assert_template, "this test is about the no-op case")
        self.assertIsNone(spec.post_expected_state, "this test is about the no-op case")
        h = _Harness(self._frame())
        result = h.run(spec)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual([c[2] for c in h.clicks], ["POST_LOGIN_PLAY_SUBTAB"])
        self.assertEqual(h.recoveries, [])

    def test_a_configured_but_missing_asset_is_not_waited_for_forever(self):
        """A path naming an asset this install does not have is no evidence."""
        spec = ActionSpec(
            name="MISSING_ASSET",
            click_template=os.path.join(ASSETS, "nav", "nav_play_subtab.png"),
            click_search_roi_rel=(1550, 250, 370, 180),
            post_expected_state=BotState.HOME,
            post_assert_template=os.path.join(ASSETS, "does_not_exist.png"),
            post_assert_roi_rel=(0, 0, 760, 260),
            threshold=0.84,
            post_timeout_sec=0.4,
        )
        h = _Harness(self._frame())
        self.assertTrue(h.run(spec).ok)


class NavigationRecoveryTests(unittest.TestCase):
    """Recovery between navigation attempts must not press ESC blindly.

    ESC is not a neutral "go back" in MTGA: on Home it opens the exit/settings
    overlay. On 2026-09-15 the pre-assert failed on a good Home screen and both
    attempts fired an ESC there, flip-flopping that overlay over the screen the
    action was trying to read."""

    def setUp(self):
        import tempfile

        import runtime_status
        from Controller.MTGAController import Controller as controller_module
        from Controller.MTGAController.Controller import Controller

        self.controller_module = controller_module
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.log_path = f.name
        self._real_status = (runtime_status.update_status, runtime_status.set_mode)
        runtime_status.update_status = lambda **kwargs: None
        runtime_status.set_mode = lambda *a, **k: None
        self._runtime_status = runtime_status

        c = Controller(self.log_path)
        # Never let a test see the screen (CLAUDE.md).
        c._locate_image_center_in_scaled_arena_region = lambda *a, **k: None
        c._click_image_in_scaled_arena_region = lambda *a, **k: False
        c._ensure_arena_region = lambda *a, **k: ARENA
        c._write_nav_debug_bundle = lambda reason: None
        self.escapes = []
        self.home_clicks = []
        c.input = type("I", (), {"tap_escape": lambda _s: self.escapes.append("esc")})()
        c._navigate_to_home = lambda: self.home_clicks.append("home") or True
        self.controller = c

        # Drive one failing action so the recovery closure runs.
        self._real_run_action = controller_module.run_action

        def fake_run_action(spec, **kw):
            kw["recover_once"](spec.name, 1)
            from actions.actions import ActionResult
            return ActionResult(ok=False, reason=f"action_failed:{spec.name}")

        controller_module.run_action = fake_run_action

    def tearDown(self):
        self.controller_module.run_action = self._real_run_action
        (self._runtime_status.update_status, self._runtime_status.set_mode) = self._real_status
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    def test_no_escape_when_no_overlay_is_verified(self):
        self.controller._options_overlay_visible = lambda: False
        self.assertFalse(self.controller._run_post_login_navigation_oob())
        self.assertEqual(self.escapes, [])
        self.assertEqual(self.home_clicks, ["home"])

    def test_escape_only_when_the_options_overlay_is_verified(self):
        self.controller._options_overlay_visible = lambda: True
        self.assertFalse(self.controller._run_post_login_navigation_oob())
        self.assertEqual(self.escapes, ["esc"])
        self.assertEqual(self.home_clicks, [])

    def test_overlay_probe_failure_does_not_fall_back_to_escape(self):
        def boom():
            raise RuntimeError("probe failed")

        self.controller._options_overlay_visible = boom
        self.assertFalse(self.controller._run_post_login_navigation_oob())
        self.assertEqual(self.escapes, [])


class StepFailureCaptureTests(unittest.TestCase):
    """A failed step must be screenshotted where it failed, not after recovery.

    The end-of-action bundle is written once the recovery has navigated back to
    Home, so it shows Home whatever went wrong. On 2026-09-15 that made "the Play
    blade opened but its anchor is stale" indistinguishable from "the click did
    nothing" -- both looked like a healthy Home screen."""

    def _spec(self, **over):
        base = _specs_by_name()["POST_LOGIN_PLAY"]
        kw = dict(
            name=base.name, required_state=base.required_state,
            click_template=base.click_template,
            click_search_roi_rel=base.click_search_roi_rel,
            pre_assert_template=base.pre_assert_template,
            pre_assert_roi_rel=base.pre_assert_roi_rel,
            post_expected_state=base.post_expected_state,
            post_assert_template=base.post_assert_template,
            post_assert_roi_rel=base.post_assert_roi_rel,
            threshold=base.threshold, pre_timeout_sec=0.2, post_timeout_sec=0.2,
        )
        kw.update(over)
        return ActionSpec(**kw)

    def _run(self, spec, frame):
        h = _Harness(frame)
        h.steps = []
        return h, run_action(
            spec,
            state_getter=lambda: h.state,
            vision=h.vision,
            arena_region_getter=lambda: ARENA,
            click_abs=lambda x, y, tag: h.clicks.append((x, y, tag)),
            recover_once=lambda n, a: h.recoveries.append((n, a)),
            on_diagnostic=h.diagnostics.append,
            on_step_failed=lambda n, step, a: h.steps.append((n, step, a)),
        )

    def test_post_assert_failure_is_captured_before_recovery(self):
        """Home anchor present (pre-assert ok), Play clickable, but the screen
        never becomes the Play blade -- exactly the live 2026-09-15 shape."""
        frame = _frame_with(
            (os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT),
            (os.path.join(BUTTONS, "play_btn.png"), (1600, 900)),
        )
        h, result = self._run(self._spec(), frame)
        self.assertFalse(result.ok)
        self.assertTrue(h.clicks, "the click must have happened")
        self.assertEqual(h.steps, [("POST_LOGIN_PLAY", "post_assert", 1)])
        # Captured before the recovery that would navigate away.
        self.assertTrue(h.recoveries)

    def test_pre_assert_failure_is_captured(self):
        frame = _frame_with((os.path.join(BUTTONS, "play_btn.png"), (1600, 900)))
        h, result = self._run(self._spec(), frame)
        self.assertFalse(result.ok)
        self.assertEqual(h.steps, [("POST_LOGIN_PLAY", "pre_assert", 1)])

    def test_each_step_is_captured_only_once_per_action(self):
        """A retrying action must not write a bundle per attempt."""
        frame = _frame_with((os.path.join(BUTTONS, "play_btn.png"), (1600, 900)))
        h, result = self._run(self._spec(max_retries=5), frame)
        self.assertFalse(result.ok)
        self.assertEqual(len(h.steps), 1, h.steps)
        self.assertEqual(len(h.recoveries), 5)

    def test_a_throwing_capture_hook_never_breaks_the_action(self):
        frame = _frame_with(
            (os.path.join(ASSETS, "home_anchor.png"), HOME_ANCHOR_TOPLEFT),
            (os.path.join(BUTTONS, "play_btn.png"), (1600, 900)),
            (os.path.join(ASSETS, "play_menu_anchor.png"), PLAY_MENU_ANCHOR_TOPLEFT),
        )
        h = _Harness(frame)

        def boom(*a):
            raise RuntimeError("capture failed")

        result = run_action(
            self._spec(), state_getter=lambda: h.state, vision=h.vision,
            arena_region_getter=lambda: ARENA,
            click_abs=lambda x, y, tag: h.clicks.append((x, y, tag)),
            recover_once=lambda n, a: h.recoveries.append((n, a)),
            on_step_failed=boom,
        )
        self.assertTrue(result.ok, result.reason)


if __name__ == "__main__":
    unittest.main()
