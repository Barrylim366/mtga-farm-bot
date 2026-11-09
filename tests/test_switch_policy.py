"""The pass rule as a pure decision: quests first, then wins.

These tests pin the semantics the controller relied on before the extraction:

* both configured, quest pass: leave as soon as the quest target is met; wins
  are the second pass and do not hold the account;
* both configured, win pass: leave when the day's wins are in; the quest state
  is not read at all;
* one configured: that dimension alone;
* neither: never;
* the anti-storm guard overrides everything;
* the lazy inputs are actually lazy, so a win-pass decision cannot read a stale
  quest count (and vice versa).
"""
import unittest

from Controller.MTGAController.switch_policy import (
    QuestSwitchCriteria,
    quest_switch_due,
    time_switch_due,
)


def criteria(*, quests_active, wins_target, phase="quests", anti_storm=False,
             quest_met=True, wins=0, quest_raises=False, wins_raises=False):
    def quest():
        if quest_raises:
            raise AssertionError("quest state must not be read in this branch")
        return quest_met

    def wins_seen():
        if wins_raises:
            raise AssertionError("wins must not be read in this branch")
        return wins

    return QuestSwitchCriteria(
        quests_active=quests_active,
        wins_target=wins_target,
        phase=phase,
        anti_storm=anti_storm,
        quest_target_met=quest,
        wins_seen=wins_seen,
    )


class QuestSwitchDueTest(unittest.TestCase):
    def test_nothing_configured_is_never_due(self):
        self.assertFalse(quest_switch_due(criteria(quests_active=False, wins_target=0)))

    def test_quests_only_follows_the_quest_target(self):
        self.assertTrue(quest_switch_due(criteria(quests_active=True, wins_target=0)))
        self.assertFalse(
            quest_switch_due(criteria(quests_active=True, wins_target=0, quest_met=False))
        )

    def test_wins_only_follows_the_win_target(self):
        self.assertTrue(
            quest_switch_due(criteria(quests_active=False, wins_target=4, wins=4))
        )
        self.assertFalse(
            quest_switch_due(criteria(quests_active=False, wins_target=4, wins=3))
        )

    def test_wins_only_never_reads_the_quest_state(self):
        self.assertTrue(
            quest_switch_due(
                criteria(quests_active=False, wins_target=4, wins=4, quest_raises=True)
            )
        )

    def test_quest_pass_leaves_without_the_wins(self):
        self.assertTrue(
            quest_switch_due(
                criteria(quests_active=True, wins_target=4, phase="quests", wins=0)
            )
        )

    def test_quest_pass_still_needs_the_quest_target(self):
        self.assertFalse(
            quest_switch_due(
                criteria(
                    quests_active=True,
                    wins_target=4,
                    phase="quests",
                    quest_met=False,
                    wins=4,
                )
            )
        )

    def test_win_pass_follows_the_wins_and_not_the_quests(self):
        self.assertTrue(
            quest_switch_due(
                criteria(
                    quests_active=True,
                    wins_target=4,
                    phase="wins",
                    wins=4,
                    quest_raises=True,
                )
            )
        )

    def test_win_pass_waits_for_the_last_win(self):
        self.assertFalse(
            quest_switch_due(
                criteria(quests_active=True, wins_target=4, phase="wins", wins=3)
            )
        )

    def test_anti_storm_overrides_every_criterion(self):
        self.assertFalse(
            quest_switch_due(
                criteria(quests_active=True, wins_target=4, phase="wins", wins=9, anti_storm=True)
            )
        )


class TimeSwitchDueTest(unittest.TestCase):
    def test_zero_interval_is_off(self):
        self.assertFalse(time_switch_due(0, 10_000))

    def test_interval_elapsed(self):
        self.assertTrue(time_switch_due(30, 30))
        self.assertTrue(time_switch_due(30, 31))

    def test_interval_not_elapsed(self):
        self.assertFalse(time_switch_due(30, 29))


if __name__ == "__main__":
    unittest.main()
