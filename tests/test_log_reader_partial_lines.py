"""A half-written Player.log line is never handed to the callbacks.

The bug this pins cost a match on 2026-08-23. MTGA writes one GreToClientEvent
per line, and a line carries *several* GRE messages -- 27804 characters and nine
messages in the measured case, up to 56 KB elsewhere. That write is not atomic,
so `readline()` returned the first 3996 characters of it. The fragment matched
the GameStateMessage pattern, became the newest line for it, and then failed to
parse:

    LogReader callback failed ...: Expecting ',' delimiter: line 1 column 3997

Everything behind the tear was lost with it -- including the `ActionsAvailableReq`
that passed the turn back to us. MTGA then had nothing left to write, because it
was waiting for the bot; `readline()` returned nothing for 16 seconds; and the
bot's model stayed on turn 6 with the opponent to act ("Deferring decision: stack
has 1 object(s)") while the real game sat in our turn 7 Main1 with our clock
running. From the outside that looks exactly like a freeze, and the match had to
be conceded by hand.

So: hold a line back until its terminator arrives. Only a silence long enough to
mean "MTGA is gone" releases an unterminated buffer, so a genuinely final line
(the client exiting mid-write) is not swallowed forever.

The same tear has a second entrance, pinned here too: `seek(0, 2)` can land
*inside* a line being written, and that tail arrives newline-terminated, looking
like a whole line while starting mid-JSON.
"""
import json
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.LogReader import LogReader

PATTERN = '"type": "GREMessageType_GameStateMessage"'


def gre_event(msg_ids, padding=0):
    """A line shaped like the real thing: one event, several messages."""
    messages = [
        {
            "type": "GREMessageType_GameStateMessage",
            "msgId": i,
            "gameStateId": i,
            "filler": "x" * padding,
        }
        for i in msg_ids[:-1]
    ]
    messages.append({"type": "GREMessageType_ActionsAvailableReq", "msgId": msg_ids[-1]})
    return json.dumps({"greToClientEvent": {"greToClientMessages": messages}}) + "\n"


class _EndOfScript(Exception):
    """Ends the endless follow loop once the scripted reads are used up."""


class _ScriptedFile:
    """Stands in for Player.log: hands out exactly what readline() would.

    An empty string means "nothing written yet", which is what the real file
    returns between MTGA's writes.
    """

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.seeked_to_end = False

    def seek(self, offset, whence=0):
        self.seeked_to_end = (offset, whence) == (0, 2)

    def readline(self):
        if not self.chunks:
            raise _EndOfScript
        return self.chunks.pop(0)


def follow(reader, chunks):
    """Collect what __follow yields for a scripted sequence of reads."""
    out = []
    gen = reader._LogReader__follow(_ScriptedFile(chunks))
    try:
        for line in gen:
            out.append(line)
    except _EndOfScript:
        pass
    return out


def make_reader(callback=None, log_path=None):
    return LogReader(
        [PATTERN],
        callback=callback or (lambda pat, line: None),
        # Never opened by the tests that drive __follow directly.
        log_path=log_path or os.path.join(ROOT, "does-not-exist", "Player.log"),
    )


def stub_clock(test, now=1000.0):
    """Replace the module's `time` *name*, not `time.sleep` on the shared module.

    Patching "…LogReader.time.sleep" reaches through to the one global time
    module and would stop every thread in the process from sleeping -- and a
    patched `time.time` would hand fake values to unrelated code. The suite
    leaves daemon timer threads behind, so that is a live hazard, not a
    theoretical one.
    """
    clock = {"now": now}

    def fake_sleep(seconds):
        clock["now"] += seconds

    fake_time = SimpleNamespace(sleep=fake_sleep, time=lambda: clock["now"])
    patcher = mock.patch("Controller.MTGAController.LogReader.time", fake_time)
    patcher.start()
    test.addCleanup(patcher.stop)
    return clock


class PartialLineTest(unittest.TestCase):
    def setUp(self):
        stub_clock(self)
        self.reader = make_reader()

    def test_a_line_torn_into_fragments_arrives_as_one_whole_line(self):
        whole = gre_event([182, 183, 184, 190], padding=400)
        cut = len(whole) // 3
        chunks = [whole[:cut], whole[cut:]]
        self.assertEqual(follow(self.reader, chunks), [whole])

    def test_the_fragments_are_withheld_until_the_terminator_arrives(self):
        """The regression itself: no caller may ever see a piece of a line."""
        whole = gre_event([182, 183, 190], padding=200)
        head, tail = whole[:1000], whole[1000:]
        # Reads: head, nothing yet, nothing yet, tail.
        yielded = follow(self.reader, [head, "", "", tail])
        self.assertEqual(yielded, [whole], "a fragment escaped, or the line was lost")

    def test_what_reaches_a_caller_is_parsable_json(self):
        whole = gre_event([182, 183, 184, 185, 190], padding=4000)
        pieces = [whole[i:i + 3996] for i in range(0, len(whole), 3996)]
        self.assertGreater(len(pieces), 1, "the fixture must actually be torn up")
        for line in follow(self.reader, pieces):
            json.loads(line)  # would raise exactly as the bot's callback did

    def test_the_last_message_of_a_torn_line_survives(self):
        """The whole point: ActionsAvailableReq sat behind the tear."""
        whole = gre_event([182, 183, 184, 190], padding=4000)
        pieces = [whole[i:i + 3996] for i in range(0, len(whole), 3996)]
        self.assertGreater(len(pieces), 1, "the fixture must actually be torn up")
        (line,) = follow(self.reader, pieces)
        messages = json.loads(line)["greToClientEvent"]["greToClientMessages"]
        self.assertEqual(messages[-1]["type"], "GREMessageType_ActionsAvailableReq")
        self.assertEqual(messages[-1]["msgId"], 190)

    def test_complete_lines_still_pass_straight_through(self):
        first = gre_event([1, 2], padding=0)
        second = gre_event([3, 4], padding=0)
        self.assertEqual(follow(self.reader, [first, second]), [first, second])

    def test_a_quiet_file_yields_nothing(self):
        self.assertEqual(follow(self.reader, ["", "", ""]), [])

    def test_the_read_starts_at_the_end_of_the_file(self):
        f = _ScriptedFile([])
        gen = self.reader._LogReader__follow(f)
        with self.assertRaises(_EndOfScript):
            next(gen)
        self.assertTrue(f.seeked_to_end, "following must not replay the whole log")


class SeekIntoALineTest(unittest.TestCase):
    """`seek(0, 2)` can land inside a line MTGA is still writing.

    Its tail then arrives newline-terminated -- a whole line as far as the
    buffering can tell -- while starting mid-JSON. A 56 KB GRE line contains the
    game-state pattern around twenty times, so such a tail nearly always matches,
    becomes the newest line for the pattern and fails to parse: the same harm as
    the tear, once per session start.
    """

    def setUp(self):
        stub_clock(self)
        self.reader = make_reader()

    def test_a_tail_from_a_mid_line_seek_is_discarded(self):
        tail = '2, "type": "GREMessageType_GameStateMessage", "c": 3}}]}}\n'
        good = gre_event([1, 2])
        with mock.patch(
            "Controller.MTGAController.LogReader.bot_logger.log_info"
        ) as log_info:
            self.assertEqual(follow(self.reader, [tail, good]), [good])
        self.assertTrue(
            any("LOG_LINE_TAIL_DISCARDED" in str(c) for c in log_info.call_args_list),
            "discarding the first line must be visible in the log",
        )

    def test_a_seek_that_lands_on_a_boundary_keeps_the_first_line(self):
        first = gre_event([1, 2])
        self.assertEqual(follow(self.reader, [first]), [first])

    def test_unitys_own_prefixed_lines_are_not_mistaken_for_a_tail(self):
        prefixed = "[UnityCrossThreadLogger]8/23/2026 8:42:18 PM: Match to X\n"
        self.assertEqual(follow(self.reader, [prefixed]), [prefixed])

    def test_only_the_very_first_line_can_be_discarded(self):
        """A later line that starts oddly is real data, not a tail."""
        first = gre_event([1, 2])
        odd = 'garbage that starts mid-token\n'
        self.assertEqual(follow(self.reader, [first, odd]), [first, odd])


class UnterminatedFlushTest(unittest.TestCase):
    """A line that never gets its terminator must not be held forever.

    MTGA can exit mid-write, and the bot would otherwise sit on the buffer for
    the rest of the session. Releasing it after a long silence is the old
    behaviour and no worse than it was; releasing it *early* is the bug above.
    """

    def setUp(self):
        self.clock = stub_clock(self)
        self.reader = make_reader()

    def test_the_buffer_is_released_after_the_silence_window(self):
        stump = '{"greToClientEvent": {"greToClie'
        with mock.patch.object(LogReader, "PARTIAL_LINE_FLUSH_SEC", 0.0), \
                mock.patch(
                    "Controller.MTGAController.LogReader.bot_logger.log_error"
                ) as log_error:
            self.assertEqual(follow(self.reader, [stump, "", ""]), [stump])
        self.assertTrue(
            any("LOG_LINE_UNTERMINATED" in str(c) for c in log_error.call_args_list),
            "releasing an unterminated line must be visible in the log",
        )

    def test_nothing_is_released_before_the_window_is_up(self):
        stump = '{"greToClientEvent": {"greToClie'
        with mock.patch.object(LogReader, "PARTIAL_LINE_FLUSH_SEC", 3600.0):
            self.assertEqual(follow(self.reader, [stump, "", "", ""]), [])

    def test_a_write_that_keeps_making_progress_is_never_cut_open(self):
        """The window is silence since the last data, not age of the line.

        Measured from the start of the line instead, a slow write would be torn
        open the moment it overran the window -- and this log really does carry
        million-character lines during client startup, written while the disk is
        busy with asset loading. Each read here advances the fake clock past the
        window, so a per-line deadline would fire; a per-data deadline must not.
        """
        whole = gre_event([182, 183, 190], padding=500)
        pieces = [whole[i:i + 200] for i in range(0, len(whole), 200)]
        self.assertGreater(len(pieces), 5, "the fixture must arrive in many chunks")

        real_readline = _ScriptedFile(pieces)
        window = LogReader.PARTIAL_LINE_FLUSH_SEC

        def ticking_readline():
            self.clock["now"] += window * 2  # every read is "late"
            return real_readline.readline()

        f = SimpleNamespace(seek=lambda *a: None, readline=ticking_readline)
        out = []
        gen = self.reader._LogReader__follow(f)
        try:
            for line in gen:
                out.append(line)
        except _EndOfScript:
            pass
        self.assertEqual(out, [whole], "a slow but progressing write was torn open")


class MonitorThreadTest(unittest.TestCase):
    """The same thing through the real monitor thread and a real file.

    The generator tests pin the logic; this one pins that the callback and the
    pattern queue never see a fragment, which is what actually broke the bot.
    """

    def setUp(self):
        import tempfile

        fd, self.path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.seen = []
        self.lock = threading.Lock()

        def callback(pattern, line):
            with self.lock:
                self.seen.append(line)

        self.reader = LogReader([PATTERN], callback=callback, log_path=self.path)
        self.reader.start_log_monitor()
        self.addCleanup(self.reader.stop_log_monitor)
        self._wait_until_following()

    def _wait_until_following(self):
        """start_log_monitor() only starts the *thread*.

        The open() and the seek-to-end inside it may not have happened yet, and
        anything appended before that seek is skipped -- which made this test
        fail in half its runs, in a way that surfaced one assertion later and
        looked unrelated. So drive a complete sentinel line through the whole
        path and only start measuring once it comes back.
        """
        sentinel = gre_event([0, 1])
        deadline = time.time() + 10.0
        while time.time() < deadline:
            self.append(sentinel)
            if self.wait_for(1, timeout=0.5):
                break
        else:
            self.fail("the log monitor never started following the file")
        with self.lock:
            self.seen.clear()
        self.reader.reset_all_patterns()

    def append(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()

    def wait_for(self, count, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if len(self.seen) >= count:
                    return True
            time.sleep(0.05)
        return False

    def test_a_half_written_line_reaches_no_callback_until_it_is_complete(self):
        whole = gre_event([182, 183, 184, 190], padding=4000)
        self.assertGreater(len(whole), 3996, "the fixture must actually be torn up")
        head, tail = whole[:3996], whole[3996:]
        self.append(head)
        self.assertFalse(
            self.wait_for(1, timeout=1.0),
            "a truncated line was delivered -- this is the 2026-08-23 bug",
        )
        self.append(tail)
        self.assertTrue(self.wait_for(1), "the completed line never arrived")
        with self.lock:
            self.assertEqual(self.seen, [whole])
        json.loads(self.seen[0])

    def test_the_newest_line_for_a_pattern_is_never_a_fragment(self):
        whole = gre_event([182, 190], padding=1200)
        cut = len(whole) // 2
        self.append(whole[:cut])
        self.assertFalse(self.wait_for(1, timeout=1.0), "a fragment was delivered")
        self.assertEqual(
            self.reader.get_latest_line_containing_pattern(PATTERN),
            "",
            "a fragment became the newest line for the pattern",
        )
        self.append(whole[cut:])
        self.assertTrue(self.wait_for(1))
        self.assertEqual(self.reader.get_latest_line_containing_pattern(PATTERN), whole)


# No unittest.main() on purpose: this module drives the real monitor thread, so
# bot_logger writes for real. Run directly, the runtime-path redirect does not
# recognise the runner (__main__ is this file) and the fixture GRE lines would be
# appended to the live bot's bot.log -- that happened on 2026-08-21 and put 273
# lines of fixture output into the log of a bot that was playing. Use
# `python -m unittest discover tests`.
