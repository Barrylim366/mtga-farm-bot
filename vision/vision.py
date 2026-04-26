from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency at runtime
    cv2 = None

try:
    import pyautogui
except Exception:  # pragma: no cover - optional dependency at runtime
    pyautogui = None

try:
    import mss
except Exception:  # pragma: no cover - optional dependency at runtime
    mss = None


@dataclass(frozen=True)
class TemplateMatch:
    x: int
    y: int
    score: float


class VisionEngine:
    """ROI-based screen capture and template matching with per-tick caching."""

    def __init__(self) -> None:
        self._tick_id = 0
        self._full_frame_cache: np.ndarray | None = None
        self._region_cache: dict[tuple[int, int, int, int], np.ndarray] = {}
        self._template_cache: dict[str, np.ndarray] = {}
        self._cv2_warned = False
        self._pyautogui_warned = False
        self._mss_instance = None
        self._mss_failed = False
        self._linux_tool_cmd: list[str] | None = None
        self._logical_screen_size: tuple[int, int] | None = None

    def begin_tick(self) -> None:
        self._tick_id += 1
        self._full_frame_cache = None
        self._region_cache.clear()

    def capture(self, region: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
        if self._full_frame_cache is None:
            frame = self._grab_full_frame()
            if frame is None:
                return None
            self._full_frame_cache = frame

        if region is None:
            return self._full_frame_cache

        x, y, w, h = region
        key = (int(x), int(y), int(w), int(h))
        if key in self._region_cache:
            return self._region_cache[key]

        frame = self._full_frame_cache
        if frame is None:
            return None
        fh, fw = frame.shape[:2]
        x1 = max(0, min(fw, int(x)))
        y1 = max(0, min(fh, int(y)))
        x2 = max(x1, min(fw, x1 + max(0, int(w))))
        y2 = max(y1, min(fh, y1 + max(0, int(h))))
        cropped = frame[y1:y2, x1:x2].copy()
        self._region_cache[key] = cropped
        return cropped

    def find_template(
        self,
        image: np.ndarray,
        template_path: str,
        threshold: float = 0.88,
    ) -> TemplateMatch | None:
        if cv2 is None:
            if not self._cv2_warned:
                self._cv2_warned = True
            return None
        if image is None or image.size == 0:
            return None
        template = self._load_template(template_path)
        if template is None:
            return None
        ih, iw = image.shape[:2]
        th, tw = template.shape[:2]
        if ih < th or iw < tw:
            return None
        result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        if float(max_val) < float(threshold):
            return None
        cx = int(max_loc[0] + (tw // 2))
        cy = int(max_loc[1] + (th // 2))
        return TemplateMatch(x=cx, y=cy, score=float(max_val))

    def assert_template(
        self,
        region: tuple[int, int, int, int],
        template_path: str,
        threshold: float = 0.88,
    ) -> bool:
        image = self.capture(region)
        if image is None:
            return False
        return self.find_template(image, template_path, threshold=threshold) is not None

    def pixel_check(
        self,
        x: int,
        y: int,
        expected_rgb: tuple[int, int, int],
        tolerance: int = 24,
    ) -> bool:
        frame = self.capture(None)
        if frame is None or frame.size == 0:
            return False
        h, w = frame.shape[:2]
        xi = int(x)
        yi = int(y)
        if xi < 0 or yi < 0 or xi >= w or yi >= h:
            return False
        b, g, r = frame[yi, xi]
        er, eg, eb = expected_rgb
        return (
            abs(int(r) - int(er)) <= tolerance
            and abs(int(g) - int(eg)) <= tolerance
            and abs(int(b) - int(eb)) <= tolerance
        )

    def save_image(self, image: np.ndarray | None, path: str) -> bool:
        if cv2 is None or image is None or image.size == 0:
            return False
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return bool(cv2.imwrite(path, image))
        except Exception:
            return False

    def wait_for_template(
        self,
        region: tuple[int, int, int, int],
        template_path: str,
        threshold: float,
        timeout_sec: float,
        poll_sec: float = 0.2,
    ) -> bool:
        deadline = time.time() + max(0.0, float(timeout_sec))
        while time.time() < deadline:
            self.begin_tick()
            if self.assert_template(region, template_path, threshold=threshold):
                return True
            time.sleep(max(0.05, float(poll_sec)))
        return False

    def _grab_full_frame(self) -> np.ndarray | None:
        if mss is not None and not self._mss_failed:
            try:
                if self._mss_instance is None:
                    self._mss_instance = mss.mss()
                monitor = self._mss_instance.monitors[0]
                raw = self._mss_instance.grab(monitor)
                arr = np.array(raw, dtype=np.uint8)
                if arr.ndim == 3 and arr.shape[2] == 4:
                    return self._normalize_frame_to_logical_size(arr[:, :, :3].copy())
                if arr.ndim == 3 and arr.shape[2] == 3:
                    return self._normalize_frame_to_logical_size(cvt_rgb_to_bgr(arr))
            except Exception:
                self._mss_failed = True
                try:
                    if self._mss_instance is not None:
                        self._mss_instance.close()
                except Exception:
                    pass
                self._mss_instance = None

        if sys.platform.startswith("linux"):
            frame = self._grab_via_linux_tool()
            if frame is not None:
                return frame

        if pyautogui is not None:
            try:
                shot = pyautogui.screenshot()
                return self._normalize_frame_to_logical_size(cvt_rgb_to_bgr(np.array(shot)))
            except Exception:
                if not self._pyautogui_warned:
                    self._pyautogui_warned = True
        return None

    def _normalize_frame_to_logical_size(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.size == 0 or cv2 is None or sys.platform != "darwin" or pyautogui is None:
            return frame
        logical = self._logical_screen_size
        if logical is None:
            try:
                size = pyautogui.size()
                logical = (int(size.width), int(size.height))
                if logical[0] > 0 and logical[1] > 0:
                    self._logical_screen_size = logical
            except Exception:
                logical = None
        if not logical:
            return frame
        target_w, target_h = logical
        actual_h, actual_w = frame.shape[:2]
        if target_w <= 0 or target_h <= 0 or actual_w <= 0 or actual_h <= 0:
            return frame
        ratio_x = float(actual_w) / float(target_w)
        ratio_y = float(actual_h) / float(target_h)
        if ratio_x < 1.2 or ratio_y < 1.2:
            return frame
        if abs(ratio_x - ratio_y) > 0.15:
            return frame
        return cv2.resize(frame, (int(target_w), int(target_h)), interpolation=cv2.INTER_AREA)

    def _grab_via_linux_tool(self) -> np.ndarray | None:
        import shutil
        import subprocess
        import tempfile

        if cv2 is None:
            return None

        def run_to_png(cmd: list[str]) -> np.ndarray | None:
            tmp_path: str | None = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="mtga_shot_")
                os.close(fd)
                full_cmd = [arg.replace("__OUT__", tmp_path) for arg in cmd]
                try:
                    proc = subprocess.run(
                        full_cmd,
                        capture_output=True,
                        timeout=10.0,
                        check=False,
                    )
                except Exception:
                    return None
                if proc.returncode != 0:
                    return None
                if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                    return None
                img = cv2.imread(tmp_path, cv2.IMREAD_COLOR)
                if img is None or img.size == 0:
                    return None
                return img
            finally:
                if tmp_path is not None:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        if self._linux_tool_cmd is not None:
            return run_to_png(self._linux_tool_cmd)

        candidates: list[list[str]] = []
        if shutil.which("grim") is not None:
            candidates.append(["grim", "__OUT__"])
        if shutil.which("spectacle") is not None:
            candidates.append(["spectacle", "-b", "-n", "-f", "-o", "__OUT__"])
        if shutil.which("gnome-screenshot") is not None:
            candidates.append(["gnome-screenshot", "-f", "__OUT__"])
        if shutil.which("scrot") is not None:
            candidates.append(["scrot", "--overwrite", "-z", "__OUT__"])

        for cmd in candidates:
            frame = run_to_png(cmd)
            if frame is not None:
                self._linux_tool_cmd = cmd
                return frame
        return None

    def _load_template(self, template_path: str) -> np.ndarray | None:
        normalized = os.path.abspath(template_path)
        cached = self._template_cache.get(normalized)
        if cached is not None:
            return cached
        if cv2 is None:
            return None
        if not os.path.exists(normalized):
            return None
        image = cv2.imread(normalized, cv2.IMREAD_COLOR)
        if image is None:
            return None
        self._template_cache[normalized] = image
        return image


def cvt_rgb_to_bgr(img_rgb: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return img_rgb[:, :, ::-1].copy()
    return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
