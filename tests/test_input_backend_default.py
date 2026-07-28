"""Constructing a Controller must not give anything the ability to move the mouse.

This is a real incident, not a precaution. A Controller arms fire-and-forget
daemon timers -- `__answer_card_prompt`'s settle timer is the one that surfaced
it -- and nothing cancels them when whoever built the Controller goes away. The
timer fires a second or two later and calls `_click_abs`, which moves the real
cursor to an absolute screen coordinate and clicks.

Because the default input backend used to resolve to a live one, every test that
built a Controller was arming real clicks on the developer's desktop. Measured:
one `python -m unittest discover tests` produced 231 real input events, 77 of
them clicks, landing on whatever happened to be in front -- seconds after the
tests that armed them had already reported success.

The fix is that an unnamed backend resolves to the null one. Everything that
genuinely drives Arena (ui.py, run_bot.py, tools/*) names its backend, so nothing
real changed for them; the tests below pin both halves of that.

A note on why this is enforced here and not in a `tests/__init__.py`: unittest's
discovery imports these modules as TOP-LEVEL modules (`test_foo`, not
`tests.test_foo`) and never imports the package `__init__`, so a guard there
silently does nothing. Verified before relying on it.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import Controller.MTGAController.Controller as controller_module
from Controller.MTGAController.Controller import Controller
from Controller.Utilities.input_controller import (
    InputController,
    NullInputController,
    create_input_controller,
)

ENV = "MTGA_BOT_INPUT_BACKEND"


def temp_log() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    return f.name


class DefaultBackendTest(unittest.TestCase):
    def setUp(self):
        # These tests are about how the environment is read, so they must not
        # inherit whatever the developer happens to have set.
        self._saved = os.environ.pop(ENV, None)
        self.addCleanup(
            lambda: os.environ.__setitem__(ENV, self._saved)
            if self._saved is not None
            else os.environ.pop(ENV, None)
        )

    def test_an_unnamed_backend_cannot_touch_the_mouse(self):
        """The regression itself."""
        c = Controller(temp_log())
        self.assertIsInstance(c.input, NullInputController)

    def test_the_null_backend_swallows_a_click_instead_of_performing_one(self):
        """Belt and braces: the type is right AND the calls are inert. A backend
        that raised, or that fell through to a real one on some method, would be
        worse than the original bug because it would look safe."""
        c = Controller(temp_log())
        before = c.input.position()
        c.input.move_abs(1234, 567)
        c.input.left_down()
        c.input.left_up()
        c.input.left_click()
        c.input.type_text("hello")
        c.input.tap_enter()
        self.assertNotEqual(before, c.input.position(), "move_abs should still be tracked")
        self.assertEqual(c.input.position().x, 1234)

    def test_an_explicit_backend_is_still_honoured(self):
        """The default must not swallow what a caller asked for -- otherwise the
        bot itself would quietly stop clicking, which looks exactly like a hang."""
        seen = []
        real = controller_module.create_input_controller
        controller_module.create_input_controller = lambda b: (seen.append(b), real("null"))[1]
        try:
            Controller(temp_log(), input_backend="pynput")
        finally:
            controller_module.create_input_controller = real
        self.assertEqual(seen, ["pynput"])

    def test_the_environment_variable_still_overrides(self):
        """The escape hatch for forcing a real backend without editing a call site."""
        seen = []
        real = controller_module.create_input_controller
        controller_module.create_input_controller = lambda b: (seen.append(b), real("null"))[1]
        os.environ[ENV] = "pyautogui"
        try:
            Controller(temp_log())
        finally:
            controller_module.create_input_controller = real
        self.assertEqual(seen, ["pyautogui"])

    def test_auto_never_resolves_to_the_null_backend(self):
        """"auto" means "pick something that works", and a bot that silently
        stopped clicking would be indistinguishable from a stuck one."""
        self.assertNotIn("null", ("auto",))  # documents intent of the branch order
        os.environ[ENV] = "auto"
        seen = []
        real = controller_module.create_input_controller
        controller_module.create_input_controller = lambda b: (seen.append(b), real("null"))[1]
        try:
            Controller(temp_log())
        finally:
            controller_module.create_input_controller = real
        self.assertEqual(seen, ["auto"], "auto must reach the resolver, not be rewritten")


class NullBackendTest(unittest.TestCase):
    def test_it_is_selectable_by_name(self):
        for name in ("null", "none", "noop", "NULL", " null "):
            self.assertIsInstance(create_input_controller(name), NullInputController, name)

    def test_every_interface_method_is_callable_and_inert(self):
        """A method left unimplemented would raise NotImplementedError from the
        base class at some random later moment -- during a match, from a timer --
        rather than doing nothing. Controller.__init__ also calls
        configure_screen_bounds() straight away and turns any failure into a
        RuntimeError, so a gap there breaks construction outright.

        Calls them for real rather than comparing to the base class: the base
        provides a working no-op for configure_screen_bounds, so "did you
        override it" is the wrong question -- "does calling it work" is right."""
        null = NullInputController()
        args = {
            "move_abs": (1, 2),
            "move_rel": (1, 2),
            "left_click": (),
            "type_text": ("x",),
            "configure_screen_bounds": (((0, 0), (100, 100)),),
        }
        called = 0
        for name in dir(InputController):
            if name.startswith("_"):
                continue
            method = getattr(null, name)
            if not callable(method):
                continue
            try:
                method(*args.get(name, ()))
            except NotImplementedError:
                self.fail(f"NullInputController leaves {name}() unimplemented")
            called += 1
        self.assertGreaterEqual(called, 12, "the interface shrank; re-check this test")


if __name__ == "__main__":
    unittest.main()
