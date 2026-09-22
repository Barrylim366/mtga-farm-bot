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


class OrderingInput(NullInputController):
    """Blocks inside a delegated call so a claim can be raced against it."""

    def __init__(self):
        super().__init__()
        self.events = []
        self.in_flight = threading.Event()
        self.proceed = threading.Event()

    def left_click(self, count=1):
        self.events.append("click_enter")
        self.in_flight.set()
        if not self.proceed.wait(timeout=5.0):
            raise AssertionError("delegate click was never released")
        self.events.append("click_exit")

    def left_up(self):
        self.events.append("left_up")


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

    def test_claim_waits_for_an_in_flight_non_owner_action(self):
        raw = OrderingInput()
        gated = ExclusiveInputController(raw)

        worker = threading.Thread(target=gated.left_click)
        worker.start()
        self.assertTrue(raw.in_flight.wait(timeout=5.0))

        claimed = threading.Event()

        def claim():
            gated.claim_exclusive_for_current_thread()
            claimed.set()

        claimer = threading.Thread(target=claim)
        claimer.start()
        self.assertFalse(
            claimed.wait(0.3),
            "a claim must not take ownership while an authorised call is still "
            "inside the delegate",
        )
        self.assertNotIn("left_up", raw.events)

        raw.proceed.set()
        worker.join(timeout=5.0)
        claimer.join(timeout=5.0)
        self.assertFalse(worker.is_alive())
        self.assertFalse(claimer.is_alive())
        self.assertTrue(claimed.is_set())
        # The drag release lands after the in-flight click, never in the middle
        # of it and never before it.
        self.assertEqual(raw.events, ["click_enter", "click_exit", "left_up"])

    def test_no_non_owner_input_reaches_the_delegate_after_a_claim(self):
        raw = OrderingInput()
        raw.proceed.set()  # nothing needs to block in this one
        gated = ExclusiveInputController(raw)
        gated.claim_exclusive_for_current_thread()
        self.assertEqual(raw.events, ["left_up"])

        for call in (gated.left_click, gated.left_down, gated.tap_enter):
            worker = threading.Thread(target=call)
            worker.start()
            worker.join(timeout=5.0)
            self.assertFalse(worker.is_alive())
        self.assertEqual(raw.events, ["left_up"])


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
        controller._stop_requested = False
        controller._Controller__concede_outcome = "match_completed"
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._buttons_dir = lambda: "buttons"
        clicks = []
        controller._click_abs = lambda *args, **_kwargs: clicks.append(args[2])

        def settle(_seconds):
            # The concede lands and the match completes during the 1.5s settle.
            controller._Controller__live_match_id = None
            controller._Controller__last_seen_match_id = None

        with mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=False), \
             mock.patch("Controller.MTGAController.Controller.time.sleep", side_effect=settle):
            controller._Controller__click_concede_and_confirm(
                (10, 20), label="STALL_CONCEDE_1", expected_match_id="match-1"
            )

        self.assertEqual(clicks, ["STALL_CONCEDE_1_CONCEDE_FALLBACK"])


class ConcedeClickGuardTest(unittest.TestCase):
    """The concede clicks must be authorised at click time, not at entry time.

    A template probe runs for up to its timeout, so every decision taken before
    it is stale by the time the click happens: the match can have ended, or a
    new one can already be on screen.
    """

    def _controller(self):
        controller = Controller.__new__(Controller)
        controller._stop_requested = False
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._buttons_dir = lambda: "buttons"
        controller._arena_region = (0, 0, 1920, 1080)
        controller._map_base_point_into_arena = lambda _arena, point: point
        controller.clicks = []
        controller._click_abs = lambda x, y, label: controller.clicks.append(label)
        controller.probes = []
        return controller

    @staticmethod
    def _end_match(controller):
        controller._Controller__live_match_id = None
        controller._Controller__last_seen_match_id = None

    def test_match_ending_during_the_concede_probe_blocks_the_fallback(self):
        controller = self._controller()

        def locate(_image, label, **_kwargs):
            controller.probes.append(label)
            self._end_match(controller)  # the match ends while the probe runs
            return (100, 200)

        controller._locate_image_center_in_scaled_arena_region = locate

        with mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=True), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"):
            controller._Controller__click_concede_and_confirm(
                (10, 20), label="STALL_CONCEDE_1", expected_match_id="match-1"
            )

        self.assertEqual(controller.probes, ["STALL_CONCEDE_1_CONCEDE_IMG"])
        self.assertEqual(controller.clicks, [])

    def test_match_replaced_during_the_concede_probe_blocks_the_fallback(self):
        controller = self._controller()

        def locate(_image, label, **_kwargs):
            controller.probes.append(label)
            # The next match is already live by the time the probe returns.
            controller._Controller__live_match_id = "match-2"
            controller._Controller__last_seen_match_id = "match-2"
            return None

        controller._locate_image_center_in_scaled_arena_region = locate

        with mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=True), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"):
            controller._Controller__click_concede_and_confirm(
                (10, 20), label="STALL_CONCEDE_1", expected_match_id="match-1"
            )

        self.assertEqual(controller.clicks, [])

    def test_match_ending_during_the_okay_probe_blocks_the_okay_fallback(self):
        controller = self._controller()

        def locate(_image, label, **_kwargs):
            controller.probes.append(label)
            if label.endswith("_OKAY_IMG"):
                self._end_match(controller)
                return None
            return (100, 200)

        controller._locate_image_center_in_scaled_arena_region = locate

        with mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=True), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"):
            controller._Controller__click_concede_and_confirm(
                (10, 20), label="STALL_CONCEDE_1", expected_match_id="match-1"
            )

        self.assertEqual(
            controller.probes,
            ["STALL_CONCEDE_1_CONCEDE_IMG", "STALL_CONCEDE_1_OKAY_IMG"],
        )
        self.assertEqual(controller.clicks, ["STALL_CONCEDE_1_CONCEDE_IMG"])

    def test_stop_request_during_the_probe_blocks_the_fallback(self):
        controller = self._controller()

        def locate(_image, label, **_kwargs):
            controller.probes.append(label)
            controller._stop_requested = True
            return None

        controller._locate_image_center_in_scaled_arena_region = locate

        with mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=True), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"):
            controller._Controller__click_concede_and_confirm(
                (10, 20), label="FORCE_CONCEDE", expected_match_id=None
            )

        self.assertEqual(controller.clicks, [])

    def test_force_concede_still_clicks_without_a_trusted_match_id(self):
        """expected_match_id=None is deliberate: the ActivePlayer timer expired."""
        controller = self._controller()
        self._end_match(controller)
        controller._locate_image_center_in_scaled_arena_region = (
            lambda _image, label, **_kwargs: controller.probes.append(label) or None
        )

        with mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=True), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"):
            controller._Controller__click_concede_and_confirm(
                (10, 20), label="FORCE_CONCEDE", expected_match_id=None
            )

        self.assertEqual(
            controller.clicks,
            ["FORCE_CONCEDE_CONCEDE_FALLBACK", "FORCE_CONCEDE_OKAY_FALLBACK"],
        )


class PerformConcedeRecheckTest(unittest.TestCase):
    def _controller(self):
        controller = Controller.__new__(Controller)
        controller._stop_requested = False
        controller._Controller__live_match_id = "match-1"
        controller._Controller__last_seen_match_id = "match-1"
        controller._get_state_from_log = lambda: BotState.IN_GAME
        controller._loaded_click_targets = {"concede": {"x": 962, "y": 631}}
        controller.input = NullInputController()
        controller.confirmed = []
        controller._Controller__click_concede_and_confirm = (
            lambda *args, **kwargs: controller.confirmed.append(kwargs.get("label"))
        )
        return controller

    def test_match_ending_during_arena_mapping_skips_the_click(self):
        controller = self._controller()

        def mapping(_xy, **_kwargs):
            controller._Controller__live_match_id = None
            controller._Controller__last_seen_match_id = None
            return ((100, 200), "arena")

        controller._map_abs_point_to_arena = mapping

        with mock.patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"), \
             mock.patch("Controller.MTGAController.Controller.runtime_status"):
            controller._Controller__perform_concede("STALL_CONCEDE_1", "match-1")

        self.assertEqual(controller.confirmed, [])

    def test_match_ending_during_window_focus_skips_the_click(self):
        controller = self._controller()
        controller._map_abs_point_to_arena = lambda _xy, **_kwargs: ((100, 200), "arena")

        def focus():
            controller._Controller__live_match_id = None
            controller._Controller__last_seen_match_id = None
            return True

        with mock.patch("Controller.MTGAController.Controller.focus_mtga_window", side_effect=focus), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"), \
             mock.patch("Controller.MTGAController.Controller.runtime_status"):
            controller._Controller__perform_concede("STALL_CONCEDE_1", "match-1")

        self.assertEqual(controller.confirmed, [])

    def test_live_match_still_concedes(self):
        controller = self._controller()
        controller._map_abs_point_to_arena = lambda _xy, **_kwargs: ((100, 200), "arena")

        with mock.patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=False), \
             mock.patch("Controller.MTGAController.Controller.time.sleep"), \
             mock.patch("Controller.MTGAController.Controller.runtime_status"):
            controller._Controller__perform_concede("STALL_CONCEDE_1", "match-1")

        self.assertEqual(controller.confirmed, ["STALL_CONCEDE_1"])


if __name__ == "__main__":
    unittest.main()
