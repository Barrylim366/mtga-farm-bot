"""Visible confirmation and recovery for a selected permanent's ability."""
from datetime import datetime
import json
from pathlib import Path

import bot_logger


class AbilityActivationMixin:
    def click_ability_control(self, control, *, action_guard):
        """Click a recognized control; never guess a card or button position."""
        if control not in ("activate", "cancel"):
            raise ValueError(control)
        if not action_guard():
            return False
        with self.input.input_transaction(timeout=0.0) as acquired:
            if not acquired or not action_guard():
                return False
            # A Cancel elsewhere in Arena is not evidence of our activation UI.
            # Food's "Sacrifice Food" confirmation must qualify here too, or
            # failure to recognise it would prevent both activation and recovery.
            if control == "cancel" and self._locate_ability_control("activate") is None:
                return False
            point = self._locate_ability_control(control)
            if point is None or not action_guard():
                return False
            label = f"ABILITY_{control.upper()}"
            if control == "activate" and getattr(self, "_last_ability_activation_control", "") == "sacrifice_food":
                label = "ABILITY_ACTIVATE_SACRIFICE_FOOD"
            self._click_abs(*point, label)
            return True

    def _locate_ability_control(self, control):
        # Templates are in the same 1920x1080 reference frame as the matcher.
        # The confirmation floats above the source; Cancel is bottom-right.
        region = (0, 180, 1920, 600) if control == "activate" else (1500, 810, 420, 240)
        variants = ("activate", "sacrifice_food") if control == "activate" else (control,)
        for variant in variants:
            point = self._locate_image_center_in_scaled_arena_region(
                f"Buttons/ability_{variant}.png", f"ABILITY_{variant.upper()}",
                rel_region=region, confidence=0.88, timeout=0.1, use_direct=False,
                scales=(0.9, 1.0, 1.1),
            )
            if point is not None:
                if control == "activate":
                    self._last_ability_activation_control = variant
                return point
        return None

    def write_ability_activation_debug(self, context):
        """Preserve failure evidence before attempting to dismiss the interface."""
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            folder = Path(bot_logger.ensure_debug_dir(f"ability-activation-{stamp}"))
            (folder / "activation.json").write_text(
                json.dumps(context, indent=2, default=str), encoding="utf-8",
            )
            arena = self._get_ui_action_arena_region(label="ABILITY_DEBUG")
            if arena is not None:
                self._vision.begin_tick()
                self._vision.save_image(self._vision.capture(arena), str(folder / "arena.png"))
            bot_logger.log_error(f"ABILITY_ACTIVATION_UNACKNOWLEDGED: {folder}")
        except Exception as exc:
            bot_logger.log_error(f"ABILITY_ACTIVATION_DEBUG_FAILED: {exc}")
