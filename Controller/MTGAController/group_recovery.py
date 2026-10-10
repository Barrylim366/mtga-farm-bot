"""Verified Scry/Surveil completion."""
import os
import sys
import time

import bot_logger
from vision.window_locator import focus_mtga_window


class GroupRecoveryMixin:
    _GROUP_DONE_ATTEMPTS = 3
    _GROUP_CHECK_LIMIT = 16
    _GROUP_SETTLE_SEC = 1.2

    def _group_prompt_blocks_gameplay(self):
        return (getattr(self, "_group_prompt", None) is not None
                or time.time() < getattr(self, "_Controller__group_req_active_until", 0.0))

    def _clear_group_prompt(self):
        self._group_prompt = None
        self._Controller__group_req_active_until = 0.0

    def _group_prompt_may_act(self, prompt):
        return (getattr(self, "_group_prompt", None) is prompt
                and not self._stop_requested and not self._suppress_selections
                and self._Controller__group_prompt_seq == prompt["seq"]
                and self._Controller__group_prompt_match_id == prompt["match_id"]
                and (self._Controller__live_match_id or self._Controller__last_seen_match_id)
                == prompt["match_id"])

    def _observe_group_overlay(self, prompt):
        """Unknown captures never prove closure. Match only the central Done."""
        import cv2

        arena = self._get_ui_action_arena_region(label="GROUP_RECOVERY")
        if arena is None or self._vision is None:
            return "unknown", None, None, {}
        self._vision.begin_tick()
        frame = self._vision.capture(arena)
        if frame is None or getattr(frame, "size", 0) == 0:
            return "unknown", None, None, {}
        image = cv2.resize(frame, (1920, 1080))
        if float(image.std()) < 3.0:
            return "unknown", None, frame, {"arena": list(arena), "blank_capture": True}
        done = self._vision.find_template(
            image[820:1060, 700:1220],
            os.path.join(self._buttons_dir(), "scry_done.png"), threshold=0.78,
        )
        heading = None
        if "surveil" in prompt["context"].lower():
            heading = self._vision.find_template(
                image[35:145, 700:1220],
                os.path.join(self._buttons_dir(), "surveil_heading.png"), threshold=0.78,
            )
        details = {"arena": list(arena), "done_roi": [700, 820, 520, 240],
                   "heading_roi": [700, 35, 520, 110], "threshold": 0.78,
                   "done_score": getattr(done, "score", None),
                   "heading_score": getattr(heading, "score", None)}
        if done is not None:
            point = self._map_base_point_into_arena(arena, (700 + done.x, 820 + done.y))
            return "open", point, frame, details
        if heading is not None:
            # Fixed Surveil fallback is permitted only with a visible heading.
            point = self._map_base_point_into_arena(arena, (960, 925))
            details["fixed_fallback"] = True
            return "open", point, frame, details
        return "absent", None, frame, details

    def _recover_group_prompt(self):
        """One observation/press, then a timer allows animation to settle."""
        prompt = getattr(self, "_group_prompt", None)
        if prompt is None:
            return True
        if not self._group_prompt_may_act(prompt):
            return False
        if not self._group_recovery_lock.acquire(False):
            self._Controller__schedule_group_resume(self._GROUP_SETTLE_SEC)
            return False
        try:
            if time.monotonic() < prompt["next_check"]:
                self._Controller__schedule_group_resume(self._GROUP_SETTLE_SEC)
                return False
            if not self._Controller__decision_exec_lock.acquire(False):
                self._Controller__schedule_group_resume(self._GROUP_SETTLE_SEC)
                return False
            try:
                with self.input.input_transaction(timeout=0.0) as acquired:
                    if not acquired or not self._group_prompt_may_act(prompt):
                        self._Controller__schedule_group_resume(self._GROUP_SETTLE_SEC)
                        return False
                    # This focus helper only supports Windows. Other platforms
                    # still verify the visible overlay before sending input.
                    if sys.platform == "win32" and not focus_mtga_window():
                        state, point, frame, details = "unknown", None, None, {}
                    else:
                        state, point, frame, details = self._observe_group_overlay(prompt)
                    if not self._group_prompt_may_act(prompt):
                        return False
                    prompt["checks"] += 1
                    if state == "absent" and prompt["seen"]:
                        prompt["absent_samples"] += 1
                        if prompt["absent_samples"] >= 2:
                            self._clear_group_prompt()
                            bot_logger.log_info("GROUP_RECOVERY_CLOSED: overlay absent in two settled observations.")
                            return True
                    else:
                        prompt["absent_samples"] = 0
                    if state == "open":
                        prompt["seen"] = True
                        budget = self._GROUP_DONE_ATTEMPTS + int(prompt["watchdog_used"])
                        if prompt["clicks"] < budget and point is not None:
                            prompt["clicks"] += 1
                            # Own input from capture through move/down/up. Recheck
                            # the prompt so a retired callback cannot press.
                            if self._group_prompt_may_act(prompt):
                                self._click_abs(*point, "GROUP_DONE")
                                bot_logger.log_info(
                                    f"GROUP_RECOVERY_DONE: context={prompt['context']} attempt={prompt['clicks']} point={point}"
                                )
                    prompt["next_check"] = time.monotonic() + self._GROUP_SETTLE_SEC
            finally:
                self._Controller__decision_exec_lock.release()
            if prompt["checks"] < self._GROUP_CHECK_LIMIT:
                self._Controller__schedule_group_resume(self._GROUP_SETTLE_SEC)
            else:
                bot_logger.log_error("GROUP_RECOVERY_UNCONFIRMED: keeping gameplay paused; bounded checks exhausted.")
            return False
        except Exception as exc:
            # Recovery errors must never open the gameplay gate.
            prompt["checks"] += 1
            if prompt["checks"] < self._GROUP_CHECK_LIMIT and self._group_prompt_may_act(prompt):
                self._Controller__schedule_group_resume(self._GROUP_SETTLE_SEC)
            bot_logger.log_error(f"GROUP_RECOVERY_FAILED: {exc}")
            return False
        finally:
            self._group_recovery_lock.release()

    def _group_watchdog_recovery(self):
        """One final recovery opportunity, without resetting the stall age."""
        prompt = getattr(self, "_group_prompt", None)
        if prompt is None or prompt["watchdog_used"] or not self._group_prompt_may_act(prompt):
            return False
        prompt["watchdog_used"] = True
        # Permit the final press plus two closure observations, even after the
        # ordinary checking budget was exhausted.
        prompt["checks"] = min(prompt["checks"], self._GROUP_CHECK_LIMIT - 3)
        self._Controller__schedule_group_resume(0.01)
        bot_logger.log_info("GROUP_WATCHDOG_RECOVERY: one final Done attempt before concession.")
        return True
