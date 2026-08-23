"""Powering the PC off is opt-in, delayed, and tied to one single event.

This is the only feature in the app that ends the user's session on the whole
machine, so the interesting assertions are all about when it must NOT happen:
not by default, not on a manual stop, not on a crash, and never immediately.

The UI side is exercised through MTGBotUI._announce_round_complete on a bare
object (no Tk main loop): the method only touches config, the message boxes and
the scheduler, all of which are injected here.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import shutdown_scheduler


class _SchedulerTestBase(unittest.TestCase):
    """Records the argv the module would run, with the platform pinned.

    The platform has to be forced rather than read off sys.platform: both
    branches must be checked on whichever OS the suite happens to run on.
    """

    windows = True

    def setUp(self):
        self.calls = []
        self._real_run = shutdown_scheduler._run
        self._real_supported = shutdown_scheduler.is_supported
        shutdown_scheduler._run = lambda args: (self.calls.append(args) or True)
        shutdown_scheduler.is_supported = lambda: True
        self.addCleanup(setattr, shutdown_scheduler, "_run", self._real_run)
        self.addCleanup(setattr, shutdown_scheduler, "is_supported", self._real_supported)
        for name, value in (("_is_windows", self.windows), ("_is_linux", not self.windows)):
            self.addCleanup(setattr, shutdown_scheduler, name, getattr(shutdown_scheduler, name))
            setattr(shutdown_scheduler, name, lambda v=value: v)

    def delay_arg(self) -> int:
        """The scheduled delay of the last call, in seconds."""
        args = self.calls[0]
        if self.windows:
            return int(args[3])
        self.assertTrue(args[2].startswith("+"), args)
        return int(args[2][1:]) * 60


class ScheduleShutdownWindowsTest(_SchedulerTestBase):
    windows = True

    def test_the_delay_is_passed_in_seconds(self):
        shutdown_scheduler.schedule_shutdown(120)
        self.assertEqual(self.calls[0][:3], ["shutdown", "/s", "/t"])
        self.assertEqual(self.calls[0][3], "120")

    def test_an_instant_shutdown_is_clamped_to_the_minimum(self):
        """A zero delay would take away the only chance to abort."""
        for requested in (0, -5, 1):
            with self.subTest(requested=requested):
                self.calls.clear()
                shutdown_scheduler.schedule_shutdown(requested)
                self.assertGreaterEqual(
                    self.delay_arg(), shutdown_scheduler.MIN_DELAY_SEC
                )

    def test_a_garbage_delay_falls_back_to_the_default(self):
        shutdown_scheduler.schedule_shutdown("soon")  # type: ignore[arg-type]
        self.assertEqual(self.delay_arg(), shutdown_scheduler.DEFAULT_DELAY_SEC)

    def test_the_comment_is_truncated_so_the_command_is_not_rejected(self):
        shutdown_scheduler.schedule_shutdown(60, comment="x" * 900)
        self.assertLessEqual(len(self.calls[0][-1]), 512)

    def test_cancelling_uses_the_abort_flag(self):
        shutdown_scheduler.cancel_shutdown()
        self.assertEqual(self.calls[0], ["shutdown", "/a"])
        self.assertEqual(shutdown_scheduler.cancel_command_hint(), "shutdown /a")


class ScheduleShutdownLinuxTest(_SchedulerTestBase):
    """The Linux flags: `-h +<minutes>`, cancelled with `-c`."""

    windows = False

    def test_the_delay_is_passed_in_minutes(self):
        shutdown_scheduler.schedule_shutdown(120)
        self.assertEqual(self.calls[0][:3], ["shutdown", "-h", "+2"])

    def test_the_wall_broadcast_is_suppressed(self):
        """A denied set-wall-message must not cost us the whole shutdown."""
        shutdown_scheduler.schedule_shutdown(120)
        self.assertIn("--no-wall", self.calls[0])

    def test_a_sub_minute_delay_never_becomes_an_instant_shutdown(self):
        """`+0` means "now" -- rounding down would delete the abort window."""
        for requested in (0, -5, 1, 30, 59):
            with self.subTest(requested=requested):
                self.calls.clear()
                shutdown_scheduler.schedule_shutdown(requested)
                self.assertEqual(self.calls[0][2], "+1")
                self.assertGreaterEqual(
                    self.delay_arg(), shutdown_scheduler.MIN_DELAY_SEC
                )

    def test_the_minute_delay_is_rounded_up_not_down(self):
        """Firing earlier than asked would shorten the user's abort window."""
        shutdown_scheduler.schedule_shutdown(121)
        self.assertEqual(self.calls[0][2], "+3")

    def test_a_garbage_delay_falls_back_to_the_default(self):
        shutdown_scheduler.schedule_shutdown("soon")  # type: ignore[arg-type]
        self.assertEqual(self.delay_arg(), shutdown_scheduler.DEFAULT_DELAY_SEC)

    def test_the_comment_is_not_passed_as_a_wall_message(self):
        shutdown_scheduler.schedule_shutdown(120, comment="x" * 900)
        self.assertEqual(self.calls[0], ["shutdown", "-h", "+2", "--no-wall"])

    def test_cancelling_uses_the_cancel_flag(self):
        shutdown_scheduler.cancel_shutdown()
        self.assertEqual(self.calls[0], ["shutdown", "-c"])
        self.assertEqual(shutdown_scheduler.cancel_command_hint(), "shutdown -c")


class PlatformSupportTest(unittest.TestCase):
    def _pin(self, *, windows, linux, have_binary=True):
        for name, value in (
            ("_is_windows", windows),
            ("_is_linux", linux),
            ("_have_shutdown_binary", have_binary),
        ):
            self.addCleanup(setattr, shutdown_scheduler, name, getattr(shutdown_scheduler, name))
            setattr(shutdown_scheduler, name, lambda v=value: v)

    def test_windows_is_supported(self):
        self._pin(windows=True, linux=False)
        self.assertTrue(shutdown_scheduler.is_supported())

    def test_linux_is_supported_when_the_binary_exists(self):
        self._pin(windows=False, linux=True)
        self.assertTrue(shutdown_scheduler.is_supported())

    def test_linux_without_a_shutdown_binary_is_not_supported(self):
        self._pin(windows=False, linux=True, have_binary=False)
        self.assertFalse(shutdown_scheduler.is_supported())

    def test_macos_is_not_supported(self):
        """`shutdown` needs root there, which a background run cannot get."""
        self._pin(windows=False, linux=False)
        self.assertFalse(shutdown_scheduler.is_supported())

    def test_nothing_is_executed_on_an_unsupported_platform(self):
        calls = []
        self.addCleanup(setattr, shutdown_scheduler, "_run", shutdown_scheduler._run)
        shutdown_scheduler._run = lambda args: (calls.append(args) or True)
        self._pin(windows=False, linux=False)
        self.assertFalse(shutdown_scheduler.schedule_shutdown(120))
        self.assertFalse(shutdown_scheduler.cancel_shutdown())
        self.assertEqual(calls, [], "ran a shutdown command on an unsupported OS")

    def test_a_failing_subprocess_is_reported_not_raised(self):
        # A command that cannot exist -- _run must swallow the OSError.
        self.assertFalse(shutdown_scheduler._run(["definitely-not-a-real-binary-xyz"]))


class ConfigDefaultTest(unittest.TestCase):
    def _manager(self):
        import ui

        path = os.path.join(tempfile.mkdtemp(prefix="shutdown-cfg-"), "calibration_config.json")
        return ui.ConfigManager(config_path=path)

    def test_the_setting_is_off_unless_the_user_turns_it_on(self):
        self.assertFalse(self._manager().get_shutdown_pc_when_round_complete())

    def test_the_setting_round_trips_through_the_config_file(self):
        cm = self._manager()
        cm.set_shutdown_pc_when_round_complete(True)
        self.assertTrue(cm.get_shutdown_pc_when_round_complete())
        cm.set_shutdown_pc_when_round_complete(False)
        self.assertFalse(cm.get_shutdown_pc_when_round_complete())

    def test_a_non_boolean_stored_value_reads_as_off(self):
        """A hand-edited or corrupt config must not power the machine down."""
        cm = self._manager()
        for junk in ("true", 1, "yes", None, [], "on"):
            with self.subTest(junk=junk):
                cm.config["shutdown_pc_when_round_complete"] = junk
                self.assertFalse(cm.get_shutdown_pc_when_round_complete())


class AnnounceRoundCompleteTest(unittest.TestCase):
    """The UI hook: which stop reasons arm the shutdown, and which do not."""

    def setUp(self):
        import ui

        self.ui_mod = ui
        self.scheduled = []
        self.cancelled = []
        self.infos = []
        self.warnings = []
        self.answer = False  # what askyesno("Cancel the shutdown?") returns

        class FakeBox:
            @staticmethod
            def showinfo(title, msg, **k):
                self.infos.append((title, msg))

            @staticmethod
            def showwarning(title, msg, **k):
                self.warnings.append((title, msg))

            @staticmethod
            def askyesno(title, msg, **k):
                return self.answer

        self._real_box = ui.messagebox
        ui.messagebox = FakeBox
        self.addCleanup(setattr, ui, "messagebox", self._real_box)

        self._real_sched = ui.shutdown_scheduler
        outer = self

        class FakeScheduler:
            DEFAULT_DELAY_SEC = 120
            ok = True

            @staticmethod
            def is_supported():
                return True

            @staticmethod
            def schedule_shutdown(delay, comment=""):
                outer.scheduled.append(delay)
                return FakeScheduler.ok

            @staticmethod
            def cancel_shutdown():
                outer.cancelled.append(True)
                return True

        self.fake_scheduler = FakeScheduler
        ui.shutdown_scheduler = FakeScheduler
        self.addCleanup(setattr, ui, "shutdown_scheduler", self._real_sched)

    def _ui(self, *, enabled):
        obj = self.ui_mod.MTGBotUI.__new__(self.ui_mod.MTGBotUI)
        obj.config_manager = type(
            "CM", (), {"get_shutdown_pc_when_round_complete": lambda _s: enabled}
        )()
        return obj

    def test_the_pc_stays_on_when_the_option_is_off(self):
        self._ui(enabled=False)._announce_round_complete()
        self.assertEqual(self.scheduled, [], "shut down without being asked to")
        self.assertEqual(len(self.infos), 1, "user was not told the round finished")

    def test_the_shutdown_is_armed_when_the_option_is_on(self):
        self._ui(enabled=True)._announce_round_complete()
        self.assertEqual(self.scheduled, [120])

    def test_answering_the_prompt_cancels_the_shutdown(self):
        self.answer = True
        self._ui(enabled=True)._announce_round_complete()
        self.assertEqual(self.cancelled, [True])

    def test_the_user_is_warned_when_arming_the_shutdown_failed(self):
        """Silence here means going to bed expecting an off machine."""
        self.fake_scheduler.ok = False
        self.addCleanup(setattr, self.fake_scheduler, "ok", True)
        self._ui(enabled=True)._announce_round_complete()
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("could NOT", self.warnings[0][1])

    def test_an_unreadable_config_is_treated_as_off(self):
        obj = self.ui_mod.MTGBotUI.__new__(self.ui_mod.MTGBotUI)

        def boom(_s):
            raise RuntimeError("config gone")

        obj.config_manager = type("CM", (), {"get_shutdown_pc_when_round_complete": boom})()
        obj._announce_round_complete()
        self.assertEqual(self.scheduled, [])


class StopReasonRoutingTest(unittest.TestCase):
    """Only the controller's end-of-round reason may reach the shutdown path.

    The UI matches on the reason string, so this pins the string the controller
    actually sends against the condition the UI actually applies -- if either
    side is reworded alone, the shutdown silently stops working (or, worse,
    starts firing on a plain stop).
    """

    @staticmethod
    def _ui_accepts(reason) -> bool:
        reason_s = str(reason or "").lower()
        return "account" in reason_s and "complet" in reason_s

    def test_the_controller_reason_matches_the_ui_condition(self):
        controller_src = os.path.join(
            ROOT, "Controller", "MTGAController", "Controller.py"
        )
        with open(controller_src, encoding="utf-8") as f:
            source = f.read()
        reason = "all accounts completed this round"
        self.assertIn(
            f'_request_stop_bot("{reason}")',
            source,
            "the end-of-round stop reason changed; the UI hook no longer fires",
        )
        self.assertTrue(self._ui_accepts(reason))

    def test_ordinary_stop_reasons_do_not_reach_the_shutdown(self):
        for reason in (
            None,
            "",
            "user pressed stop",
            "bot error",
            "missing calibrated Log Out button(s)",
            "account switch failed",
            "switching account",
        ):
            with self.subTest(reason=reason):
                self.assertFalse(self._ui_accepts(reason))


if __name__ == "__main__":
    unittest.main()
