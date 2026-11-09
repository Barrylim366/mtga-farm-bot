"""The Account Manager's Log In: the log-side decision and the input order.

No Tk and no screen here. The window is checked by looking at it; what these
drive is the part that silently decides whether to type into a login form or to
log out first, plus the input sequence, which must stay the account switch's.

Both are exercised on a Controller built with __new__ (the pattern the other
controller tests use), with the screen-touching helpers replaced by stubs that
fail loudly if the flow ever reaches for them.
"""
import json
import os
import shutil
import tempfile
import unittest
import uuid
from unittest import mock

import account_login
from Controller.MTGAController.Controller import Controller
from state.state_machine import PlayerLogStateTracker


class _RecordingInput:
    """Stands in for the real input backend and records every call in order."""

    def __init__(self, log_path=None, on_enter=None):
        self.calls = []
        self._log_path = log_path
        self._on_enter = on_enter

    def tap_delete(self):
        self.calls.append("tap_delete")

    def tap_escape(self):
        self.calls.append("tap_escape")

    def type_text(self, text):
        self.calls.append(f"type_text:{text}")

    def tap_tab(self):
        self.calls.append("tap_tab")

    def tap_enter(self):
        self.calls.append("tap_enter")
        if self._on_enter is not None and self._log_path:
            with open(self._log_path, "a", encoding="utf-8") as handle:
                handle.write(self._on_enter)


class LoginScreenDetectionTest(unittest.TestCase):
    """Which side of the login boundary the newest record is on."""

    def test_an_empty_log_answers_nothing(self):
        self.assertIsNone(account_login.client_is_at_login_screen(""))
        self.assertIsNone(account_login.client_is_at_login_screen(None))

    def test_a_log_without_either_record_answers_nothing(self):
        self.assertIsNone(account_login.client_is_at_login_screen("Some unrelated line\n"))

    def test_the_panel_with_nothing_after_it_is_the_login_screen(self):
        text = "...\nALT_Prefab.LoginPanelPrefab\n"
        self.assertTrue(account_login.client_is_at_login_screen(text))

    def test_a_login_after_the_panel_means_signed_in(self):
        text = (
            "ALT_Prefab.LoginPanelPrefab\n"
            "[Accounts - Login] Attempting Login with credentials.\n"
            "[Accounts - Login] Logged in successfully. Display Name: luteus#52668\n"
        )
        self.assertFalse(account_login.client_is_at_login_screen(text))

    def test_the_main_nav_after_the_panel_means_signed_in(self):
        text = "ALT_Prefab.LoginPanelPrefab\n... a whole match ...\nMainNav load in\n"
        self.assertFalse(account_login.client_is_at_login_screen(text))

    def test_a_panel_after_hours_of_play_means_the_login_screen_again(self):
        text = (
            "ALT_Prefab.LoginPanelPrefab\n"
            "Logged in successfully. Display Name: luteus#52668\n"
            "Client.SceneChange {\"toSceneName\":\"Home\"}\n"
            "Sending player back to Login screen.\n"
            "ALT_Prefab.LoginPanelPrefab\n"
        )
        self.assertTrue(account_login.client_is_at_login_screen(text))

    def test_the_sending_back_record_counts_as_the_login_screen(self):
        text = "MainNav load in\n[Accounts - Startup] Fast login error: -1\nSending player back to Login screen.\n"
        self.assertTrue(account_login.client_is_at_login_screen(text))


class SameAccountTest(unittest.TestCase):
    def test_a_bare_name_matches_its_discriminated_spelling(self):
        self.assertTrue(account_login.same_account("mycoides", "mycoides#47724"))
        self.assertTrue(account_login.same_account("mycoides#47724", "mycoides"))

    def test_case_does_not_matter(self):
        self.assertTrue(account_login.same_account("Mycoides", "mycoides#47724"))

    def test_two_accounts_are_not_the_same(self):
        self.assertFalse(account_login.same_account("luteus#1", "mycoides#2"))

    def test_a_shared_visible_name_is_not_enough_when_both_are_discriminated(self):
        """Two accounts can differ only by their digits; merging them would
        report "already signed in" for the wrong one."""
        self.assertFalse(account_login.same_account("player#11111", "player#22222"))
        self.assertTrue(account_login.same_account("player#11111", "PLAYER#11111"))

    def test_an_empty_name_is_not_an_identity(self):
        self.assertFalse(account_login.same_account("", "mycoides"))
        self.assertFalse(account_login.same_account("mycoides", ""))


class NewestLoginNameTest(unittest.TestCase):
    """Which account is signed in is a fact of the log, and it is the last one."""

    def test_the_newest_login_wins(self):
        text = (
            "Logged in successfully. Display Name: alpha#11111\n"
            "Logged in successfully. Display Name: charlie#33333\n"
        )
        self.assertEqual(account_login.newest_login_name(text), "charlie#33333")

    def test_the_match_server_handshake_names_the_account_too(self):
        text = (
            "Logged in successfully. Display Name: alpha#11111\n"
            '{"authenticateResponse": {"screenName": "alpha"}}\n'
        )
        self.assertEqual(account_login.newest_login_name(text), "alpha")

    def test_the_newest_of_both_kinds_wins(self):
        text = (
            '{"authenticateResponse": {"screenName": "bravo#22222"}}\n'
            "Logged in successfully. Display Name: alpha#11111\n"
        )
        self.assertEqual(account_login.newest_login_name(text), "alpha#11111")

    def test_the_discriminator_is_kept(self):
        text = "Logged in successfully. Display Name: mycoides#47724\n"
        self.assertEqual(account_login.newest_login_name(text), "mycoides#47724")

    def test_nothing_to_read_answers_none(self):
        self.assertIsNone(account_login.newest_login_name(""))
        self.assertIsNone(account_login.newest_login_name("Draft started\n"))


class _NoScreen:
    def __call__(self, *args, **kwargs):
        raise AssertionError("the manual login must not touch the screen in this test")


def new_work_dir() -> str:
    """A scratch dir under the process TEMP that this process can write into.

    Deliberately not tempfile.mkdtemp: on Python 3.14 that creates the directory
    with an owner-only DACL, which a restricted token (the test sandbox) cannot
    write into, so the first child file fails with "Access denied".
    """
    path = os.path.join(tempfile.gettempdir(), f"manual-login-{uuid.uuid4().hex[:10]}")
    os.makedirs(path, exist_ok=True)
    return path


class _ControllerTestCase(unittest.TestCase):
    def make_controller(self, log_text: str = "", log_path: str | None = None):
        """A Controller built the way the other controller tests build one.

        Every helper that would capture the screen or move the real mouse is
        replaced: `__new__` leaves no `_vision`, and the image helpers are
        explicit traps so an unexpected screen call fails as a test, not as a
        click on the live game.
        """
        directory = new_work_dir()
        self.addCleanup(shutil.rmtree, directory, True)
        if log_path is None:
            log_path = os.path.join(directory, "Player.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write(log_text)
        controller = Controller.__new__(Controller)
        controller._log_path = log_path
        controller._state_tracker = PlayerLogStateTracker(max_lines=200)
        controller._stop_requested = False
        # Defaults __init__ would have set; only the ones these flows read.
        controller.log_out_btn_coors = (959, 667)
        controller.log_out_ok_btn_coors = (1875, 809)
        controller._arena_region = (0, 0, 1920, 1080)
        controller._last_good_arena_region = None
        controller._locate_image_center_in_scaled_arena_region = _NoScreen()
        controller._click_image_in_scaled_arena_region = _NoScreen()
        controller._locate_image_center_in_rescaled_region = _NoScreen()
        return controller


class ManualLoginDecisionTest(_ControllerTestCase):
    """What sign_in_for_manual_play decides before any input is sent."""

    ACCOUNT = {
        "name": "luteus",
        "screen_name": "luteus",
        "email": "luteus@example.test",
        "pw": "secret",
        "folder": "luteus",
    }

    def test_a_match_in_progress_is_refused(self):
        log = (
            "Logged in successfully. Display Name: luteus#1\n"
            "MainNav load in\n"
            '{"type": "GREMessageType_GameStateMessage"}\n'
        )
        controller = self.make_controller(log)
        controller._focus_arena_for_manual_login = _NoScreen()
        controller._manual_login_screen_kind = _NoScreen()
        ok, message = controller.sign_in_for_manual_play(dict(self.ACCOUNT))
        self.assertFalse(ok)
        self.assertIn("match", message.lower())

    def test_incomplete_credentials_are_refused_before_touching_arena(self):
        controller = self.make_controller("MainNav load in\n")
        controller._focus_arena_for_manual_login = _NoScreen()
        ok, message = controller.sign_in_for_manual_play({**self.ACCOUNT, "pw": ""})
        self.assertFalse(ok)
        self.assertIn("password", message)

    def test_the_account_already_signed_in_is_a_no_op(self):
        log = (
            "ALT_Prefab.LoginPanelPrefab\n"
            "[Accounts - Login] Logged in successfully. Display Name: luteus#52668\n"
            "MainNav load in\n"
        )
        controller = self.make_controller(log)
        controller._focus_arena_for_manual_login = lambda: (True, "")
        controller._manual_login_screen_kind = lambda: ("menu", "")
        controller.set_current_account_manual = lambda *_a, **_k: None
        controller._publish_account_switch_status = lambda: None
        controller._manual_logout_for_manual_login = _NoScreen()
        controller._submit_manual_login = _NoScreen()
        ok, message = controller.sign_in_for_manual_play(dict(self.ACCOUNT))
        self.assertTrue(ok)
        self.assertIn("already signed in", message)

    def test_a_different_account_is_logged_out_first(self):
        log = (
            "ALT_Prefab.LoginPanelPrefab\n"
            "[Accounts - Login] Logged in successfully. Display Name: anthracis#9\n"
            "MainNav load in\n"
        )
        controller = self.make_controller(log)
        controller._focus_arena_for_manual_login = lambda: (True, "")
        controller._manual_login_screen_kind = lambda: ("menu", "")
        controller._login_delete_delay_sec = 0.0
        calls = []
        controller._manual_logout_for_manual_login = lambda **kwargs: (calls.append("logout"), (True, ""))[1]
        controller._submit_manual_login = lambda account, **kwargs: (
            calls.append(("submit", kwargs["settle_sec"])), (True, "done")
        )[1]
        ok, message = controller.sign_in_for_manual_play(dict(self.ACCOUNT))
        self.assertTrue(ok)
        self.assertEqual(calls[0], "logout")
        self.assertEqual(calls[1][0], "submit")
        self.assertEqual(calls[1][1], 0.0)

    def test_the_login_screen_skips_the_logout(self):
        controller = self.make_controller("Sending player back to Login screen.\n")
        controller._focus_arena_for_manual_login = lambda: (True, "")
        controller._manual_login_screen_kind = lambda: ("login", "")
        controller._manual_logout_for_manual_login = _NoScreen()
        controller._submit_manual_login = lambda account, **kwargs: (True, "typed")
        ok, _message = controller.sign_in_for_manual_play(dict(self.ACCOUNT))
        self.assertTrue(ok)

    def test_a_row_without_an_arena_name_is_not_assumed_to_be_signed_in(self):
        """Without a configured Arena name there is nothing to compare the log
        against, so the safe round trip happens instead of a guess."""
        log = (
            "ALT_Prefab.LoginPanelPrefab\n"
            "[Accounts - Login] Logged in successfully. Display Name: luteus#52668\n"
            "MainNav load in\n"
        )
        controller = self.make_controller(log)
        controller._focus_arena_for_manual_login = lambda: (True, "")
        controller._manual_login_screen_kind = lambda: ("menu", "")
        controller._login_delete_delay_sec = 0.0
        calls = []
        controller._manual_logout_for_manual_login = lambda **_k: (calls.append("logout"), (True, ""))[1]
        controller._submit_manual_login = lambda account, **kwargs: (calls.append("submit"), (True, "done"))[1]
        row = {k: v for k, v in self.ACCOUNT.items() if k != "screen_name"}
        ok, _message = controller.sign_in_for_manual_play(row)
        self.assertTrue(ok)
        self.assertEqual(calls, ["logout", "submit"])

    def test_an_unreadable_screen_sends_no_input(self):
        controller = self.make_controller("MainNav load in\n")
        controller._focus_arena_for_manual_login = lambda: (True, "")
        controller._manual_login_screen_kind = lambda: ("unknown", "")
        controller._manual_logout_for_manual_login = _NoScreen()
        controller._submit_manual_login = _NoScreen()
        ok, message = controller.sign_in_for_manual_play(dict(self.ACCOUNT))
        self.assertFalse(ok)
        self.assertIn("Arena is not showing", message)


class ScreenKindTest(_ControllerTestCase):
    """The screen probe's verdicts, with the vision layer faked."""

    @staticmethod
    def detection(ok, code, anchor=None, region=(0, 0, 1920, 1080)):
        from vision.window_locator import ArenaDetectionResult

        return ArenaDetectionResult(
            ok=ok, region=region, code=code, message=f"{code} message", matched_anchor=anchor
        )

    class Provider:
        def __init__(self, detections):
            self._detections = list(detections)
            self.last_detection_result = self._detections[0]

        def detect(self, **_kwargs):
            if len(self._detections) > 1:
                self.last_detection_result = self._detections.pop(0)
            return self.last_detection_result

    def test_a_menu_anchor_is_a_menu(self):
        controller = self.make_controller("ALT_Prefab.LoginPanelPrefab\n")
        controller._arena_region_provider = self.Provider(
            [self.detection(True, "ok", anchor="home_anchor.png")]
        )
        controller._remember_arena_region = lambda _r: None
        controller._login_form_email_point = lambda **_k: None
        self.assertEqual(controller._manual_login_screen_kind(), ("menu", ""))

    def test_the_wizards_form_wins_over_the_options_anchor(self):
        # The login form's top-right gear matches the Options anchor; the form's
        # e-mail field is the tie-breaker and means "login", not "log out".
        controller = self.make_controller("ALT_Prefab.LoginPanelPrefab\n")
        controller._arena_region_provider = self.Provider(
            [self.detection(True, "ok", anchor="options_anchor.png")]
        )
        controller._remember_arena_region = lambda _r: None
        controller._login_form_email_point = lambda **_k: (960, 634)
        controller.input = mock.Mock()

        self.assertEqual(controller._manual_login_screen_kind(), ("login", ""))
        controller.input.tap_escape.assert_not_called()

    def test_no_anchor_and_a_login_panel_in_the_log_is_the_login_screen(self):
        controller = self.make_controller(
            "Logged in successfully. Display Name: luteus#1\n"
            "MainNav load in\n"
            "ALT_Prefab.LoginPanelPrefab\n"
        )
        controller._arena_region_provider = self.Provider([self.detection(False, "anchor_not_found")])
        controller._remember_arena_region = lambda _r: None
        controller._manual_login_splash_point = lambda **_k: None
        self.assertEqual(controller._manual_login_screen_kind(), ("login", ""))

    def test_no_anchor_and_a_sign_in_in_the_log_is_not_the_login_screen(self):
        controller = self.make_controller(
            "ALT_Prefab.LoginPanelPrefab\n"
            "Logged in successfully. Display Name: luteus#1\n"
            "MainNav load in\n"
        )
        controller._MANUAL_LOGIN_SCREEN_TIMEOUT_SEC = 0.0
        controller._arena_region_provider = self.Provider([self.detection(False, "anchor_not_found")])
        controller._remember_arena_region = lambda _r: None
        controller._manual_login_splash_point = lambda **_k: None
        controller._login_form_email_point = lambda **_k: None
        controller._clear_manual_login_popup = lambda: False
        self.assertEqual(controller._manual_login_screen_kind(), ("unknown", ""))

    def test_a_bad_window_geometry_is_unavailable(self):
        controller = self.make_controller("MainNav load in\n")
        controller._arena_region_provider = self.Provider([self.detection(False, "window_wrong_size")])
        kind, detail = controller._manual_login_screen_kind()
        self.assertEqual(kind, "unavailable")
        self.assertIn("window_wrong_size", detail)


class LogoutRetryTest(_ControllerTestCase):
    """A successful Log Out is never retried; a swallowed ESC is, once."""

    @staticmethod
    def quiet(controller, kind=("menu", "")):
        controller._manual_login_frame = lambda: None
        controller._write_manual_login_bundle = lambda *a, **k: None
        controller._manual_login_options_visible = lambda: False
        controller._manual_login_screen_kind = lambda: kind

    def test_a_clicked_logout_is_never_retried(self):
        """The 13:52 bug: a logout that worked was retried from the login screen,
        and the retry's Home click + ESC backed out of the credentials form."""
        controller = self.make_controller("MainNav load in\n")
        homes = []
        controller._navigate_to_home = lambda: homes.append(1) or True
        self.quiet(controller)
        attempts = []
        controller._run_mapped_logout_sequence = lambda **kwargs: attempts.append(kwargs) or True
        controller._manual_login_wait_for_logout = lambda **_k: False
        ok, message = controller._manual_logout_for_manual_login(say=lambda _t: None)
        self.assertFalse(ok)
        self.assertIn("login screen did not appear", message)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(homes), 1)   # one warm-up, then no second attempt

    def test_a_swallowed_escape_is_retried_once_from_a_menu(self):
        controller = self.make_controller("MainNav load in\n")
        controller._navigate_to_home = lambda: True
        self.quiet(controller)
        attempts = []
        controller._run_mapped_logout_sequence = lambda **kwargs: attempts.append(kwargs) or False
        controller._manual_login_wait_for_logout = lambda **_k: False
        ok, message = controller._manual_logout_for_manual_login(say=lambda _t: None)
        self.assertFalse(ok)
        self.assertIn("Options menu", message)
        self.assertEqual(len(attempts), 2)
        for kwargs in attempts:
            self.assertFalse(kwargs["allow_coordinate_fallback"])
            self.assertTrue(kwargs["verify_options"])
            self.assertTrue(kwargs["allow_verified_coordinate_log_out"])
            self.assertGreater(kwargs["log_out_timeout"], 2.5)

    def test_reaching_the_login_screen_after_a_missed_click_counts_as_logged_out(self):
        controller = self.make_controller("MainNav load in\n")
        controller._navigate_to_home = lambda: True
        self.quiet(controller, kind=("login", ""))
        controller._run_mapped_logout_sequence = lambda **_k: False
        controller._manual_login_wait_for_logout = lambda **_k: False
        controller._MANUAL_LOGOUT_ATTEMPTS = 2
        ok, message = controller._manual_logout_for_manual_login(say=lambda _t: None)
        self.assertTrue(ok, message)

    def test_a_screen_that_is_no_longer_a_menu_stops_the_retry(self):
        controller = self.make_controller("MainNav load in\n")
        controller._navigate_to_home = lambda: True
        self.quiet(controller, kind=("unknown", ""))
        calls = []
        controller._run_mapped_logout_sequence = lambda **_k: calls.append(1) or False
        controller._manual_login_wait_for_logout = lambda **_k: False
        controller._MANUAL_LOGOUT_ATTEMPTS = 2
        ok, message = controller._manual_logout_for_manual_login(say=lambda _t: None)
        self.assertFalse(ok)
        self.assertIn("Home screen", message)
        self.assertEqual(len(calls), 1)

    def test_a_later_attempt_succeeds(self):
        controller = self.make_controller("MainNav load in\n")
        homes = []
        controller._navigate_to_home = lambda: homes.append(True) or True
        self.quiet(controller)
        clicked = [False, True]          # first ESC swallowed, second worked
        waited = [True]
        controller._run_mapped_logout_sequence = lambda **_k: clicked.pop(0)
        controller._manual_login_wait_for_logout = lambda **_k: waited.pop(0)
        ok, message = controller._manual_logout_for_manual_login(say=lambda _t: None)
        self.assertTrue(ok)
        self.assertEqual(message, "")
        self.assertEqual(len(homes), 2)


class LogoutSequenceOptionsTest(_ControllerTestCase):
    """The switch's sequence with the manual path's Options verification."""

    def build(self, *, image_points=(None,), options=(False,)):
        controller = self.make_controller("MainNav load in\n")
        controller.input = _RecordingInput()
        controller.clicks = []
        controller._click_abs = lambda *a, **k: controller.clicks.append(a)
        controller._map_abs_point_to_arena = lambda point, **kwargs: (point, "test")
        controller._press_esc_for_options = lambda: controller.input.calls.append("tap_escape")
        self.points = list(image_points)
        self.options = list(options)

        def locate(**_kwargs):
            return self.points.pop(0) if len(self.points) > 1 else self.points[0]

        def visible():
            return self.options.pop(0) if len(self.options) > 1 else self.options[0]

        controller._locate_log_out_button = locate
        controller._manual_login_options_visible = visible
        return controller

    def run_sequence(self, controller, **kwargs):
        with mock.patch("Controller.MTGAController.Controller.time.sleep", lambda *_a: None):
            return controller._run_mapped_logout_sequence(**kwargs)

    def labels(self, controller):
        return [call[2] for call in controller.clicks]

    def test_options_already_open_are_not_toggled_off(self):
        controller = self.build(image_points=((1581, 733),), options=(True,))
        self.assertTrue(self.run_sequence(controller, verify_options=True))
        self.assertEqual(controller.input.calls, [])   # no ESC at all
        self.assertEqual(self.labels(controller), ["LOG_OUT_BTN", "LOG_OUT_OK", "LOG_OUT_OK"])

    def test_a_swallowed_escape_is_pressed_again(self):
        # Round 1 opens nothing; round 2 does and its search finds the link.
        controller = self.build(image_points=(None, (1581, 733)), options=(False,))
        self.assertTrue(self.run_sequence(controller, verify_options=True))
        self.assertEqual(controller.input.calls, ["tap_escape", "tap_escape"])

    def test_never_opening_options_sends_no_click(self):
        controller = self.build(image_points=(None,), options=(False,))
        self.assertFalse(
            self.run_sequence(controller, allow_coordinate_fallback=False, verify_options=True)
        )
        self.assertEqual(controller.input.calls, ["tap_escape", "tap_escape"])
        self.assertEqual(controller.clicks, [])

    def test_the_verified_coordinate_is_used_when_the_template_misses(self):
        controller = self.build(image_points=(None,), options=(True,))
        self.assertTrue(
            self.run_sequence(
                controller,
                allow_coordinate_fallback=False,
                verify_options=True,
                allow_verified_coordinate_log_out=True,
            )
        )
        self.assertEqual(
            self.labels(controller), ["LOG_OUT_BTN_VERIFIED", "LOG_OUT_OK", "LOG_OUT_OK"]
        )

    def test_the_verified_coordinate_is_never_used_without_the_overlay(self):
        controller = self.build(image_points=(None,), options=(False,))
        self.assertFalse(
            self.run_sequence(
                controller,
                allow_coordinate_fallback=False,
                allow_verified_coordinate_log_out=True,
            )
        )
        self.assertEqual(controller.clicks, [])

    def test_the_verified_coordinate_is_never_used_off_1920(self):
        """The point was measured on a 1920-wide client; MTGA draws the overlay at
        a constant pixel size, so elsewhere only the image search is right."""
        controller = self.build(image_points=(None,), options=(True,))
        controller._arena_region = (0, 0, 2560, 1440)
        self.assertFalse(
            self.run_sequence(
                controller,
                allow_coordinate_fallback=False,
                verify_options=True,
                allow_verified_coordinate_log_out=True,
            )
        )
        self.assertEqual(controller.clicks, [])


class CredentialSequenceTest(_ControllerTestCase):
    """The typed sequence is the account switch's, call for call."""

    ACCOUNT = {
        "name": "luteus",
        "screen_name": "luteus",
        "email": "luteus@example.test",
        "pw": "s3cr3t",
        "folder": "luteus",
    }

    def build(self, on_enter=None):
        log = "MainNav load in\n"
        controller = self.make_controller(log)
        with open(controller._log_path, "w", encoding="utf-8") as handle:
            handle.write(log)
        controller.input = _RecordingInput(controller._log_path, on_enter=on_enter)
        # Never reached: the splash probe is stubbed out below. Present only so a
        # future caller cannot fall through to the real Buttons folder.
        controller._buttons_dir = lambda: os.path.join(os.path.dirname(controller._log_path), "no-buttons")
        controller._manual_login_splash_point = lambda **_k: None
        controller._latch_identity_from_switch_target = lambda _account: True
        controller.set_current_account_manual = lambda *_a, **_k: None
        controller._publish_account_switch_status = lambda: None
        controller.bundles = []
        controller._write_manual_login_bundle = lambda reason, **kwargs: controller.bundles.append(reason)
        return controller

    def test_the_order_and_the_waits_are_the_switches(self):
        success = "[Accounts - Login] Logged in successfully. Display Name: luteus#52668\n"
        controller = self.build(on_enter=success)
        with mock.patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False):
            ok, message = controller._submit_manual_login(
                dict(self.ACCOUNT), label="luteus", settle_sec=0.0, say=lambda _t: None
            )
        self.assertTrue(ok, message)
        self.assertIn("luteus", message)
        self.assertEqual(
            controller.input.calls,
            [
                "tap_delete",
                "type_text:luteus@example.test",
                "tap_tab",
                "type_text:s3cr3t",
                "tap_enter",
            ],
        )

    def test_an_unconfirmed_login_is_reported_not_trusted(self):
        controller = self.build(on_enter="")
        controller._MANUAL_LOGIN_CONFIRM_TIMEOUT = 0.6
        with mock.patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False):
            ok, message = controller._submit_manual_login(
                dict(self.ACCOUNT), label="luteus", settle_sec=0.0, say=lambda _t: None
            )
        self.assertFalse(ok)
        self.assertIn("did not confirm", message)
        self.assertEqual(controller.bundles, ["login-not-confirmed"])

    def test_a_different_account_signed_in_is_reported(self):
        other = "[Accounts - Login] Logged in successfully. Display Name: anthracis#9\n"
        controller = self.build(on_enter=other)
        with mock.patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False):
            ok, message = controller._submit_manual_login(
                dict(self.ACCOUNT), label="luteus", settle_sec=0.0, say=lambda _t: None
            )
        self.assertFalse(ok)
        self.assertIn("anthracis", message)
        self.assertIn("luteus", message)

    def test_a_row_without_an_arena_name_trusts_the_credentials_it_typed(self):
        """The login is confirmed by Arena; an unconfigured Arena name is not a
        mismatch, it is missing data."""
        other = "[Accounts - Login] Logged in successfully. Display Name: anthracis#9\n"
        controller = self.build(on_enter=other)
        row = {k: v for k, v in self.ACCOUNT.items() if k != "screen_name"}
        with mock.patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False):
            ok, message = controller._submit_manual_login(
                row, label="luteus", settle_sec=0.0, say=lambda _t: None
            )
        self.assertTrue(ok, message)
        self.assertIn("anthracis", message)


class FailureBundleTest(_ControllerTestCase):
    """The 2026-11-05 lesson: a failed manual login leaves screenshots behind."""

    def test_one_bundle_per_login_with_the_state_and_log_tail(self):
        controller = self.make_controller("MainNav load in\n")
        controller._manual_login_bundle_done = False
        controller._vision = None
        target = new_work_dir()
        with mock.patch(
            "Controller.MTGAController.Controller.bot_logger.ensure_debug_dir",
            return_value=target,
        ):
            controller._write_manual_login_bundle(
                "first-reason", extra={"log_out_clicked": False}
            )
            # The guard: a second failure in the same sign-in must not stack
            # bundles, and must not overwrite the first one either.
            controller._write_manual_login_bundle("second-reason")
        with open(os.path.join(target, "state.json"), encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(state["reason"], "first-reason")
        self.assertFalse(state["log_out_clicked"])
        self.assertIn("last_scene", state)
        self.assertTrue(os.path.exists(os.path.join(target, "log_tail.txt")))


class IntegrityGuardTest(unittest.TestCase):
    """Windows discards lower-integrity input silently; say so instead."""

    def test_a_lower_integrity_bot_is_told_why(self):
        import vision.window_locator as window_locator

        with mock.patch.object(
            window_locator, "process_integrity_level", side_effect=["Low", "Medium"]
        ), mock.patch.object(window_locator, "mtga_window_process_id", return_value=4242):
            message = window_locator.arena_integrity_problem()
        self.assertIsNotNone(message)
        self.assertIn("discards", message)
        self.assertIn("Low", message)
        self.assertIn("Medium", message)

    def test_equal_integrity_is_fine(self):
        import vision.window_locator as window_locator

        with mock.patch.object(
            window_locator, "process_integrity_level", side_effect=["Medium", "Medium"]
        ), mock.patch.object(window_locator, "mtga_window_process_id", return_value=4242):
            self.assertIsNone(window_locator.arena_integrity_problem())

    def test_an_unreadable_level_never_blocks_a_working_bot(self):
        import vision.window_locator as window_locator

        with mock.patch.object(
            window_locator, "process_integrity_level", side_effect=[None, "Medium"]
        ), mock.patch.object(window_locator, "mtga_window_process_id", return_value=4242):
            self.assertIsNone(window_locator.arena_integrity_problem())

    def test_no_arena_window_never_blocks(self):
        import vision.window_locator as window_locator

        with mock.patch.object(
            window_locator, "process_integrity_level", return_value="Low"
        ), mock.patch.object(window_locator, "mtga_window_process_id", return_value=None):
            self.assertIsNone(window_locator.arena_integrity_problem())


if __name__ == "__main__":
    unittest.main()
