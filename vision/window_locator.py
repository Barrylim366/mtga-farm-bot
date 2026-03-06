from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from vision.vision import VisionEngine

if os.name == "nt":
    import ctypes
    from ctypes import wintypes


@dataclass(frozen=True)
class WindowRect:
    x: int
    y: int
    w: int
    h: int


class ArenaRegionProvider:
    def __init__(
        self,
        *,
        vision: VisionEngine,
        assets_dir: str,
        expected_size: tuple[int, int] = (1920, 1080),
        global_anchor_name: str = "global_anchor.png",
        global_anchor_offset: tuple[int, int] | None = None,
    ) -> None:
        self._vision = vision
        self._assets_dir = assets_dir
        self._expected_size = expected_size
        self._global_anchor_name = global_anchor_name
        self._global_anchor_offset = global_anchor_offset
        self._cached_region: tuple[int, int, int, int] | None = None

    def acquire(self) -> tuple[int, int, int, int] | None:
        if self._cached_region is not None:
            return self._cached_region

        rect = self._find_mtga_window_rect()
        if rect is not None and self._size_is_close(rect.w, rect.h):
            candidate = (rect.x, rect.y, rect.w, rect.h)
            if self._verify_rect_with_anchor(candidate):
                self._cached_region = candidate
                return candidate

        anchor_based = self._acquire_from_global_anchor()
        if anchor_based is not None:
            self._cached_region = anchor_based
            return anchor_based

        if rect is not None:
            fallback = (rect.x, rect.y, rect.w, rect.h)
            self._cached_region = fallback
            return fallback
        return None

    def reacquire(self) -> tuple[int, int, int, int] | None:
        self._cached_region = None
        return self.acquire()

    def _verify_rect_with_anchor(self, region: tuple[int, int, int, int]) -> bool:
        anchor_path = os.path.join(self._assets_dir, self._global_anchor_name)
        if not os.path.exists(anchor_path):
            return True
        roi = (region[0], region[1], min(region[2], 520), min(region[3], 220))
        self._vision.begin_tick()
        return self._vision.assert_template(roi, anchor_path, threshold=0.78)

    def _acquire_from_global_anchor(self) -> tuple[int, int, int, int] | None:
        anchor_path = os.path.join(self._assets_dir, self._global_anchor_name)
        if not os.path.exists(anchor_path):
            return None
        if not self._global_anchor_offset:
            return None

        self._vision.begin_tick()
        frame = self._vision.capture(None)
        if frame is None:
            return None
        match = self._vision.find_template(frame, anchor_path, threshold=0.80)
        if match is None:
            return None

        origin_x = int(match.x - self._global_anchor_offset[0])
        origin_y = int(match.y - self._global_anchor_offset[1])
        return (origin_x, origin_y, int(self._expected_size[0]), int(self._expected_size[1]))

    def _find_mtga_window_rect(self) -> WindowRect | None:
        if os.name == "nt":
            return _find_mtga_window_rect_windows()
        return None

    def _size_is_close(self, w: int, h: int) -> bool:
        ew, eh = self._expected_size
        return abs(int(w) - int(ew)) <= 40 and abs(int(h) - int(eh)) <= 40


def _find_mtga_window_rect_windows() -> WindowRect | None:
    user32 = ctypes.windll.user32

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    titles: list[tuple[int, str]] = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = str(buff.value or "").strip()
        if not title:
            return True
        low = title.lower()
        if "mtga" in low or "magic: the gathering arena" in low or "magic the gathering arena" in low:
            titles.append((int(hwnd), title))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    if not titles:
        return None

    rect = wintypes.RECT()
    hwnd = titles[0][0]
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    x = int(rect.left)
    y = int(rect.top)
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    if w <= 0 or h <= 0:
        return None
    # Approximate Windows borders/title bar to get the client-like region.
    if sys.platform == "win32":
        x += 8
        y += 31
        w -= 16
        h -= 39
    return WindowRect(x=x, y=y, w=w, h=h)
