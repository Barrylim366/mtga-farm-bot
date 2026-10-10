"""Payment fallback and lock-contention replay; all input and vision are mocked."""
import os
import json
import threading
import unittest
from unittest.mock import Mock, patch

from Controller.MTGAController.Controller import Controller


class AutoPayTest(unittest.TestCase):
    def setUp(self):
        c = self.c = Controller.__new__(Controller)
        c._Controller__live_match_id = "match"
        c._Controller__pending_pay_costs_ts = 100.0
        c._Controller__pending_pay_costs_submit_state_id = None
        c._Controller__pending_target_select = None
        c._Controller__submit_selection_lock = threading.Lock()
        c._Controller__read_game_state_id = lambda: 141
        c._Controller__selection_submit_allowed = lambda: True
        c._stop_requested = False
        c.can_execute_game_action = lambda match: match == "match" and not c._stop_requested
        c._buttons_dir = lambda: "Buttons"
        c._locate_image_center = Mock(return_value=None)
        c._locate_image_center_in_scaled_arena_region = Mock(side_effect=self.locate)
        c._click_image_in_scaled_arena_region = Mock(return_value=False)
        c._click_abs = Mock()
        c.input = Mock()
        self.callbacks = []
        self.addCleanup(patch.stopall)
        patch("Controller.MTGAController.Controller.threading.Timer", self.timer).start()
        patch("Controller.MTGAController.Controller.bot_logger.log_info").start()
        patch("Controller.MTGAController.Controller.bot_logger.log_error").start()

    def locate(self, path, label, **kwargs):
        return (3100, 1100) if os.path.basename(path) == "auto_pay_text.png" else None

    def timer(self, delay, callback, args=(), kwargs=None):
        callbacks = self.callbacks
        class Timer:
            def start(self):
                callbacks.append(lambda: callback(*args, **(kwargs or {})))
        return Timer()

    def pay(self, **kwargs):
        return self.c.submit_selection(reason="pay_costs_auto_submit", force=True,
                                       allow_okay_fallback=False, **kwargs)

    def test_missing_submit_clicks_auto_pay_and_keeps_pause_until_ack(self):
        self.assertTrue(self.pay())
        self.c._click_abs.assert_called_once_with(3100, 1100, "PAY_COSTS_AUTO_PAY")
        self.assertEqual(self.c._Controller__pending_pay_costs_submit_state_id, 141)
        self.assertEqual(self.c._Controller__pending_pay_costs_ts, 100.0)
        self.c._Controller__maybe_clear_pending_pay_costs()
        self.assertEqual(self.c._Controller__pending_pay_costs_ts, 100.0)
        self.c._Controller__read_game_state_id = lambda: 142
        self.c._Controller__maybe_clear_pending_pay_costs()
        self.assertEqual(self.c._Controller__pending_pay_costs_ts, 0.0)

    def test_ordinary_target_submission_never_looks_for_auto_pay(self):
        self.assertFalse(self.c.submit_selection(reason="target_selection_ready"))
        labels = [call.args[1] for call in
                  self.c._locate_image_center_in_scaled_arena_region.call_args_list]
        self.assertNotIn("PAY_COSTS_AUTO_PAY", labels)
        self.c._click_abs.assert_not_called()

    def test_submit_still_takes_precedence(self):
        self.c._locate_image_center_in_scaled_arena_region.side_effect = None
        self.c._locate_image_center_in_scaled_arena_region.return_value = (3000, 1000)
        self.assertTrue(self.pay())
        self.c._click_abs.assert_called_once_with(3000, 1000, "SUBMIT_SELECTION_IMG")

    def test_busy_target_search_retries_payment_after_release(self):
        lock = self.c._Controller__submit_selection_lock
        lock.acquire()
        self.assertFalse(self.pay())
        self.assertEqual(len(self.callbacks), 1)
        self.c._click_abs.assert_not_called()
        lock.release()
        self.callbacks.pop(0)()
        self.c._click_abs.assert_called_once_with(3100, 1100, "PAY_COSTS_AUTO_PAY")

    def test_retry_stops_after_payment_prompt_changes(self):
        lock = self.c._Controller__submit_selection_lock
        lock.acquire()
        self.pay()
        lock.release()
        self.c._Controller__pending_pay_costs_ts = 101.0
        self.callbacks.pop(0)()
        self.c._click_abs.assert_not_called()
        self.assertFalse(self.callbacks)

    def queue_retry(self):
        lock = self.c._Controller__submit_selection_lock
        lock.acquire()
        self.pay()
        lock.release()

    def test_retry_stops_after_stop(self):
        self.queue_retry()
        self.c._stop_requested = True
        self.callbacks.pop(0)()
        self.c._click_abs.assert_not_called()
        self.assertFalse(self.callbacks)

    def test_retry_stops_after_match_change(self):
        self.queue_retry()
        self.c._Controller__live_match_id = "next-match"
        self.c.can_execute_game_action = lambda match: match == "next-match"
        self.callbacks.pop(0)()
        self.c._click_abs.assert_not_called()
        self.assertFalse(self.callbacks)

    def test_mana_request_schedules_payment_for_original_prompt(self):
        self.c._Controller__system_seat_id = 1
        payload = {"greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_PayCostsReq", "systemSeatIds": [1],
            "payCostsReq": {"manaCost": [{"color": ["ManaColor_Red"], "count": 1}]},
        }]}}
        self.c._Controller__handle_pay_costs_req(json.dumps(payload))
        self.assertEqual(len(self.callbacks), 1)
        self.callbacks.pop(0)()
        self.c._click_abs.assert_called_once_with(3100, 1100, "PAY_COSTS_AUTO_PAY")

    def test_initial_delayed_payment_stops_if_prompt_already_resolved(self):
        self.c._Controller__handle_pay_costs_req("no payload")
        self.c._Controller__pending_pay_costs_ts = 0.0
        self.callbacks.pop(0)()
        self.c._click_abs.assert_not_called()

    def test_lock_retries_are_bounded(self):
        lock = self.c._Controller__submit_selection_lock
        lock.acquire()
        try:
            self.pay()
            count = 0
            while self.callbacks:
                self.callbacks.pop(0)()
                count += 1
                self.assertLessEqual(count, 80)
            self.assertEqual(count, 80)
            self.c._click_abs.assert_not_called()
        finally:
            lock.release()

    def test_prompt_changes_during_search_prevents_click(self):
        def locate(path, label, **kwargs):
            if label == "PAY_COSTS_AUTO_PAY":
                self.c._Controller__pending_pay_costs_ts = 0.0
                return (3100, 1100)
            return None
        self.c._locate_image_center_in_scaled_arena_region.side_effect = locate
        self.assertFalse(self.pay())
        self.c._click_abs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
