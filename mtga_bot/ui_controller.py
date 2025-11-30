from __future__ import annotations

import logging
import random
import shutil
import subprocess
import time
import os
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
        user_mouse_pause_seconds: float = 7.0,
        target_overrides: Optional[dict[str, tuple[float, float]]] = None,
        hand_y_ratio: float = 0.85,
        hand_x_ratios: Optional[list[float]] = None,
        land_y_ratio: float = 0.84,
        land_x_ratios: Optional[list[float]] = None,
        use_image_search: bool = True,
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
        self._last_known_mouse_pos: Optional[tuple[int, int]] = None
        self._last_bot_move_time: float = 0.0
        self._user_pause_until: float = 0.0
        self._user_pause_seconds = float(user_mouse_pause_seconds)
        self._bot_move_grace = 0.4
        self.target_overrides = self._normalize_target_overrides(target_overrides or {})
        self.hand_y_ratio = float(hand_y_ratio)
        self.hand_x_ratios = self._normalize_ratio_list(hand_x_ratios, [0.52, 0.58, 0.64])
        self.land_y_ratio = float(land_y_ratio)
        self.land_x_ratios = self._normalize_ratio_list(land_x_ratios, [0.38, 0.44, 0.5])
        self._image_search_available = use_image_search
        self._prefer_scrot = bool(os.environ.get("MTGA_BOT_USE_SCROT", "1") != "0")

    def perform(self, action: Action) -> None:
        """Dispatch an Action to the appropriate UI gesture."""
        self._maybe_pause_for_user_mouse()
        if action.action_type == ActionType.QUEUE_FOR_MATCH:
            self.click_named_target("queue_button")
        elif action.action_type == ActionType.KEEP_HAND:
            self.confirm_keep_hand()
        elif action.action_type == ActionType.ATTACK_ALL:
            self.press_key("a")  # MTGA default "attack with all"
        elif action.action_type == ActionType.PLAY_LAND:
            self.play_land()
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
        self._maybe_pause_for_user_mouse()
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
        self._maybe_pause_for_user_mouse()
        if target_name in self.target_overrides:
            rel_x, rel_y = self.target_overrides[target_name]
            logger.debug("Using configured target for %s at rel (%s, %s)", target_name, rel_x, rel_y)
            self._click_relative(rel_x, rel_y, label=target_name)
            self.sleep_briefly()
            return

        if self.dry_run:
            logger.info("[dry-run] Would click %s", target_name)
            self.sleep_briefly()
            return

        self._ensure_pyautogui()
        if self.image_dir:
            candidate = self.image_dir / f"{target_name}.png"
            # fallback alias keep_hand -> keep7_button.png
            if not candidate.exists() and target_name == "keep_hand":
                candidate = self.image_dir / "keep7_button.png"
            if candidate.exists() and self._image_search_available:
                try:
                    location = self._pyautogui.locateCenterOnScreen(str(candidate), confidence=self.confidence)
                except Exception as exc:
                    self._image_search_available = False
                    logger.warning("Image search disabled (screenshot backend missing?): %s", exc)
                    location = None
                if location:
                    logger.debug("Found %s via image at (%s, %s)", target_name, location.x, location.y)
                    self._click_absolute(location.x, location.y, label=target_name)
                    self.sleep_briefly()
                    return

        # Heuristic fallback positions for known targets.
        if target_name == "keep_hand":
            # Keep button sits bottom-right in mulligan view.
            self._click_relative(0.85, 0.93, label=target_name)
            return
        if target_name == "queue_button":
            self._click_relative(0.5, 0.75, label=target_name)
            return
        if target_name == "concede":
            self._click_relative(0.55, 0.6, label=target_name)
            return

        # Generic click near the center as a last resort.
        screen_size = self._pyautogui.size()
        x = screen_size.width * 0.5 + random.randint(-40, 40)
        y = screen_size.height * 0.75 + random.randint(-30, 30)
        self._click_absolute(x, y, label=f"target:{target_name}")
        self.sleep_briefly()

    def press_key(self, key: str) -> None:
        if self.dry_run:
            logger.info("[dry-run] Would press key %s", key)
            self.sleep_briefly()
            return

        # On Wayland, pyautogui keypresses may be blocked; prefer ydotool when available.
        self._maybe_pause_for_user_mouse()
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

        self._maybe_pause_for_user_mouse()
        # Casting is highly deck-specific; here we just click the hand area to play a card.
        self._ensure_pyautogui()
        screen_size = self._pyautogui.size()
        y = screen_size.height * self.hand_y_ratio + random.randint(-25, 25)
        # Click a couple of slots across the hand to improve reliability.
        rel_x_choices = list(self.hand_x_ratios)
        random.shuffle(rel_x_choices)
        for rel_x in rel_x_choices[:2]:
            x = screen_size.width * rel_x + random.randint(-40, 40)
            self._click_absolute(x, y, label="cast_spell")
            time.sleep(0.1)
        self.sleep_briefly()

    def play_land(self) -> None:
        if self.dry_run:
            logger.info("[dry-run] Would play a land from hand")
            self.sleep_briefly()
            return

        self._maybe_pause_for_user_mouse()
        # Lands usually sit on the left side of the hand; click a couple of nearby slots.
        self._ensure_pyautogui()
        screen_size = self._pyautogui.size()
        y = screen_size.height * self.land_y_ratio + random.randint(-20, 20)
        rel_x_choices = list(self.land_x_ratios)
        random.shuffle(rel_x_choices)
        for rel_x in rel_x_choices[:2]:
            x = screen_size.width * rel_x + random.randint(-30, 30)
            self._click_absolute(x, y, label="play_land")
            time.sleep(0.12)
        self.sleep_briefly()

    def surrender(self) -> None:
        if self.dry_run:
            logger.info("[dry-run] Would surrender the current match")
            self.sleep_briefly()
            return

        self._maybe_pause_for_user_mouse()
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
            if self._prefer_scrot and not os.environ.get("PYAUTOGUI_SCREENSHOT"):
                # scrot works on X11/Xwayland; harmless on Wayland if present.
                os.environ["PYAUTOGUI_SCREENSHOT"] = "scrot"
            self._pyautogui.PAUSE = 0
            self._pyautogui.FAILSAFE = False

    def _click_relative(self, rel_x: float, rel_y: float, label: Optional[str] = None) -> None:
        """Click at a relative screen position (0..1 in both axes) with slight jitter."""
        self._maybe_pause_for_user_mouse()
        if self.click_region:
            origin_x, origin_y, width, height = self.click_region
        else:
            screen_size = self._pyautogui.size()
            origin_x, origin_y, width, height = 0, 0, screen_size.width, screen_size.height

        jitter_x = random.randint(-10, 10)
        jitter_y = random.randint(-10, 10)
        x = origin_x + width * rel_x + jitter_x
        y = origin_y + height * rel_y + jitter_y
        self._click_absolute(x, y, label=label)
        # Extra down/up to ensure click is registered in windowed mode
        if not self.dry_run and not self._use_ydotool:
            try:
                self._pyautogui.mouseDown(x, y)
                time.sleep(0.05)
                self._pyautogui.mouseUp(x, y)
            except Exception:
                pass
        self.sleep_briefly()

    def _click_absolute(self, x: float, y: float, label: Optional[str] = None) -> None:
        """Perform a left-click at absolute screen coordinates, preferring ydotool on Wayland."""
        self._maybe_pause_for_user_mouse()
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
                self._record_bot_mouse_position(x, y, label=label)
                return
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("ydotool click failed at (%s,%s): %s (falling back to pyautogui)", x, y, exc)
                self._use_ydotool = False

        self._ensure_pyautogui()
        # Use a down/up sequence; .click can be ignored in some windowed/overlay scenarios.
        self._pyautogui.moveTo(x, y, duration=0)
        self._pyautogui.mouseDown()
        time.sleep(0.03)
        self._pyautogui.mouseUp()
        self._record_bot_mouse_position(x, y, label=label)

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
            logger.debug("ydotool key failed for %s (%s): %s (disabling ydotool)", key, code, exc)
            self._use_ydotool = False
            return False

    def _record_bot_mouse_position(self, x: float, y: float, label: Optional[str] = None) -> None:
        self._last_bot_move_time = time.time()
        self._last_known_mouse_pos = (int(x), int(y))
        if label:
            logger.debug("Clicked %s at (%.1f, %.1f)", label, x, y)

    def _maybe_pause_for_user_mouse(self) -> None:
        """Pause UI actions if manual mouse movement is detected."""
        while True:
            pause_remaining = self._user_pause_remaining()
            if pause_remaining <= 0:
                return
            time.sleep(pause_remaining)

    def _user_pause_remaining(self) -> float:
        now = time.time()
        if self._user_pause_until > now:
            return self._user_pause_until - now

        try:
            self._ensure_pyautogui()
        except Exception:
            return 0.0

        pos = self._pyautogui.position()
        current_pos = (int(pos.x), int(pos.y))
        if self._last_known_mouse_pos is None:
            self._last_known_mouse_pos = current_pos
            return 0.0

        moved = current_pos != self._last_known_mouse_pos
        self._last_known_mouse_pos = current_pos

        if moved and now - self._last_bot_move_time > self._bot_move_grace:
            self._user_pause_until = now + self._user_pause_seconds
            logger.info("User mouse movement detected; pausing UI actions for %.1f seconds", self._user_pause_seconds)
            return self._user_pause_seconds

        return 0.0

    @staticmethod
    def _normalize_target_overrides(raw: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        cleaned: dict[str, tuple[float, float]] = {}
        for key, value in raw.items():
            try:
                x, y = value
                cleaned[key] = (float(x), float(y))
            except Exception:
                continue
        return cleaned

    @staticmethod
    def _normalize_ratio_list(raw: Optional[list[float]], fallback: list[float]) -> list[float]:
        if not raw:
            return list(fallback)
        cleaned: list[float] = []
        for item in raw:
            try:
                cleaned.append(float(item))
            except Exception:
                continue
        return cleaned or list(fallback)
