import threading
import unittest
from unittest import mock

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.input_controller import ExclusiveInputController, NullInputController
from state.state_machine import BotState


class RecordingInput(NullInputController):
    def __init__(self):
        super().__init__()
        self.clicks = 0

    def left_click(self, count=1):
        self.clicks += count


class ExclusiveInputControllerTest(unittest.TestCase):
    def test_non_owner_retry_thread_cannot_click_during_concede(self):
        raw = RecordingInput()
        gated = ExclusiveInputController(raw)
        gated.claim_exclusive_for_current_thread()

        worker = threading.Thread(target=gated.left_click)
        worker.start()
        worker.join()
        self.assertEqual(raw.clicks, 0)

        gated.left_click()
        self.assertEqual(raw.clicks, 1, "the concede owner must retain input")

        gated.release_exclusive()
        worker = threading.Thread(target=gated.left_click)
        worker.start()
        worker.join()
        self.assertEqual(raw.clicks, 2, "normal input resumes for the next match")


class ClaimedConcedeRetryTest(unittest.TestCase):
    def test_claimed_sequence_retries_until_match_completion(self):
        controller = Controller.__new__(Controller)
        controller._stop_requested = False
        controller._Controller__concede_completed_event = threading.Event()
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._Controller__concede_outcome = None
        controller._get_state_from_log = lambda: BotState.IN_GAME
        attempts = []

        def perform(label):
            attempts.append(label)
            if len(attempts) == 2:
                controller._Controller__concede_outcome = "match_completed"
                controller._Controller__concede_completed_event.set()

        controller._Controller__perform_concede = perform
        controller._Controller__run_claimed_concede_sequence("STALL_CONCEDE")

        self.assertEqual(attempts, ["STALL_CONCEDE_1", "STALL_CONCEDE_2"])

    def test_soak_log_distinguishes_timeout_from_successful_recovery(self):
        class CompletesOnSecondWait:
            def __init__(self):
                self.completed = False
                self.waits = 0

            def is_set(self):
                return self.completed

            def wait(self, timeout):
                self.waits += 1
                self.completed = self.waits == 2
                if self.completed:
                    controller._Controller__concede_outcome = "match_completed"
                return self.completed

        controller = Controller.__new__(Controller)
        controller._stop_requested = False
        controller._Controller__concede_completed_event = CompletesOnSecondWait()
        controller._Controller__concession_claim_reason = "stalled_local_context"
        controller._Controller__concede_outcome = None
        controller._Controller__soak_concede_claimed_at = 10.0
        controller._Controller__soak_concede_attempts = 0
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._Controller__perform_concede = lambda _label: None
        controller.input = NullInputController()

        with mock.patch("Controller.MTGAController.Controller.time.monotonic", return_value=15.0), \
             mock.patch("Controller.MTGAController.Controller.bot_logger.log_info") as log_info:
            controller._Controller__run_claimed_concede_sequence("STALL_CONCEDE")

        messages = [call.args[0] for call in log_info.call_args_list if call.args]
        soak_messages = [message for message in messages if message.startswith("[SOAK_STALL_V1] ")]
        self.assertTrue(any('"event":"concede_attempt_timeout"' in message for message in soak_messages))
        self.assertTrue(any('"event":"recovery_observed"' in message for message in soak_messages))
        self.assertTrue(any('"attempts":2' in message for message in soak_messages))

    def test_attempt_limit_releases_input_and_suppresses_same_signature(self):
        class NeverCompletes:
            def is_set(self):
                return False

            def wait(self, timeout):
                return False

        controller = Controller.__new__(Controller)
        controller._stop_requested = False
        controller._suppress_selections = True
        controller._Controller__concede_completed_event = NeverCompletes()
        controller._Controller__concession_claimed = True
        controller._Controller__concession_claim_reason = "stalled_local_context"
        controller._Controller__concede_outcome = None
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._Controller__failed_stall_signature = None
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller.input = NullInputController()
        attempts = []
        controller._Controller__perform_concede = lambda label: attempts.append(label)
        signature = ("prompt", (), True, 1, "main", "step", (), (), (), ())

        controller._Controller__run_claimed_concede_sequence(
            "STALL_CONCEDE", signature, "match-1"
        )

        self.assertEqual(len(attempts), 5)
        self.assertFalse(controller._Controller__concession_claimed)
        self.assertFalse(controller._suppress_selections)
        self.assertEqual(controller._Controller__failed_stall_signature, signature)

    def test_stop_wakes_sequence_as_cancellation_not_recovery(self):
        controller = Controller.__new__(Controller)
        controller._stop_requested = True
        controller._Controller__concede_completed_event = threading.Event()
        controller._Controller__concede_completed_event.set()
        controller._Controller__concede_outcome = "stop_requested"
        controller._Controller__live_match_id = None
        controller._Controller__last_seen_match_id = None
        controller.input = NullInputController()

        with mock.patch("Controller.MTGAController.Controller.bot_logger.log_info") as log_info:
            controller._Controller__run_claimed_concede_sequence("STALL_CONCEDE")

        messages = [call.args[0] for call in log_info.call_args_list if call.args]
        self.assertTrue(any('"event":"concede_sequence_ended_without_completion"' in msg for msg in messages))
        self.assertFalse(any('"event":"recovery_observed"' in msg for msg in messages))


if __name__ == "__main__":
    unittest.main()
