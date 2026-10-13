"""bot.log lines are mirrored to the terminal the bot runs in.

The mirror is off under the test runner (the guard is checked here too), so
these tests switch it on by hand and point stdout at a buffer.
"""

import io
import sys
import time
import unittest
from unittest import mock

import bot_logger


class _TtyBuffer(io.StringIO):
    encoding = "cp1252"

    def isatty(self):
        return True


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class ConsoleMirrorTest(unittest.TestCase):
    def setUp(self):
        self.buffer = _TtyBuffer()
        self._saved = (dict(bot_logger._console_state), bot_logger._console_queue)
        bot_logger._console_state.update(checked=True, enabled=True, dropped=0)
        bot_logger._console_queue = None
        self._stdout = mock.patch.object(sys, "stdout", self.buffer)
        self._stdout.start()

    def tearDown(self):
        self._stdout.stop()
        state, q = self._saved
        bot_logger._console_state.clear()
        bot_logger._console_state.update(state)
        bot_logger._console_queue = q

    def test_a_logged_line_reaches_the_console(self):
        bot_logger._mirror_to_console(["[t] [INFO] FORGE attack turn=3: no attack\n"])
        self.assertTrue(_wait_for(lambda: "FORGE attack turn=3" in self.buffer.getvalue()))

    def test_long_lines_are_shortened_for_the_console_only(self):
        bot_logger._mirror_to_console(["x" * 5000 + "\n"])
        self.assertTrue(_wait_for(lambda: self.buffer.getvalue().endswith(" ...\n")))
        self.assertLess(len(self.buffer.getvalue()), bot_logger._CONSOLE_LINE_MAX + 10)

    def test_characters_the_console_cannot_show_do_not_raise(self):
        bot_logger._mirror_to_console(["Current ACC: Дмитрий 名前\n"])
        self.assertTrue(_wait_for(lambda: "Current ACC:" in self.buffer.getvalue()))

    def test_disabled_mirror_writes_nothing(self):
        bot_logger._console_state["enabled"] = False
        bot_logger._mirror_to_console(["[t] [INFO] hidden\n"])
        time.sleep(0.05)
        self.assertEqual(self.buffer.getvalue(), "")

    def test_the_test_runner_itself_never_mirrors(self):
        bot_logger._console_state.update(checked=False, enabled=False)
        self.assertFalse(bot_logger._console_enabled())


if __name__ == "__main__":
    unittest.main()
