"""Verified Scry/Surveil completion and simple pile choices."""
import os
import sys
import time

import bot_logger
from vision.window_locator import focus_mtga_window


class GroupRecoveryMixin:
    _GROUP_DONE_ATTEMPTS = 3
    _GROUP_CHECK_LIMIT = 16
    _GROUP_SETTLE_SEC = 1.2

    def _observe_pile_choice_payload(self, payload):
        """Arm before ordinary decisions, and retire on server-confirmed progress."""
        tracker = getattr(self, "_Controller__decision_watchdog", None)
        pending = tracker.pending if tracker is not None else None
        prompt = getattr(self, "_group_prompt", None)
        if (prompt is not None and prompt.get("kind") == "selectngroup"
                and pending is not prompt["watchdog_request"]):
            self._clear_group_prompt()
            self._Controller__group_prompt_seq += 1
            timer = self._Controller__group_resume_timer
            if timer is not None:
                timer.cancel()
                self._Controller__group_resume_timer = None
            bot_logger.log_info("PILE_CHOICE_CLOSED: server advanced the pending decision.")
        if self._stop_requested or self._suppress_selections:
            return
        seat = self._Controller__system_seat_id
        match_id = self._Controller__live_match_id
        if seat is None or not match_id or not self.can_execute_game_action(match_id):
            return
        messages = payload.get("greToClientEvent", {}).get("greToClientMessages", [])
        for message in messages:
            if (message.get("type") != "GREMessageType_SelectNGroupReq"
                    or seat not in (message.get("systemSeatIds") or [])):
                continue
            req = message.get("selectNGroupReq") or {}
            if req.get("minSel") != 1 or req.get("maxSel") != 1:
                bot_logger.log_info("PILE_CHOICE_UNSUPPORTED: only single-pile selection is implemented.")
                continue
            groups = req.get("groups") or []
            if not groups or pending is None or pending.kind != "selectngroup":
                continue
            key = (match_id, req.get("sourceId"), message.get("gameStateId"),
                   tuple(g.get("groupId") for g in groups))
            current = getattr(self, "_group_prompt", None)
            if (key == getattr(self, "_last_pile_choice_key", None)
                    or (current is not None and current.get("kind") == "selectngroup"
                        and current["watchdog_request"] is pending
                        and current["group_ids"] == key[3])):
                continue
            self._last_pile_choice_key = key
            self._Controller__group_prompt_seq += 1
            self._Controller__group_prompt_match_id = match_id
            self._Controller__group_req_active_until = 0.0
            self._group_prompt = {
                "kind": "selectngroup", "context": "pile choice",
                "seq": self._Controller__group_prompt_seq, "match_id": match_id,
                "group_ids": key[3],
                "watchdog_request": pending, "clicks": 0, "checks": 0,
                "seen": False, "absent_samples": 0, "last_click": float("-inf"),
                "next_check": time.monotonic() + 0.8,
            }
            bot_logger.log_info(f"PILE_CHOICE_ARMED: source={req.get('sourceId')} groups={groups}")
            self._Controller__record_decision("select_n_group", "choose_any", req)
            self._Controller__schedule_group_resume(0.8)

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
                == prompt["match_id"]
                and (prompt.get("kind") != "selectngroup"
                     or (self.can_execute_game_action(prompt["match_id"])
                         and self._Controller__decision_watchdog.pending
                         is prompt["watchdog_request"])))

    def _observe_group_overlay(self, prompt):
        """Unknown captures never prove closure. Match Done or pile Choose."""
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
        if prompt.get("kind") == "selectngroup":
            choose = self._vision.find_template(
                image[820:1030, 150:1770],
                os.path.join(self._buttons_dir(), "pile_choose.png"), threshold=0.82,
                scales=(0.9, 0.95, 1.0, 1.05, 1.1),
            )
            details = {"arena": list(arena), "choose_score": getattr(choose, "score", None)}
            if choose is None:
                return "absent", None, frame, details
            point = self._map_base_point_into_arena(arena, (150 + choose.x, 820 + choose.y))
            return "open", point, frame, details
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
        is_pile = prompt.get("kind") == "selectngroup"
        interval = 1.0 if is_pile else self._GROUP_SETTLE_SEC
        if is_pile and prompt["checks"] >= self._GROUP_CHECK_LIMIT:
            return False
        if not self._group_prompt_may_act(prompt):
            return False
        if not self._group_recovery_lock.acquire(False):
            self._Controller__schedule_group_resume(self._GROUP_SETTLE_SEC)
            return False
        try:
            if time.monotonic() < prompt["next_check"]:
                self._Controller__schedule_group_resume(interval)
                return False
            decision_lock = self._Controller__decision_exec_lock
            if not decision_lock.acquire(False):
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
                            tracker = getattr(self, "_Controller__decision_watchdog", None)
                            kind = "selectngroup" if is_pile else "group"
                            if tracker is not None and tracker.pending is not None and tracker.pending.kind == kind:
                                with self._Controller__stall_watchdog_lock:
                                    tracker.clear("group overlay visibly closed")
                                    self._Controller__update_stall_watchdog()
                            bot_logger.log_info(f"{'PILE_CHOICE' if is_pile else 'GROUP_RECOVERY'}_CLOSED: overlay absent in two settled observations.")
                            return True
                    else:
                        prompt["absent_samples"] = 0
                    if state == "open":
                        prompt["seen"] = True
                        budget = self._GROUP_DONE_ATTEMPTS
                        ready = not is_pile or time.monotonic() - prompt["last_click"] >= 2.0
                        if prompt["clicks"] < budget and point is not None and ready:
                            prompt["clicks"] += 1
                            # Own input from capture through move/down/up. Recheck
                            # the prompt so a retired callback cannot press.
                            if self._group_prompt_may_act(prompt):
                                prompt["last_click"] = time.monotonic()
                                self._click_abs(*point, "PILE_CHOOSE" if is_pile else "GROUP_DONE")
                                label = "PILE_CHOICE_CLICK" if is_pile else "GROUP_RECOVERY_DONE"
                                bot_logger.log_info(
                                    f"{label}: context={prompt['context']} attempt={prompt['clicks']} point={point}"
                                )
                    if is_pile:
                        bot_logger.log_info(f"PILE_CHOICE_CHECK: state={state} checks={prompt['checks']} clicks={prompt['clicks']} details={details}")
                    prompt["next_check"] = time.monotonic() + interval
            finally:
                decision_lock.release()
            if prompt["checks"] < self._GROUP_CHECK_LIMIT:
                self._Controller__schedule_group_resume(interval)
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
