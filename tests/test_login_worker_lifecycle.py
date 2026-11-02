"""Home/login transitions must not cancel the worker that resumes queueing."""
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.input_controller import ExclusiveInputController, NullInputController
from state.state_machine import BotState
from Game import Game


class LoginWorkerLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.c = Controller.__new__(Controller)
        self.c.input = ExclusiveInputController(NullInputController())
        self.c._Controller__live_match_id = None
        self.c._Controller__last_seen_match_id = None
        self.c._Controller__retired_match_id = None
        self.c._Controller__group_prompt_seq = 0
        self.c._clear_group_prompt = Mock()
        self.c._Controller__clear_stall_watchdog = Mock()
        self.c._stop_requested = False
        self.c._account_switch_in_progress = False
        self.c._switch_start_lock = threading.RLock()
        self.c._queue_spam_thread = None
        self.c.check_play_time_limit = Mock(return_value=False)
        self.c._get_state_from_log = Mock(return_value=BotState.HOME)
        self.queued = threading.Event()
        self.c._queue_spam_loop = self.queued.set

    def main_nav_on_log_thread(self):
        callback = threading.Thread(target=self.c._Controller__leave_live_match, args=("MainNav",))
        callback.start()
        callback.join(2)
        self.assertFalse(callback.is_alive())

    def test_login_main_nav_allows_switch_worker_to_start_queue(self):
        def finish_login():
            # Player.log's callback runs separately while the switch waits for
            # login. Its retirement must not invalidate this worker's children.
            self.main_nav_on_log_thread()
            self.c.start_queueing()

        with patch("Controller.MTGAController.Controller.runtime_status"):
            switch = self.c._input_thread(target=finish_login)
            switch.start()
            switch.join(2)
            self.assertFalse(switch.is_alive())
            self.assertTrue(self.queued.wait(1), "post-login queue worker was cancelled")
            self.c._queue_spam_thread.join(2)

    def test_match_departure_cancels_gameplay_but_repeated_home_keeps_menu_worker(self):
        old_action = Mock()
        old_worker = self.c.input.worker_target(old_action)
        self.c._Controller__live_match_id = "finished-match"
        self.main_nav_on_log_thread()
        old_worker()
        old_action.assert_not_called()

        menu_action = Mock()
        menu_worker = self.c.input.worker_target(menu_action)
        self.main_nav_on_log_thread()
        menu_worker()
        menu_action.assert_called_once()
        self.assertEqual(self.c._Controller__retired_match_id, "finished-match")


class PostMatchWorkerLifecycleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        log = Path(directory.name) / "Player.log"
        log.write_text("")
        self.c = Controller(str(log), input_backend="null", account_switch_enabled=False)
        self.c.input = ExclusiveInputController(NullInputController())
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = Mock(return_value=False)
        self.c._get_state_from_log = Mock(return_value=BotState.HOME)
        self.c.start_queueing = Mock()

    def test_restart_preserves_delayed_post_match_queue(self):
        self.c._post_match_ready_ts = 100.0
        pending = self.c._input_timer(20, self.c._maybe_post_match_action)
        game = Game(self.c, Mock())
        with patch("Controller.MTGAController.Controller.time.time", return_value=110):
            game._restart_game()
        self.c.start_queueing.assert_not_called()
        with patch("Controller.MTGAController.Controller.time.time", return_value=121):
            pending.function()
        self.c.start_queueing.assert_called_once()

    def test_restart_preserves_running_account_switch_and_its_children(self):
        children = []
        resume = Mock()
        entered, finish = threading.Event(), threading.Event()

        def switch():
            entered.set()
            finish.wait(2)
            children.append(self.c.input.worker_target(resume))

        worker = self.c._input_thread(target=switch)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.c.reset_for_new_game()
        finally:
            finish.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(children), 1)
        children[0]()
        resume.assert_called_once()

    def test_reset_still_retires_workers_when_a_match_has_not_left_yet(self):
        action = Mock()
        old = self.c.input.worker_target(action)
        self.c._Controller__live_match_id = "old-match"
        self.c.reset_for_new_game()
        old()
        action.assert_not_called()
        self.assertTrue(self.c.input.left_click())


if __name__ == "__main__":
    unittest.main()
