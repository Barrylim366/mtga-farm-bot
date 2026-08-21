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
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.Utilities.input_controller import (
    InputControllerError,
    PyAutoGUIInputController,
)

EMAIL = "giacomo.joggerst@kompass-fb.com"


class FakePyAutoGUI:
    def __init__(self):
        self.typed = []

    def typewrite(self, text, interval=0):
        self.typed.append(text)


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


class TypeTextLayoutTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
