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


class HandScanBandTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        c = Controller(f.name)
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


if __name__ == "__main__":
    unittest.main()
