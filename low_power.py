"""Low-Power / Overnight Mode Manager for MTGA Farm Bot.

Reduces CPU/GPU usage, temperature, and fan noise during overnight runs
by applying dynamic sleep throttling and frame rate pacing.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import bot_logger
from runtime_paths import runtime_file


def _get_config_path() -> Path:
    return runtime_file("config", "low_power.json")


class LowPowerManager:
    _instance: "LowPowerManager | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.enabled = False
        self.sleep_multiplier = 1.0
        self._load_config()

    @classmethod
    def get_instance(cls) -> "LowPowerManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = LowPowerManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _load_config(self) -> None:
        path = _get_config_path()
        try:
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    self.enabled = bool(cfg.get("low_power_mode", False))
                    self.sleep_multiplier = 1.35 if self.enabled else 1.0
        except Exception:
            pass

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.sleep_multiplier = 1.35 if self.enabled else 1.0
        path = _get_config_path()
        try:
            cfg = {}
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["low_power_mode"] = self.enabled
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            bot_logger.log_info(f"[LOW_POWER] Low-power overnight mode set to {self.enabled}")
        except Exception as e:
            bot_logger.log_error(f"[LOW_POWER] Failed to persist low-power setting: {e}")

    def throttle_sleep(self, base_seconds: float) -> None:
        """Apply power-saving sleep interval."""
        delay = base_seconds * self.sleep_multiplier if self.enabled else base_seconds
        time.sleep(delay)


def sleep_throttled(base_seconds: float) -> None:
    LowPowerManager.get_instance().throttle_sleep(base_seconds)
