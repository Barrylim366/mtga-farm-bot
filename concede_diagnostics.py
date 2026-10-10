"""Permanent, window-only concede diagnostics. No raw game state is persisted."""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone

import bot_logger
import runtime_status
from runtime_paths import ensure_runtime_subdir
from version import __version__
from vision.vision import cv2

_LOCK = threading.RLock()
_ACTIVE = set()
MAX_INCIDENTS = 30
NAME_MASKS = ((0, 0, 480, 90), (0, 1000, 480, 80))
_INCIDENT_NAME = re.compile(r"^\d{8}-\d{6}-\d{6}-[0-9a-f]{8}$")


def prepare_image(image):
    """Mask a private BGR copy before reducing resolution; reject unknown layouts."""
    if cv2 is None or image is None or image.size == 0:
        raise ValueError("capture_unavailable")
    height, width = image.shape[:2]
    if image.ndim != 3 or image.shape[2] != 3 or abs(width / height - 16 / 9) > .02:
        raise ValueError("unsupported_layout")
    result = image.copy()
    for x, y, w, h in NAME_MASKS:
        x1, y1 = int(x * width / 1920), int(y * height / 1080)
        x2 = min(width, int((x + w) * width / 1920 + .999))
        y2 = min(height, int((y + h) * height / 1080 + .999))
        result[y1:y2, x1:x2] = 0
    scale = min(1., 1280 / width, 720 / height)
    if scale < 1:
        result = cv2.resize(result, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    return result


class ConcedeIncident:
    @classmethod
    def create(cls, reason, match_id):
        try:
            return cls(reason, match_id)
        except Exception as exc:
            bot_logger.log_error(f"CONCEDE_INCIDENT_FAILED: stage=create error={type(exc).__name__}")
            return None

    def __init__(self, reason, match_id):
        session_id = runtime_status.read_status().get("session_id")
        self.root = ensure_runtime_subdir("concedes").resolve()
        self.path = self.root / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:8])
        with _LOCK:
            self.path.mkdir()
            _ACTIVE.add(self.path)
        self.payload = {
            "created_at_epoch": time.time(), "version": __version__,
            "session_id": session_id,
            "match_id": match_id, "reason": reason, "attempt_count": 0,
            "attempts": [], "outcome": "in_progress",
        }
        self._write()
        bot_logger.log_info(f"CONCEDE_INCIDENT_CREATED: path={self.path}")

    def _write(self):
        try:
            temporary = self.path / "incident.json.tmp"
            temporary.write_text(json.dumps(self.payload, indent=2), encoding="utf-8")
            temporary.replace(self.path / "incident.json")
        except Exception as exc:
            bot_logger.log_error(f"CONCEDE_INCIDENT_FAILED: stage=metadata error={type(exc).__name__}")

    def start_attempt(self, number):
        self.payload["attempt_count"] = number
        self.payload["attempts"].append({"attempt": number, "at_epoch": time.time(), "capture_status": "not_reached"})
        self._write()

    def capture(self, vision, provider):
        """Use fresh verified window bounds, never the controller's cached rectangle."""
        attempt = self.payload["attempts"][-1]
        try:
            vision.begin_tick()
            detection = provider.detect(write_debug_on_fail=False)
            if not detection.ok or detection.region is None:
                raise ValueError("window_unavailable")
            region = tuple(int(v) for v in detection.region)
            if region[0] < 0 or region[1] < 0 or region[2] <= 0 or region[3] <= 0:
                raise ValueError("invalid_window")
            image = vision.capture(region)
            if image is None or image.shape[:2] != (region[3], region[2]):
                raise ValueError("incomplete_window")
            image = prepare_image(image)
            filename = f"attempt-{attempt['attempt']}.jpg"
            if not cv2.imwrite(str(self.path / filename), image, [cv2.IMWRITE_JPEG_QUALITY, 80]):
                raise ValueError("write_failed")
            attempt.update(capture_status="saved", screenshot=filename,
                           width=int(image.shape[1]), height=int(image.shape[0]))
        except Exception as exc:
            # Persist only our fixed error codes, never exception text from game state.
            code = str(exc) if type(exc) is ValueError and str(exc) in {
                "window_unavailable", "invalid_window", "incomplete_window", "capture_unavailable",
                "unsupported_layout", "write_failed",
            } else "capture_failed"
            attempt["capture_status"] = code
            bot_logger.log_error(f"CONCEDE_INCIDENT_FAILED: stage=capture status={code} path={self.path}")
        self._write()

    def finish(self, outcome):
        self.payload.update(outcome=outcome, finished_at_epoch=time.time())
        self._write()
        bot_logger.log_info(f"CONCEDE_INCIDENT_FINISHED: outcome={outcome} path={self.path}")
        with _LOCK:
            _ACTIVE.discard(self.path)
            try:
                # Only directories created by this helper qualify; never follow symlinks.
                entries = [p for p in self.root.iterdir() if _INCIDENT_NAME.fullmatch(p.name)
                           and p.is_dir() and not p.is_symlink()
                           and p.resolve().parent == self.root]
                entries.sort(key=lambda p: p.name, reverse=True)
                for old in entries[MAX_INCIDENTS:]:
                    if old not in _ACTIVE:
                        shutil.rmtree(old)
            except Exception as exc:
                bot_logger.log_error(f"CONCEDE_INCIDENT_FAILED: stage=retention error={type(exc).__name__}")
