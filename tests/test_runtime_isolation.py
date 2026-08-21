"""The suite must not write into the repo's runtime/ directory.

runtime/ is the live bot's: status.json (which the watchdog reads),
logs/bot.log (which the watchdog copies into analysis/history.log), the decision
and click recorders, the debug bundles. A Controller built in a test writes
there just as readily as one driving a real match -- see tests/__init__.py for
what that cost on 2026-08-21.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_logger
import runtime_paths
import runtime_status


class RuntimeIsolationTest(unittest.TestCase):
    def test_the_runtime_root_is_redirected(self):
        root = Path(runtime_paths.get_runtime_root())
        repo_runtime = Path(runtime_paths.get_repo_root()) / "runtime"
        self.assertNotEqual(
            root.resolve(),
            repo_runtime.resolve(),
            "tests are writing into the live bot's runtime/ -- run the suite as "
            "`python -m unittest discover tests` so tests/__init__.py can redirect it",
        )

    def test_every_artefact_path_follows_the_redirect(self):
        """Not just status.json: the log and the recorders too, or the watchdog
        still picks test noise up out of history.log."""
        repo_runtime = (Path(runtime_paths.get_repo_root()) / "runtime").resolve()
        # Name these explicitly. An earlier version of this test probed
        # getattr(bot_logger, "LOG_FILE") -- a name that does not exist -- got
        # None, skipped the check, and passed while the suite was truncating the
        # live bot.log on every run.
        paths = {
            "status": Path(runtime_status.get_status_path()),
            "runtime_file": Path(runtime_paths.runtime_file("probe.txt")),
            "subdir": Path(runtime_paths.ensure_runtime_subdir("debug")),
            "bot_log": Path(bot_logger.get_bot_log_path()),
            "app_log_dir": Path(bot_logger.get_app_log_dir()),
            "debug_dir": Path(bot_logger.ensure_debug_dir()),
        }
        for name, path in paths.items():
            with self.subTest(artefact=name):
                self.assertFalse(
                    str(path.resolve()).startswith(str(repo_runtime)),
                    f"{name} resolves into the live runtime dir: {path}",
                )

    def test_writing_a_log_line_lands_outside_the_repo(self):
        """The end-to-end version: bot_logger froze its path into a module
        constant at import time, so it kept writing to the repo no matter what
        the environment said. Anything that resolves once, at import, is immune
        to the redirect -- assert on the actual write, not just on the path."""
        repo_log = Path(runtime_paths.get_repo_root()) / "runtime" / "logs" / "bot.log"
        before = repo_log.stat().st_mtime_ns if repo_log.exists() else None
        bot_logger.log_info("runtime isolation probe")
        after = repo_log.stat().st_mtime_ns if repo_log.exists() else None
        self.assertEqual(before, after, f"a test log line touched {repo_log}")
        written = Path(bot_logger.get_bot_log_path())
        self.assertTrue(written.exists(), "the probe line went nowhere")
        self.assertIn("runtime isolation probe", written.read_text(encoding="utf-8"))

    def test_the_override_is_honoured_and_not_cached(self):
        """A cached root would defeat the redirect for anything imported early."""
        original = os.environ.get(runtime_paths.RUNTIME_DIR_ENV)
        self.addCleanup(
            lambda: os.environ.__setitem__(runtime_paths.RUNTIME_DIR_ENV, original)
            if original is not None
            else os.environ.pop(runtime_paths.RUNTIME_DIR_ENV, None)
        )
        import tempfile

        probe = tempfile.mkdtemp(prefix="mtga-override-probe-")
        os.environ[runtime_paths.RUNTIME_DIR_ENV] = probe
        self.assertEqual(
            Path(runtime_paths.get_runtime_root()).resolve(), Path(probe).resolve()
        )

    def _without_override(self):
        original = os.environ.get(runtime_paths.RUNTIME_DIR_ENV)
        self.addCleanup(
            lambda: os.environ.__setitem__(runtime_paths.RUNTIME_DIR_ENV, original)
            if original is not None
            else os.environ.pop(runtime_paths.RUNTIME_DIR_ENV, None)
        )
        os.environ[runtime_paths.RUNTIME_DIR_ENV] = "   "

    def test_the_production_default_is_the_app_root(self):
        self.assertEqual(
            runtime_paths.default_runtime_root().resolve(),
            (Path(runtime_paths.get_app_root()) / "runtime").resolve(),
        )

    def test_an_empty_override_still_avoids_the_live_runtime_dir(self):
        """With no usable override, a test process must NOT fall back to the
        app root -- that is the live bot's runtime dir.

        This is the hole that made the whole redirect optional. The suite is
        started as `python -m unittest discover tests`, which sets top_level_dir
        to tests/, so the `tests` package is never imported and its __init__
        never sets the variable. The only reason it ever looked isolated is that
        tests/test_combat_blocks.py imports `tests.test_combat_shadow`, pulling
        the package in mid-run -- and 'test_cast_stall_guards' sorts before
        'test_combat_blocks', so its fixtures (card 999) were appended to the
        live bot.log of a bot that was playing at the time.
        """
        self._without_override()
        root = Path(runtime_paths.get_runtime_root()).resolve()
        self.assertNotEqual(root, runtime_paths.default_runtime_root().resolve())
        self.assertTrue(
            runtime_paths._running_under_test_runner(),
            "the test-runner guard does not recognise this runner, so an "
            "unset MTGA_RUNTIME_DIR would write into the live runtime dir",
        )

    def test_the_log_path_survives_losing_the_override(self):
        """End-to-end version of the above: the guard has to hold for the
        artefact that actually got polluted."""
        self._without_override()
        repo_log = Path(runtime_paths.get_repo_root()) / "runtime" / "logs" / "bot.log"
        before = repo_log.stat().st_mtime_ns if repo_log.exists() else None
        bot_logger.log_info("runtime isolation probe (no override)")
        after = repo_log.stat().st_mtime_ns if repo_log.exists() else None
        self.assertEqual(before, after, f"a test log line touched {repo_log}")


if __name__ == "__main__":
    unittest.main()
