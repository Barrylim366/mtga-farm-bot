"""The overlay's text, and the rule that keeps the bot from finding it.

Pure: nothing here creates a window or looks at the screen.
"""

import unittest

import overlay


def _lines(status=None, **overrides):
    kwargs = dict(
        queue_label="Historic",
        switch_text="12 Min till Account Switch",
        show_accounts=True,
        current_account="Alpha",
        next_account="Beta",
        games=3,
        wins=2,
    )
    kwargs.update(overrides)
    return overlay.build_lines(status or {}, **kwargs)


class OverlayTitleTest(unittest.TestCase):
    def test_title_cannot_be_taken_for_the_game(self):
        low = overlay.TITLE.lower()
        self.assertNotIn("mtga", low)
        self.assertNotIn("arena", low)


class BackgroundAlphaTest(unittest.TestCase):
    def test_transparency_maps_to_window_alpha(self):
        self.assertEqual(overlay.background_alpha(0), 1.0)
        self.assertEqual(overlay.background_alpha(25), 0.75)
        self.assertEqual(overlay.background_alpha(100), 0.0)

    def test_out_of_range_and_garbage_are_clamped_or_defaulted(self):
        self.assertEqual(overlay.background_alpha(250), 0.0)
        self.assertEqual(overlay.background_alpha(-5), 1.0)
        self.assertEqual(overlay.background_alpha("x"), 0.75)


class BuildLinesTest(unittest.TestCase):
    def test_out_of_game_shows_queue_switch_accounts_and_session(self):
        texts = [t for t, _ in _lines()]
        self.assertEqual(texts[0], "Session Info")
        self.assertIn("Queue: Historic", texts)
        self.assertIn("12 Min till Account Switch", texts)
        self.assertIn("Current ACC: Alpha", texts)
        self.assertIn("Next ACC: Beta", texts)
        self.assertIn("Session: 3 games · 2 wins", texts)
        self.assertFalse(any(t.startswith("Turn ") for t in texts))

    def test_account_lines_hidden_when_switching_is_off(self):
        texts = [t for t, _ in _lines(show_accounts=False, switch_text="Account switch: off")]
        self.assertIn("Account switch: off", texts)
        self.assertFalse(any("ACC:" in t for t in texts))

    def test_unknown_accounts_show_a_placeholder(self):
        texts = [t for t, _ in _lines(current_account="", next_account="")]
        self.assertIn("Current ACC: …", texts)
        self.assertIn("Next ACC: …", texts)

    def test_in_game_shows_turn_phase_whose_turn_and_last_move(self):
        status = {
            "bot_state": "BotState.IN_GAME",
            "local_system_seat_id": 2,
            "last_move_name": "all_attack",
            "turn_info": {"turnNumber": 5, "phase": "Phase_Combat", "step": "Step_DeclareAttack", "activePlayer": 2},
        }
        lines = _lines(status)
        self.assertIn(("Turn 5 · Combat (Declare Attack) · your turn", "match"), lines)
        self.assertIn(("Last move: all_attack", "match"), lines)

    def test_opponents_turn(self):
        status = {
            "bot_state": "BotState.IN_GAME",
            "local_system_seat_id": 2,
            "turn_info": {"turnNumber": 4, "phase": "Phase_Main1", "activePlayer": 1},
        }
        texts = [t for t, _ in _lines(status)]
        self.assertIn("Turn 4 · Main1 · opponent's turn", texts)

    def test_stale_turn_info_is_ignored_out_of_game(self):
        status = {"bot_state": "BotState.HOME", "turn_info": {"turnNumber": 9, "phase": "Phase_Combat"}}
        texts = [t for t, _ in _lines(status)]
        self.assertFalse(any(t.startswith("Turn ") for t in texts))

    def test_gold_prefers_the_current_account(self):
        texts = [t for t, _ in _lines({"gold_farmed": {"Alpha": 250, "Beta": 100}})]
        self.assertIn("Gold farmed: 250", texts)

    def test_gold_falls_back_to_the_session_total(self):
        texts = [t for t, _ in _lines({"gold_farmed": {"Gamma": 250, "Beta": 100}})]
        self.assertIn("Gold farmed: 350", texts)

    def test_daily_quests_follow_a_heading_with_the_active_one_marked(self):
        status = {
            "active_quest_id": "q2",
            "quests": [
                {"id": "q1", "name": "Selesnya Silence", "colors": "GW", "goal": 30, "progress": 7},
                {"id": "q2", "name": "Orzhov Domination", "colors": "WB", "goal": 30, "progress": 11},
                {"id": "q3", "name": "Nissas Journey", "goal": 25, "progress": 8},
            ],
        }
        lines = _lines(status)
        start = lines.index(("Daily Quests", "quest_title"))
        self.assertEqual(lines[start + 1:], [
            ("Selesnya Silence  (GW)  7/30", "quest"),
            ("▶ Orzhov Domination  (WB)  11/30", "quest_active"),
            ("Nissas Journey  8/25", "quest"),
        ])

    def test_daily_quests_without_data_say_so(self):
        lines = _lines({"quests": []})
        start = lines.index(("Daily Quests", "quest_title"))
        self.assertEqual(lines[start + 1], ("No quest data yet", "muted"))

    def test_no_gold_line_without_data(self):
        texts = [t for t, _ in _lines({"gold_farmed": {}})]
        self.assertFalse(any(t.startswith("Gold") for t in texts))


if __name__ == "__main__":
    unittest.main()
