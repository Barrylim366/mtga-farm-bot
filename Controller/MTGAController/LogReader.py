import threading
import time
from collections import deque
import os
import sys
import bot_logger


class LogReader:
    LOG_UPDATE_SPEED = 0.1
    # A single GreToClientEvent line carries *several* GRE messages and can be
    # tens of kilobytes (56 KB measured), so MTGA's write is nowhere near
    # atomic and readline() happily returns the first few thousand characters
    # of one. Such a fragment is worse than useless: it matches the pattern,
    # replaces the newest line for it, and then fails to parse -- taking every
    # message behind the tear with it. A line is therefore held back until its
    # terminator arrives. Only if nothing at all follows for this long is the
    # buffer released anyway, so a genuinely last unterminated line (MTGA
    # exiting mid-write) is not swallowed forever.
    PARTIAL_LINE_FLUSH_SEC = 5.0

    @staticmethod
    def _default_player_log_path() -> str:
        home = os.path.expanduser("~")
        if os.name == "nt":
            return os.path.join(
                home,
                "AppData",
                "LocalLow",
                "Wizards Of The Coast",
                "MTGA",
                "Player.log",
            )
        if sys.platform == "darwin":
            return os.path.join(
                home,
                "Library",
                "Logs",
                "Wizards Of The Coast",
                "MTGA",
                "Player.log",
            )
        return os.path.join(
            home,
            ".local",
            "share",
            "Steam",
            "steamapps",
            "compatdata",
            "2141910",
            "pfx",
            "drive_c",
            "users",
            "steamuser",
            "AppData",
            "LocalLow",
            "Wizards Of The Coast",
            "MTGA",
            "Player.log",
        )

    def __init__(self, patterns, callback=lambda pat, patstr: None,
                 log_path=None,
                 player_id="LE3ZCMCJZBHUDGATTY2EJLUEIM"):
        self.__player = player_id
        self.__log_path = log_path or self._default_player_log_path()
        self.__lines_containing_pattern = {}
        self.__has_new_line = {}
        self.__lines_queue = {}
        for pattern in patterns:
            self.__lines_containing_pattern[pattern] = ""
            self.__has_new_line[pattern] = False
            self.__lines_queue[pattern] = deque()

        self.__log_monitor_thread = None
        self.__stop_monitor = False

        # Callback func should take two parameters: pattern, and string containing pattern
        self.__callback = callback

    def __follow(self, the_file):
        the_file.seek(0, 2)
        partial = ""
        last_data_at = 0.0
        # Seeking to the end can land *inside* a line MTGA is still writing. Its
        # tail then arrives newline-terminated and is indistinguishable from a
        # whole line -- but it starts mid-JSON, still matches the pattern, and
        # fails to parse exactly like the tear this buffering exists to prevent.
        # A real line starts with the JSON object or with Unity's `[...]` prefix,
        # so anything else can only be a tail.
        expect_first_line = True
        while not self.__stop_monitor:
            chunk = the_file.readline()
            if not chunk:
                if partial and (time.time() - last_data_at) >= self.PARTIAL_LINE_FLUSH_SEC:
                    # Nothing at all has arrived for the whole window, so this is
                    # not a slow write -- MTGA is gone (it can exit mid-line).
                    # Release the buffer rather than sit on it for the session.
                    bot_logger.log_error(
                        f"LOG_LINE_UNTERMINATED: releasing {len(partial)} buffered "
                        f"characters after {self.PARTIAL_LINE_FLUSH_SEC:.0f}s of silence "
                        "without a line terminator."
                    )
                    line, partial = partial, ""
                    expect_first_line = False
                    yield line
                    continue
                time.sleep(self.LOG_UPDATE_SPEED)
                continue
            # Measured from the last data, not from the start of the line: a slow
            # write that is still making progress must never be cut open, and a
            # buffer can only be held while MTGA keeps writing to it.
            last_data_at = time.time()
            partial += chunk
            if not partial.endswith("\n"):
                # MTGA is still writing this line. Yielding now would hand a
                # truncated JSON message to the callbacks -- measured on
                # 2026-08-23: 3996 characters of a 27804-character line whose
                # last message was the ActionsAvailableReq that passed the turn
                # back to us. The bot kept waiting for a stack that had already
                # resolved, its own clock ran, and the match had to be conceded
                # by hand.
                continue
            line, partial = partial, ""
            if expect_first_line:
                expect_first_line = False
                if not line.lstrip().startswith(("{", "[")):
                    bot_logger.log_info(
                        f"LOG_LINE_TAIL_DISCARDED: started reading {len(line)} "
                        "characters into a line MTGA was already writing."
                    )
                    continue
            yield line

    def __monitor_log_file(self):
        # debug: print(self.__log_path)
        try:
            # Player.log is UTF-8 and contains non-Latin card/player names (e.g.
            # Japanese opponents). Without encoding="utf-8", Windows opens it as
            # cp1252 and a single undecodable byte raises UnicodeDecodeError,
            # killing the monitor thread and blinding the bot for the rest of the
            # session. errors="replace" keeps reading past any stray byte; the
            # patterns we match are ASCII JSON keys, so replacements are harmless.
            with open(self.__log_path, "r", encoding="utf-8", errors="replace") as log_file:
                log_lines = self.__follow(log_file)
                for line in log_lines:
                    if self.__stop_monitor:
                        return
                    for pattern in self.__lines_containing_pattern:
                        if pattern in line:
                            self.__lines_containing_pattern[pattern] = line
                            self.__lines_queue[pattern].append(line)
                            self.__has_new_line[pattern] = True
                            bot_logger.log_raw_line(pattern, line)
                            try:
                                self.__callback(pattern, self.__lines_containing_pattern[pattern])
                            except Exception as e:
                                bot_logger.log_error(
                                    f"LogReader callback failed for pattern '{pattern}': {e}"
                                )
        except Exception as e:
            bot_logger.log_error(f"LogReader monitor failed: {e}")

    def start_log_monitor(self):
        self.__stop_monitor = False
        self.__log_monitor_thread = threading.Thread(target=self.__monitor_log_file)
        self.__log_monitor_thread.start()

    def stop_log_monitor(self):
        self.__stop_monitor = True
        if self.__log_monitor_thread is None:
            return
        if threading.current_thread() is self.__log_monitor_thread:
            return
        if self.__log_monitor_thread.is_alive():
            self.__log_monitor_thread.join(timeout=5)

    def is_monitoring(self):
        return self.__log_monitor_thread is not None and self.__log_monitor_thread.is_alive()

    def get_latest_line_containing_pattern(self, pattern):
        if self.__lines_queue[pattern]:
            line = self.__lines_queue[pattern].popleft()
        else:
            line = self.__lines_containing_pattern[pattern]
        self.__has_new_line[pattern] = bool(self.__lines_queue[pattern])
        return line

    def has_new_line(self, pattern):
        return bool(self.__lines_queue[pattern])

    def clear_new_line_flag(self, pattern):
        """Clear the new line flag for a pattern - use before starting a new scan"""
        self.__has_new_line[pattern] = False
        self.__lines_queue[pattern].clear()

    def reset_all_patterns(self):
        """Reset all cached pattern data for a fresh start"""
        for pattern in self.__lines_containing_pattern:
            self.__lines_containing_pattern[pattern] = ""
            self.__has_new_line[pattern] = False
            self.__lines_queue[pattern].clear()

    def full_log_read(self):
        """ Full read of the log so far """
        if not self.is_monitoring():
            log_file = open(self.__log_path, "r", encoding="utf-8", errors="replace")
            line = log_file.readline()
            while line:
                for pattern in self.__lines_containing_pattern:
                    if pattern in line:
                        self.__has_new_line[pattern] = True
                        self.__lines_containing_pattern[pattern] = line
                        bot_logger.log_raw_line(pattern, line)
                        try:
                            self.__callback(pattern, self.__lines_containing_pattern[pattern])
                        except Exception as e:
                            bot_logger.log_error(
                                f"LogReader callback failed during full read for pattern '{pattern}': {e}"
                            )
                line = log_file.readline()
        else:
            print("Unable to do read as log monitoring is already in progress")
