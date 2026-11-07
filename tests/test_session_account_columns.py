"""The Current Session per-account panel: gold and today's quests.

Pure string work, so no window is created here: the block builder reads only the
rows, the alias map and daily_progress's per-account tally.
"""

import unittest

import ui


class SessionAccountColumnsTest(unittest.TestCase):
    def make(self, rows=None, aliases=None, quests=None):
        window = ui.CurrentSessionWindow.__new__(ui.CurrentSessionWindow)
        window._gold_rows = [("thuringiensis#1", 750), ("mycoides", 0)] if rows is None else rows
        window._gold_aliases = (
            {"thuringiensis#1": "thuringiensis"} if aliases is None else aliases
        )
        window._quests_today = {"thuringiensis": 2} if quests is None else quests
        return window

    def test_the_header_names_all_three_columns(self):
        header = self.make()._gold_text_block().splitlines()[0]
        self.assertIn("Account", header)
        self.assertIn("Gold", header)
        self.assertIn("Quests", header)

    def test_quests_are_matched_through_the_configured_alias(self):
        # The tally is keyed by the Arena name the log carries; the row may be
        # keyed by the same screenName, or only reachable through the alias.
        window = self.make(quests={"thuringiensis#1": 3})
        row = [line for line in window._gold_text_block().splitlines() if "thuringiensis" in line][0]
        self.assertTrue(row.rstrip().endswith("3"), row)

    def test_an_account_without_quests_shows_zero(self):
        row = [line for line in self.make()._gold_text_block().splitlines() if "mycoides" in line][0]
        self.assertTrue(row.rstrip().endswith("0"), row)

    def test_a_long_name_is_shortened_to_fit(self):
        window = self.make(rows=[("W" * 40 + "#1", 5)], aliases={}, quests={})
        row = window._gold_text_block().splitlines()[1]
        self.assertIn("…", row)
        self.assertNotIn("W" * 30, row)

    def test_no_rows_says_so(self):
        self.assertEqual(self.make(rows=[])._gold_text_block(), "No gold farmed yet.")

    def test_lookup_is_case_insensitive(self):
        window = self.make(quests={"Thuringiensis": 4})
        self.assertEqual(window._quests_for("thuringiensis"), 4)


if __name__ == "__main__":
    unittest.main()
