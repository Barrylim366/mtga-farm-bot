"""The Current Session per-account panel: gold, today's wins and today's quests.

Most of it is pure string work, so most tests create no window: the block builder
reads only the rows, the alias map, the daily tallies and the current account.
The bold row is a font on a canvas item, so one class builds the real window to
check it (and skips when Tk has no display, as the other layout tests do).
"""

import tkinter as tk
import unittest

import ui


class SessionAccountColumnsTest(unittest.TestCase):
    def make(self, rows=None, aliases=None, quests=None, wins=None, current=""):
        window = ui.CurrentSessionWindow.__new__(ui.CurrentSessionWindow)
        window._gold_rows = [("thuringiensis#1", 750), ("mycoides", 0)] if rows is None else rows
        window._gold_aliases = (
            {"thuringiensis#1": "thuringiensis"} if aliases is None else aliases
        )
        window._quests_today = {"thuringiensis": 2} if quests is None else quests
        window._wins_today = {"thuringiensis": 3} if wins is None else wins
        window._current_account = current
        return window

    def test_the_header_names_all_four_columns(self):
        header = self.make()._gold_text_block().splitlines()[0]
        self.assertIn("Account", header)
        self.assertIn("Gold", header)
        self.assertIn("Wins", header)
        self.assertIn("Quests", header)

    def test_wins_and_quests_are_matched_through_the_configured_alias(self):
        # The tally is keyed by the Arena name the log carries; the row may be
        # keyed by the same screenName, or only reachable through the alias.
        window = self.make(quests={"thuringiensis#1": 3}, wins={"thuringiensis#1": 2})
        row = [line for line in window._gold_text_block().splitlines() if "thuringiensis" in line][0]
        gold, wins, quests = row.split()[-3:]
        self.assertEqual((gold, wins, quests), ("750", "2", "3"))

    def test_an_account_without_tallies_shows_zero_in_both_columns(self):
        row = [line for line in self.make()._gold_text_block().splitlines() if "mycoides" in line][0]
        gold, wins, quests = row.split()[-3:]
        self.assertEqual((gold, wins, quests), ("0", "0", "0"))

    def test_a_long_name_is_shortened_to_fit(self):
        window = self.make(rows=[("W" * 40 + "#1", 5)], aliases={}, quests={})
        row = window._gold_text_block().splitlines()[1]
        self.assertIn("…", row)
        self.assertNotIn("W" * 30, row)

    def test_no_rows_says_so(self):
        self.assertEqual(self.make(rows=[])._gold_text_block(), "No gold farmed yet.")

    def test_lookup_is_case_insensitive(self):
        window = self.make(quests={"Thuringiensis": 4}, wins={"THURINGIENSIS": 5})
        self.assertEqual(window._quests_for("thuringiensis"), 4)
        self.assertEqual(window._wins_for("thuringiensis"), 5)

    def test_the_current_account_is_marked_on_its_row_only(self):
        window = self.make(current="thuringiensis")
        lines = window._gold_lines()
        self.assertEqual(lines[0][1], False)          # header
        self.assertEqual(lines[1][1], True)           # the account playing now
        self.assertEqual(lines[2][1], False)

    def test_the_current_account_is_matched_through_the_alias_and_case(self):
        window = self.make(current="THURINGIENSIS")
        self.assertTrue(window._is_current_row("thuringiensis#1"))
        self.assertFalse(window._is_current_row("mycoides"))

    def test_no_current_account_marks_nothing(self):
        window = self.make(current="")
        self.assertFalse(any(current for _text, current in window._gold_lines()))


class SessionAccountRowFontTest(unittest.TestCase):
    """The bold row is a real font on a real canvas item, so build the window."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.root._ui_scale = 0.5        # this machine's setting
        self.root._button_skins = {}
        self.root.bot_running = False

    def make_window(self, current):
        window = ui.CurrentSessionWindow(self.root, 12, 5, spawn_xy=(20, 20))
        window.withdraw()
        self.addCleanup(window.destroy)
        window._gold_rows = [("thuringiensis#1", 750), ("mycoides", 0)]
        window._gold_aliases = {"thuringiensis#1": "thuringiensis"}
        window._quests_today = {"thuringiensis": 2}
        window._wins_today = {"thuringiensis": 3}
        window._current_account = current
        window._layout_scene()
        window.update_idletasks()
        return window

    def test_only_the_current_account_row_is_bold(self):
        window = self.make_window("thuringiensis")
        self.assertEqual(len(window._gold_row_items), 2)
        self.assertIn("bold", window._canvas.itemcget(window._gold_row_items[0], "font"))
        self.assertNotIn("bold", window._canvas.itemcget(window._gold_row_items[1], "font"))
        self.assertNotIn("bold", window._canvas.itemcget(window._gold_text_item, "font"))

    def test_the_rows_carry_their_win_and_quest_counts(self):
        window = self.make_window("")
        first = window._canvas.itemcget(window._gold_row_items[0], "text")
        self.assertIn("thuringiensis", first)
        gold, wins, quests = first.split()[-3:]
        self.assertEqual((gold, wins, quests), ("750", "3", "2"))

    def test_the_rows_do_not_overlap_at_half_scale(self):
        window = self.make_window("mycoides")
        boxes = [window._canvas.bbox(item) for item in window._gold_row_items]
        header = window._canvas.bbox(window._gold_text_item)
        self.assertIsNotNone(header)
        for upper, lower in zip([header] + boxes, boxes):
            # A pixel of slack: bbox is the ink box, not the line box.
            self.assertLessEqual(upper[3], lower[1] + 1, (upper, lower))
        # The panel and the window still fit the rows it was sized for.
        panel = window._canvas.bbox(window._gold_panel_item)
        self.assertIsNotNone(panel)
        self.assertLessEqual(boxes[-1][3], panel[3])


if __name__ == "__main__":
    unittest.main()
