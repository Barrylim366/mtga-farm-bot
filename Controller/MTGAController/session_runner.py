"""Explicit state machine for one bot session.

The controller currently enforces the same order with ad-hoc loops and guards
spread across the post-login routine, the queue loop and the switch. This module
names that order and makes it testable:

    LOGIN -> VERIFY_HOME -> READ_STATE -> DECIDE -> PLAY
                                              \\-> SWITCH -> VERIFY_HOME
    DECIDE -> ROUND_DONE when the round policy says every pass is finished.

The runner only chooses the next state from a snapshot. The controller still
performs the actions (type credentials, click Home, queue, log out); moving
those actions behind the runner is the next step. The state machine is kept
free of Controller and screen imports so its transitions can be tested without
a process that can click.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from Controller.MTGAController.round_policy import ROUND_COMPLETE


class SessionState(Enum):
    IDLE = auto()
    LOGIN = auto()
    VERIFY_HOME = auto()
    READ_STATE = auto()
    DECIDE = auto()
    PLAY = auto()
    SWITCH = auto()
    ROUND_DONE = auto()


@dataclass(frozen=True)
class SessionSnapshot:
    """What the controller knows when it asks the runner to advance.

    Every field has a safe default so a caller can fill only what it has; the
    defaults describe "nothing known yet", which keeps the machine waiting
    rather than acting on a guess.
    """

    signed_in: bool = False
    home_verified: bool = False
    state_fresh: bool = False
    switch_due: bool = False
    round_step: str = ""
    in_match: bool = False
    switch_finished: bool = False


def next_session_state(state: SessionState, snapshot: SessionSnapshot) -> SessionState:
    """The next state, given the current one and what is known now."""
    if state in (SessionState.IDLE, SessionState.LOGIN):
        return SessionState.VERIFY_HOME if snapshot.signed_in else SessionState.LOGIN
    if state is SessionState.VERIFY_HOME:
        return SessionState.READ_STATE if snapshot.home_verified else SessionState.VERIFY_HOME
    if state is SessionState.READ_STATE:
        return SessionState.DECIDE if snapshot.state_fresh else SessionState.READ_STATE
    if state is SessionState.DECIDE:
        if snapshot.round_step == ROUND_COMPLETE:
            return SessionState.ROUND_DONE
        return SessionState.SWITCH if snapshot.switch_due else SessionState.PLAY
    if state is SessionState.PLAY:
        if snapshot.switch_due:
            return SessionState.SWITCH
        if snapshot.in_match:
            return SessionState.PLAY
        # Between matches: read the fresh state again before deciding; a quest
        # or win may have completed during the match.
        return SessionState.DECIDE
    if state is SessionState.SWITCH:
        return SessionState.VERIFY_HOME if snapshot.switch_finished else SessionState.SWITCH
    return state


class SessionRunner:
    """Holds the current session state and advances it from snapshots."""

    def __init__(self) -> None:
        self.state = SessionState.IDLE

    def reset(self) -> SessionState:
        self.state = SessionState.IDLE
        return self.state

    def advance(self, snapshot: SessionSnapshot) -> SessionState:
        self.state = next_session_state(self.state, snapshot)
        return self.state
