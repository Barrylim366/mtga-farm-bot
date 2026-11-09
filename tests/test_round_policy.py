"""Round/pass progression: when does a pass end, and when is the round done?"""
import unittest

from Controller.MTGAController.round_policy import (
    CONTINUE,
    ROUND_COMPLETE,
    START_WIN_PASS,
    RoundProgress,
    next_round_step,
)


def progress(*, mode="quests", count=6, completed=3, quests_active=True,
             wins_target=4, phase="quests"):
    return RoundProgress(
        mode=mode,
        known_account_count=count,
        completed_count=completed,
        quests_active=quests_active,
        wins_target=wins_target,
        phase=phase,
    )


class NextRoundStepTest(unittest.TestCase):
    def test_time_mode_never_completes_a_round(self):
        self.assertEqual(
            next_round_step(progress(mode="time", completed=6)), CONTINUE
        )

    def test_an_unknown_account_count_never_completes_a_round(self):
        self.assertEqual(next_round_step(progress(count=0, completed=6)), CONTINUE)

    def test_accounts_remain_in_the_pass(self):
        self.assertEqual(next_round_step(progress(completed=5)), CONTINUE)

    def test_the_quest_pass_ending_starts_the_win_pass(self):
        self.assertEqual(
            next_round_step(progress(completed=6, phase="quests")), START_WIN_PASS
        )

    def test_the_win_pass_ending_completes_the_round(self):
        self.assertEqual(
            next_round_step(progress(completed=6, phase="wins")), ROUND_COMPLETE
        )

    def test_quests_only_completes_at_the_end_of_its_pass(self):
        self.assertEqual(
            next_round_step(progress(completed=6, wins_target=0)), ROUND_COMPLETE
        )

    def test_wins_only_completes_at_the_end_of_its_pass(self):
        self.assertEqual(
            next_round_step(progress(completed=6, quests_active=False)), ROUND_COMPLETE
        )

    def test_more_completions_than_accounts_is_still_the_end(self):
        self.assertEqual(
            next_round_step(progress(completed=8, phase="wins")), ROUND_COMPLETE
        )


if __name__ == "__main__":
    unittest.main()
