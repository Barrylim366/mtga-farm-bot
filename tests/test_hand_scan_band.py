"""The hand sweep must stay on the hand row.

2026-08-21, live: a cast click for card 281 landed at rel x=120 -- the player
avatar, right next to the graveyard pile -- and opened MTGA's graveyard viewer.
That overlay covers the board, so every following sweep hovered nothing while
the window was perfectly focused, the bot burned its rope, and the match was
lost on the timer (MY_TIMER_CRITICAL, ActivePlayer remaining=0.0s).

The click is explained by hover latency, which the scan code already documents:
a hover is only logged after a client -> server -> log round trip, so a late
event can name the target card while the cursor has already moved past it, and
the sweep clicks wherever it happens to be. The sweep therefore must not travel
anywhere a stray click is expensive.

Numbers below come from 1988 hand clicks in runtime/debug/clicks.jsonl, spanning
2026-07 to 2026-08.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller

# Every misfire ever recorded sat at or below this, in the avatar / graveyard /
# exile strip on the left.
OBSERVED_MISFIRES = (0, 94, 103, 112, 120, 131, 140, 141, 150, 159, 170)
# Real hand clicks: the lowest ever seen, the 1st percentile, the median area,
# the 99th percentile and the highest ever seen.
OBSERVED_REAL_CLICKS = (272, 280, 560, 960, 1480, 1572)
# The right-hand side of the board at sweep height: the Next button and the
# auto-pass arrow. Clicking either passes priority for us.
RIGHT_SIDE_BUTTONS = (1755, 1870)


def _controller(**kwargs):
    import tempfile

    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    return Controller(f.name, **kwargs)


def _saved_band(p1x, p2x, y=1050):
    """A calibration_config.json click_targets blob, as loaded on startup."""
    return {
        "hand_scan_points": {
            "p1": {"x": p1x, "y": y},
            "p2": {"x": p2x, "y": y},
        }
    }


class HandScanBandTest(unittest.TestCase):
    def setUp(self):
        c = _controller()
        self.left, self.y = c.hand_scan_p1
        self.right, self.y2 = c.hand_scan_p2

    def test_the_band_runs_left_to_right_at_one_height(self):
        self.assertLess(self.left, self.right)
        self.assertEqual(self.y, self.y2)

    def test_no_recorded_misfire_is_inside_the_band(self):
        for x in OBSERVED_MISFIRES:
            with self.subTest(rel_x=x):
                self.assertLess(
                    x, self.left,
                    f"rel x={x} is in the avatar/graveyard strip and the sweep "
                    "would still travel there",
                )

    def test_every_recorded_real_click_is_inside_the_band(self):
        for x in OBSERVED_REAL_CLICKS:
            with self.subTest(rel_x=x):
                self.assertGreaterEqual(x, self.left, "a real hand card is cut off")
                self.assertLessEqual(x, self.right, "a real hand card is cut off")

    def test_the_band_stops_short_of_the_priority_buttons(self):
        """The sweep reaching these is worse than failing to find a card: a
        stray click there passes the turn."""
        for x in RIGHT_SIDE_BUTTONS:
            with self.subTest(rel_x=x):
                self.assertLess(self.right, x)

    def test_the_band_stays_inside_the_game_frame(self):
        self.assertGreaterEqual(self.left, 0)
        self.assertLessEqual(self.right, 1920)


class SavedCalibrationCannotWidenTheBandTest(unittest.TestCase):
    """The instance defaults above are not what production uses.

    Every real start passes click_targets loaded from calibration_config.json,
    and that file overrides hand_scan_p1/p2 outright. When the band was first
    narrowed, only the defaults were changed -- the running bot kept sweeping
    0..1920 from its saved config, and the very next session's first
    `HAND_SCAN mapped` line still read raw_p1=(0, 1050) raw_p2=(1920, 1050).
    The test that was supposed to cover it built a Controller with no
    click_targets at all, so it never touched the path that actually decides.
    """

    def test_the_stale_full_width_config_is_narrowed(self):
        c = _controller(click_targets=_saved_band(0, 1920))
        self.assertEqual(c.hand_scan_p1[0], Controller._HAND_SCAN_MIN_X)
        self.assertEqual(c.hand_scan_p2[0], Controller._HAND_SCAN_MAX_X)

    def test_the_row_height_from_the_config_is_kept(self):
        """Only x is unsafe; the user's measured hand height must survive."""
        c = _controller(click_targets=_saved_band(0, 1920, y=1042))
        self.assertEqual(c.hand_scan_p1[1], 1042)
        self.assertEqual(c.hand_scan_p2[1], 1042)

    def test_a_calibration_inside_the_band_is_left_alone(self):
        c = _controller(click_targets=_saved_band(300, 1600))
        self.assertEqual(c.hand_scan_p1[0], 300)
        self.assertEqual(c.hand_scan_p2[0], 1600)

    def test_an_out_of_frame_band_lands_on_the_safe_defaults(self):
        """A band outside the 1920x1080 frame is dropped, not carried through.

        Worth pinning down because it is why the clamp can be unconditional:
        this fallback runs first, so the legacy-absolute detection (x > 1920) is
        unreachable for the hand points and the clamp cannot be hiding it.
        """
        c = _controller(click_targets=_saved_band(2000, 3400, y=1178))
        self.assertEqual(c.hand_scan_p1[0], Controller._HAND_SCAN_MIN_X)
        self.assertEqual(c.hand_scan_p2[0], Controller._HAND_SCAN_MAX_X)

    def test_a_legacy_profile_elsewhere_still_narrows_the_band(self):
        """The clamp must not be skipped just because some *other* click target
        is a legacy absolute coordinate -- that would leave the full-width sweep
        in place on exactly the oldest installs."""
        targets = _saved_band(0, 1920)
        targets["keep_hand"] = {"x": 2620, "y": 1948}
        c = _controller(click_targets=targets)
        self.assertTrue(c._legacy_absolute_click_profile)
        self.assertEqual(c.hand_scan_p1[0], Controller._HAND_SCAN_MIN_X)
        self.assertEqual(c.hand_scan_p2[0], Controller._HAND_SCAN_MAX_X)


class ShippedDefaultsAgreeTest(unittest.TestCase):
    """run_bot.py and ui.py each carry their own copy of the default band; a
    fresh install gets its config from them, so a stale copy there reintroduces
    the full-width sweep for every new user."""

    def _bands_in(self, filename):
        import re

        path = os.path.join(ROOT, filename)
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        block = re.search(
            r'"hand_scan_points"\s*:\s*\{.*?"p2"\s*:\s*\{\s*"x"\s*:\s*(\d+)',
            src,
            re.S,
        )
        p1 = re.search(
            r'"hand_scan_points"\s*:\s*\{\s*"p1"\s*:\s*\{\s*"x"\s*:\s*(\d+)', src, re.S
        )
        self.assertIsNotNone(p1, f"no hand_scan_points p1 found in {filename}")
        self.assertIsNotNone(block, f"no hand_scan_points p2 found in {filename}")
        return int(p1.group(1)), int(block.group(1))

    def test_run_bot_default_matches_the_controller(self):
        left, right = self._bands_in("run_bot.py")
        self.assertEqual((left, right), (Controller._HAND_SCAN_MIN_X, Controller._HAND_SCAN_MAX_X))

    def test_ui_default_matches_the_controller(self):
        left, right = self._bands_in("ui.py")
        self.assertEqual((left, right), (Controller._HAND_SCAN_MIN_X, Controller._HAND_SCAN_MAX_X))


if __name__ == "__main__":
    unittest.main()
