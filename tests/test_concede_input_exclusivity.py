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

        controller._Controller__perform_concede = lambda label, *_args: perform(label)
        controller._Controller__run_claimed_concede_sequence("STALL_CONCEDE")

        self.assertEqual(attempts, ["STALL_CONCEDE_1", "STALL_CONCEDE_2"])

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
        controller._Controller__perform_concede = lambda label, *_args: attempts.append(label)
        signature = ("prompt", (), True, 1, "main", "step", (), (), (), ())

        controller._Controller__run_claimed_concede_sequence(
            "STALL_CONCEDE", signature, "match-1"
        )

        self.assertEqual(len(attempts), 2)
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

        controller._Controller__run_claimed_concede_sequence("STALL_CONCEDE")
        self.assertEqual(controller._Controller__concede_outcome, "stop_requested")

    def test_completed_match_skips_confirmation(self):
        controller = Controller.__new__(Controller)
        controller._Controller__concede_outcome = "match_completed"
        controller._Controller__last_seen_match_id = None
        controller._Controller__live_match_id = None
        clicks = []
        controller._click_abs = lambda *args, **_kwargs: clicks.append(args[2])

        with mock.patch.object(controller, "_Controller__is_live_match", side_effect=[True, False]), \
             mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=False), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"):
            controller._Controller__click_concede_and_confirm(
                (10, 20), label="STALL_CONCEDE_1", expected_match_id="match-1"
            )

        self.assertEqual(clicks, ["STALL_CONCEDE_1_CONCEDE_FALLBACK"])


if __name__ == "__main__":
    unittest.main()
