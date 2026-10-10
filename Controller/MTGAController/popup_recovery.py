"""Recover persistent reward/continue overlays using their text, not button shape."""
import json
import os
import threading
import time

import bot_logger
from vision.vision import VisionEngine, cv2
from vision.window_locator import focus_mtga_window


class PopupRecoveryMixin:
    _POPUP_IDLE_SEC = 5.0
    _RECONNECT_IDLE_SEC = 120.0
    # Tight text captures from the reported seasonal-reward screen, normalized
    # to 1920x1080. Claim's lettering distinguishes it from orange Play pills.
    _POPUP_TARGETS = (
        ("reconnect_text.png", "POPUP_RECONNECT", (600, 430, 720, 300), 0.85),
        ("claim_rewards_text.png", "POPUP_CLAIM_REWARDS", (1300, 850, 620, 230), 0.85),
        ("claim_text.png", "POPUP_CLAIM", (1300, 850, 620, 230), 0.85),
        ("click_to_continue_text.png", "POPUP_CONTINUE", (520, 940, 880, 140), 0.70),
    )
    _POPUP_SCALES = tuple(round(0.45 + 0.05 * i, 2) for i in range(32))

    def _init_popup_recovery(self):
        self._popup_recovery_lock = threading.Lock()
        self._popup_recovery_stop = threading.Event()
        self._popup_recovery_thread = None
        self._popup_recovery_active = False
        # Keep captures independent of concurrent gameplay's per-tick cache.
        self._popup_vision = VisionEngine()
        self._popup_signature = None
        self._popup_since = 0.0
        self._popup_modal_present = False

    def _start_popup_recovery(self):
        # A controller constructed for tests/read-only scripts must never see
        # the screen merely because begin_session() was called.
        from Controller.Utilities.input_controller import NullInputController
        from runtime_paths import _running_under_test_runner
        if isinstance(self.input, NullInputController) or _running_under_test_runner():
            return
        if self._popup_recovery_thread is not None and self._popup_recovery_thread.is_alive():
            if not self._popup_recovery_stop.is_set():
                return
        self._popup_signature = None
        self._popup_recovery_active = True
        stop = threading.Event()
        self._popup_recovery_stop = stop

        def watch():
            while not stop.wait(1.0):
                if self._stop_requested:
                    return
                self._recover_blocking_popup(stop_event=stop)

        self._popup_recovery_thread = threading.Thread(
            target=watch, name="popup-recovery", daemon=True,
        )
        self._popup_recovery_thread.start()

    def _popup_progress_signature(self):
        # Mouse attempts, hover annotations, log traffic, and ticking ropes are
        # not progress. Actual game/scene/account changes restart the wait.
        game = self.updated_game_state.get_full_state()
        return json.dumps([
            str(self._get_state_from_log()),
            getattr(self, "_current_account_screen_name", None),
            {key: game.get(key) for key in ("turnInfo", "players", "gameObjects", "zones")},
        ], sort_keys=True, default=str)

    def _find_blocking_popup(self):
        self._popup_modal_present = False
        if cv2 is None:
            return None
        arena = self._ensure_arena_region(force_reacquire=True)
        detection = self._arena_region_provider.last_detection_result
        # Seasonal rewards and disconnect modals hide every normal UI anchor.
        # The OS still supplies a fresh, visible client rectangle. Use that
        # rectangle for text verification only, never stale cached geometry.
        if detection is not None:
            if detection.code in {"window_wrong_size", "window_off_screen", "window_not_found"}:
                return None
            if detection.code == "anchor_not_found" and detection.region is not None:
                arena = detection.region
        if arena is None:
            return None
        self._popup_vision.begin_tick()
        frame = self._popup_vision.capture(arena)
        if frame is None or not frame.size or frame.shape[:2] != (arena[3], arena[2]):
            return None
        image = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        # Text correlation also matches dimmed controls behind a modal. Detect
        # the disconnect message first and block all reward clicks until it is
        # gone, even if the Reconnect button itself cannot be recognized yet.
        disconnected = self._popup_vision.find_template(
            image[380:610, 300:1620],
            os.path.join(self._buttons_dir(), "disconnected_text.png"),
            threshold=0.85, scales=self._POPUP_SCALES,
        ) is not None
        self._popup_modal_present = disconnected
        for filename, label, roi, confidence in self._POPUP_TARGETS:
            if disconnected != (label == "POPUP_RECONNECT"):
                continue
            path = os.path.join(self._buttons_dir(), filename)
            if not os.path.exists(path):
                continue
            region = self._scale_base_region_to_arena(arena, roi)
            x, y, w, h = roi
            match = self._popup_vision.find_template(
                image[y:y + h, x:x + w], path, threshold=confidence, scales=self._POPUP_SCALES,
            )
            if match is not None:
                point = (region[0] + round(match.x * region[2] / roi[2]),
                         region[1] + round(match.y * region[3] / roi[3]))
                return label, point, tuple(arena)
        return None

    def _popup_may_act(self):
        return (getattr(self, "_popup_recovery_active", False) and not self._stop_requested
                and not self._popup_recovery_stop.is_set()
                and (not self._account_switch_in_progress
                     or self._switch_owner_ident == threading.get_ident()))

    def _recover_blocking_popup(self, *, stop_event=None, block_navigation=False):
        """Verified reward/continue clicks after 5s; Reconnect after 120s idle.

        Also called by Home navigation so the thread owning login/quest work
        can clear its own overlay. Never steal that thread's UI or an input
        transaction. Re-search under ownership before pressing anything.
        """
        def may_act():
            return self._popup_may_act() and (stop_event is None or not stop_event.is_set())

        if not may_act():
            return False
        if not self._popup_recovery_lock.acquire(False):
            # A competing recovery probe may be inspecting an overlay. Menu
            # navigation must yield rather than race that probe with a click.
            return block_navigation
        try:
            candidate = self._find_blocking_popup()
            if candidate is None:
                self._popup_signature = None
                return block_navigation and self._popup_modal_present
            if not may_act():
                return False
            label, point, arena = candidate
            signature = (label, arena, self._popup_progress_signature())
            now = time.monotonic()
            if signature != self._popup_signature:
                self._popup_signature, self._popup_since = signature, now
                return block_navigation
            idle_sec = self._RECONNECT_IDLE_SEC if label == "POPUP_RECONNECT" else self._POPUP_IDLE_SEC
            if now - self._popup_since < idle_sec:
                return block_navigation
            if not self._home_navigation_lock.acquire(False):
                return block_navigation
            try:
                if not self._Controller__decision_exec_lock.acquire(False):
                    return block_navigation
                try:
                    transaction = getattr(self.input, "input_transaction", None)
                    if not callable(transaction):
                        return False
                    with transaction(timeout=0.0) as acquired:
                        if not acquired or not may_act():
                            return block_navigation
                        if not focus_mtga_window():
                            return block_navigation
                        # Focus can change what is visible. No stale second click.
                        fresh = self._find_blocking_popup()
                        if (fresh is None or fresh[0] != label or fresh[2] != arena
                                or max(abs(a - b) for a, b in zip(point, fresh[1])) > 8
                                or self._popup_progress_signature() != signature[2]
                                or not may_act()):
                            self._popup_signature = None
                            return block_navigation
                        self._click_abs(*fresh[1], label)
                        self._popup_signature = None
                        bot_logger.log_info(f"{label}: confirmed popup persisted for {idle_sec:.0f}s without progress; clicked text.")
                        return True
                finally:
                    self._Controller__decision_exec_lock.release()
            finally:
                self._home_navigation_lock.release()
        except Exception as exc:
            bot_logger.log_error(f"Popup recovery failed: {exc}")
            return block_navigation
        finally:
            self._popup_recovery_lock.release()
