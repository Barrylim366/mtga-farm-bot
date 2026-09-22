"""Unit tests for the decision-loop guard logic added/fixed on session_watchdog.

These are pure unit tests of guard predicates and state-machine transitions on
Controller/AI.DummyAI -- MTGA itself is not scriptable, so nothing here drives
the real game. Where a test needs a live Controller instance it constructs one
against a throwaway log file (Controller's __init__ does not touch the screen
or network as long as no decision/vision method that requires a real MTGA
window is invoked).

Controller uses name-mangled double-underscore attributes; from outside the
class body they must be accessed as `_Controller__name`.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from Controller.Utilities.GameState import GameState
from state.state_machine import BotState


def _cleanup_controller(c: Controller) -> None:
    """Cancel every background threading.Timer a Controller may have armed
    during a test. reset_inactivity_timer() alone starts a real 180s,
    non-daemon Timer -- left uncancelled it keeps the whole test process
    alive well past the test run."""
    for attr in (
        "_Controller__inactivity_timer",
        "_Controller__decision_execution_thread",
        "_Controller__decision_heartbeat_timer",
        "_Controller__group_resume_timer",
        "_Controller__mulligan_execution_thread",
        "_Controller__assign_damage_execution_thread",
    ):
        timer = getattr(c, attr, None)
        if timer is not None and hasattr(timer, "cancel"):
            timer.cancel()


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._Controller__live_match_id = "test-match-1"
    c._Controller__last_seen_match_id = "test-match-1"
    c._get_state_from_log = lambda: BotState.IN_GAME
    return c


def seed_state(
    c: Controller,
    *,
    decision_player: int = 1,
    active_player: int = 1,
    phase: str = "Phase_Main1",
    step: str = "Step_Main",
    stack_object_ids=None,
    pending_message_count: int = 0,
    game_state_id: int = 100,
):
    """Push a minimal-but-complete GameState onto a controller, bypassing the
    Player.log parsing layer (which is exercised separately via raw_dict in
    the tests that need it)."""
    zones = []
    if stack_object_ids:
        zones.append({
            "zoneId": 1,
            "type": "ZoneType_Stack",
            "objectInstanceIds": list(stack_object_ids),
        })
    game_dict = {
        "turnInfo": {
            "turnNumber": 5,
            "phase": phase,
            "step": step,
            "activePlayer": active_player,
            "priorityPlayer": decision_player,
            "decisionPlayer": decision_player,
            "nextPhase": phase,
            "nextStep": step,
        },
        "timers": [],
        "gameObjects": [],
        "players": [{"systemSeatNumber": 1}, {"systemSeatNumber": 2}],
        "annotations": [],
        "actions": [],
        "zones": zones,
        "pendingMessageCount": pending_message_count,
        "gameStateId": game_state_id,
    }
    c.updated_game_state = GameState(game_dict)


def make_raw_dict(
    *,
    decision_player: int = 1,
    active_player: int = 1,
    phase: str = "Phase_Main1",
    step: str = "Step_Main",
    stack_object_ids=None,
    game_state_id: int = 100,
    prev_game_state_id: int = 99,
    match_id: str = "test-match-1",
):
    """Build a raw greToClientEvent dict shaped like what
    __get_game_state_from_raw_dict / __update_game_state expect from
    Player.log. Deliberately omits pendingMessageCount -- it is not in
    GameState.GAME_STATE_KEYS, so real GRE traffic never carries it through
    this path either; tests that need a specific pending_count pre-seed it on
    updated_game_state directly (see seed_state) and rely on GameState.update
    doing a merge, not a replace.
    """
    zones = []
    if stack_object_ids:
        zones.append({
            "zoneId": 1,
            "type": "ZoneType_Stack",
            "objectInstanceIds": list(stack_object_ids),
        })
    return {
        "greToClientEvent": {
            "greToClientMessages": [
                {
                    "type": "GREMessageType_GameStateMessage",
                    "gameStateMessage": {
                        "gameStateId": game_state_id,
                        "prevGameStateId": prev_game_state_id,
                        "gameInfo": {"matchID": match_id, "stage": "GameStage_Play"},
                        "turnInfo": {
                            "turnNumber": 5,
                            "phase": phase,
                            "step": step,
                            "activePlayer": active_player,
                            "priorityPlayer": decision_player,
                            "decisionPlayer": decision_player,
                            "nextPhase": phase,
                            "nextStep": step,
                        },
                        "timers": [],
                        "gameObjects": [],
                        "players": [{"systemSeatNumber": 1}, {"systemSeatNumber": 2}],
                        "annotations": [],
                        "actions": [],
                        "zones": zones,
                    },
                }
            ]
        }
    }


class StackDeferTimeoutProceedsTest(unittest.TestCase):
    """Finding 1: once the 15s stack-defer clock expires with the decision
    ours, __update_game_state must actually proceed past the pending_count>0
    defer instead of falling straight back into it (which used to restart the
    15s clock and log STACK_DEFER_TIMEOUT forever without ever proceeding)."""

    def test_stack_defer_timeout_proceeds_past_pending_gate(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__system_seat_id = 1
        c._Controller__has_mulled_keep = True
        # Pre-seed pending_count=1 directly on the live GameState: not part of
        # GAME_STATE_KEYS, so the raw_dict update below will not clear it (see
        # make_raw_dict's docstring) -- this reproduces "pendingMessageCount
        # blocking priority" without needing the full GRE pending-message wire
        # format.
        seed_state(c, decision_player=1, active_player=1, stack_object_ids=[500])
        c.updated_game_state.game_dict["pendingMessageCount"] = 1
        # Simulate having already waited past the 15s stack-defer timeout.
        c._Controller__stack_defer_since = time.time() - 20.0

        raw_dict = make_raw_dict(decision_player=1, active_player=1, stack_object_ids=[500])

        try:
            c._Controller__update_game_state(raw_dict)
        finally:
            # Never let a real Timer escape the test process.
            if c._Controller__decision_execution_thread is not None:
                c._Controller__decision_execution_thread.cancel()

        # The bug: flow fell straight back into "Deferring decision:
        # pendingMessageCount=..." and returned, and __stack_defer_since got
        # reset to "now" by __stack_defer_expired, so it could never expire
        # again on the next tick. The fix: it skips that one gate and reaches
        # the decision-arming code, which stamps __last_decision_ts (Finding 4)
        # and/or arms __decision_delay_key.
        self.assertTrue(
            c._Controller__decision_delay_key is not None
            or (
                c._Controller__decision_execution_thread is not None
                or c._Controller__last_decision_ts > 0
            ),
            "expected the decision loop to proceed past the pending-message gate "
            "once the stack-defer timeout expired",
        )
        # And the stack-defer clock must not still be sitting at ~now (i.e. it
        # must not have silently re-armed itself again this same tick).
        self.assertEqual(c._Controller__stack_defer_since, 0.0)


class FreshMatchStateIsolationTest(unittest.TestCase):
    def test_first_diff_resets_old_match_before_merge_and_starts_once(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__live_match_id = "old-match"
        c._Controller__last_seen_match_id = "old-match"
        c._Controller__pending_card_prompt = {"kind": "old"}
        c._Controller__pending_target_select = {"source_id": 99}
        c._Controller__pending_select_n = {"ids": [99]}
        c._Controller__inst_id_grp_id_dict = {99: 999}
        seed_state(c, decision_player=1, active_player=1, game_state_id=500)
        c.updated_game_state.game_dict["actions"] = [{"actionType": "ActionType_Cast"}]
        c.updated_game_state.game_dict["timers"] = [{"timerId": 99, "durationSec": 2}]

        first_diff = make_raw_dict(
            match_id="new-match", game_state_id=1, prev_game_state_id=0
        )
        message = first_diff["greToClientEvent"]["greToClientMessages"][0]["gameStateMessage"]
        message["type"] = "GameStateType_Diff"
        message.pop("turnInfo")
        message.pop("actions")
        message.pop("timers")

        # Assert on the reset itself, not on a log line: the audit event this
        # used to grep for no longer exists, and a missing phrase made the test
        # green for the wrong reason.
        real_reset = c._Controller__reset_live_game_state
        with mock.patch.object(
            c, "_Controller__reset_live_game_state", wraps=real_reset
        ) as reset:
            c._Controller__update_game_state(first_diff)
            c._Controller__update_game_state(first_diff)

        state = c.updated_game_state.get_full_state()
        self.assertFalse(state.get("turnInfo"))
        self.assertFalse(state.get("actions"))
        self.assertFalse(state.get("timers"))
        self.assertIsNone(c._Controller__pending_card_prompt)
        self.assertIsNone(c._Controller__pending_target_select)
        self.assertIsNone(c._Controller__pending_select_n)
        self.assertEqual(c.get_inst_id_grp_id_dict(), {})
        self.assertEqual(c.get_current_match_id(), "new-match")
        self.assertEqual(c._Controller__retired_match_id, "old-match")

        # Exactly once: the first diff switches matches, the identical second
        # one must not reset the baseline it just established.
        self.assertEqual(reset.call_count, 1)
        reason = reset.call_args.args[0]
        self.assertIn("old-match -> new-match", reason)

    def test_late_retired_match_diff_is_rejected_before_merge(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__live_match_id = "new-match"
        c._Controller__last_seen_match_id = "new-match"
        c._Controller__retired_match_id = "old-match"
        seed_state(c, decision_player=1, active_player=1, game_state_id=10)
        before = c.updated_game_state.get_full_state().copy()

        late = make_raw_dict(
            match_id="old-match", game_state_id=999, prev_game_state_id=998,
            decision_player=2, active_player=2,
        )
        c._Controller__update_game_state(late)

        self.assertEqual(c.updated_game_state.get_full_state(), before)
        self.assertEqual(c.get_current_match_id(), "new-match")


class SafeToRedriveDecisionTest(unittest.TestCase):
    """Findings 3 + 6: the guard used by both the group/scry resume timer and
    the idle-decision heartbeat must refuse to fire while a pay-costs prompt
    or a select-N prompt is open, and the two callers must agree (single
    shared predicate)."""

    def _base_controller(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__system_seat_id = 1
        c._Controller__has_mulled_keep = True
        seed_state(c, decision_player=1, active_player=1)
        return c

    def test_blocks_while_pay_costs_prompt_open(self):
        c = self._base_controller()
        c._Controller__pending_pay_costs_ts = time.time()
        self.assertFalse(c._Controller__safe_to_redrive_decision())

    def test_blocks_while_select_n_in_progress(self):
        c = self._base_controller()
        c._Controller__select_n_in_progress = True
        c._Controller__select_n_in_progress_since = time.time()
        self.assertFalse(c._Controller__safe_to_redrive_decision())

    def test_blocks_while_pending_select_n(self):
        c = self._base_controller()
        c._Controller__pending_select_n = {"ids": [1, 2], "ts": time.time(), "token": 1}
        self.assertFalse(c._Controller__safe_to_redrive_decision())

    def test_allows_when_clear(self):
        c = self._base_controller()
        self.assertTrue(c._Controller__safe_to_redrive_decision())

    def test_resume_after_group_req_respects_pay_costs(self):
        """The scry/group resume path must not fire a decision into an open
        PayCostsReq -- this was the concrete gap Finding 3 described."""
        c = self._base_controller()
        c._Controller__pending_pay_costs_ts = time.time()
        invoked = []
        c._Controller__decision_callback = lambda *a, **k: invoked.append((a, k))

        c._Controller__resume_decision_after_group_req()

        self.assertEqual(invoked, [], "resume must not invoke the decision callback while pay costs is pending")

    def test_resume_after_group_req_respects_select_n(self):
        c = self._base_controller()
        c._Controller__pending_select_n = {"ids": [1], "ts": time.time(), "token": 1}
        invoked = []
        c._Controller__decision_callback = lambda *a, **k: invoked.append((a, k))

        c._Controller__resume_decision_after_group_req()

        self.assertEqual(invoked, [], "resume must not invoke the decision callback while a SelectN prompt is open")

    def test_resume_after_group_req_fires_when_safe(self):
        c = self._base_controller()
        invoked = []
        c._Controller__decision_callback = lambda *a, **k: invoked.append((a, k))

        c._Controller__resume_decision_after_group_req()

        self.assertEqual(len(invoked), 1)


class HeartbeatRespectsStackDeferTest(unittest.TestCase):
    """Finding 5: while the 15s stack-defer clock is running and not yet
    expired, the 8s heartbeat must not preempt it."""

    def test_heartbeat_noop_while_stack_defer_running(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__system_seat_id = 1
        c._Controller__has_mulled_keep = True
        seed_state(c, decision_player=1, active_player=1, stack_object_ids=[500])
        c._Controller__stack_defer_since = time.time() - 5.0  # 5s < 15s timeout
        c._Controller__last_decision_ts = time.time() - 60.0  # long idle, would normally fire
        invoked = []
        c._Controller__decision_callback = lambda *a, **k: invoked.append((a, k))

        c._Controller__maybe_wake_stalled_decision()

        self.assertEqual(invoked, [], "heartbeat must not fire while stack-defer clock is still running")

    def test_heartbeat_fires_once_stack_defer_expired(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__system_seat_id = 1
        c._Controller__has_mulled_keep = True
        seed_state(c, decision_player=1, active_player=1, stack_object_ids=[500])
        c._Controller__stack_defer_since = time.time() - 20.0  # past 15s timeout
        c._Controller__last_decision_ts = time.time() - 60.0
        invoked = []
        c._Controller__decision_callback = lambda *a, **k: invoked.append((a, k))

        c._Controller__maybe_wake_stalled_decision()

        self.assertEqual(len(invoked), 1)


class DecisionTimestampStampedOnFreshPriorityTest(unittest.TestCase):
    """Finding 4: __last_decision_ts must be refreshed as soon as a fresh
    GameStateMessage shows the decision becoming ours, not only once a
    decision actually executes -- otherwise a heartbeat tick landing in the
    gap between the state update and the delayed-decision timer being armed
    can fire immediately, skipping the settle delay."""

    def test_last_decision_ts_stamped_when_priority_becomes_ours(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__system_seat_id = 1
        c._Controller__has_mulled_keep = True
        seed_state(c, decision_player=1, active_player=1)
        # Simulate a long opponent turn: idle clock is stale (> heartbeat idle
        # threshold) right before priority passes to us.
        c._Controller__last_decision_ts = time.time() - 60.0

        raw_dict = make_raw_dict(decision_player=1, active_player=1)
        before = time.time()
        try:
            c._Controller__update_game_state(raw_dict)
        finally:
            if c._Controller__decision_execution_thread is not None:
                c._Controller__decision_execution_thread.cancel()

        self.assertGreaterEqual(c._Controller__last_decision_ts, before)


class SubmitSelectionCancellationTest(unittest.TestCase):
    def test_match_completion_during_probe_stops_fallbacks_and_clicks(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        c._Controller__pending_target_select = {
            "source_id": 10,
            "selected": 1,
            "min": 1,
        }
        probes = []
        clicks = []

        def complete_match_during_first_probe(*_args, **_kwargs):
            probes.append("scaled")
            c._Controller__live_match_id = None
            c._Controller__last_seen_match_id = None
            return None

        c._locate_image_center_in_scaled_arena_region = complete_match_during_first_probe
        c._locate_image_center = lambda *_args, **_kwargs: probes.append("full")
        c._click_abs = lambda *_args, **_kwargs: clicks.append((_args, _kwargs))

        with mock.patch("Controller.MTGAController.Controller.os.path.exists", return_value=True):
            submitted = c.submit_selection(
                reason="test_completion_during_probe",
                expected_match_id="test-match-1",
            )

        self.assertFalse(submitted)
        self.assertEqual(probes, ["scaled"])
        self.assertEqual(clicks, [])

    def test_are_you_sure_probe_refuses_retired_match(self):
        c = make_controller()
        self.addCleanup(_cleanup_controller, c)
        probes = []
        c._Controller__live_match_id = None
        c._Controller__last_seen_match_id = None
        c._locate_image_center_in_scaled_arena_region = (
            lambda *_args, **_kwargs: probes.append("probe")
        )

        dismissed = c._dismiss_are_you_sure_if_present(
            context="TARGET_SUBMIT id=395",
            expected_match_id="test-match-1",
        )

        self.assertFalse(dismissed)
        self.assertEqual(probes, [])


if __name__ == "__main__":
    unittest.main()
