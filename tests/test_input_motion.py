"""Unit tests for the Windows mouse-motion path.

Background (measured 2026-08-26): Magic Arena updated to Unity 6
(`Initialize engine version: 6000.3.14f1`, up from `2022.3.62f2`) and every
hover scan in the bot stopped identifying anything -- `SCAN_STOPPED`,
`HAND_SELECT_STOPPED`, `CAST_UNAVAILABLE`, and a bot that ran its mouse along
the hand row all game without playing a card. The whole session's `Player.log`
held 0 of our own hovers against 1507 in the previous session.

The cause is that pynput moves the cursor on Windows with `SetCursorPos`, which
teleports it without producing device input. Unity reads the mouse through Raw
Input, and the new client no longer treats a teleported pointer as hovering.
Counting hover lines over 12 stops on the hand row:

    warp (SetCursorPos)   0 hovers at every dwell from 0.02s to 0.40s
    SendInput motion      hovers at every dwell, including 10px/0.01s

The dwell was a red herring: an early measurement that nudged the cursor with
small relative `mouse_event` calls only produced hovers above ~0.30s, which made
the sweep pacing look like the culprit. Injecting proper absolute motion reports
every card at the original 10px/0.01s pacing, so the pacing was left alone.

Nothing here sends input or touches the screen: the normalisation is a pure
function and the fallback tests drive stubs.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.Utilities import input_controller as ic


class NormalisationTests(unittest.TestCase):
    """SendInput takes 0..65535 over the virtual desktop, not pixels. Getting
    this wrong does not fail loudly -- it clicks next to the target."""

    # The machine this was measured on: single 3440x1440 screen at the origin.
    DESKTOP = (0, 0, 3440, 1440)

    def norm(self, x, y, desktop=None):
        return ic._Win32MouseMotion.normalise(x, y, desktop or self.DESKTOP)

    def test_the_origin_maps_to_zero(self):
        self.assertEqual(self.norm(0, 0), (0, 0))

    def test_the_last_pixel_maps_to_the_full_range(self):
        """The span is width-1, not width: dividing by the pixel count leaves the
        far edge of the screen unreachable."""
        self.assertEqual(self.norm(3439, 1439), (65535, 65535))

    def test_a_point_inside_round_trips_to_within_a_pixel(self):
        # The hand scan line on this machine.
        for x, y in ((1519, 1178), (2480, 1178), (3438, 1178)):
            with self.subTest(point=(x, y)):
                nx, ny = self.norm(x, y)
                back_x = round(nx * (3440 - 1) / 65535.0)
                back_y = round(ny * (1440 - 1) / 65535.0)
                self.assertLessEqual(abs(back_x - x), 1, f"x drifted to {back_x}")
                self.assertLessEqual(abs(back_y - y), 1, f"y drifted to {back_y}")

    def test_a_monitor_left_of_the_primary_is_handled(self):
        """A secondary screen gives the virtual desktop a negative origin. Without
        subtracting it every coordinate clamps to 0 and the pointer parks in the
        top-left corner."""
        desktop = (-1920, 0, 5360, 1440)
        self.assertEqual(self.norm(-1920, 0, desktop), (0, 0))
        mid_x, _mid_y = self.norm(0, 0, desktop)
        self.assertGreater(mid_x, 0, "the primary screen must not map to the edge")

    def test_out_of_range_points_are_clamped_not_wrapped(self):
        self.assertEqual(self.norm(-500, -500), (0, 0))
        self.assertEqual(self.norm(99999, 99999), (65535, 65535))

    def test_a_degenerate_desktop_does_not_divide_by_zero(self):
        self.assertEqual(self.norm(0, 0, (0, 0, 1, 1)), (0, 0))


class _FakeMouse:
    def __init__(self):
        self.position = (0, 0)
        self.moves = []

    def move(self, dx, dy):
        self.moves.append((dx, dy))


class _FakeMotion:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def move_abs(self, x, y):
        self.calls.append((x, y))
        return self.result


class FallbackTests(unittest.TestCase):
    """A warp is useless against the new client, but it is what every previous
    version did -- so a machine where SendInput cannot be set up must degrade to
    it rather than refuse to play."""

    def controller(self, motion):
        c = ic.PynputInputController.__new__(ic.PynputInputController)
        c._mouse = _FakeMouse()
        c._win32_motion = motion
        return c

    def test_a_delivered_motion_event_is_not_followed_by_a_warp(self):
        motion = _FakeMotion(True)
        c = self.controller(motion)
        c.move_abs(2480, 1178)
        self.assertEqual(motion.calls, [(2480, 1178)])
        self.assertEqual(c._mouse.position, (0, 0), "fell back needlessly")

    def test_a_refused_motion_event_falls_back_to_the_warp(self):
        motion = _FakeMotion(False)
        c = self.controller(motion)
        c.move_abs(2480, 1178)
        self.assertEqual(c._mouse.position, (2480, 1178))

    def test_no_motion_backend_still_moves_the_cursor(self):
        c = self.controller(None)
        c.move_abs(2480, 1178)
        self.assertEqual(c._mouse.position, (2480, 1178))

    def test_a_relative_move_is_sent_as_an_absolute_target(self):
        """A relative SendInput is scaled by pointer speed and acceleration, so a
        sweep would drift and the hovered card could not be mapped back to a
        position. The delta is resolved against the current position instead."""
        motion = _FakeMotion(True)
        c = self.controller(motion)
        c._mouse.position = (1519, 1178)
        c.move_rel(10, 0)
        self.assertEqual(motion.calls, [(1529, 1178)])
        self.assertEqual(c._mouse.moves, [], "pynput must not also move")

    def test_a_relative_move_falls_back_to_a_relative_warp(self):
        motion = _FakeMotion(False)
        c = self.controller(motion)
        c._mouse.position = (1519, 1178)
        c.move_rel(10, -2)
        self.assertEqual(c._mouse.moves, [(10, -2)])


class BackendAvailabilityTests(unittest.TestCase):
    def test_the_helper_is_available_on_windows_and_absent_elsewhere(self):
        motion = ic._win32_mouse_motion_or_none()
        if os.name == "nt":
            self.assertIsNotNone(
                motion, "Windows must get real motion or the bot cannot play"
            )
        else:
            self.assertIsNone(motion)

    def test_a_broken_setup_yields_none_instead_of_raising(self):
        """Import- or ctypes-level breakage must not stop the bot from starting."""
        original = ic._Win32MouseMotion.__init__

        def boom(self):
            raise RuntimeError("no user32 here")

        ic._Win32MouseMotion.__init__ = boom
        try:
            if os.name == "nt":
                self.assertIsNone(ic._win32_mouse_motion_or_none())
        finally:
            ic._Win32MouseMotion.__init__ = original

    @unittest.skipUnless(os.name == "nt", "Windows-only")
    def test_the_windows_default_backend_uses_real_motion(self):
        """`auto` resolves to pynput on Windows, and pynput without this is the
        broken configuration -- pin that the wiring is actually in place."""
        c = ic.create_input_controller("auto")
        self.assertIsNotNone(getattr(c, "_win32_motion", None))


if __name__ == "__main__":
    unittest.main()
