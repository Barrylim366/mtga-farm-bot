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

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

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

_COMMON_16_9_SIZES: tuple[tuple[int, int], ...] = (
    (1024, 576),
    (1152, 648),
    (1280, 720),
    (1360, 765),
    (1440, 810),
    (1600, 900),
    (1920, 1080),
    (2560, 1440),
    (3840, 2160),
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
                message="MTGA window not found. Open MTGA in a visible windowed 16:9 window.",
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

        if not self._is_supported_arena_size(rect.w, rect.h):
            return ArenaDetectionResult(
                ok=False,
                region=region,
                code="window_wrong_size",
                message=self._window_size_message(rect.w, rect.h, screen_size=(rect.w, rect.h)),
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
                    f"MTGA window found at {rect.w}x{rect.h}, but no known UI anchors matched. "
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

    def _detect_linux_via_x11(self) -> ArenaDetectionResult | None:
        diagnostics: dict[str, Any] = {"platform": "linux", "method": "xwininfo"}
        import shutil
        import subprocess
        import re

        xwininfo = shutil.which("xwininfo")
        if xwininfo is None:
            diagnostics["error"] = "xwininfo_not_installed"
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="x11_tool_missing",
                message="xwininfo not installed.",
                diagnostics=diagnostics,
            )

        try:
            proc = subprocess.run(
                [xwininfo, "-tree", "-root"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except Exception as exc:
            diagnostics["error"] = f"xwininfo_failed: {exc}"
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="x11_probe_failed",
                message=f"xwininfo failed: {exc}",
                diagnostics=diagnostics,
            )

        if proc.returncode != 0:
            diagnostics["error"] = "xwininfo_nonzero_exit"
            diagnostics["stderr"] = (proc.stderr or "").strip()[:400]
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="x11_probe_failed",
                message="xwininfo could not enumerate windows (no X11/XWayland display?).",
                diagnostics=diagnostics,
            )

        text = proc.stdout or ""
        pattern = re.compile(
            r'^\s*(0x[0-9a-fA-F]+)\s+"([^"]*)":\s*\([^)]*\)\s+'
            r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)\s+\+(-?\d+)\+(-?\d+)',
            re.MULTILINE,
        )

        candidates: list[dict[str, Any]] = []
        for match in pattern.finditer(text):
            wid, title, w, h, _rx, _ry, ax, ay = match.groups()
            low = title.lower()
            if "mtga" not in low and "magic: the gathering arena" not in low and "magic the gathering arena" not in low:
                continue
            candidates.append({
                "id": wid,
                "title": title,
                "rect": WindowRect(x=int(ax), y=int(ay), w=int(w), h=int(h)),
            })
        diagnostics["candidate_windows"] = [
            {"id": c["id"], "title": c["title"], "rect": _rect_to_dict(c["rect"])}
            for c in candidates
        ]

        if not candidates:
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="window_not_found",
                message="MTGA window not found via X11. Make sure MTGA is running and visible.",
                diagnostics=diagnostics,
            )

        ew, eh = int(self._expected_size[0]), int(self._expected_size[1])

        def _score(item: dict[str, Any]) -> tuple[float, float]:
            title_l = str(item["title"]).lower()
            rect: WindowRect = item["rect"]
            bonus = 0.0
            if "magic: the gathering arena" in title_l:
                bonus += 20.0
            elif "magic the gathering arena" in title_l:
                bonus += 18.0
            elif "mtga" in title_l:
                bonus += 12.0
            penalty = abs(rect.w - ew) + abs(rect.h - eh)
            return (bonus - penalty / 50.0, -float(penalty))

        selected = max(candidates, key=_score)
        rect: WindowRect = selected["rect"]
        diagnostics["selected_window"] = {
            "id": selected["id"],
            "title": selected["title"],
            "rect": _rect_to_dict(rect),
        }

        candidate_origins: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for dy in (0, 30, 24, 36, 28, 22, 40, 48):
            for dx in (0, 1, -1, 4, -4, 8, -8):
                origin = (rect.x + dx, rect.y + dy)
                if origin in seen:
                    continue
                seen.add(origin)
                candidate_origins.append(origin)

        verify_checks: list[dict[str, Any]] = []
        for ox, oy in candidate_origins:
            if ox < 0 or oy < 0:
                continue
            region = (int(ox), int(oy), int(rect.w), int(rect.h))
            matched_anchor, checks = self._verify_region_with_any_anchor(region)
            verify_checks.append({"origin": [int(ox), int(oy)], "matched_anchor": matched_anchor})
            if matched_anchor is not None:
                diagnostics["verification"] = verify_checks
                return ArenaDetectionResult(
                    ok=True,
                    region=region,
                    code="ok",
                    message="MTGA window detected via X11.",
                    matched_anchor=matched_anchor,
                    diagnostics=diagnostics,
                )

        diagnostics["verification"] = verify_checks

        fallback_region = (int(rect.x), int(rect.y), int(rect.w), int(rect.h))
        if not self._is_supported_arena_size(rect.w, rect.h):
            return ArenaDetectionResult(
                ok=False,
                region=fallback_region,
                code="window_wrong_size",
                message=self._window_size_message(rect.w, rect.h, screen_size=(rect.w, rect.h)),
                diagnostics=diagnostics,
            )

        return ArenaDetectionResult(
            ok=False,
            region=fallback_region,
            code="anchor_not_found",
            message=(
                "MTGA window found via X11 but no known UI anchor matched. "
                "Open a supported Arena screen (Home, Play, Decks, Store, Options, or in-game) and try again."
            ),
            diagnostics=diagnostics,
        )

    def _detect_macos_via_quartz(self) -> ArenaDetectionResult | None:
        diagnostics: dict[str, Any] = {"platform": "macos", "method": "quartz"}
        try:
            import Quartz  # type: ignore
        except Exception as exc:
            diagnostics["error"] = f"quartz_import_failed: {exc}"
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="macos_quartz_unavailable",
                message="macOS window enumeration is unavailable.",
                diagnostics=diagnostics,
            )

        try:
            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
            )
        except Exception as exc:
            diagnostics["error"] = f"quartz_enumeration_failed: {exc}"
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="macos_quartz_failed",
                message=f"macOS window enumeration failed: {exc}",
                diagnostics=diagnostics,
            )

        candidates: list[dict[str, Any]] = []
        for item in windows or []:
            try:
                owner = str(item.get("kCGWindowOwnerName") or "")
                title = str(item.get("kCGWindowName") or "")
                name_blob = f"{owner} {title}".lower()
                if "mtga" not in name_blob and "magic: the gathering arena" not in name_blob and "magic the gathering arena" not in name_blob:
                    continue
                bounds = item.get("kCGWindowBounds") or {}
                window_rect = WindowRect(
                    x=int(bounds.get("X") or 0),
                    y=int(bounds.get("Y") or 0),
                    w=int(bounds.get("Width") or 0),
                    h=int(bounds.get("Height") or 0),
                )
                if window_rect.w < 320 or window_rect.h < 180:
                    continue
                rect = _estimate_macos_client_rect(window_rect)
                candidates.append(
                    {
                        "title": title or owner,
                        "owner": owner,
                        "client_rect": rect,
                        "window_rect": window_rect,
                        "score": 0.0,
                    }
                )
            except Exception:
                continue

        diagnostics["candidate_windows"] = [
            {
                "title": str(item.get("title", "")),
                "owner": str(item.get("owner", "")),
                "client_rect": _rect_to_dict(item.get("client_rect")),
                "window_rect": _rect_to_dict(item.get("window_rect")),
            }
            for item in candidates
        ]

        selected = _pick_best_windows_candidate(candidates, self._expected_size)
        if selected is None:
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="window_not_found",
                message="MTGA window not found. Make sure MTGA is open, visible, and running in windowed mode.",
                diagnostics=diagnostics,
            )

        rect = selected["client_rect"]
        region = (rect.x, rect.y, rect.w, rect.h)
        diagnostics["selected_window"] = {
            "title": str(selected.get("title", "")),
            "owner": str(selected.get("owner", "")),
            "client_rect": _rect_to_dict(rect),
            "window_rect": _rect_to_dict(selected.get("window_rect")),
            "score": float(selected.get("score", 0.0)),
        }

        if not self._is_supported_arena_size(rect.w, rect.h):
            return ArenaDetectionResult(
                ok=False,
                region=region,
                code="window_wrong_size",
                message=self._window_size_message(rect.w, rect.h, screen_size=(rect.w, rect.h)),
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
                    f"MTGA window found at {rect.w}x{rect.h}, but no known UI anchors matched. "
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
        platform_name = "macos" if sys.platform == "darwin" else "linux"

        if sys.platform.startswith("linux"):
            x11_result = self._detect_linux_via_x11()
            if x11_result is not None and x11_result.ok:
                return x11_result
            x11_diagnostics = x11_result.diagnostics if x11_result is not None else None
            if x11_result is not None and x11_result.code != "window_not_found":
                return x11_result
        elif sys.platform == "darwin":
            macos_result = self._detect_macos_via_quartz()
            if macos_result is not None and macos_result.ok:
                return macos_result
            x11_diagnostics = macos_result.diagnostics if macos_result is not None else None
            if macos_result is not None and macos_result.code != "window_not_found":
                return macos_result
        else:
            x11_diagnostics = None

        diagnostics: dict[str, Any] = {
            "platform": platform_name,
            "expected_size": {"width": int(self._expected_size[0]), "height": int(self._expected_size[1])},
        }
        if x11_diagnostics:
            diagnostics["x11_probe"] = x11_diagnostics

        self._vision.begin_tick()
        frame = self._vision.capture(None)
        if frame is None or getattr(frame, "size", 0) == 0:
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="screen_capture_failed",
                message=(
                    "Could not capture the screen. Install a screenshot backend "
                    "(on Linux: `sudo pacman -S scrot` or `sudo apt install scrot`; "
                    "Wayland sessions may additionally require `gnome-screenshot` "
                    "or running the UI via XWayland)."
                ),
                diagnostics=diagnostics,
            )

        fh, fw = frame.shape[:2]
        ew, eh = int(self._expected_size[0]), int(self._expected_size[1])
        diagnostics["screen_size"] = {"width": int(fw), "height": int(fh)}
        candidate_size = self._best_fit_16_9_size(fw, fh)
        diagnostics["fallback_candidate_size"] = {"width": int(candidate_size[0]), "height": int(candidate_size[1])}

        if fw < 960 or fh < 540:
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="screen_too_small",
                message=(
                    f"Captured screen is {fw}x{fh}, which is too small for reliable Arena detection. "
                    "Use a larger visible desktop area and run MTGA in a visible windowed 16:9 size."
                ),
                diagnostics=diagnostics,
            )

        search_frame = frame
        scale_x = 1.0
        scale_y = 1.0
        if cv2 is not None and candidate_size != (ew, eh):
            scale_x = float(ew) / float(candidate_size[0] or ew)
            scale_y = float(eh) / float(candidate_size[1] or eh)
            normalized_w = max(1, int(round(float(fw) * scale_x)))
            normalized_h = max(1, int(round(float(fh) * scale_y)))
            search_frame = cv2.resize(frame, (normalized_w, normalized_h), interpolation=cv2.INTER_LINEAR)
            diagnostics["normalized_search_size"] = {"width": int(normalized_w), "height": int(normalized_h)}

        seed_matches: list[dict[str, Any]] = []
        anchor_scan: list[dict[str, Any]] = []
        for spec in _ANCHOR_SPECS:
            template_path = os.path.join(self._assets_dir, str(spec["name"]))
            if not os.path.exists(template_path):
                anchor_scan.append({"anchor": spec["name"], "status": "missing_file"})
                continue

            template_size = _read_template_size(template_path)
            if template_size is None:
                anchor_scan.append({"anchor": spec["name"], "status": "template_unreadable"})
                continue
            th_, tw_ = template_size

            threshold = float(spec["threshold"])
            match = self._vision.find_template(search_frame, template_path, threshold=threshold)
            if match is None:
                anchor_scan.append({
                    "anchor": spec["name"],
                    "status": "not_found_on_screen",
                    "threshold": threshold,
                })
                continue

            mx = int(round((float(match.x) - float(tw_ // 2)) / float(scale_x)))
            my = int(round((float(match.y) - float(th_ // 2)) / float(scale_y)))
            seed_matches.append({
                "name": str(spec["name"]),
                "roi": tuple(int(v) for v in spec["roi"]),
                "tw": int(tw_),
                "th": int(th_),
                "screen_tl": (int(mx), int(my)),
                "score": float(match.score),
            })
            anchor_scan.append({
                "anchor": spec["name"],
                "status": "found_on_screen",
                "screen_tl": [int(mx), int(my)],
                "score": float(match.score),
            })

        diagnostics["anchor_scan"] = anchor_scan

        if not seed_matches:
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="window_not_found",
                message=(
                    "MTGA window not found. Make sure MTGA is visible, runs in a windowed 16:9 client area, "
                    "and that your display scaling is set to 100%."
                ),
                diagnostics=diagnostics,
            )

        origin = self._solve_origin_from_seeds(seed_matches, frame_size=(fw, fh), candidate_size=candidate_size)
        if origin is None:
            return ArenaDetectionResult(
                ok=False,
                region=None,
                code="anchor_not_found",
                message=(
                    "Arena anchors were visible on screen, but no consistent visible 16:9 MTGA window position matched them. "
                    "Check MTGA is undistorted, fully visible, "
                    "and not covered by overlays."
                ),
                diagnostics=diagnostics,
            )

        region = (int(origin[0]), int(origin[1]), int(candidate_size[0]), int(candidate_size[1]))
        matched_anchor, anchor_checks = self._verify_region_with_any_anchor(region)
        diagnostics["anchor_checks"] = anchor_checks
        diagnostics["selected_origin"] = [int(origin[0]), int(origin[1])]
        if matched_anchor is None:
            return ArenaDetectionResult(
                ok=False,
                region=region,
                code="anchor_not_found",
                message=(
                    "Found a candidate MTGA window position but no known UI anchor could be "
                    "verified there. Open a supported Arena screen such as Home, Play, Decks, "
                    "Store, Options, or an in-game board, and try again."
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

    def _solve_origin_from_seeds(
        self,
        seed_matches: list[dict[str, Any]],
        *,
        frame_size: tuple[int, int],
        candidate_size: tuple[int, int] | None = None,
    ) -> tuple[int, int] | None:
        if candidate_size is None:
            ew, eh = int(self._expected_size[0]), int(self._expected_size[1])
        else:
            ew, eh = int(candidate_size[0]), int(candidate_size[1])
        fw, fh = int(frame_size[0]), int(frame_size[1])

        def consistency_score(ox: int, oy: int) -> int:
            if ox < 0 or oy < 0 or ox + ew > fw or oy + eh > fh:
                return -1
            count = 0
            for s in seed_matches:
                rx, ry, rw, rh = s["roi"]
                tw_, th_ = s["tw"], s["th"]
                mx, my = s["screen_tl"]
                ax = mx - ox
                ay = my - oy
                if rx <= ax <= rx + max(0, rw - tw_) and ry <= ay <= ry + max(0, rh - th_):
                    count += 1
            return count

        best_origin: tuple[int, int] | None = None
        best_score = -1

        seed_matches_sorted = sorted(
            seed_matches,
            key=lambda s: (s["roi"][2] - s["tw"]) * (s["roi"][3] - s["th"]),
        )

        for seed in seed_matches_sorted:
            rx, ry, rw, rh = seed["roi"]
            tw_, th_ = seed["tw"], seed["th"]
            mx, my = seed["screen_tl"]

            ax_min = max(0, int(rx))
            ax_max = max(ax_min, int(rx) + max(0, int(rw) - int(tw_)))
            ay_min = max(0, int(ry))
            ay_max = max(ay_min, int(ry) + max(0, int(rh) - int(th_)))

            coarse_step = 20
            for ax in range(ax_min, ax_max + 1, coarse_step):
                for ay in range(ay_min, ay_max + 1, coarse_step):
                    ox = int(mx) - ax
                    oy = int(my) - ay
                    score = consistency_score(ox, oy)
                    if score > best_score:
                        best_score = score
                        best_origin = (ox, oy)

        if best_origin is None or best_score < 1:
            return None

        fine_step = 2
        radius = 24
        refined_origin = best_origin
        refined_score = best_score
        for dx in range(-radius, radius + 1, fine_step):
            for dy in range(-radius, radius + 1, fine_step):
                ox = best_origin[0] + dx
                oy = best_origin[1] + dy
                score = consistency_score(ox, oy)
                if score > refined_score:
                    refined_score = score
                    refined_origin = (ox, oy)

        return refined_origin

    def _find_mtga_window_rect(self) -> WindowRect | None:
        if os.name == "nt":
            selected = _pick_best_windows_candidate(_list_mtga_window_rects_windows(), self._expected_size)
            if selected is not None:
                return selected["client_rect"]
        return None

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

            roi = _scaled_abs_region(region, tuple(spec["roi"]), self._expected_size)
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

            desired_w = max(1, int(spec["roi"][2]))
            desired_h = max(1, int(spec["roi"][3]))
            if cv2 is not None:
                ih, iw = image.shape[:2]
                if iw > 0 and ih > 0 and (iw != desired_w or ih != desired_h):
                    image = cv2.resize(image, (desired_w, desired_h), interpolation=cv2.INTER_LINEAR)

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

    def _best_fit_16_9_size(self, width: int, height: int) -> tuple[int, int]:
        width = max(1, int(width))
        height = max(1, int(height))
        max_width = min(width, (height * 16) // 9)
        max_width -= max_width % 16
        if max_width < 960:
            max_width = min(width - (width % 16), 960)
        max_height = max(1, (max_width * 9) // 16)
        return (int(max_width), int(max_height))

    def _is_supported_arena_size(self, w: int, h: int) -> bool:
        w = int(w)
        h = int(h)
        if w < 960 or h < 540:
            return False
        return abs((w * 9) - (h * 16)) <= max(24, int(max(w, h) * 0.01))

    def _window_size_message(self, w: int, h: int, *, screen_size: tuple[int, int] | None = None) -> str:
        return (
            f"MTGA window found at {int(w)}x{int(h)}. Use a visible windowed 16:9 resolution instead.\n\n"
            + supported_16x9_message(screen_size=screen_size)
        )

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


def _scaled_abs_region(
    base_region: tuple[int, int, int, int],
    rel_region: tuple[int, int, int, int],
    reference_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    bx, by, bw, bh = [int(v) for v in base_region]
    rx, ry, rw, rh = [int(v) for v in rel_region]
    ref_w = max(1, int(reference_size[0]))
    ref_h = max(1, int(reference_size[1]))

    x = bx + int(round((float(rx) / float(ref_w)) * float(bw)))
    y = by + int(round((float(ry) / float(ref_h)) * float(bh)))
    w = max(1, int(round((float(rw) / float(ref_w)) * float(bw))))
    h = max(1, int(round((float(rh) / float(ref_h)) * float(bh))))

    x = max(bx, min(x, bx + max(0, bw - 1)))
    y = max(by, min(y, by + max(0, bh - 1)))
    w = min(w, max(1, (bx + bw) - x))
    h = min(h, max(1, (by + bh) - y))
    return (x, y, w, h)


def supported_16x9_message(*, screen_size: tuple[int, int] | None = None) -> str:
    sizes: list[tuple[int, int]] = list(_COMMON_16_9_SIZES)
    if screen_size is not None:
        sw = max(1, int(screen_size[0]))
        sh = max(1, int(screen_size[1]))
        max_fit_width = min(sw, (sh * 16) // 9)
        max_fit_width -= max_fit_width % 16
        max_fit_height = max(1, (max_fit_width * 9) // 16)
        if max_fit_width >= 960 and (max_fit_width, max_fit_height) not in sizes:
            sizes.append((max_fit_width, max_fit_height))
        sizes = [item for item in sizes if item[0] <= sw and item[1] <= sh]

    if not sizes:
        sizes = list(_COMMON_16_9_SIZES[:6])

    sizes = sorted(set(sizes))
    size_text = ", ".join(f"{w}x{h}" for w, h in sizes)
    formula = "Supported window sizes: any exact 16:9 resolution (16*n x 9*n)."
    if screen_size is None:
        return f"{formula}\nExamples: {size_text}"
    return f"{formula}\nExamples that fit this visible area: {size_text}"


def _read_template_size(template_path: str) -> tuple[int, int] | None:
    if cv2 is None:
        return None
    try:
        image = cv2.imread(template_path, cv2.IMREAD_COLOR)
    except Exception:
        return None
    if image is None:
        return None
    h, w = image.shape[:2]
    return int(h), int(w)


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


def _estimate_macos_client_rect(window_rect: WindowRect) -> WindowRect:
    candidates = [0, 22, 24, 28, 30, 32, 36]
    best = window_rect
    best_err = abs((int(window_rect.w) * 9) - (int(window_rect.h) * 16))
    for titlebar in candidates:
        client_h = int(window_rect.h) - int(titlebar)
        if client_h < 180:
            continue
        err = abs((int(window_rect.w) * 9) - (client_h * 16))
        if err < best_err:
            best_err = err
            best = WindowRect(
                x=int(window_rect.x),
                y=int(window_rect.y) + int(titlebar),
                w=int(window_rect.w),
                h=int(client_h),
            )
    return best


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
