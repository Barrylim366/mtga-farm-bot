import os
import platform
import select
import shutil
import subprocess
import stat
import sys
import time
from dataclasses import dataclass


# Owns the X11 clipboard while the parent presses Ctrl+V, then wipes it. Run as
# a separate interpreter on purpose: an X selection is served by a live process,
# and Tk must own it from its own main loop -- a short-lived root that is merely
# updated once hands out stale content (observed while debugging this).
# The text arrives length-prefixed on stdin, never on the command line, so a
# password never shows up in the process list.
_LINUX_CLIPBOARD_HELPER = r"""
import select
import sys
import time
import tkinter

size = int(sys.stdin.readline())
data = sys.stdin.read(size)
root = tkinter.Tk()
root.withdraw()
root.clipboard_clear()
root.clipboard_append(data)
root.update()
sys.stdout.write("READY\n")
sys.stdout.flush()
deadline = time.monotonic() + 20.0
while time.monotonic() < deadline:
    root.update()
    if select.select([sys.stdin], [], [], 0.05)[0]:
        break
root.clipboard_clear()
root.update()
"""


class InputControllerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Point:
    x: int
    y: int


class InputController:
    def move_abs(self, x: int, y: int) -> None:
        raise NotImplementedError

    def move_rel(self, dx: int, dy: int) -> None:
        raise NotImplementedError

    def left_click(self, count: int = 1) -> None:
        raise NotImplementedError

    def left_down(self) -> None:
        raise NotImplementedError

    def left_up(self) -> None:
        raise NotImplementedError

    def tap_enter(self) -> None:
        raise NotImplementedError

    def tap_shift_enter(self) -> None:
        raise NotImplementedError

    def tap_tab(self) -> None:
        raise NotImplementedError

    def tap_delete(self) -> None:
        raise NotImplementedError

    def type_text(self, text: str) -> None:
        raise NotImplementedError

    def tap_escape(self) -> None:
        raise NotImplementedError

    def tap_printscreen(self) -> None:
        raise NotImplementedError

    def tap_win_printscreen(self) -> None:
        raise NotImplementedError

    def position(self) -> Point:
        raise NotImplementedError

    def configure_screen_bounds(self, screen_bounds: tuple[tuple[int, int], tuple[int, int]]) -> None:
        return


class NullInputController(InputController):
    """Swallows every input. For running the test suite (and anything else that
    constructs a Controller without meaning to drive the machine).

    This exists because it is genuinely easy to move the real mouse by accident:
    a Controller schedules fire-and-forget timers (the card-prompt settle timer,
    for one), and a test that arms one gets a real click a second or two later --
    after the test has passed, at absolute screen coordinates, on whatever the
    user happens to be doing. Measured before this backend existed: one full
    `python -m unittest discover tests` produced 77 real clicks.

    Reports a fixed cursor position rather than reading the real one, so nothing
    downstream can accidentally depend on the user's actual pointer either.
    """

    def __init__(self, *, initial_position: Point = Point(0, 0)) -> None:
        self._position = initial_position

    def move_abs(self, x: int, y: int) -> None:
        self._position = Point(int(x), int(y))

    def move_rel(self, dx: int, dy: int) -> None:
        self._position = Point(self._position.x + int(dx), self._position.y + int(dy))

    def left_click(self, count: int = 1) -> None:
        return

    def left_down(self) -> None:
        return

    def left_up(self) -> None:
        return

    def tap_enter(self) -> None:
        return

    def tap_shift_enter(self) -> None:
        return

    def tap_tab(self) -> None:
        return

    def tap_delete(self) -> None:
        return

    def type_text(self, text: str) -> None:
        return

    def tap_escape(self) -> None:
        return

    def tap_printscreen(self) -> None:
        return

    def tap_win_printscreen(self) -> None:
        return

    def position(self) -> Point:
        return self._position


class _Win32MouseMotion:
    """Moves the cursor with a real motion event instead of a warp.

    pynput sets the cursor position on Windows with `SetCursorPos`, which
    teleports the pointer without producing any device input. Unity reads the
    mouse through Raw Input, and since Magic Arena moved to Unity 6 on
    2026-08-26 a teleported cursor no longer counts as hovering anything:
    measured across 12 stops on the hand row, warps produced 0 hover messages
    at every dwell from 0.02s to 0.40s, while `SendInput` motion over the same
    path and the same 0.40s dwell produced 8. Without this the bot can sweep its
    hand all game and never identify a single card -- which is exactly what it
    did until this was found.

    A settle is still required on top of the motion (see
    Controller.HOVER_SETTLE_SEC); the two conditions are independent, and
    testing only one of them is what made the first fix look sufficient.

    SendInput is given absolute coordinates over the virtual desktop, so the
    pointer lands where it is told regardless of pointer speed or acceleration
    (a relative move would be scaled by both). The normalisation is lossy at
    1/65535 of the desktop, so the position is corrected with SetCursorPos
    afterwards: the motion event has already been delivered by then, and that
    keeps clicks landing on the exact pixel the caller asked for.
    """

    _SM_XVIRTUALSCREEN = 76
    _SM_YVIRTUALSCREEN = 77
    _SM_CXVIRTUALSCREEN = 78
    _SM_CYVIRTUALSCREEN = 79
    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_VIRTUALDESK = 0x4000
    _MOUSEEVENTF_ABSOLUTE = 0x8000
    _INPUT_MOUSE = 0

    def __init__(self) -> None:
        if os.name != "nt":
            raise InputControllerError("win32 mouse motion is Windows-only")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class _INPUTunion(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]

        self._INPUT = INPUT
        self._MOUSEINPUT = MOUSEINPUT
        self._user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        )
        self._user32.SendInput.restype = wintypes.UINT

    def _virtual_desktop(self) -> tuple[int, int, int, int]:
        metric = self._user32.GetSystemMetrics
        return (
            int(metric(self._SM_XVIRTUALSCREEN)),
            int(metric(self._SM_YVIRTUALSCREEN)),
            max(1, int(metric(self._SM_CXVIRTUALSCREEN))),
            max(1, int(metric(self._SM_CYVIRTUALSCREEN))),
        )

    @staticmethod
    def normalise(
        x: int, y: int, desktop: tuple[int, int, int, int]
    ) -> tuple[int, int]:
        """Screen pixel -> the 0..65535 coordinates SendInput expects.

        Kept separate from the send so the arithmetic can be tested without
        moving the real cursor.
        """
        origin_x, origin_y, width, height = desktop
        # 65535 spans the desktop inclusive of its last pixel, so the divisor is
        # the span, not the pixel count -- off by one here puts every click a
        # fraction of a pixel short of where it was aimed.
        span_x = max(1, width - 1)
        span_y = max(1, height - 1)
        norm_x = int(round((int(x) - origin_x) * 65535.0 / span_x))
        norm_y = int(round((int(y) - origin_y) * 65535.0 / span_y))
        return (
            min(65535, max(0, norm_x)),
            min(65535, max(0, norm_y)),
        )

    def move_abs(self, x: int, y: int) -> bool:
        """Return True if a motion event was delivered."""
        x, y = int(x), int(y)
        norm_x, norm_y = self.normalise(x, y, self._virtual_desktop())
        event = self._INPUT(
            type=self._INPUT_MOUSE,
            mi=self._MOUSEINPUT(
                dx=norm_x,
                dy=norm_y,
                mouseData=0,
                dwFlags=(
                    self._MOUSEEVENTF_MOVE
                    | self._MOUSEEVENTF_ABSOLUTE
                    | self._MOUSEEVENTF_VIRTUALDESK
                ),
                time=0,
                dwExtraInfo=None,
            ),
        )
        sent = self._user32.SendInput(
            1, self._ctypes.byref(event), self._ctypes.sizeof(self._INPUT)
        )
        if sent != 1:
            return False
        # Land exactly on the requested pixel; the motion event is already out.
        self._user32.SetCursorPos(x, y)
        return True


def _win32_mouse_motion_or_none() -> "_Win32MouseMotion | None":
    if os.name != "nt":
        return None
    try:
        return _Win32MouseMotion()
    except Exception:
        # A warp is worse than nothing on the Unity 6 client, but it is still
        # what every previous version did -- degrade, do not refuse to start.
        return None


class PynputInputController(InputController):
    def __init__(self) -> None:
        try:
            from pynput import keyboard, mouse
            from pynput.mouse import Button
        except Exception as e:  # pragma: no cover
            raise InputControllerError(f"Failed to import pynput: {e}") from e

        self._keyboard = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._Key = keyboard.Key
        self._Button = Button
        # Windows only, and only for movement: pynput's clicks and keystrokes go
        # through SendInput already. See _Win32MouseMotion.
        self._win32_motion = _win32_mouse_motion_or_none()

    def move_abs(self, x: int, y: int) -> None:
        if self._win32_motion is not None and self._win32_motion.move_abs(x, y):
            return
        self._mouse.position = (int(x), int(y))

    def move_rel(self, dx: int, dy: int) -> None:
        if self._win32_motion is not None:
            # Resolved against the current position and sent absolutely: a
            # relative SendInput would be scaled by the pointer speed and
            # acceleration curve, and a sweep that drifts cannot be mapped back
            # to a card.
            x, y = self._mouse.position
            if self._win32_motion.move_abs(int(x) + int(dx), int(y) + int(dy)):
                return
        self._mouse.move(int(dx), int(dy))

    def left_click(self, count: int = 1) -> None:
        self._mouse.click(self._Button.left, int(count))

    def left_down(self) -> None:
        self._mouse.press(self._Button.left)

    def left_up(self) -> None:
        self._mouse.release(self._Button.left)

    def tap_enter(self) -> None:
        self._keyboard.press(self._Key.enter)
        self._keyboard.release(self._Key.enter)

    def tap_shift_enter(self) -> None:
        self._keyboard.press(self._Key.shift)
        self._keyboard.press(self._Key.enter)
        self._keyboard.release(self._Key.enter)
        self._keyboard.release(self._Key.shift)

    def tap_tab(self) -> None:
        self._keyboard.press(self._Key.tab)
        self._keyboard.release(self._Key.tab)

    def tap_delete(self) -> None:
        self._keyboard.press(self._Key.delete)
        self._keyboard.release(self._Key.delete)

    def type_text(self, text: str) -> None:
        self._keyboard.type(text or "")

    def tap_escape(self) -> None:
        self._keyboard.press(self._Key.esc)
        self._keyboard.release(self._Key.esc)

    def tap_printscreen(self) -> None:
        self._keyboard.press(self._Key.print_screen)
        self._keyboard.release(self._Key.print_screen)

    def tap_win_printscreen(self) -> None:
        self._keyboard.press(self._Key.cmd)
        self._keyboard.press(self._Key.print_screen)
        self._keyboard.release(self._Key.print_screen)
        self._keyboard.release(self._Key.cmd)

    def position(self) -> Point:
        x, y = self._mouse.position
        return Point(int(x), int(y))


class PyAutoGUIInputController(InputController):
    def __init__(self) -> None:
        try:
            import pyautogui
        except Exception as e:  # pragma: no cover
            raise InputControllerError(f"Failed to import pyautogui: {e}") from e
        self._pyautogui = pyautogui
        try:
            self._pyautogui.FAILSAFE = True
            self._pyautogui.PAUSE = 0.0
        except Exception:
            pass
        self._unicode_keyboard = self._make_unicode_keyboard()
        self._win32_motion = _win32_mouse_motion_or_none()
        if platform.system().lower() == "darwin":
            self._verify_mouse_control()

    @staticmethod
    def _make_unicode_keyboard():
        """A pynput keyboard used *only* for typing text. None if unavailable.

        pyautogui's `typewrite` maps every character to a hardcoded US-layout
        virtual keycode and posts that keycode; the OS then resolves it against
        whatever keyboard layout is currently *active*. On a German (QWERTZ)
        Mac that means `@` (US shift+2) comes out as `"`, y/z are swapped and
        `-`/`_` are mangled -- so account e-mails and passwords were typed
        wrong, silently, and the login just failed. Characters missing from
        pyautogui's table entirely (umlauts, ...) are dropped without a word.

        pynput inserts the literal character instead: it uses
        CGEventKeyboardSetUnicodeString on macOS and remaps the keymap on X11,
        both independent of the active layout. Mouse handling stays on
        pyautogui, which is accurate and fast on both platforms.
        """
        try:
            from pynput import keyboard

            return keyboard.Controller()
        except Exception:
            return None

    def _verify_mouse_control(self) -> None:
        """Fail fast with a clear message if macOS blocks synthetic mouse control."""
        try:
            start_x, start_y = self._pyautogui.position()
            width, _height = self._pyautogui.size()
            if int(width) <= 1:
                return
            candidate_x = int(start_x) + 1 if int(start_x) + 1 < int(width) else int(start_x) - 1
            candidate_y = int(start_y)
            if candidate_x == int(start_x):
                return

            self._pyautogui.moveTo(candidate_x, candidate_y, duration=0)
            time.sleep(0.03)
            now_x, now_y = self._pyautogui.position()

            # Always move cursor back to original position.
            self._pyautogui.moveTo(int(start_x), int(start_y), duration=0)

            if int(now_x) == int(start_x) and int(now_y) == int(start_y):
                raise InputControllerError(
                    "Mouse control blocked. Grant Accessibility permission to Terminal/Python in macOS "
                    "(System Settings -> Privacy & Security -> Accessibility)."
                )
        except InputControllerError:
            raise
        except Exception as e:
            raise InputControllerError(f"Mouse control self-test failed: {e}") from e

    def move_abs(self, x: int, y: int) -> None:
        # pyautogui also warps on Windows, and a warp hovers nothing on the
        # Unity 6 client -- see _Win32MouseMotion. "auto" never picks this
        # backend on Windows, but an explicit input_backend=pyautogui would
        # otherwise be silently unable to play a card.
        if self._win32_motion is not None and self._win32_motion.move_abs(x, y):
            return
        self._pyautogui.moveTo(int(x), int(y), duration=0)

    def move_rel(self, dx: int, dy: int) -> None:
        if self._win32_motion is not None:
            x, y = self._pyautogui.position()
            if self._win32_motion.move_abs(int(x) + int(dx), int(y) + int(dy)):
                return
        self._pyautogui.moveRel(int(dx), int(dy), duration=0)

    def left_click(self, count: int = 1) -> None:
        self._pyautogui.click(button="left", clicks=max(1, int(count)))

    def left_down(self) -> None:
        self._pyautogui.mouseDown(button="left")

    def left_up(self) -> None:
        self._pyautogui.mouseUp(button="left")

    def tap_enter(self) -> None:
        self._pyautogui.press("enter")

    def tap_shift_enter(self) -> None:
        self._pyautogui.hotkey("shift", "enter")

    def tap_tab(self) -> None:
        self._pyautogui.press("tab")

    def tap_delete(self) -> None:
        self._pyautogui.press("delete")

    # Linux input event codes. These are *physical* keys, so unlike a character
    # they mean the same thing under every keyboard layout.
    _EVDEV_CTRL = 29
    _EVDEV_A = 30
    _EVDEV_V = 47
    _CLIPBOARD_READY_TIMEOUT_SEC = 5.0

    def type_text(self, text: str) -> None:
        text = text or ""
        if not text:
            return
        if platform.system().lower() == "linux":
            if self._type_text_via_clipboard(text):
                return
            # No fall-through to the keystroke path on purpose: here we *know*
            # it mistypes, and a silently wrong password is exactly what cost a
            # live run an hour on 2026-08-23 -- the login never went through and
            # the bot then span in GO_HOME on a login screen it believed it had
            # passed. A raised error is visible: _perform_account_switch logs it
            # and aborts the switch instead of playing on as the wrong account.
            raise InputControllerError(
                "Linux: could not paste the text via the clipboard, and typing "
                "it keystroke by keystroke mistypes characters that need AltGr "
                "(e.g. '@'), so nothing was typed."
            )
        if self._unicode_keyboard is None:
            # No pynput at all: layout-correct typing is impossible here, but
            # typing nothing is worse than typing something.
            self._pyautogui.typewrite(text, interval=0)
            return
        try:
            self._unicode_keyboard.type(text)
        except Exception as e:
            # Deliberately no typewrite() fallback: pynput types character by
            # character, so a mid-string failure has already put part of the
            # text into the field and retyping the whole string would duplicate
            # it. A visible error beats a silently half-typed password.
            raise InputControllerError(f"Failed to type text: {e}") from e

    def _type_text_via_clipboard(self, text: str) -> bool:
        """Paste the text instead of typing it. Linux only.

        Ctrl+V carries no character, so nothing here depends on the active
        keymap -- which is the entire point. On X11/XWayland pynput resolves a
        character to its AltGr level (`@` sits on AltGr+Q on a German layout)
        and then presses that key *without* AltGr: measured 2026-08-23, `a@b`
        arrived as `aqb`, so every account e-mail was typed wrong and every
        switch that had to type its credentials failed.

        Windows and macOS keep the keystroke path untouched -- it works there,
        and this one needs a clipboard, a helper process and Ctrl+V to work.
        """
        helper = None
        try:
            helper = subprocess.Popen(
                [sys.executable, "-c", _LINUX_CLIPBOARD_HELPER],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            helper.stdin.write(f"{len(text)}\n{text}")
            helper.stdin.flush()
            if not select.select(
                [helper.stdout], [], [], self._CLIPBOARD_READY_TIMEOUT_SEC
            )[0]:
                return False
            if helper.stdout.readline().strip() != "READY":
                return False
            # Replace, do not append: the caller may be on a field MTGA
            # pre-filled, and tap_delete() only deletes forward from the caret.
            if not self._press_ctrl_combo(self._EVDEV_A, "a"):
                return False
            time.sleep(0.15)
            if not self._press_ctrl_combo(self._EVDEV_V, "v"):
                return False
            time.sleep(0.35)
            return True
        except Exception:
            return False
        finally:
            if helper is not None:
                try:
                    helper.stdin.close()  # tells the helper to wipe and exit
                except Exception:
                    pass
                try:
                    helper.wait(timeout=3)
                except Exception:
                    helper.kill()

    def _press_ctrl_combo(self, evdev_code: int, pyautogui_key: str) -> bool:
        """Ctrl+<key>, by physical key code where possible.

        ydotool talks to uinput, which sidesteps both the layout and the X
        server; pyautogui's X11 backend is the fallback because it has been seen
        to raise out of Xlib on this session (BadRRMode during sync).
        """
        if shutil.which("ydotool"):
            try:
                subprocess.run(
                    [
                        "ydotool", "key",
                        f"{self._EVDEV_CTRL}:1", f"{evdev_code}:1",
                        f"{evdev_code}:0", f"{self._EVDEV_CTRL}:0",
                    ],
                    timeout=5,
                    check=True,
                    capture_output=True,
                )
                return True
            except Exception:
                pass
        try:
            self._pyautogui.hotkey("ctrl", pyautogui_key)
            return True
        except Exception:
            return False

    def tap_escape(self) -> None:
        self._pyautogui.press("esc")

    def tap_printscreen(self) -> None:
        try:
            self._pyautogui.press("printscreen")
        except Exception:
            pass

    def tap_win_printscreen(self) -> None:
        system = platform.system().lower()
        try:
            if system == "darwin":
                self._pyautogui.hotkey("command", "shift", "3")
            else:
                self._pyautogui.hotkey("win", "printscreen")
        except Exception:
            pass

    def position(self) -> Point:
        x, y = self._pyautogui.position()
        return Point(int(x), int(y))


def _detect_desktop_bounds() -> tuple[tuple[int, int], tuple[int, int]] | None:
    try:
        import mss
        with mss.mss() as sct:
            if sct.monitors:
                mon = sct.monitors[0]
                left = int(mon.get("left", 0))
                top = int(mon.get("top", 0))
                width = int(mon.get("width", 0))
                height = int(mon.get("height", 0))
                if width > 1 and height > 1:
                    return ((left, top), (left + width, top + height))
    except Exception:
        pass
    return None


class YdotoolInputController(InputController):
    """
    Uses `ydotool` + `ydotoold` to inject input (Wayland-friendly).
    Note: ydotool does not provide a reliable "get current cursor position" API,
    so we track position based on our own moves.
    """

    _BTN_LEFT_CLICK = "0xC0"
    _BTN_LEFT_DOWN = "0x40"
    _BTN_LEFT_UP = "0x80"

    _KEY_ENTER = 28
    _KEY_LEFTSHIFT = 42
    _KEY_TAB = 15
    _KEY_DELETE = 111
    _KEY_ESC = 1
    _KEY_PRINTSCREEN = 99
    _KEY_LEFTMETA = 125

    def __init__(self) -> None:
        if shutil.which("ydotool") is None:
            raise InputControllerError("`ydotool` binary is not installed or not in PATH")

        uid = os.getuid()
        default_socket = f"/run/user/{uid}/.ydotool_socket"
        socket_path = os.environ.get("YDOTOOL_SOCKET", default_socket)
        if not os.path.exists(socket_path):
            raise InputControllerError(
                f"ydotool socket not found at `{socket_path}`; start `ydotoold` and/or set `YDOTOOL_SOCKET`"
            )

        st = os.stat(socket_path)
        if not stat.S_ISSOCK(st.st_mode):
            raise InputControllerError(f"`{socket_path}` exists but is not a unix socket")

        # On some systems ydotool refuses to connect to a socket owned by a different user (even if chmod 666).
        if st.st_uid != uid:
            raise InputControllerError(
                f"ydotool socket `{socket_path}` is owned by uid={st.st_uid}, expected uid={uid}. "
                f"Start `ydotoold` with a user-owned socket or run: `sudo chown $USER:$USER {socket_path}`"
            )

        if not os.access(socket_path, os.W_OK):
            raise InputControllerError(f"No permission to access ydotool socket `{socket_path}`")

        detected = _detect_desktop_bounds()
        if detected is not None:
            (x0, y0), (x1, y1) = detected
            self._screen_origin = Point(x0, y0)
            self._screen_size = Point(x1 - x0, y1 - y0)
        else:
            self._screen_origin = Point(0, 0)
            self._screen_size = Point(0, 0)  # width/height
        self._abs_max = 65535
        self._pos = Point(0, 0)

    def _run(self, *args: str) -> None:
        try:
            subprocess.run(["ydotool", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            stdout = (e.stdout or b"").decode(errors="replace").strip()
            stderr = (e.stderr or b"").decode(errors="replace").strip()
            msg = (stderr or stdout).strip()
            raise InputControllerError(f"ydotool failed: {msg or e}") from e

    def move_abs(self, x: int, y: int) -> None:
        x_i, y_i = int(x), int(y)
        # `ydotool mousemove --absolute` often expects an absolute range (typically 0..65535),
        # not pixel coordinates. We map pixel coordinates using the configured screen bounds.
        if self._screen_size.x <= 1 or self._screen_size.y <= 1:
            raise InputControllerError(
                "ydotool backend is not configured with screen bounds; provide `screen_bounds` to Controller"
            )

        rel_x = max(0, min(x_i - self._screen_origin.x, self._screen_size.x - 1))
        rel_y = max(0, min(y_i - self._screen_origin.y, self._screen_size.y - 1))

        abs_x = int(round(rel_x / (self._screen_size.x - 1) * self._abs_max))
        abs_y = int(round(rel_y / (self._screen_size.y - 1) * self._abs_max))

        self._run("mousemove", "--absolute", str(abs_x), str(abs_y))
        self._pos = Point(x_i, y_i)

    def move_rel(self, dx: int, dy: int) -> None:
        dx_i, dy_i = int(dx), int(dy)
        self._run("mousemove", str(dx_i), str(dy_i))
        self._pos = Point(self._pos.x + dx_i, self._pos.y + dy_i)

    def left_click(self, count: int = 1) -> None:
        count_i = int(count)
        if count_i <= 0:
            return
        if count_i == 1:
            self._run("click", self._BTN_LEFT_CLICK)
            return
        self._run("click", "-r", str(count_i), self._BTN_LEFT_CLICK)

    def left_down(self) -> None:
        self._run("click", self._BTN_LEFT_DOWN)

    def left_up(self) -> None:
        self._run("click", self._BTN_LEFT_UP)

    def _key(self, code: int, down: bool) -> None:
        self._run("key", f"{int(code)}:{1 if down else 0}")

    def tap_enter(self) -> None:
        self._key(self._KEY_ENTER, True)
        self._key(self._KEY_ENTER, False)

    def tap_shift_enter(self) -> None:
        self._key(self._KEY_LEFTSHIFT, True)
        self._key(self._KEY_ENTER, True)
        self._key(self._KEY_ENTER, False)
        self._key(self._KEY_LEFTSHIFT, False)

    def tap_tab(self) -> None:
        self._key(self._KEY_TAB, True)
        self._key(self._KEY_TAB, False)

    def tap_delete(self) -> None:
        self._key(self._KEY_DELETE, True)
        self._key(self._KEY_DELETE, False)

    def type_text(self, text: str) -> None:
        if text is None:
            return
        self._run("type", str(text))

    def tap_escape(self) -> None:
        self._key(self._KEY_ESC, True)
        self._key(self._KEY_ESC, False)

    def tap_printscreen(self) -> None:
        self._key(self._KEY_PRINTSCREEN, True)
        self._key(self._KEY_PRINTSCREEN, False)

    def tap_win_printscreen(self) -> None:
        self._key(self._KEY_LEFTMETA, True)
        self._key(self._KEY_PRINTSCREEN, True)
        self._key(self._KEY_PRINTSCREEN, False)
        self._key(self._KEY_LEFTMETA, False)

    def position(self) -> Point:
        return self._pos

    def configure_screen_bounds(self, screen_bounds: tuple[tuple[int, int], tuple[int, int]]) -> None:
        (x0, y0), (x1, y1) = screen_bounds
        width = int(x1) - int(x0)
        height = int(y1) - int(y0)
        if width <= 1 or height <= 1:
            raise InputControllerError(f"Invalid screen_bounds for ydotool: {screen_bounds!r}")

        # If desktop bounds were auto-detected and are larger than the provided bounds
        # (e.g. an ultrawide/multi-monitor desktop vs default 1920x1080 bounds),
        # preserve the full desktop bounds so absolute ydotool coordinates map accurately.
        detected = _detect_desktop_bounds()
        if detected is not None:
            (dx0, dy0), (dx1, dy1) = detected
            det_w = dx1 - dx0
            det_h = dy1 - dy0
            if det_w > width or det_h > height:
                self._screen_origin = Point(dx0, dy0)
                self._screen_size = Point(det_w, det_h)
                return

        self._screen_origin = Point(int(x0), int(y0))
        self._screen_size = Point(width, height)


def create_input_controller(backend: str | None) -> InputController:
    """
    backend:
      - "ydotool" / "pynput" / "pyautogui" / "null" / "auto" / None
    """
    normalized = (backend or os.environ.get("MTGA_BOT_INPUT_BACKEND") or "auto").strip().lower()
    # Checked before "auto" resolution so it cannot be reached by accident, and
    # never selected by "auto": a bot that silently stopped clicking would look
    # exactly like a bot that is stuck.
    if normalized in ("null", "none", "noop"):
        return NullInputController()
    if normalized in ("auto", ""):
        system = platform.system().lower()
        if system == "darwin":
            pyautogui_err: InputControllerError | None = None
            try:
                return PyAutoGUIInputController()
            except InputControllerError as e:
                pyautogui_err = e
            try:
                return PynputInputController()
            except InputControllerError as e:
                raise InputControllerError(
                    f"Auto backend failed on macOS. pyautogui error: {pyautogui_err}; pynput error: {e}"
                ) from e

        if system == "linux":
            # On Linux with X11 / XWayland (where Proton/Steam MTGA runs), PyAutoGUI
            # communicates directly with the X11 server for instant, accurate clicking.
            try:
                return PyAutoGUIInputController()
            except Exception:
                pass

            if shutil.which("ydotool") is not None:
                try:
                    return YdotoolInputController()
                except Exception:
                    pass

            return PynputInputController()

        return PynputInputController()

    if normalized == "ydotool":
        return YdotoolInputController()
    if normalized == "pynput":
        return PynputInputController()
    if normalized == "pyautogui":
        return PyAutoGUIInputController()

    raise InputControllerError(f"Unknown input backend: {backend!r}")
