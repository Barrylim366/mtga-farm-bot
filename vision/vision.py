from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
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


# One screen capture at a time, process-wide.
#
# On KDE/Wayland the only working backend is `spectacle`, which is a
# single-instance application: a second invocation does not take its own shot,
# it blocks. The bot runs ~100 threads that all want to see, so the invocations
# pile up -- measured 2026-08-23, three `spectacle -b -n -f -o /tmp/mtga_shot_*`
# processes hanging at once while *every* thread in the bot went silent for 48s
# mid-match (no log line at all, not even the periodic arena reacquire), the
# turn timer ran out, and the debug bundle being written stopped after its JSON
# with both screenshots missing. Killing the stuck processes released it
# immediately.
#
# So: serialise, and let parallel callers share one frame instead of queueing
# for their own. The shared frame is read-only by convention -- every consumer
# either template-matches it or slices a view out of it.
_CAPTURE_LOCK = threading.Lock()
_FRAME_TTL_SEC = 0.12
_last_frame: "np.ndarray | None" = None
_last_frame_ts = 0.0


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
        self._mss_failed_until = 0.0
        self._mss_consecutive_invalid = 0
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
        scales: list[float] | tuple[float, ...] | None = None,
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
        # cv2.matchTemplate is NOT scale-invariant: a template that is a slightly
        # different pixel size than it appears on screen (DPI scaling, region
        # normalization) never matches. When `scales` is given we try the template
        # at each scale and keep the best hit; scales=None keeps the original
        # single-scale behavior (unchanged for all existing callers).
        scale_list = [1.0] if not scales else list(scales)
        best: tuple[float, int, int] | None = None
        for s in scale_list:
            if s == 1.0:
                t = template
            else:
                th0, tw0 = template.shape[:2]
                nw, nh = max(1, int(round(tw0 * s))), max(1, int(round(th0 * s)))
                interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR
                t = cv2.resize(template, (nw, nh), interpolation=interp)
            th, tw = t.shape[:2]
            if ih < th or iw < tw:
                continue
            result = cv2.matchTemplate(image, t, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
            if best is None or float(max_val) > best[0]:
                best = (float(max_val), int(max_loc[0] + (tw // 2)), int(max_loc[1] + (th // 2)))
        if best is None or best[0] < float(threshold):
            return None
        return TemplateMatch(x=best[1], y=best[2], score=best[0])

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
        """A frame, shared with whoever asked in the last _FRAME_TTL_SEC."""
        global _last_frame, _last_frame_ts
        fresh = _last_frame
        if fresh is not None and time.time() - _last_frame_ts <= _FRAME_TTL_SEC:
            return fresh
        with _CAPTURE_LOCK:
            # Another thread may have refreshed it while we waited for the lock;
            # that is the whole point of holding one.
            if _last_frame is not None and time.time() - _last_frame_ts <= _FRAME_TTL_SEC:
                return _last_frame
            frame = self._grab_full_frame_uncached()
            if frame is not None:
                _last_frame = frame
                _last_frame_ts = time.time()
            return frame

    def _grab_full_frame_uncached(self) -> np.ndarray | None:
        if mss is not None and time.time() >= self._mss_failed_until:
            try:
                if self._mss_instance is None:
                    self._mss_instance = mss.mss()
                monitor = self._mss_instance.monitors[0]
                raw = self._mss_instance.grab(monitor)
                arr = np.array(raw, dtype=np.uint8)
                if arr.ndim == 3 and arr.shape[2] == 4:
                    frame = self._normalize_frame_to_logical_size(arr[:, :, :3].copy())
                    if not self._is_invalid_linux_wayland_capture_frame(frame):
                        self._mss_consecutive_invalid = 0
                        return frame
                    self._record_invalid_mss_capture()
                if arr.ndim == 3 and arr.shape[2] == 3:
                    frame = self._normalize_frame_to_logical_size(cvt_rgb_to_bgr(arr))
                    if not self._is_invalid_linux_wayland_capture_frame(frame):
                        self._mss_consecutive_invalid = 0
                        return frame
                    self._record_invalid_mss_capture()
            except Exception:
                self._suspend_mss_capture()

        if sys.platform.startswith("linux"):
            frame = self._grab_via_linux_tool()
            if frame is not None:
                return frame

        if pyautogui is not None:
            try:
                shot = pyautogui.screenshot()
                frame = self._normalize_frame_to_logical_size(cvt_rgb_to_bgr(np.array(shot)))
                if not self._is_invalid_linux_wayland_capture_frame(frame):
                    return frame
            except Exception:
                if not self._pyautogui_warned:
                    self._pyautogui_warned = True
        return None

    def _is_invalid_linux_wayland_capture_frame(self, frame: np.ndarray | None) -> bool:
        if frame is None or frame.size == 0:
            return False
        if not sys.platform.startswith("linux"):
            return False
        if not self._is_linux_wayland_session():
            return False
        # On KDE/Wayland, some backends can return a correctly sized all-black
        # frame. Treat that as a failed capture so the next backend can run.
        return int(np.max(frame)) == 0

    def _is_linux_wayland_session(self) -> bool:
        if not sys.platform.startswith("linux"):
            return False
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return True
        return bool(os.environ.get("WAYLAND_DISPLAY"))

    def _record_invalid_mss_capture(self) -> None:
        self._mss_consecutive_invalid += 1
        self._suspend_mss_capture()

    def _suspend_mss_capture(self, cooldown_sec: float = 30.0) -> None:
        self._mss_failed_until = time.time() + max(1.0, float(cooldown_sec))
        try:
            if self._mss_instance is not None:
                self._mss_instance.close()
        except Exception:  # best-effort cleanup only
            pass
        self._mss_instance = None

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
        import tempfile

        if cv2 is None:
            return None

        def run_to_png(cmd: list[str]) -> np.ndarray | None:
            tmp_path: str | None = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="mtga_shot_")
                os.close(fd)
                full_cmd = [arg.replace("__OUT__", tmp_path) for arg in cmd]
                # Popen rather than run(), so a tool that ignores its own
                # deadline can be killed by *process group*. A plain
                # run(timeout=...) kills the child it spawned, and spectacle
                # leaves a grandchild behind that keeps holding the
                # single-instance slot -- which is how three of them ended up
                # hanging at once and froze the bot.
                try:
                    proc = subprocess.Popen(
                        full_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                except Exception:
                    return None
                try:
                    proc.communicate(timeout=8.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        proc.kill()
                    try:
                        proc.communicate(timeout=2.0)
                    except Exception:
                        pass
                    return None
                except Exception:
                    return None
                if proc.returncode != 0:
                    return None
                if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                    return None
                img = cv2.imread(tmp_path, cv2.IMREAD_COLOR)
                if img is None or img.size == 0:
                    return None
                if self._is_invalid_linux_wayland_capture_frame(img):
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
        image = imread_unicode(normalized)
        if image is None:
            return None
        self._template_cache[normalized] = image
        return image


def imread_unicode(path: str, flags: int | None = None) -> np.ndarray | None:
    """cv2.imread that supports non-ASCII paths (e.g. Chinese folder names).

    On Windows, cv2.imread internally uses an ANSI fopen, so any template or
    image living under a path with non-ASCII characters silently fails to
    load. Reading the file as raw bytes and decoding via cv2.imdecode keeps
    the whole bot working regardless of the install folder name.
    """
    if cv2 is None:
        return None
    if flags is None:
        flags = cv2.IMREAD_COLOR
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        image = cv2.imdecode(data, flags)
        return image
    except Exception:
        try:
            return cv2.imread(path, flags)
        except Exception:
            return None


def cvt_rgb_to_bgr(img_rgb: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return img_rgb[:, :, ::-1].copy()
    return cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
