import threading
import time
from collections import deque
import os
import bot_logger


class LogReader:
    LOG_UPDATE_SPEED = 0.1

    @staticmethod
    def _default_player_log_path() -> str:
        home = os.path.expanduser("~")
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
        while not self.__stop_monitor:
            line = the_file.readline()
            if not line:
                time.sleep(self.LOG_UPDATE_SPEED)
                continue
            yield line

    def __monitor_log_file(self):
        # debug: print(self.__log_path)
        try:
            with open(self.__log_path, "r") as log_file:
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
            log_file = open(self.__log_path, "r")
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
