import os
import platform
import shutil
import subprocess
import stat
from dataclasses import dataclass


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

    def move_abs(self, x: int, y: int) -> None:
        self._mouse.position = (int(x), int(y))

    def move_rel(self, dx: int, dy: int) -> None:
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


class YdotoolInputController(InputController):
    """
    Uses `ydotool` + `ydotoold` to inject input (Wayland-friendly).
    Note: ydotool does not provide a reliable "get current cursor position" API,
    so we track position based on our own moves.
    """

    _KEY_ENTER = 28
    _KEY_LEFTSHIFT = 42
    _KEY_TAB = 15
    _KEY_DELETE = 111
    _KEY_ESC = 1
    _KEY_PRINTSCREEN = 99
    _KEY_LEFTMETA = 125

    # ydotool click codes: 0xC0 = left click (down then up), 0x40 = left down, 0x80 = left up
    _BTN_LEFT_CLICK = "0xC0"
    _BTN_LEFT_DOWN = "0x40"
    _BTN_LEFT_UP = "0x80"

    def __init__(self, *, initial_position: Point = Point(0, 0)) -> None:
        if shutil.which("ydotool") is None:
            raise InputControllerError("`ydotool` not found in PATH")

        self._pos = initial_position

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

        self._screen_origin = Point(0, 0)
        self._screen_size = Point(0, 0)  # width/height
        self._abs_max = 65535

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
        self._screen_origin = Point(int(x0), int(y0))
        self._screen_size = Point(width, height)


def create_input_controller(backend: str | None) -> InputController:
    """
    backend:
      - "ydotool" / "pynput" / "auto" / None
    """
    normalized = (backend or os.environ.get("MTGA_BOT_INPUT_BACKEND") or "auto").strip().lower()
    if normalized in ("auto", ""):
        if platform.system().lower() == "linux" and shutil.which("ydotool") is not None:
            ydotool_err: InputControllerError | None = None
            try:
                return YdotoolInputController()
            except InputControllerError as e:
                ydotool_err = e

            try:
                return PynputInputController()
            except InputControllerError as e:
                raise InputControllerError(
                    f"Auto backend failed. ydotool error: {ydotool_err}; pynput error: {e}"
                ) from e

        return PynputInputController()

    if normalized == "ydotool":
        return YdotoolInputController()
    if normalized == "pynput":
        return PynputInputController()

    raise InputControllerError(f"Unknown input backend: {backend!r}")
