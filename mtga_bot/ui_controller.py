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
        click_region: Optional[tuple[int, int, int, int]] = None,
    ) -> None:
        self.image_dir = Path(image_dir) if image_dir else None
        self.dry_run = dry_run
        self.confidence = confidence
        self.pause_range = pause_range
        # click_region allows anchoring relative clicks to a windowed client:
        # (x, y, width, height) in absolute screen pixels.
        self.click_region = click_region
        self._pyautogui = None
        self._ydotool = shutil.which("ydotool") if shutil.which("ydotool") else None
        self._use_ydotool = self._ydotool is not None

    def perform(self, action: Action) -> None:
        """Dispatch an Action to the appropriate UI gesture."""
        if action.action_type == ActionType.QUEUE_FOR_MATCH:
            self.click_named_target("queue_button")
        elif action.action_type == ActionType.KEEP_HAND:
            self.confirm_keep_hand()
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

    def confirm_keep_hand(self) -> None:
        """
        Keep-hand confirmation is timing sensitive. Try a click and also send a key
        to increase the chance of hitting the dialog.
        """
        max_attempts = 10

        if self.dry_run:
            logger.info("[dry-run] Would keep starting hand (looping %s attempts)", max_attempts)
            self.sleep_briefly()
            return

        # Give the mulligan dialog a moment to appear, then focus the window.
        time.sleep(0.6)
        self._ensure_pyautogui()
        # Focus roughly center-top where the dialog sits.
        self._click_relative(0.5, 0.2)

        # Calibrated keep/mulligan positions from UI.png (1920x1080). We scale by click_region/screen.
        rel_keep = (0.65, 0.86)
        rel_keep_alt = (0.58, 0.83)

        for attempt in range(max_attempts):
            logger.debug("Keep-hand attempt %s/%s", attempt + 1, max_attempts)

            # First attempt: known target / image if present.
            self.click_named_target("keep_hand")

            # Calibrated fallbacks near the Keep 7 button (bottom-right-ish but higher than before).
            self._click_relative(*rel_keep)
            self._click_relative(*rel_keep_alt)

            # Extra safety: space/enter often work in MTGA dialogs.
            self.press_key("space")
            self.press_key("enter")

            # Small delay; MTGA often needs a moment to register mulligan choices.
            time.sleep(0.4)

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
        if self.click_region:
            origin_x, origin_y, width, height = self.click_region
        else:
            screen_size = self._pyautogui.size()
            origin_x, origin_y, width, height = 0, 0, screen_size.width, screen_size.height

        jitter_x = random.randint(-10, 10)
        jitter_y = random.randint(-10, 10)
        x = origin_x + width * rel_x + jitter_x
        y = origin_y + height * rel_y + jitter_y
        self._click_absolute(x, y)
        # Extra down/up to ensure click is registered in windowed mode
        if not self.dry_run and not self._use_ydotool:
            try:
                self._pyautogui.mouseDown(x, y)
                time.sleep(0.05)
                self._pyautogui.mouseUp(x, y)
            except Exception:
                pass
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
        # Use a down/up sequence; .click can be ignored in some windowed/overlay scenarios.
        self._pyautogui.moveTo(x, y, duration=0)
        self._pyautogui.mouseDown()
        time.sleep(0.03)
        self._pyautogui.mouseUp()

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
            "enter": 28,  # KEY_ENTER
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
