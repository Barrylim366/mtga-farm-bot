from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import bot_logger
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


@dataclass(frozen=True)
class ArenaDetectionResult:
    ok: bool
    region: tuple[int, int, int, int] | None
    code: str
    message: str
    matched_anchor: str | None = None
    diagnostics: dict[str, Any] | None = None
    debug_dir: str | None = None


_ANCHOR_SPECS: tuple[dict[str, Any], ...] = (
    {"name": "global_anchor.png", "roi": (0, 0, 640, 260), "threshold": 0.78},
    {"name": "home_anchor.png", "roi": (0, 0, 760, 260), "threshold": 0.78},
    {"name": "play_menu_anchor.png", "roi": (0, 0, 900, 300), "threshold": 0.78},
    {"name": "find_match_anchor.png", "roi": (0, 0, 960, 320), "threshold": 0.78},
    {"name": "historic_anchor.png", "roi": (0, 0, 960, 320), "threshold": 0.78},
    {"name": "my_decks_anchor.png", "roi": (0, 0, 960, 320), "threshold": 0.78},
    {"name": "store_anchor.png", "roi": (0, 0, 960, 320), "threshold": 0.78},
    {"name": "options_anchor.png", "roi": (320, 0, 1280, 460), "threshold": 0.78},
    {"name": "ingame_anchor.png", "roi": (0, 0, 1920, 360), "threshold": 0.78},
    {"name": "attack_all.png", "roi": (1120, 700, 760, 320), "threshold": 0.80},
)


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

        result = self.detect(write_debug_on_fail=False)
        if result.ok and result.region is not None:
            self._cached_region = result.region
            return result.region
        return None

    def reacquire(self) -> tuple[int, int, int, int] | None:
        self._cached_region = None
        return self.acquire()

    def detect(
        self,
        *,
        write_debug_on_fail: bool = False,
        debug_label: str = "arena-setup",
    ) -> ArenaDetectionResult:
        if os.name == "nt":
            result = self._detect_windows()
        else:
            result = self._detect_generic()

        if write_debug_on_fail and not result.ok:
            debug_dir = self._write_detection_debug_bundle(result, debug_label=debug_label)
            return ArenaDetectionResult(
                ok=result.ok,
                region=result.region,
                code=result.code,
                message=result.message,
                matched_anchor=result.matched_anchor,
                diagnostics=result.diagnostics,
                debug_dir=debug_dir,
            )
        return result

    def _detect_windows(self) -> ArenaDetectionResult:
        diagnostics: dict[str, Any] = {
            "platform": "windows",
            "expected_size": {"width": int(self._expected_size[0]), "height": int(self._expected_size[1])},
        }

        scaling_percent = _get_windows_display_scaling_percent()
        if scaling_percent is not None:
            diagnostics["display_scaling_percent"] = scaling_percent
            if abs(int(scaling_percent) - 100) > 3:
                return ArenaDetectionResult(
                    ok=False,
                    region=None,
                    code="display_scaling_wrong",
                    message=(
                        f"Windows display scaling is {int(scaling_percent)}%. "
                        "Set Windows display scaling to 100% and try again."
                    ),
                    diagnostics=diagnostics,
                )

        candidates = _list_mtga_window_rects_windows()
        diagnostics["candidate_windows"] = [
            {
                "title": str(item.get("title", "")),
                "client_rect": _rect_to_dict(item.get("client_rect")),
                "window_rect": _rect_to_dict(item.get("window_rect")),
                "score": float(item.get("score", 0.0)),
            }
            for item in candidates
        ]
        selected = _pick_best_windows_candidate(candidates, self._expected_size)
        if selected is None:
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="window_not_found",
                message="MTGA window not found. Open MTGA in a visible windowed 1920x1080 window.",
                diagnostics=diagnostics,
            )

        rect = selected["client_rect"]
        region = (rect.x, rect.y, rect.w, rect.h)
        diagnostics["selected_window"] = {
            "title": str(selected.get("title", "")),
            "client_rect": _rect_to_dict(rect),
            "window_rect": _rect_to_dict(selected.get("window_rect")),
            "score": float(selected.get("score", 0.0)),
        }

        if not self._size_is_close(rect.w, rect.h, tolerance=8):
            return ArenaDetectionResult(
                ok=False,
                region=region,
                code="window_wrong_size",
                message=(
                    f"MTGA client area found at {rect.w}x{rect.h}. "
                    f"Set Arena to windowed {self._expected_size[0]}x{self._expected_size[1]}."
                ),
                diagnostics=diagnostics,
            )

        matched_anchor, anchor_checks = self._verify_region_with_any_anchor(region)
        diagnostics["anchor_checks"] = anchor_checks
        if matched_anchor is None:
            return ArenaDetectionResult(
                ok=False,
                region=region,
                code="anchor_not_found",
                message=(
                    "MTGA window found at 1920x1080, but no known UI anchors matched. "
                    "Open a supported Arena screen such as Home, Play, Decks, Store, Options, or an in-game board."
                ),
                diagnostics=diagnostics,
            )

        return ArenaDetectionResult(
            ok=True,
            region=region,
            code="ok",
            message="MTGA window detected and verified.",
            matched_anchor=matched_anchor,
            diagnostics=diagnostics,
        )

    def _detect_generic(self) -> ArenaDetectionResult:
        rect = self._find_mtga_window_rect()
        if rect is not None:
            return ArenaDetectionResult(
                ok=True,
                region=(rect.x, rect.y, rect.w, rect.h),
                code="ok",
                message="MTGA window detected.",
            )
        return ArenaDetectionResult(
            ok=False,
            region=None,
            code="window_not_found",
            message="MTGA window not found.",
        )

    def _verify_rect_with_anchor(self, region: tuple[int, int, int, int]) -> bool:
        matched_anchor, _anchor_checks = self._verify_region_with_any_anchor(region)
        return matched_anchor is not None

    def _verify_region_with_any_anchor(
        self,
        region: tuple[int, int, int, int],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        checks: list[dict[str, Any]] = []
        self._vision.begin_tick()
        for spec in _ANCHOR_SPECS:
            template_path = os.path.join(self._assets_dir, str(spec["name"]))
            if not os.path.exists(template_path):
                checks.append(
                    {
                        "anchor": spec["name"],
                        "status": "missing_file",
                        "threshold": float(spec["threshold"]),
                    }
                )
                continue

            roi = _abs_region(region, tuple(spec["roi"]))
            image = self._vision.capture(roi)
            if image is None or getattr(image, "size", 0) == 0:
                checks.append(
                    {
                        "anchor": spec["name"],
                        "status": "capture_failed",
                        "roi": list(roi),
                        "threshold": float(spec["threshold"]),
                    }
                )
                continue

            match = self._vision.find_template(image, template_path, threshold=0.0)
            score = float(match.score) if match is not None else 0.0
            passed = bool(match is not None and score >= float(spec["threshold"]))
            checks.append(
                {
                    "anchor": spec["name"],
                    "status": "matched" if passed else "not_matched",
                    "roi": list(roi),
                    "threshold": float(spec["threshold"]),
                    "score": score,
                }
            )
            if passed:
                return str(spec["name"]), checks
        return None, checks

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
            selected = _pick_best_windows_candidate(_list_mtga_window_rects_windows(), self._expected_size)
            if selected is not None:
                return selected["client_rect"]
        return None

    def _write_detection_debug_bundle(
        self,
        result: ArenaDetectionResult,
        *,
        debug_label: str,
    ) -> str | None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        debug_dir = Path(bot_logger.ensure_debug_dir(f"{debug_label}-{stamp}"))
        try:
            payload = {
                "ok": result.ok,
                "code": result.code,
                "message": result.message,
                "region": list(result.region) if result.region is not None else None,
                "matched_anchor": result.matched_anchor,
                "diagnostics": result.diagnostics or {},
            }
            with open(debug_dir / "arena_detection.json", "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception:
            pass

        try:
            self._vision.begin_tick()
            full = self._vision.capture(None)
            self._vision.save_image(full, str(debug_dir / "full_screen.png"))
            if result.region is not None:
                arena = self._vision.capture(result.region)
                self._vision.save_image(arena, str(debug_dir / "arena_region.png"))
        except Exception:
            pass

        bot_logger.log_error(f"Arena setup debug bundle saved: {debug_dir}")
        return str(debug_dir)

    def _size_is_close(self, w: int, h: int, *, tolerance: int = 40) -> bool:
        ew, eh = self._expected_size
        return abs(int(w) - int(ew)) <= int(tolerance) and abs(int(h) - int(eh)) <= int(tolerance)


def focus_mtga_window(expected_size: tuple[int, int] = (1920, 1080)) -> bool:
    if os.name != "nt":
        return False
    try:
        candidates = _list_mtga_window_rects_windows()
        selected = _pick_best_windows_candidate(candidates, expected_size)
        if selected is None:
            return False
        hwnd = int(selected.get("hwnd") or 0)
        if hwnd <= 0:
            return False
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        SW_SHOW = 5
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)
        user32.BringWindowToTop(wintypes.HWND(hwnd))
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        user32.SetActiveWindow(wintypes.HWND(hwnd))
        return True
    except Exception:
        return False


def run_arena_setup_check(
    *,
    assets_dir: str,
    expected_size: tuple[int, int] = (1920, 1080),
    write_debug_on_fail: bool = True,
) -> ArenaDetectionResult:
    provider = ArenaRegionProvider(
        vision=VisionEngine(),
        assets_dir=assets_dir,
        expected_size=expected_size,
    )
    return provider.detect(write_debug_on_fail=write_debug_on_fail)


def _abs_region(base_region: tuple[int, int, int, int], rel_region: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    bx, by, bw, bh = [int(v) for v in base_region]
    rx, ry, rw, rh = [int(v) for v in rel_region]
    x = bx + max(0, rx)
    y = by + max(0, ry)
    w = max(0, min(rw, max(0, bw - max(0, rx))))
    h = max(0, min(rh, max(0, bh - max(0, ry))))
    return (x, y, w, h)


def _get_windows_display_scaling_percent() -> int | None:
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        if hasattr(user32, "GetDpiForSystem"):
            dpi = int(user32.GetDpiForSystem())
            if dpi > 0:
                return int(round((float(dpi) / 96.0) * 100.0))
    except Exception:
        pass

    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        if not hdc:
            return None
        try:
            dpi_x = int(gdi32.GetDeviceCaps(hdc, 88))
            if dpi_x > 0:
                return int(round((float(dpi_x) / 96.0) * 100.0))
        finally:
            user32.ReleaseDC(0, hdc)
    except Exception:
        pass
    return None


def _rect_to_dict(rect: WindowRect | None) -> dict[str, int] | None:
    if rect is None:
        return None
    return {"x": int(rect.x), "y": int(rect.y), "w": int(rect.w), "h": int(rect.h)}


def _pick_best_windows_candidate(
    candidates: list[dict[str, Any]],
    expected_size: tuple[int, int],
) -> dict[str, Any] | None:
    if not candidates:
        return None
    ew, eh = int(expected_size[0]), int(expected_size[1])

    def _score(item: dict[str, Any]) -> tuple[float, float]:
        rect = item.get("client_rect")
        if not isinstance(rect, WindowRect):
            return (-1e9, -1e9)
        title = str(item.get("title", "")).lower()
        size_penalty = abs(int(rect.w) - ew) + abs(int(rect.h) - eh)
        title_bonus = 0.0
        if "magic: the gathering arena" in title:
            title_bonus += 20.0
        elif "magic the gathering arena" in title:
            title_bonus += 18.0
        elif "mtga" in title:
            title_bonus += 12.0
        exact_bonus = 30.0 if rect.w == ew and rect.h == eh else 0.0
        closeness = max(0.0, 25.0 - (float(size_penalty) / 10.0))
        score = title_bonus + exact_bonus + closeness
        return (score, -float(size_penalty))

    best_item: dict[str, Any] | None = None
    best_score: tuple[float, float] | None = None
    for item in candidates:
        score = _score(item)
        item["score"] = float(score[0])
        if best_score is None or score > best_score:
            best_score = score
            best_item = item
    return best_item


def _list_mtga_window_rects_windows() -> list[dict[str, Any]]:
    user32 = ctypes.windll.user32

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    titles: list[dict[str, Any]] = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
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
            client_rect = _get_client_rect_windows(int(hwnd))
            if client_rect is None:
                return True
            window_rect = _get_window_rect_windows(int(hwnd))
            titles.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "client_rect": client_rect,
                    "window_rect": window_rect,
                    "score": 0.0,
                }
            )
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return titles


def _get_window_rect_windows(hwnd: int) -> WindowRect | None:
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    x = int(rect.left)
    y = int(rect.top)
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    if w <= 0 or h <= 0:
        return None
    return WindowRect(x=x, y=y, w=w, h=h)


def _get_client_rect_windows(hwnd: int) -> WindowRect | None:
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None

    top_left = wintypes.POINT(int(rect.left), int(rect.top))
    bottom_right = wintypes.POINT(int(rect.right), int(rect.bottom))
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(top_left)):
        return None
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(bottom_right)):
        return None

    x = int(top_left.x)
    y = int(top_left.y)
    w = int(bottom_right.x - top_left.x)
    h = int(bottom_right.y - top_left.y)
    if w <= 0 or h <= 0:
        return None
    return WindowRect(x=x, y=y, w=w, h=h)
