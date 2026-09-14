import threading
import unittest

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.input_controller import ExclusiveInputController, NullInputController


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
        attempts = []

        def perform(label):
            attempts.append(label)
            if len(attempts) == 2:
                controller._Controller__concede_completed_event.set()

        controller._Controller__perform_concede = perform
        controller._Controller__run_claimed_concede_sequence("STALL_CONCEDE")

        self.assertEqual(attempts, ["STALL_CONCEDE_1", "STALL_CONCEDE_2"])


if __name__ == "__main__":
    unittest.main()
