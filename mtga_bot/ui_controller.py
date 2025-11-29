from __future__ import annotations

import logging
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .quest_ai import Action, ActionType

logger = logging.getLogger(__name__)


class UIController:
    """
    Thin wrapper around pyautogui for MTGA UI actions.
    Uses a dry-run mode by default to prevent accidental clicks while developing.
    """

    def __init__(
        self,
        image_dir: Optional[str] = None,
        dry_run: bool = True,
        confidence: float = 0.9,
        pause_range: tuple[float, float] = (0.35, 0.75),
    ) -> None:
        self.image_dir = Path(image_dir) if image_dir else None
        self.dry_run = dry_run
        self.confidence = confidence
        self.pause_range = pause_range
        self._pyautogui = None
        self._ydotool = shutil.which("ydotool") if shutil.which("ydotool") else None
        self._use_ydotool = self._ydotool is not None

    def perform(self, action: Action) -> None:
        """Dispatch an Action to the appropriate UI gesture."""
        if action.action_type == ActionType.QUEUE_FOR_MATCH:
            self.click_named_target("queue_button")
        elif action.action_type == ActionType.KEEP_HAND:
            self.click_named_target("keep_hand")
        elif action.action_type == ActionType.ATTACK_ALL:
            self.press_key("a")  # MTGA default "attack with all"
        elif action.action_type == ActionType.CAST_SPELL:
            color = (action.details or {}).get("color")
            self.cast_spell(color_hint=color)
        elif action.action_type == ActionType.SURRENDER:
            self.surrender()
        elif action.action_type == ActionType.END_STEP:
            self.press_key("space")
        elif action.action_type == ActionType.EXIT:
            logger.info("Exit action received; stopping UI actions.")
        else:
            self.sleep_briefly()

    def click_named_target(self, target_name: str) -> None:
        """
        Click a cached screenshot of a UI element if available, otherwise falls back to a generic click.
        """
        if self.dry_run:
            logger.info("[dry-run] Would click %s", target_name)
            self.sleep_briefly()
            return

        self._ensure_pyautogui()
        if self.image_dir:
            candidate = self.image_dir / f"{target_name}.png"
            if candidate.exists():
                location = self._pyautogui.locateCenterOnScreen(str(candidate), confidence=self.confidence)
                if location:
                    self._click_absolute(location.x, location.y)
                    self.sleep_briefly()
                    return

        # Heuristic fallback positions for known targets.
        if target_name == "keep_hand":
            # Keep button sits bottom-right in mulligan view.
            self._click_relative(0.85, 0.93)
            return
        if target_name == "queue_button":
            self._click_relative(0.5, 0.75)
            return
        if target_name == "concede":
            self._click_relative(0.55, 0.6)
            return

        # Generic click near the center as a last resort.
        screen_size = self._pyautogui.size()
        x = screen_size.width * 0.5 + random.randint(-40, 40)
        y = screen_size.height * 0.75 + random.randint(-30, 30)
        self._click_absolute(x, y)
        self.sleep_briefly()

    def press_key(self, key: str) -> None:
        if self.dry_run:
            logger.info("[dry-run] Would press key %s", key)
            self.sleep_briefly()
            return

        # On Wayland, pyautogui keypresses may be blocked; prefer ydotool when available.
        if self._use_ydotool and self._press_key_with_ydotool(key):
            self.sleep_briefly()
            return

        self._ensure_pyautogui()
        self._pyautogui.press(key)
        self.sleep_briefly()

    def cast_spell(self, color_hint: Optional[str] = None) -> None:
        if self.dry_run:
            logger.info("[dry-run] Would cast spell (color: %s)", color_hint or "any")
            self.sleep_briefly()
            return

        # Casting is highly deck-specific; here we just click the hand area to play a card.
        self._ensure_pyautogui()
        screen_size = self._pyautogui.size()
        x = screen_size.width * 0.6 + random.randint(-80, 80)
        y = screen_size.height * 0.85 + random.randint(-25, 25)
        self._click_absolute(x, y)
        self.sleep_briefly()

    def surrender(self) -> None:
        if self.dry_run:
            logger.info("[dry-run] Would surrender the current match")
            self.sleep_briefly()
            return

        # MTGA surrender is a two-step interaction: open menu then confirm concede.
        self._ensure_pyautogui()
        self.press_key("esc")
        time.sleep(0.5)
        self.click_named_target("concede")

    def sleep_briefly(self) -> None:
        pause = random.uniform(*self.pause_range)
        time.sleep(pause)

    def _ensure_pyautogui(self) -> None:
        if self._pyautogui is None:
            import pyautogui

            self._pyautogui = pyautogui
            self._pyautogui.PAUSE = 0
            self._pyautogui.FAILSAFE = False

    def _click_relative(self, rel_x: float, rel_y: float) -> None:
        """Click at a relative screen position (0..1 in both axes) with slight jitter."""
        screen_size = self._pyautogui.size()
        jitter_x = random.randint(-10, 10)
        jitter_y = random.randint(-10, 10)
        x = screen_size.width * rel_x + jitter_x
        y = screen_size.height * rel_y + jitter_y
        self._click_absolute(x, y)
        self.sleep_briefly()

    def _click_absolute(self, x: float, y: float) -> None:
        """Perform a left-click at absolute screen coordinates, preferring ydotool on Wayland."""
        if self._use_ydotool:
            try:
                # Move then click left button (0x1).
                subprocess.run(
                    [self._ydotool, "mousemove", "--absolute", str(int(x)), str(int(y))],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    [self._ydotool, "click", "0x1"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("ydotool click failed at (%s,%s): %s", x, y, exc)

        self._ensure_pyautogui()
        self._pyautogui.click(x, y)

    def _press_key_with_ydotool(self, key: str) -> bool:
        """
        Attempt to send a keypress via ydotool (Wayland-friendly).
        Returns True if a command was issued.
        """
        if not self._ydotool:
            return False

        keymap = {
            "a": 30,  # KEY_A
            "space": 57,  # KEY_SPACE
            "esc": 1,  # KEY_ESC
        }
        code = keymap.get(key.lower())
        if code is None:
            # Fallback: type the key as text (works for simple characters).
            try:
                subprocess.run(
                    [self._ydotool, "type", key],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception as exc:  # pragma: no cover - best-effort
                logger.debug("ydotool type failed for %s: %s", key, exc)
                return False

        try:
            # ydotool key expects <code>:<state>, 1=down, 0=up
            subprocess.run(
                [self._ydotool, "key", f"{code}:1", f"{code}:0"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as exc:  # pragma: no cover - best-effort
            logger.debug("ydotool key failed for %s (%s): %s", key, code, exc)
            return False
