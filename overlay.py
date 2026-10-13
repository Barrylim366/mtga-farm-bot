"""Info panel that lies over the top-left corner of the Arena window.

The panel is a window the bot must never notice, so it is only shown once
Windows confirms all three of:

- clicks pass through it (WS_EX_LAYERED | WS_EX_TRANSPARENT), so a click the
  bot aims at a button under the panel lands on the game;
- it never becomes the foreground window (WS_EX_NOACTIVATE), because Arena
  only answers hovers while it is the foreground window;
- screen captures do not contain it (WDA_EXCLUDEFROMCAPTURE), so template
  matching and the debug captures see the game behind it.

Keeping a window out of captures needs Windows 10 version 2004 or newer.
Where any of the three cannot be confirmed -- and on every other platform --
the panel stays off.

Its title must never contain "MTGA" or "Arena": the window locator finds the
game by title (and then filters on MTGA.exe), and the panel must not even be a
candidate.
"""

from __future__ import annotations

import os
import tkinter as tk
from typing import Any

import bot_logger

TITLE = "Burning Lotus Info"

# Gap between the Arena client area's top-left corner and the panel, in the
# UI's own (logical) pixels.
CORNER_GAP = 12

_GWL_EXSTYLE = -20
_GA_ROOT = 2
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000
_OVERLAY_STYLES = _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
_WDA_EXCLUDEFROMCAPTURE = 0x00000011
_SW_HIDE = 0
_SW_SHOWNOACTIVATE = 4
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040


def _pretty_enum(value: Any, prefix: str) -> str:
    """'Phase_Combat' -> 'Combat', 'Step_DeclareAttack' -> 'Declare Attack'."""
    text = str(value or "").strip()
    if text.startswith(prefix):
        text = text[len(prefix):]
    out = []
    for i, ch in enumerate(text):
        if ch.isupper() and i > 0 and text[i - 1].islower():
            out.append(" ")
        out.append(ch)
    return "".join(out).replace("_", " ").strip()


def build_lines(
    status: dict[str, Any],
    *,
    queue_label: str,
    switch_text: str,
    show_accounts: bool,
    current_account: str,
    next_account: str,
    games: int,
    wins: int,
) -> list[tuple[str, str]]:
    """What the panel says, as (text, kind) rows. kind is one of 'title',
    'match', 'info', 'muted', 'quest_title', 'quest', 'quest_active'. Pure:
    everything comes in as arguments."""
    lines: list[tuple[str, str]] = [("Session Info", "title")]

    bot_state = str(status.get("bot_state") or "")
    turn_info = status.get("turn_info") if isinstance(status.get("turn_info"), dict) else {}
    turn = turn_info.get("turnNumber")
    if "IN_GAME" in bot_state and turn:
        parts = [f"Turn {turn}"]
        phase = _pretty_enum(turn_info.get("phase"), "Phase_")
        step = _pretty_enum(turn_info.get("step"), "Step_")
        if phase:
            parts.append(f"{phase} ({step})" if step and step != phase else phase)
        seat = status.get("local_system_seat_id")
        active = turn_info.get("activePlayer")
        if seat is not None and active is not None:
            parts.append("your turn" if active == seat else "opponent's turn")
        lines.append((" · ".join(parts), "match"))
        move = str(status.get("last_move_name") or "").strip()
        if move:
            lines.append((f"Last move: {move}", "match"))

    lines.append((f"Queue: {queue_label}", "info"))
    lines.append((switch_text, "info"))
    if show_accounts:
        lines.append((f"Current ACC: {current_account or '…'}", "info"))
        lines.append((f"Next ACC: {next_account or '…'}", "info"))
    lines.append((f"Session: {int(games)} games · {int(wins)} wins", "muted"))

    gold = status.get("gold_farmed")
    if isinstance(gold, dict) and gold:
        own = gold.get(current_account) if current_account else None
        if isinstance(own, (int, float)):
            lines.append((f"Gold farmed: {int(own)}", "muted"))
        else:
            total = sum(int(v) for v in gold.values() if isinstance(v, (int, float)))
            lines.append((f"Gold farmed: {total}", "muted"))

    quests = status.get("quests")
    quests = [q for q in quests if isinstance(q, dict)] if isinstance(quests, list) else []
    active_id = str(status.get("active_quest_id") or "")
    lines.append(("Daily Quests", "quest_title"))
    if not quests:
        lines.append(("No quest data yet", "muted"))
    for q in quests[:3]:
        parts = [str(q.get("name") or "Quest")]
        colors = str(q.get("colors") or "")
        if colors:
            parts.append(f"({colors})")
        goal = q.get("goal")
        if isinstance(goal, int) and goal > 0:
            parts.append(f"{q.get('progress')}/{goal}")
        is_active = bool(active_id) and str(q.get("id") or "") == active_id
        lines.append((("▶ " if is_active else "") + "  ".join(parts), "quest_active" if is_active else "quest"))
    return lines


class _Win32:
    """The handful of user32 calls the panel needs. Only built on Windows."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        u = ctypes.windll.user32
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.SetWindowDisplayAffinity.restype = wintypes.BOOL
        u.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowDisplayAffinity.restype = wintypes.BOOL
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.ShowWindow.restype = wintypes.BOOL
        u.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        u.SetWindowPos.restype = wintypes.BOOL
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.ClientToScreen.restype = wintypes.BOOL
        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsWindow.restype = wintypes.BOOL
        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsIconic.restype = wintypes.BOOL
        self.user32 = u

    def root_of(self, child_hwnd: int) -> int:
        return int(self.user32.GetAncestor(child_hwnd, _GA_ROOT) or 0)

    def dress(self, hwnd: int) -> bool:
        """Make hwnd click-through, never-foreground and capture-excluded, then
        answer whether Windows confirms all three."""
        style = int(self.user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE))
        if style & _OVERLAY_STYLES != _OVERLAY_STYLES:
            self.user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, style | _OVERLAY_STYLES | _WS_EX_TOPMOST)
            # Extended-style changes only take full effect after a frame change.
            self.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED | _SWP_NOACTIVATE,
            )
        self.user32.SetWindowDisplayAffinity(hwnd, _WDA_EXCLUDEFROMCAPTURE)
        return self.is_safe(hwnd)

    def is_safe(self, hwnd: int) -> bool:
        style = int(self.user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE))
        through = _WS_EX_LAYERED | _WS_EX_TRANSPARENT
        affinity = self._wintypes.DWORD(0)
        known = bool(self.user32.GetWindowDisplayAffinity(hwnd, self._ctypes.byref(affinity)))
        return (
            style & through == through
            and bool(style & _WS_EX_NOACTIVATE)
            and known
            and int(affinity.value) == _WDA_EXCLUDEFROMCAPTURE
        )

    def show_at(self, hwnd: int, x: int, y: int, size: tuple[int, int] | None = None) -> None:
        """Show hwnd at (x, y) on top of the topmost band, without activating
        it. Called for the background first and the text second, so the text
        lands above."""
        w, h = size if size is not None else (0, 0)
        flags = _SWP_NOACTIVATE | _SWP_SHOWWINDOW | (0 if size is not None else _SWP_NOSIZE)
        self.user32.SetWindowPos(hwnd, _HWND_TOPMOST, int(x), int(y), int(w), int(h), flags)
        self.user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)

    def hide(self, hwnd: int) -> None:
        self.user32.ShowWindow(hwnd, _SW_HIDE)

    def client_origin(self, hwnd: int) -> tuple[int, int] | None:
        """Top-left of hwnd's client area in THIS thread's coordinate space --
        the same space Tk and SetWindowPos use here (the UI is DPI-unaware, so
        this is deliberately not the physical-pixel rect the bot clicks in)."""
        if not self.user32.IsWindow(hwnd) or self.user32.IsIconic(hwnd):
            return None
        pt = self._wintypes.POINT(0, 0)
        if not self.user32.ClientToScreen(hwnd, self._ctypes.byref(pt)):
            return None
        return int(pt.x), int(pt.y)


def _find_arena_hwnd() -> int:
    """The Arena window, by the same enumeration the bot uses to find it."""
    try:
        from vision.window_locator import _list_mtga_window_rects_windows
    except Exception:
        return 0
    try:
        candidates = _list_mtga_window_rects_windows()
    except Exception:
        return 0
    if not candidates:
        return 0

    def _area(item: dict[str, Any]) -> int:
        rect = item.get("client_rect")
        return int(getattr(rect, "w", 0)) * int(getattr(rect, "h", 0))

    return int(max(candidates, key=_area).get("hwnd") or 0)


def background_alpha(transparency_percent: int) -> float:
    """Window alpha for the panel's background at this transparency (0-100 %)."""
    try:
        value = int(transparency_percent)
    except (TypeError, ValueError):
        value = 25
    return 1.0 - max(0, min(100, value)) / 100.0


class GameOverlay:
    """The panel. Built and driven from the Tk thread only.

    Two windows on top of each other, so that only the background can be made
    see-through: below, a plain window in the background color whose alpha
    follows the transparency setting; above, the text on a color-keyed
    background (fully transparent), so the text itself stays fully opaque.
    Both are dressed and checked the same way.
    """

    # Color key of the text window. One step off the background color, so the
    # anti-aliased glyph edges blend toward a color that matches the panel.
    _KEY = "#1c0704"

    def __init__(
        self, root: tk.Misc, *, colors: dict[str, str], font_family: str, scale,
        title_photo=None, quest_title_photo=None, bg_transparency: int = 25,
    ) -> None:
        self._root = root
        self._colors = colors
        # The main window's molten-gold look, for the "Session Info" and
        # "Daily Quests" headings.
        self._heading_photos = {"title": title_photo, "quest_title": quest_title_photo}
        self._win32: _Win32 | None = None
        self._hwnds: list[int] = []  # [background, text]
        self._safe = False
        self._shown = False
        self._last_lines: list[tuple[str, str]] | None = None
        self._labels: list[tk.Label] = []
        self._windows: list[tk.Toplevel] = []
        self._window: tk.Toplevel | None = None  # the text window
        self._bg_window: tk.Toplevel | None = None
        self._reported_off = False
        if os.name != "nt":
            self._report_off("only supported on Windows")
            return
        try:
            self._win32 = _Win32()
        except Exception as exc:
            self._report_off(f"user32 unavailable ({exc})")
            return

        bg_win = self._new_window(colors["bg"])
        text_win = self._new_window(self._KEY)
        self._windows = [bg_win, text_win]
        try:
            bg_win.attributes("-alpha", background_alpha(bg_transparency))
            text_win.attributes("-transparentcolor", self._KEY)
        except tk.TclError as exc:
            self._report_off(f"Tk cannot make the panel see-through ({exc})")
            return
        self._frame = tk.Frame(
            text_win, bg=self._KEY, padx=scale(12), pady=scale(8),
            highlightthickness=1, highlightbackground=colors["accent"],
        )
        self._frame.pack(fill=tk.BOTH, expand=True)
        self._fonts = {
            "title": (font_family, max(9, scale(11)), "bold"),
            "match": (font_family, max(8, scale(10))),
            "info": (font_family, max(8, scale(10))),
            "muted": (font_family, max(8, scale(9))),
            "quest_title": (font_family, max(9, scale(10)), "bold"),
            "quest": (font_family, max(8, scale(10))),
            "quest_active": (font_family, max(8, scale(10))),
        }
        self._bg_window, self._window = bg_win, text_win
        # Map them once far off-screen so Tk creates the real top-level
        # windows, then dress them before they are ever shown where they could
        # be seen. From here on they are shown/hidden through user32 only:
        # Tk's own deiconify would activate them.
        try:
            for win in self._windows:
                win.geometry("+-32000+-32000")
                win.deiconify()
                win.update_idletasks()
                hwnd = self._win32.root_of(int(win.winfo_id()))
                self._win32.hide(hwnd)
                self._hwnds.append(hwnd)
            self._safe = all(h and self._win32.dress(h) for h in self._hwnds)
        except Exception as exc:
            self._safe = False
            self._report_off(f"dressing the window failed ({exc})")
        if not self._safe:
            self._hide_all()
            self._report_off("Windows did not confirm click-through + no-focus + capture exclusion "
                             "(capture exclusion needs Windows 10 2004+)")

    def _new_window(self, bg: str) -> tk.Toplevel:
        win = tk.Toplevel(self._root)
        win.withdraw()
        win.title(TITLE)
        win.overrideredirect(True)
        win.configure(bg=bg)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        return win

    @property
    def available(self) -> bool:
        return self._safe

    def set_background_transparency(self, percent: int) -> None:
        """Live change from Settings -> Game Overlay."""
        if self._bg_window is None:
            return
        try:
            self._bg_window.attributes("-alpha", background_alpha(percent))
        except tk.TclError:
            pass

    def _report_off(self, reason: str) -> None:
        if self._reported_off:
            return
        self._reported_off = True
        try:
            bot_logger.log_info(f"Overlay not shown: {reason}")
        except Exception:
            pass

    def _hide_all(self) -> None:
        if self._win32 is None:
            return
        for hwnd in self._hwnds:
            try:
                self._win32.hide(hwnd)
            except Exception:
                pass
        self._shown = False

    def _render(self, lines: list[tuple[str, str]]) -> None:
        if lines == self._last_lines:
            return
        self._last_lines = list(lines)
        color_for = {
            "title": self._colors["accent"],
            "match": self._colors["active"],
            "info": self._colors["accent"],
            "muted": self._colors["muted"],
            "quest_title": self._colors["accent"],
            "quest": self._colors["muted"],
            "quest_active": self._colors["active"],
        }
        while len(self._labels) < len(lines):
            label = tk.Label(self._frame, bg=self._KEY, anchor="w", justify="left")
            label.pack(fill=tk.X, anchor="w")
            self._labels.append(label)
        for i, label in enumerate(self._labels):
            if i < len(lines):
                text, kind = lines[i]
                photo = self._heading_photos.get(kind)
                label.configure(text=text, fg=color_for.get(kind, self._colors["accent"]),
                                font=self._fonts.get(kind, self._fonts["info"]),
                                image=photo if photo is not None else "")
                if not label.winfo_manager():
                    label.pack(fill=tk.X, anchor="w")
            elif label.winfo_manager():
                label.pack_forget()

    def update(self, lines: list[tuple[str, str]], *, wanted: bool) -> None:
        """Refresh the text and put the panel on screen while the caller wants
        it (the bot runs, or its settings are open for a preview) and Arena's
        window exists and is not minimized. Which window is in front does not
        matter: minimizing the bot's own window or working in another app keeps
        the panel up until the bot stops or closes."""
        if not self._safe or self._window is None or self._win32 is None:
            return
        try:
            # Re-check every time: a dressed window that lost a style must not
            # stay over the game.
            if not all(self._win32.is_safe(h) for h in self._hwnds):
                self._safe = False
                self._hide_all()
                self._report_off("window lost its overlay styles")
                return
            origin = None
            if wanted:
                arena = _find_arena_hwnd()
                origin = self._win32.client_origin(arena) if arena else None
            if origin is None:
                if self._shown:
                    self._hide_all()
                return
            self._render(lines)
            self._window.update_idletasks()
            x, y = origin[0] + CORNER_GAP, origin[1] + CORNER_GAP
            size = (self._window.winfo_reqwidth(), self._window.winfo_reqheight())
            bg_hwnd, text_hwnd = self._hwnds
            self._win32.show_at(bg_hwnd, x, y, size)
            self._win32.show_at(text_hwnd, x, y)
            self._shown = True
        except Exception:
            # Never let the panel take the UI down; just keep it off.
            self._hide_all()

    def destroy(self) -> None:
        for win in self._windows:
            try:
                win.destroy()
            except Exception:
                pass
        self._windows = []
        self._window = self._bg_window = None
        self._safe = False
