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
        user_mouse_pause_seconds: float = 4.0,
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
        self._tried_start_ydotoold = False
        self._ydotoold_started = False
        self._autoguessed_region: Optional[tuple[int, int, int, int]] = None
        self._maybe_autoguess_click_region()
        if logger.isEnabledFor(logging.DEBUG):
            try:
                import pyautogui

                size = pyautogui.size()
                logger.debug(
                    "UIController init: screen=%sx%s click_region=%s image_dir=%s use_image_search=%s ydotool=%s",
                    size.width,
                    size.height,
                    self.click_region or "full-screen",
                    self.image_dir,
                    self._image_search_available,
                    self._use_ydotool,
                )
            except Exception as exc:
                logger.debug("UIController init: could not read screen size (%s)", exc)

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
        max_attempts = 2 if "keep_hand" not in self.target_overrides else 1
        logger.debug(
            "confirm_keep_hand: start attempts=%s override=%s",
            max_attempts,
            "keep_hand" in self.target_overrides,
        )

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

            # Only use extra fallbacks when no explicit override is configured.
            if "keep_hand" not in self.target_overrides:
                # Calibrated fallbacks near the Keep 7 button (bottom-right-ish but higher than before).
                self._click_relative_cluster(*rel_keep, label="keep_hand_fallback", cluster_px=6)
                self._click_relative_cluster(*rel_keep_alt, label="keep_hand_fallback_alt", cluster_px=6)

            # Extra safety: space/enter often work in MTGA dialogs.
            self.press_key("space")
            self.press_key("enter")

            # Small delay; MTGA often needs a moment to register mulligan choices.
            time.sleep(0.4)
        logger.debug("confirm_keep_hand: finished attempts=%s", max_attempts)

    def click_named_target(self, target_name: str) -> None:
        """
        Click a cached screenshot of a UI element if available, otherwise falls back to a generic click.
        """
        self._maybe_pause_for_user_mouse()
        if self.dry_run:
            override = self.target_overrides.get(target_name)
            if override:
                logger.info("[dry-run] Would click %s at rel (%s, %s)", target_name, *override)
            else:
                logger.info("[dry-run] Would click %s", target_name)
            self.sleep_briefly()
            return

        self._ensure_pyautogui()
        if target_name in self.target_overrides:
            rel_x, rel_y = self.target_overrides[target_name]
            cluster_px = 0 if target_name == "queue_button" else 10
            logger.debug(
                "Using configured target for %s at rel (%s, %s) with cluster_px=%s",
                target_name,
                rel_x,
                rel_y,
                cluster_px,
            )
            self._click_relative_cluster(rel_x, rel_y, label=target_name, cluster_px=cluster_px)
            return

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
            self._click_relative_cluster(0.85, 0.93, label=target_name, cluster_px=8)
            return
        if target_name == "queue_button":
            self._click_relative(0.5, 0.75, label=target_name, jitter_px=4)
            return
        if target_name == "concede":
            self._click_relative_cluster(0.55, 0.6, label=target_name, cluster_px=6)
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
        # Drag from hand into battlefield area to ensure spells leave the hand.
        self._ensure_pyautogui()
        origin_x, origin_y, width, height = self._resolve_click_region()
        start_y = origin_y + height * self.hand_y_ratio + random.randint(-25, 25)
        target_y = origin_y + height * 0.55 + random.randint(-30, 30)
        rel_x_choices = list(self.hand_x_ratios)
        if not rel_x_choices:
            rel_x_choices = [0.32, 0.4, 0.5, 0.6, 0.68]
        random.shuffle(rel_x_choices)
        for rel_x in rel_x_choices[:5]:
            start_x = origin_x + width * rel_x + random.randint(-40, 40)
            target_x = origin_x + width * 0.5 + random.randint(-60, 60)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "cast_spell drag from (%.1f, %.1f) -> (%.1f, %.1f) rel_x=%.3f region=%s",
                    start_x,
                    start_y,
                    target_x,
                    target_y,
                    rel_x,
                    (origin_x, origin_y, width, height),
                )
            self._drag_absolute(start_x, start_y, target_x, target_y, label="cast_spell")
            time.sleep(0.15)
        self.sleep_briefly()

    def play_land(self) -> None:
        if self.dry_run:
            logger.info("[dry-run] Would play a land from hand")
            self.sleep_briefly()
            return

        self._maybe_pause_for_user_mouse()
        # Lands: drag from hand into the battlefield to ensure play triggers.
        self._ensure_pyautogui()
        origin_x, origin_y, width, height = self._resolve_click_region()
        start_y = origin_y + height * self.land_y_ratio + random.randint(-20, 20)
        target_y = origin_y + height * 0.58 + random.randint(-25, 25)
        rel_x_choices = list(self.land_x_ratios)
        if not rel_x_choices:
            rel_x_choices = [0.32, 0.4, 0.5, 0.6, 0.68]
        # Try up to 5 distinct positions across the hand to cover variable hand sizes.
        random.shuffle(rel_x_choices)
        for rel_x in rel_x_choices[:5]:
            start_x = origin_x + width * rel_x + random.randint(-30, 30)
            target_x = origin_x + width * 0.45 + random.randint(-50, 50)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "play_land drag from (%.1f, %.1f) -> (%.1f, %.1f) rel_x=%.3f region=%s",
                    start_x,
                    start_y,
                    target_x,
                    target_y,
                    rel_x,
                    (origin_x, origin_y, width, height),
                )
            self._drag_absolute(start_x, start_y, target_x, target_y, label="play_land")
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

    def _click_relative(
        self,
        rel_x: float,
        rel_y: float,
        label: Optional[str] = None,
        jitter_px: int = 10,
        repeat_down_up: bool = True,
        sleep: bool = True,
        offset_px: tuple[int, int] = (0, 0),
    ) -> None:
        """Click at a relative screen position (0..1 in both axes) with configurable jitter."""
        self._maybe_pause_for_user_mouse()
        self._ensure_pyautogui()
        origin_x, origin_y, width, height = self._resolve_click_region()

        jitter_x = random.randint(-jitter_px, jitter_px) if jitter_px else 0
        jitter_y = random.randint(-jitter_px, jitter_px) if jitter_px else 0
        x = origin_x + width * rel_x + offset_px[0] + jitter_x
        y = origin_y + height * rel_y + offset_px[1] + jitter_y
        x = max(origin_x, min(origin_x + width - 1, x))
        y = max(origin_y, min(origin_y + height - 1, y))
        self._click_absolute(x, y, label=label)
        if repeat_down_up and not self.dry_run and not self._use_ydotool:
            try:
                self._pyautogui.mouseDown(x, y)
                time.sleep(0.05)
                self._pyautogui.mouseUp(x, y)
            except Exception:
                logger.debug("Secondary mouseDown/mouseUp failed at (%.1f, %.1f)", x, y)
        if sleep:
            self.sleep_briefly()

    def _click_relative_cluster(
        self, rel_x: float, rel_y: float, label: Optional[str] = None, cluster_px: int = 0
    ) -> None:
        """
        Click once on the target plus a small cross around it to absorb tiny misalignments.
        cluster_px=0 keeps the old single-click behaviour.
        """
        offsets = [(0, 0)]
        if cluster_px > 0:
            offsets.extend([(cluster_px, 0), (-cluster_px, 0), (0, cluster_px), (0, -cluster_px)])

        for idx, (dx, dy) in enumerate(offsets):
            # Only sleep once after the cluster to avoid long delays.
            self._click_relative(
                rel_x,
                rel_y,
                label=label if idx == 0 else label,
                jitter_px=0,
                repeat_down_up=True,
                sleep=False,
                offset_px=(dx, dy),
            )
            time.sleep(0.05)
        self.sleep_briefly()

    def _click_absolute(self, x: float, y: float, label: Optional[str] = None) -> None:
        """Perform a left-click at absolute screen coordinates, preferring ydotool on Wayland."""
        self._maybe_pause_for_user_mouse()
        if self._use_ydotool:
            self._maybe_start_ydotoold()
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
        try:
            # Use a down/up sequence; .click can be ignored in some windowed/overlay scenarios.
            self._pyautogui.moveTo(x, y, duration=0)
            self._pyautogui.mouseDown()
            time.sleep(0.03)
            self._pyautogui.mouseUp()
            self._record_bot_mouse_position(x, y, label=label)
        except Exception as exc:
            logger.warning(
                "pyautogui click failed at (%.1f, %.1f): %s. On Wayland, ensure X11 or ydotoold is running.",
                x,
                y,
                exc,
            )

    def _drag_absolute(self, start_x: float, start_y: float, end_x: float, end_y: float, label: Optional[str] = None) -> None:
        """Drag from start to end (pyautogui only; ydotool lacks drag)."""
        self._maybe_pause_for_user_mouse()
        self._ensure_pyautogui()
        try:
            self._pyautogui.moveTo(start_x, start_y, duration=0)
            self._pyautogui.mouseDown()
            self._pyautogui.dragTo(end_x, end_y, duration=0.15, button="left")
            self._pyautogui.mouseUp()
            self._record_bot_mouse_position(end_x, end_y, label=label or "drag")
        except Exception as exc:
            logger.debug("Drag failed (%s): %s", label or "drag", exc)

    def _press_key_with_ydotool(self, key: str) -> bool:
        """
        Attempt to send a keypress via ydotool (Wayland-friendly).
        Returns True if a command was issued.
        """
        if not self._ydotool:
            return False

        self._maybe_start_ydotoold()
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

    def _resolve_click_region(self) -> tuple[int, int, int, int]:
        """
        Resolve the current click anchor: either the configured region or the full screen.
        """
        if self.click_region:
            return self.click_region
        if self._autoguessed_region:
            return self._autoguessed_region
        screen_size = self._pyautogui.size()
        return (0, 0, screen_size.width, screen_size.height)

    def _maybe_start_ydotoold(self) -> None:
        """
        Best-effort attempt to launch ydotoold if available. Requires permissions/root.
        Controlled via MTGA_BOT_START_YDOTOOLD=1 (default). Silent failure if not permitted.
        """
        if self._tried_start_ydotoold or not self._ydotool:
            return
        self._tried_start_ydotoold = True
        if os.environ.get("MTGA_BOT_START_YDOTOOLD", "1") == "0":
            return
        daemon_cmd = os.environ.get("MTGA_BOT_YDOTOOLD", "ydotoold")
        try:
            subprocess.Popen(
                [daemon_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._ydotoold_started = True
            time.sleep(0.15)
            logger.debug("ydotoold start attempted via %s", daemon_cmd)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("ydotoold could not be started (%s): %s", daemon_cmd, exc)

    def _maybe_autoguess_click_region(self) -> None:
        """
        On ultra-wide screens without an explicit click_region, guess a centered 16:9 region.
        Can be disabled via MTGA_BOT_AUTOGUESS_REGION=0.
        """
        if self.click_region or os.environ.get("MTGA_BOT_AUTOGUESS_REGION", "1") == "0":
            return
        try:
            import pyautogui

            size = pyautogui.size()
        except Exception:
            return
        screen_w, screen_h = size.width, size.height
        aspect = screen_w / max(1, screen_h)
        if aspect < 2.0:
            return
        # Fit 16:9 into the current screen.
        target_w = int(screen_h * 16 / 9)
        target_h = screen_h
        if target_w > screen_w:
            target_w = screen_w
            target_h = int(screen_w * 9 / 16)
        origin_x = int((screen_w - target_w) / 2)
        origin_y = int((screen_h - target_h) / 2)
        self._autoguessed_region = (origin_x, origin_y, target_w, target_h)
        logger.info(
            "Auto-guessed click_region for ultrawide: x=%s y=%s w=%s h=%s (disable via MTGA_BOT_AUTOGUESS_REGION=0)",
            origin_x,
            origin_y,
            target_w,
            target_h,
        )

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
