"""Transitions of the session state machine, without a Controller or screen."""
import unittest

from Controller.MTGAController.round_policy import ROUND_COMPLETE
from Controller.MTGAController.session_runner import (
    SessionRunner,
    SessionSnapshot,
    SessionState,
    next_session_state,
)


class SessionTransitionTest(unittest.TestCase):
    def test_login_waits_until_signed_in(self):
        self.assertIs(
            next_session_state(SessionState.LOGIN, SessionSnapshot()),
            SessionState.LOGIN,
        )
        self.assertIs(
            next_session_state(SessionState.LOGIN, SessionSnapshot(signed_in=True)),
            SessionState.VERIFY_HOME,
        )

    def test_home_must_be_verified_before_reading_state(self):
        self.assertIs(
            next_session_state(SessionState.VERIFY_HOME, SessionSnapshot(signed_in=True)),
            SessionState.VERIFY_HOME,
        )
        self.assertIs(
            next_session_state(
                SessionState.VERIFY_HOME,
                SessionSnapshot(signed_in=True, home_verified=True),
            ),
            SessionState.READ_STATE,
        )

    def test_state_must_be_fresh_before_deciding(self):
        self.assertIs(
            next_session_state(SessionState.READ_STATE, SessionSnapshot(home_verified=True)),
            SessionState.READ_STATE,
        )
        self.assertIs(
            next_session_state(
                SessionState.READ_STATE,
                SessionSnapshot(home_verified=True, state_fresh=True),
            ),
            SessionState.DECIDE,
        )

    def test_decide_plays_switches_or_finishes(self):
        self.assertIs(
            next_session_state(SessionState.DECIDE, SessionSnapshot()),
            SessionState.PLAY,
        )
        self.assertIs(
            next_session_state(SessionState.DECIDE, SessionSnapshot(switch_due=True)),
            SessionState.SWITCH,
        )
        self.assertIs(
            next_session_state(
                SessionState.DECIDE, SessionSnapshot(round_step=ROUND_COMPLETE)
            ),
            SessionState.ROUND_DONE,
        )

    def test_play_redecides_after_a_match(self):
        self.assertIs(
            next_session_state(SessionState.PLAY, SessionSnapshot(in_match=True)),
            SessionState.PLAY,
        )
        self.assertIs(
            next_session_state(SessionState.PLAY, SessionSnapshot()),
            SessionState.DECIDE,
        )

    def test_play_switches_as_soon_as_the_target_is_met(self):
        self.assertIs(
            next_session_state(SessionState.PLAY, SessionSnapshot(switch_due=True)),
            SessionState.SWITCH,
        )

    def test_switch_waits_for_confirmation_then_verifies_the_new_home(self):
        self.assertIs(
            next_session_state(SessionState.SWITCH, SessionSnapshot()),
            SessionState.SWITCH,
        )
        self.assertIs(
            next_session_state(
                SessionState.SWITCH, SessionSnapshot(switch_finished=True)
            ),
            SessionState.VERIFY_HOME,
        )

    def test_round_done_is_terminal(self):
        self.assertIs(
            next_session_state(SessionState.ROUND_DONE, SessionSnapshot(signed_in=True)),
            SessionState.ROUND_DONE,
        )

    def test_a_full_visit_walks_the_intended_order(self):
        runner = SessionRunner()
        self.assertIs(runner.advance(SessionSnapshot()), SessionState.LOGIN)
        self.assertIs(
            runner.advance(SessionSnapshot(signed_in=True)), SessionState.VERIFY_HOME
        )
        self.assertIs(
            runner.advance(SessionSnapshot(signed_in=True, home_verified=True)),
            SessionState.READ_STATE,
        )
        self.assertIs(
            runner.advance(
                SessionSnapshot(signed_in=True, home_verified=True, state_fresh=True)
            ),
            SessionState.DECIDE,
        )
        self.assertIs(
            runner.advance(
                SessionSnapshot(
                    signed_in=True, home_verified=True, state_fresh=True
                )
            ),
            SessionState.PLAY,
        )
        self.assertIs(
            runner.advance(
                SessionSnapshot(
                    signed_in=True,
                    home_verified=True,
                    state_fresh=True,
                    switch_due=True,
                )
            ),
            SessionState.SWITCH,
        )
        self.assertIs(
            runner.advance(SessionSnapshot(switch_finished=True)),
            SessionState.VERIFY_HOME,
        )

    def test_reset_returns_to_idle(self):
        runner = SessionRunner()
        runner.advance(SessionSnapshot(signed_in=True))
        self.assertIs(runner.reset(), SessionState.IDLE)


if __name__ == "__main__":
    unittest.main()
