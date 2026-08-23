"""One screen capture at a time, and never an orphaned screenshot tool.

The bug this pins froze the bot mid-match on 2026-08-23. On KDE/Wayland the only
working backend is `spectacle`, a single-instance application: a second
invocation does not take its own shot, it blocks. The bot runs ~100 threads that
all want to see, so the invocations piled up -- three
`spectacle -b -n -f -o /tmp/mtga_shot_*` processes hanging at once, *every*
thread in the bot silent for 48 seconds (no log line at all, not even the
periodic arena reacquire), the turn timer running out, and the debug bundle
being written stopping after its JSON with both screenshots missing. Killing
those three processes released the bot instantly.

Hence two contracts:

  1. parallel callers share one frame instead of each queueing for their own;
  2. a backend that overruns its deadline is killed by *process group*, because
     the one that hung left a grandchild holding the single-instance slot and a
     plain run(timeout=...) only kills the child it spawned.

No test here touches the screen: the backend is stubbed, so what is measured is
the serialisation, not the screenshot.
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vision import vision as vision_mod
from vision.vision import VisionEngine


def bare_engine() -> VisionEngine:
    """No __init__: it imports pyautogui and needs a display."""
    return object.__new__(VisionEngine)


class SharedFrameTest(unittest.TestCase):
    def setUp(self):
        self._reset_cache()
        self.addCleanup(self._reset_cache)
        self.engine = bare_engine()
        self.captures = []

    @staticmethod
    def _reset_cache():
        vision_mod._last_frame = None
        vision_mod._last_frame_ts = 0.0

    def _slow_backend(self, delay=0.05):
        def grab():
            self.captures.append(time.time())
            time.sleep(delay)
            return np.zeros((4, 4, 3), dtype=np.uint8)

        return grab

    def test_parallel_callers_share_a_single_capture(self):
        """Eight threads, one screenshot -- the pile-up cannot form."""
        self.engine._grab_full_frame_uncached = self._slow_backend()
        threads = [
            threading.Thread(target=self.engine._grab_full_frame) for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(
            len(self.captures), 1, f"{len(self.captures)} captures for 8 callers"
        )

    def test_a_stale_frame_is_replaced(self):
        """Sharing must not mean serving yesterday's board."""
        self.engine._grab_full_frame_uncached = self._slow_backend(delay=0)
        self.engine._grab_full_frame()
        vision_mod._last_frame_ts = time.time() - (vision_mod._FRAME_TTL_SEC + 0.05)
        self.engine._grab_full_frame()
        self.assertEqual(len(self.captures), 2)

    def test_the_frame_is_actually_returned(self):
        self.engine._grab_full_frame_uncached = self._slow_backend(delay=0)
        frame = self.engine._grab_full_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (4, 4, 3))

    def test_a_failed_capture_is_not_cached_as_success(self):
        """Caching a None would blind every caller for the whole TTL."""
        self.engine._grab_full_frame_uncached = lambda: None
        self.assertIsNone(self.engine._grab_full_frame())
        self.assertIsNone(vision_mod._last_frame)

    def test_the_ttl_is_short_enough_for_a_live_board(self):
        """Long enough to collapse a burst, short enough that a click still
        lands on what was seen."""
        self.assertLessEqual(vision_mod._FRAME_TTL_SEC, 0.5)

    def test_a_burst_shares_one_capture_even_when_the_backend_is_slow(self):
        """The regression that made the bot feel sluggish.

        A capture takes 610-730ms here. With a TTL below that, every queued
        caller found the shared frame already expired and took its own, so N
        callers cost N captures and the median gap between decisions doubled
        (9.5s -> 18.6s). A frame taken while a caller waited must win outright,
        regardless of the TTL.
        """
        self.engine._grab_full_frame_uncached = self._slow_backend(delay=0.7)
        threads = [
            threading.Thread(target=self.engine._grab_full_frame) for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(
            len(self.captures),
            1,
            f"{len(self.captures)} captures for 5 callers -- they queued instead of sharing",
        )


class TimeoutReapingTest(unittest.TestCase):
    """A backend that overruns must leave nothing behind.

    cv2 is stubbed because the suite's interpreter has no OpenCV -- without it
    _grab_via_linux_tool returns before it ever spawns anything, and the test
    would pass while measuring nothing.
    """

    def setUp(self):
        patcher = mock.patch("vision.vision.cv2", mock.Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_overrunning_tool_is_killed_by_process_group(self):
        engine = bare_engine()
        engine._linux_tool_cmd = ["spectacle", "-b", "-n", "-f", "-o", "__OUT__"]
        proc = mock.Mock()
        proc.pid = 4321
        proc.communicate.side_effect = [
            __import__("subprocess").TimeoutExpired(cmd="spectacle", timeout=8.0),
            ("", ""),
        ]
        with mock.patch("vision.vision.subprocess.Popen", return_value=proc), \
                mock.patch("vision.vision.os.getpgid", return_value=4321) as getpgid, \
                mock.patch("vision.vision.os.killpg") as killpg:
            self.assertIsNone(engine._grab_via_linux_tool())
        getpgid.assert_called_once_with(4321)
        self.assertEqual(killpg.call_args.args[0], 4321)

    def test_the_tool_runs_in_its_own_session_so_the_group_exists(self):
        engine = bare_engine()
        engine._linux_tool_cmd = ["spectacle", "-b", "-n", "-f", "-o", "__OUT__"]
        proc = mock.Mock()
        proc.pid = 999
        proc.communicate.return_value = ("", "")
        proc.returncode = 1  # stops before touching the filesystem
        with mock.patch("vision.vision.subprocess.Popen", return_value=proc) as popen:
            engine._grab_via_linux_tool()
        self.assertTrue(
            popen.call_args.kwargs.get("start_new_session"),
            "without start_new_session there is no group to kill",
        )


if __name__ == "__main__":
    unittest.main()
