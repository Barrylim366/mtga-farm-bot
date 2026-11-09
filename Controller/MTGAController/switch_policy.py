"""Pure policy for "does this account still owe anything in this pass?".

The account round has two passes when both quests and wins are configured:
the quest pass leaves an account as soon as its quest target is met, and the
win pass leaves when the day's wins are in. A single configured dimension is
judged alone. The controller used to spell this out inline in
``_account_switch_due``; keeping it here makes the rule testable without a
Controller, and gives the session runner one decision point to call.

Quest and win values arrive as callables so the policy evaluates only what the
pass needs: a win-pass decision never reads the quest state, and vice versa.
The guards outside the pass rule (manual skip, master switch, repeated-failure
give-up, time mode) stay in the controller.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class QuestSwitchCriteria:
    """Everything the quest-mode switch rule needs. Values that are only needed
    in one branch are lazy callables so the other branch does not read them."""

    quests_active: bool
    wins_target: int
    phase: str  # "quests" | "wins"
    anti_storm: bool
    quest_target_met: Callable[[], bool]
    wins_seen: Callable[[], int]


def quest_switch_due(criteria: QuestSwitchCriteria) -> bool:
    """Whether the current account is finished with this pass.

    * Both dimensions configured, quest pass: quests only (wins are pass 2).
    * Both configured, win pass: wins only (quests were cleared in pass 1).
    * One dimension configured: that dimension alone.
    * Neither configured: never due.
    * The anti-storm guard wins over every criterion.
    """
    if not criteria.quests_active and criteria.wins_target <= 0:
        return False
    if criteria.anti_storm:
        return False
    if not criteria.quests_active:
        # Wins-only: do not read the (disabled) quest target at all.
        return criteria.wins_seen() >= criteria.wins_target
    both_configured = criteria.wins_target > 0
    if both_configured and criteria.phase == "wins":
        return criteria.wins_seen() >= criteria.wins_target
    wins_required = 0 if (both_configured and criteria.phase == "quests") else criteria.wins_target
    return criteria.quest_target_met() and criteria.wins_seen() >= wins_required


def time_switch_due(interval_seconds: float, seconds_since_last: float) -> bool:
    """Time mode: switch every N seconds; a zero or negative interval is off."""
    return interval_seconds > 0 and seconds_since_last >= interval_seconds
