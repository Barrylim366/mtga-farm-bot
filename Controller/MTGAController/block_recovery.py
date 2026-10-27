"""Repair Arena's explicit insufficient-blocker warnings, without guessing keywords."""
import copy
import threading
import time

import AI.Utilities.CombatLogic as CombatLogic
import bot_logger


class BlockRecoveryMixin:
    def _init_block_recovery(self):
        self._block_condition = threading.Condition(threading.RLock())
        self._block_execution_lock = threading.Lock()
        self._block_context = None
        self._block_request = {}
        self._block_revision = 0
        self._block_msg_id = -1
        self._block_reserved = set()
        self._block_recovery_scheduled = False
        self._block_recovery_owner = None
        self._block_failed = False
        self._block_plan_id = 0

    def _reset_block_recovery(self):
        # Keep locks stable while a retired worker unwinds.
        if not hasattr(self, "_block_condition"):
            self._init_block_recovery()
            return
        with self._block_condition:
            self._block_context = None
            self._block_request = {}
            self._block_reserved.clear()
            self._block_recovery_scheduled = False
            self._block_recovery_owner = None
            self._block_failed = False
            self._block_plan_id += 1
            self._block_condition.notify_all()

    def _block_key(self):
        turn = self.updated_game_state.get_turn_info() or {}
        return (getattr(self, "_Controller__live_match_id", None),
                turn.get("turnNumber"), turn.get("phase"), turn.get("step"))

    def _block_live(self, context):
        return (not self._stop_requested and not self._suppress_selections
                and context is not None and bool(context[0])
                and context == self._block_context == self._block_key()
                and context[-1] == "Step_DeclareBlock")

    @staticmethod
    def _block_warnings(request):
        return sorted({w["instanceId"] for w in request.get("blockWarnings", [])
                       if w.get("type") == "BlockWarningType_InsufficientBlockers"
                       and w.get("instanceId") is not None})

    @staticmethod
    def _block_selected(request):
        return {(b["blockerInstanceId"], a)
                for b in request.get("blockers", [])
                for a in b.get("selectedAttackerInstanceIds", [])}

    def _observe_block_request(self, message):
        """Publish before throttling; the worker needs every accepted assignment."""
        if not hasattr(self, "_block_condition"):
            self._init_block_recovery()
        with self._block_condition:
            context = self._block_key()
            if context != self._block_context:
                self._block_context = context
                self._block_request = {}
                self._block_msg_id = -1
                self._block_reserved = set()
                self._block_failed = False
            msg_id = message.get("msgId")
            request = message.get("declareBlockersReq") or {}
            if msg_id is not None and msg_id <= self._block_msg_id:
                return
            if msg_id is not None:
                self._block_msg_id = msg_id
            # Duplicated dispatch of the same GRE message must not acknowledge a click.
            if request != self._block_request:
                self._block_request = copy.deepcopy(request)
                self._block_revision += 1
                self._block_condition.notify_all()
                warned = self._block_warnings(request)
                if warned:
                    bot_logger.log_info(f"BLOCK_RECOVERY_WARNING: insufficient blockers for {warned}")

    def _block_submission_blocked(self):
        if not hasattr(self, "_block_condition"):
            return False
        with self._block_condition:
            return (self._block_live(self._block_context or (None, None, None, None))
                    and (self._block_failed or bool(self._block_warnings(self._block_request))))

    def _schedule_block_recovery(self):
        with self._block_condition:
            if self._block_failed or self._block_recovery_scheduled:
                return
            self._block_recovery_scheduled = True
            owner = self._block_recovery_owner = object()
            self._block_plan_id += 1
            context = self._block_context
        self._Controller__begin_declare_blocks_pause()

        def recover():
            try:
                # A warning can arrive while the ordinary assignment worker owns
                # this lock. That worker also repairs before submitting.
                with self._block_execution_lock:
                    if (self._block_recovery_owner is owner
                            and self._block_live(context) and self._block_submission_blocked()):
                        self._finish_block_assignment("SUBMIT_BLOCKS", context)
            except Exception as exc:
                bot_logger.log_error(f"BLOCK_RECOVERY_FAILED: {exc}")
                if self._block_recovery_owner is owner and self._block_live(context):
                    self._block_failed = True
                    self._write_declare_block_debug_bundle(
                        shadow={"reason": "block_recovery_exception", "error": str(exc)},
                        executing=False, force=True)
            finally:
                with self._block_condition:
                    # Reset can retire us even before worker_target invokes us.
                    # Only the current owner may clear or rearm recovery state.
                    if self._block_recovery_owner is owner:
                        self._block_recovery_scheduled = False
                        self._block_recovery_owner = None
                        if context == self._block_context:
                            self._Controller__end_declare_blocks_pause()
                        elif self._block_submission_blocked() and not self._block_failed:
                            # A new combat may arrive while the worker unwinds.
                            self._schedule_block_recovery()

        self._input_thread(target=recover, name="block-recovery", daemon=True).start()

    def _wait_block_request(self, revision, predicate, context, timeout):
        deadline = time.monotonic() + max(0, timeout)
        with self._block_condition:
            while self._block_live(context):
                if self._block_revision > revision and predicate(self._block_request):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._block_condition.wait(min(remaining, 0.1))
        return False

    def _block_recovery_record(self, move, data):
        bot_logger.log_info(f"BLOCK_RECOVERY: {move} {data}")
        self._Controller__record_decision("blockers", move, data)

    def _cancel_invalid_blocks(self, context, reason):
        def needed():
            return (self._block_live(context)
                    and bool(self._block_warnings(self._block_request)))
        if not needed():
            return False
        self._block_recovery_record("cancel_blocks", {"reason": reason})
        if not self._block_live(context):
            return False
        deadline = time.monotonic() + 5.0
        with self._block_condition:
            revision = self._block_revision
        clicked = self._click_image_in_scaled_arena_region(
            "Buttons/cancel_blocks.png", "CANCEL_BLOCKS",
            rel_region=(1500, 780, 420, 150), confidence=0.55, timeout=1.5,
            action_guard=needed,
        )
        if clicked and self._wait_block_request(
                revision, lambda req: not self._block_selected(req)
                and not self._block_warnings(req), context,
                deadline - time.monotonic()):
            self._block_reserved.clear()
            bot_logger.log_info("BLOCK_RECOVERY_CANCEL_ACKNOWLEDGED: assignments cleared")
            return True
        if not clicked and not needed():
            bot_logger.log_info("BLOCK_RECOVERY_OBSOLETE: warning cleared before Cancel")
            return False
        if self._block_live(context):
            self._block_failed = True
            bot_logger.log_error("BLOCK_RECOVERY_FAILED: cancellation not confirmed; leaving to watchdog")
            self._write_declare_block_debug_bundle(
                shadow={"reason": "block_recovery_failed", "request": self._block_request},
                executing=False, force=True,
            )
        return False

    def _repair_blocks(self, context, deadline):
        attempted = set()
        while self._block_live(context):
            with self._block_condition:
                request = copy.deepcopy(self._block_request)
                revision = self._block_revision
            warned = self._block_warnings(request)
            if not warned:
                return "SUBMIT_BLOCKS"
            reason = "no unused legal blocker"
            if time.monotonic() < deadline:
                selected = {b for b, _ in self._block_selected(request)}
                board_context = self._Controller__combat_board_context()
                board = CombatLogic.index_by_instance(
                    (board_context or {}).get("game_objects", []),
                    (board_context or {}).get("live_instance_ids"))
                attacker = warned[0]
                candidates = [b["blockerInstanceId"] for b in request.get("blockers", [])
                              if attacker in b.get("attackerInstanceIds", [])
                              and b["blockerInstanceId"] not in
                              (selected | self._block_reserved | attempted)
                              and b["blockerInstanceId"] in board]
                if candidates:
                    blocker = min(candidates, key=lambda b: (CombatLogic.creature_value(board[b]), b))
                    def needed():
                        # Read the live request, not the snapshot which chose this
                        # candidate. Normal assignment may still be acknowledged.
                        with self._block_condition:
                            latest = self._block_request
                            return (self._block_live(context)
                                    and attacker in self._block_warnings(latest)
                                    and not any(b == blocker for b, _ in self._block_selected(latest))
                                    and any(b.get("blockerInstanceId") == blocker
                                            and attacker in b.get("attackerInstanceIds", [])
                                            for b in latest.get("blockers", [])))
                    if not needed():
                        continue
                    attempted.add(blocker)
                    self._block_recovery_record("add_blocker", {"blocker": blocker, "attacker": attacker})
                    if self.select_battlefield_permanent(
                            blocker, max_scan_sec=min(3.5, max(0, deadline - time.monotonic())),
                            still_active=needed):
                        if not self._block_live(context):
                            return None
                        if not needed():
                            bot_logger.log_info("BLOCK_RECOVERY_OBSOLETE: assignment changed during selection")
                            continue
                        if self.select_attacking_creature(
                                attacker, max_scan_sec=min(3.5, max(0, deadline - time.monotonic())),
                                still_active=needed):
                            if self._wait_block_request(
                                    revision, lambda req: (blocker, attacker) in self._block_selected(req)
                                    or attacker not in self._block_warnings(req),
                                    context, min(2.0, max(0, deadline - time.monotonic()))):
                                bot_logger.log_info("BLOCK_RECOVERY_ASSIGNMENT_ACKNOWLEDGED: fresh request advanced")
                                continue
                            reason = "extra assignment not confirmed"
                        else:
                            if not needed():
                                continue
                            # Drop the half-selection before looking for Cancel Blocks.
                            if self._block_live(context):
                                self.input.tap_escape()
                            reason = "attacker selection failed"
                    else:
                        if not needed():
                            continue
                        reason = "blocker selection failed"
            else:
                reason = "recovery budget exhausted"
            if not self._block_warnings(self._block_request):
                return "SUBMIT_BLOCKS"
            if self._cancel_invalid_blocks(context, reason):
                return "NO_BLOCKS"
            if self._block_live(context) and not self._block_warnings(self._block_request):
                return "SUBMIT_BLOCKS"
            return None
        return None

    def _finish_block_assignment(self, label, context=None):
        if context is not None and not self._block_live(context):
            return
        # No observed request is normal in isolated executor tests.
        if not hasattr(self, "_block_condition") or self._block_context is None:
            self._Controller__click_combat_submit_button(label)
            return
        context = context or self._block_context
        deadline = time.monotonic() + 12.0
        while self._block_live(context) and not self._block_failed:
            if self._block_warnings(self._block_request):
                self._Controller__begin_declare_blocks_pause()
                label = self._repair_blocks(context, deadline)
                if label is None:
                    return
            self._Controller__click_combat_submit_button(label)
            # A warning arriving during verified submission aborts its retries.
            if not self._block_warnings(self._block_request):
                return
