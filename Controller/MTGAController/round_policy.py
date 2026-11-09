"""Pure round/pass progression for a quests-mode session.

The controller decides two different things at the end of an account visit:

* the pass rule ("does this account still owe anything?", ``switch_policy``),
  and
* the round rule ("is this the last account of the pass?", here).

Keeping the round rule separate makes the two-pass completion testable without a
Controller. The controller still owns the actions: logging out, recording the
completion mark, resetting per-account state and stopping the bot.
"""
from __future__ import annotations

from dataclasses import dataclass

# The current account is finished; move to the next one in the same pass.
CONTINUE = "switch"
# Every account finished the quest pass; the win pass starts with this switch.
START_WIN_PASS = "start_win_pass"
# Every account finished every pass; stop instead of switching.
ROUND_COMPLETE = "round_complete"


@dataclass(frozen=True)
class RoundProgress:
    mode: str  # "quests" | "time"
    known_account_count: int
    completed_count: int
    quests_active: bool
    wins_target: int
    phase: str  # "quests" | "wins"


def next_round_step(progress: RoundProgress) -> str:
    """What the round should do after the current account is marked complete.

    * Time mode never completes a round.
    * An unknown account count (no identity tracking) never completes one.
    * Otherwise the pass ends when every configured account is marked; if both
      dimensions are configured and the quest pass is ending, the win pass
      starts with the same switch, and only a finished win pass ends the round.
    """
    if progress.mode != "quests" or progress.known_account_count <= 0:
        return CONTINUE
    if progress.completed_count < progress.known_account_count:
        return CONTINUE
    if (
        progress.quests_active
        and progress.wins_target > 0
        and progress.phase == "quests"
    ):
        return START_WIN_PASS
    return ROUND_COMPLETE
