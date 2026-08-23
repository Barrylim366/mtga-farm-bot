"""Typing credentials must not depend on the active keyboard layout.

The bug this pins: on macOS the auto backend is PyAutoGUIInputController, and
`pyautogui.typewrite` translates each character to a hardcoded *US*-layout
virtual keycode. macOS resolves that keycode against whatever layout is
currently active, so on a German QWERTZ Mac the `@` in an account e-mail
(US shift+2) arrived as `"`, y/z came out swapped, and `-`/`_` were mangled.
The account switch then typed a wrong e-mail and -- invisibly -- a wrong
password, and the login simply failed.

pynput inserts the literal character instead (CGEventKeyboardSetUnicodeString
on macOS, keymap remapping on X11), so type_text routes through it.

That assumption held on macOS and Windows and turned out to be **false on
X11/XWayland**: pynput resolves `@` to its AltGr level (AltGr+Q on a German
layout) and then presses Q without AltGr. Measured 2026-08-23 on a live run:
`a@b` arrived as `aqb`, the account e-mail was typed as `...qmail.de`, the
login failed, and the bot span for an hour in GO_HOME on a login screen it
believed it had passed. Linux therefore pastes via the clipboard (Ctrl+V
carries no character, so no layout can distort it) while Windows and macOS keep
the keystroke path exactly as it was.

Every test here pins the platform: the routing is per-platform now, so a test
that does not say which one it means would assert whatever the dev box happens
to run.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unittest import mock

from Controller.Utilities.input_controller import (
    InputControllerError,
    PyAutoGUIInputController,
)

EMAIL = "giacomo.joggerst@kompass-fb.com"


class FakePyAutoGUI:
    def __init__(self):
        self.typed = []
        self.hotkeys = []

    def typewrite(self, text, interval=0):
        self.typed.append(text)

    def hotkey(self, *keys):
        self.hotkeys.append(keys)


class FakeKeyboard:
    def __init__(self, fail_at=None):
        self.typed = []
        self._fail_at = fail_at

    def type(self, text):
        if self._fail_at is not None and self._fail_at in text:
            raise RuntimeError("keymap remap failed")
        self.typed.append(text)


def make_backend(keyboard):
    """Builds the backend without __init__ so the test never imports the real
    pyautogui (which needs a display) or touches the developer's mouse."""
    backend = object.__new__(PyAutoGUIInputController)
    backend._pyautogui = FakePyAutoGUI()
    backend._unicode_keyboard = keyboard
    return backend


def pin_platform(testcase, name):
    patcher = mock.patch(
        "Controller.Utilities.input_controller.platform.system", return_value=name
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)


class TypeTextLayoutTest(unittest.TestCase):
    """The keystroke path -- Windows and macOS, unchanged by the Linux fix."""

    def setUp(self):
        pin_platform(self, "Darwin")
    def test_text_goes_through_pynput_not_typewrite(self):
        """The regression itself: typewrite() is what mistypes `@` on QWERTZ."""
        keyboard = FakeKeyboard()
        backend = make_backend(keyboard)
        backend.type_text(EMAIL)
        self.assertEqual(keyboard.typed, [EMAIL])
        self.assertEqual(backend._pyautogui.typed, [], "typewrite must not be used when pynput is available")

    def test_falls_back_to_typewrite_only_when_pynput_is_missing(self):
        """Typing nothing at all would be worse than typing it via the US map."""
        backend = make_backend(None)
        backend.type_text(EMAIL)
        self.assertEqual(backend._pyautogui.typed, [EMAIL])

    def test_a_failure_mid_string_raises_instead_of_retyping(self):
        """pynput types character by character, so a partial failure has already
        put text in the field. Retyping the whole string would duplicate it --
        for a password that is unrecoverable and invisible."""
        backend = make_backend(FakeKeyboard(fail_at="@"))
        with self.assertRaises(InputControllerError):
            backend.type_text(EMAIL)
        self.assertEqual(backend._pyautogui.typed, [], "must not fall back and duplicate input")

    def test_empty_text_touches_no_backend(self):
        keyboard = FakeKeyboard()
        backend = make_backend(keyboard)
        backend.type_text("")
        backend.type_text(None)
        self.assertEqual(keyboard.typed, [])
        self.assertEqual(backend._pyautogui.typed, [])


class TypeTextLinuxClipboardTest(unittest.TestCase):
    """Linux pastes instead of typing, and never silently types on failure."""

    def setUp(self):
        pin_platform(self, "Linux")
        self.keyboard = FakeKeyboard()
        self.backend = make_backend(self.keyboard)

    def test_the_text_is_pasted_and_never_typed(self):
        with mock.patch.object(
            self.backend, "_type_text_via_clipboard", return_value=True
        ) as paste:
            self.backend.type_text(EMAIL)
        paste.assert_called_once_with(EMAIL)
        self.assertEqual(self.keyboard.typed, [], "typed the text despite pasting it")
        self.assertEqual(self.backend._pyautogui.typed, [])

    def test_a_failed_paste_raises_instead_of_typing_a_wrong_password(self):
        """The heart of the fix: the old fallback typed `@` as `q` and the bot
        played on as nobody, for an hour, with no error anywhere."""
        with mock.patch.object(
            self.backend, "_type_text_via_clipboard", return_value=False
        ):
            with self.assertRaises(InputControllerError) as caught:
                self.backend.type_text(EMAIL)
        self.assertIn("AltGr", str(caught.exception))
        self.assertEqual(self.keyboard.typed, [])
        self.assertEqual(self.backend._pyautogui.typed, [])

    def test_empty_text_never_reaches_the_clipboard(self):
        with mock.patch.object(self.backend, "_type_text_via_clipboard") as paste:
            self.backend.type_text("")
            self.backend.type_text(None)
        paste.assert_not_called()

    def test_the_field_is_selected_before_pasting(self):
        """Without Ctrl+A the paste appends to whatever MTGA pre-filled, and
        tap_delete() only deletes forward from the caret."""
        combos = []
        with mock.patch.object(
            self.backend, "_press_ctrl_combo",
            side_effect=lambda code, key: combos.append(key) or True,
        ), mock.patch(
            "Controller.Utilities.input_controller.subprocess.Popen"
        ) as popen, mock.patch(
            "Controller.Utilities.input_controller.select.select",
            return_value=([mock.Mock()], [], []),
        ), mock.patch(
            "Controller.Utilities.input_controller.time.sleep"
        ):
            popen.return_value.stdout.readline.return_value = "READY\n"
            self.assertTrue(self.backend._type_text_via_clipboard("x@y"))
        self.assertEqual(combos, ["a", "v"], "expected Ctrl+A then Ctrl+V")

    def test_the_secret_is_not_passed_on_the_command_line(self):
        """A password in argv is visible to every process on the machine."""
        with mock.patch.object(self.backend, "_press_ctrl_combo", return_value=True), \
                mock.patch(
                    "Controller.Utilities.input_controller.subprocess.Popen"
                ) as popen, \
                mock.patch(
                    "Controller.Utilities.input_controller.select.select",
                    return_value=([mock.Mock()], [], []),
                ), \
                mock.patch("Controller.Utilities.input_controller.time.sleep"):
            popen.return_value.stdout.readline.return_value = "READY\n"
            self.backend._type_text_via_clipboard("sup3r-s3cret")
        argv = popen.call_args.args[0]
        self.assertNotIn(
            "sup3r-s3cret", " ".join(argv), "the secret ended up in argv"
        )
        written = "".join(
            c.args[0] for c in popen.return_value.stdin.write.call_args_list
        )
        self.assertIn("sup3r-s3cret", written, "the secret must go in over stdin")

    def test_ydotool_is_preferred_over_the_x11_backend(self):
        """pyautogui's X11 path has raised out of Xlib on this session."""
        with mock.patch(
            "Controller.Utilities.input_controller.shutil.which", return_value="/usr/bin/ydotool"
        ), mock.patch(
            "Controller.Utilities.input_controller.subprocess.run"
        ) as run:
            self.assertTrue(self.backend._press_ctrl_combo(47, "v"))
        self.assertEqual(run.call_args.args[0][0], "ydotool")
        self.assertEqual(self.backend._pyautogui.hotkeys, [])

    def test_the_x11_backend_takes_over_when_ydotool_is_missing(self):
        with mock.patch(
            "Controller.Utilities.input_controller.shutil.which", return_value=None
        ):
            self.assertTrue(self.backend._press_ctrl_combo(47, "v"))
        self.assertEqual(self.backend._pyautogui.hotkeys, [("ctrl", "v")])


if __name__ == "__main__":
    unittest.main()
