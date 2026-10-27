"""Unit tests for match end dismissal behavior.

Verifies:
1. When the bottom continue click dismisses the end screen, the secondary fallback click is NEVER executed.
2. The click targets never land on the Home screen's middle promotional carousel banner zone (y=0.50).
3. Immediate confirmation after each individual click prevents sending clicks onto the Home screen.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller
from state.state_machine import BotState
from vision.window_locator import ArenaDetectionResult


class DismissEndScreenTests(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        self.c = Controller(f.name)
        self.c._arena_region = (0, 0, 1920, 1080)
        self.c._get_ui_action_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
        self.c._ensure_arena_region = lambda *a, **k: (0, 0, 1920, 1080)
        self.c.input = Mock()
        self.current_pos = (0, 0)
        self.clicked_points = []

        def _mock_move(x, y):
            self.current_pos = (x, y)

        def _mock_click(count=1):
            self.clicked_points.append(self.current_pos)

        self.c.input.move_abs = Mock(side_effect=_mock_move)
        self.c.input.left_click = Mock(side_effect=_mock_click)
        self.c._note_match_finished = Mock()
        self.c._update_gold_from_inventory = Mock()
        self.c._maybe_post_match_action = Mock()
        self.c._find_blocking_popup = Mock(return_value=None)
        self.c._click_abs = Mock()
        self.c._locate_image_center = Mock(return_value=None)
        self.c._locate_image_center_in_rescaled_region = Mock(return_value=None)
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._click_image_in_scaled_arena_region = Mock(return_value=False)

    @patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
    def test_single_click_dismiss_stops_immediately_and_never_clicks_banners(self, _mock_focus):
        """When the continue button clears the end screen, no second click is fired."""
        # Returns True immediately after the first click
        self.c._match_end_screen_cleared = Mock(return_value=True)

        self.c.dismiss_end_screen()

        # Exactly 1 click should be registered
        self.assertEqual(self.c.input.left_click.call_count, 1)
        self.assertEqual(len(self.clicked_points), 1, "Must not fire blind second click after dismissal")
        click_x, click_y = self.clicked_points[0]

        # Verify it clicked the bottom continue button (y ≈ 0.93 * 1080 = 1004)
        self.assertEqual(click_x, 960)
        self.assertAlmostEqual(click_y, int(1080 * 0.93), delta=5)

        # Must NOT have clicked dead center (540), which is where promotional banners are
        self.assertNotEqual(click_y, 540)

    @patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
    def test_secondary_click_uses_safe_lower_zone_when_first_click_fails(self, _mock_focus):
        """If first click does not clear, second click uses safe lower zone (y=0.82), not center (y=0.50)."""
        # First check False, second check True
        call_count = [0]

        def cleared_side_effect():
            call_count[0] += 1
            return call_count[0] >= 2

        self.c._match_end_screen_cleared = Mock(side_effect=cleared_side_effect)

        self.c.dismiss_end_screen()

        # 2 clicks registered
        self.assertEqual(self.c.input.left_click.call_count, 2)
        self.assertEqual(len(self.clicked_points), 2)
        first_x, first_y = self.clicked_points[0]
        second_x, second_y = self.clicked_points[1]

        # First is bottom continue (~1004)
        self.assertAlmostEqual(first_y, int(1080 * 0.93), delta=5)

        # Second is safe lower fallback (~885), NOT middle banner area (540)
        self.assertAlmostEqual(second_y, int(1080 * 0.82), delta=5)
        self.assertNotEqual(second_y, 540)

    @patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
    def test_reward_popup_is_claimed_when_detected(self, _mock_focus):
        """When reward overlay (Claim/Continue) is detected post-match, it is clicked directly."""
        self.c._match_end_screen_cleared = Mock(return_value=True)
        popups = [
            ("POPUP_CLAIM", (960, 800), (0, 0, 1920, 1080)),
            None,
        ]
        self.c._find_blocking_popup = Mock(side_effect=popups)

        self.c.dismiss_end_screen()

        # Should click the reward popup via _click_abs
        self.c._click_abs.assert_called_once_with(960, 800, "POPUP_CLAIM")

    @patch("Controller.MTGAController.Controller.time.sleep")
    @patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
    def test_survey_during_dismissal_advances_to_immediate_click_then_claim(self, _focus, _sleep):
        happy = ("POPUP_HAPPY_FACE", (1045, 577), (0, 0, 1920, 1080))
        claim = ("POPUP_CLAIM_REWARDS", (1700, 1000), (0, 0, 1920, 1080))
        self.c._arena_region_provider.detect = Mock(return_value=ArenaDetectionResult(
            False, (0, 0, 1920, 1080), "anchor_not_found", "survey hides anchors",
        ))
        self.c._get_state_from_log = Mock(return_value=BotState.IN_GAME)
        # First search is the result-screen-cleared check, then immediate handling.
        self.c._find_blocking_popup = Mock(side_effect=[happy, happy, claim, None])

        self.c.dismiss_end_screen()

        self.assertEqual(self.c.input.left_click.call_count, 1)
        self.assertEqual([c.args for c in self.c._click_abs.call_args_list], [
            (1045, 577, "POPUP_HAPPY_FACE"),
            (1700, 1000, "POPUP_CLAIM_REWARDS"),
        ])


if __name__ == "__main__":
    unittest.main()
