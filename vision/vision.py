from __future__ import annotations

import os
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

    def begin_tick(self) -> None:
        self._tick_id += 1
        self._full_frame_cache = None
        self._region_cache.clear()

    def capture(self, region: tuple[int, int, int, int] | None = None) -> np.ndarray | None:
        if pyautogui is None:
            if not self._pyautogui_warned:
                self._pyautogui_warned = True
            return None

        if self._full_frame_cache is None:
            shot = pyautogui.screenshot()
            self._full_frame_cache = cvt_rgb_to_bgr(np.array(shot))

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
