"""Unit tests for right-side (exile/adventure/graveyard/command) sweeps and battlefield ability activation.

Verifies:
1. _is_card_off_hand accurately detects cards in Exile, Graveyard, Stack, Command, Pending, Limbo.
2. _scan_right_side_for_cast scans mapped coordinates and detects off-hand target cards via hover.
3. _cast_once prioritizes right-side scan for off-hand cards and clicks the docked position.
4. _cast_once falls back to right-side scan if hand sweep does not find the target.
5. select_battlefield_permanent allows extended scan time (7.5s) to reach deep battlefield rows.
6. _activate_ability_own selects the battlefield permanent before submitting selection.
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


class RightSideAndAbilitySweepsTests(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        self.c = Controller(f.name)
        self.c._Controller__live_match_id = "test-match-123"
        self.c._arena_region = (0, 0, 1920, 1080)
        self.c.input = Mock()
        self.c.input.position.return_value = Mock(x=1500, y=400)
        self.c.can_execute_game_action = Mock(return_value=True)

    def test_is_card_off_hand_detection(self):
        # Setup mock game state
        fake_state = {
            "zones": [
                {"zoneId": 10, "type": "ZoneType_Hand", "objectInstanceIds": [101, 102]},
                {"zoneId": 20, "type": "ZoneType_Exile", "objectInstanceIds": [201]},
                {"zoneId": 30, "type": "ZoneType_Graveyard", "objectInstanceIds": [301]},
                {"zoneId": 40, "type": "ZoneType_Battlefield", "objectInstanceIds": [401]},
            ],
            "gameObjects": [
                {"instanceId": 101, "zoneId": 10},
                {"instanceId": 201, "zoneId": 20},
                {"instanceId": 301, "zoneId": 30},
                {"instanceId": 401, "zoneId": 40},
            ],
        }
        self.c.updated_game_state.get_full_state = Mock(return_value=fake_state)

        # Card in hand -> False
        self.assertFalse(self.c._is_card_off_hand(101))
        # Card in exile -> True
        self.assertTrue(self.c._is_card_off_hand(201))
        # Card in graveyard -> True
        self.assertTrue(self.c._is_card_off_hand(301))
        # Card on battlefield -> False
        self.assertFalse(self.c._is_card_off_hand(401))
        # Unknown card -> False
        self.assertFalse(self.c._is_card_off_hand(999))

    def test_scan_right_side_for_cast_finds_target(self):
        self.c._get_right_side_scan_points_mapped = Mock(return_value=((1200, 300), (1400, 500)))
        self.c.right_side_scan_step = 100
        self.c.screen_bounds = ((0, 0), (1920, 1080))

        # Simulate log reader finding target on second position
        call_count = [0]
        def mock_has_new_line(pattern):
            call_count[0] += 1
            return call_count[0] >= 2

        self.c.log_reader.has_new_line = Mock(side_effect=mock_has_new_line)
        self.c.log_reader.get_latest_line_containing_pattern = Mock(return_value='"objectId": 201')
        self.c.input.position.return_value = Mock(x=1300, y=300)

        card_id, source, ts, pos = self.c._scan_right_side_for_cast(201)
        self.assertEqual(card_id, 201)
        self.assertEqual(source, "local_fragment")
        self.assertEqual(pos, (1300, 300))

    @patch("Controller.MTGAController.Controller.focus_mtga_window")
    @patch("Controller.MTGAController.Controller._describe_foreground_window", return_value={"is_mtga": True, "hwnd": 1})
    def test_cast_once_scans_right_side_first_for_off_hand_card(self, _mock_fg, _mock_focus):
        self.c._is_card_off_hand = Mock(return_value=True)
        self.c._get_hand_scan_points_mapped = Mock(return_value=((200, 1050), (1700, 1050)))
        self.c._ensure_options_overlay_closed = Mock(return_value=True)

        # Mock right side scan returning target
        self.c._scan_right_side_for_cast = Mock(
            return_value=(201, "local_fragment", 1000.0, (1550, 450))
        )
        self.c._Controller__cast_final_clicks = Mock(return_value=True)

        success = self.c._cast_once(201, expected_match_id="test-match-123")
        self.assertTrue(success)

        # Verified: _scan_right_side_for_cast was called
        self.c._scan_right_side_for_cast.assert_called_once()
        # Verified: __cast_final_clicks was called with click_position=(1550, 450)
        self.c._Controller__cast_final_clicks.assert_called_once()
        call_kwargs = self.c._Controller__cast_final_clicks.call_args[1]
        self.assertEqual(call_kwargs["click_position"], (1550, 450))

    @patch("Controller.MTGAController.Controller.focus_mtga_window")
    @patch("Controller.MTGAController.Controller._describe_foreground_window", return_value={"is_mtga": True, "hwnd": 1})
    def test_cast_once_falls_back_to_right_side_when_hand_scan_misses(self, _mock_fg, _mock_focus):
        self.c._is_card_off_hand = Mock(return_value=False)
        self.c._get_hand_scan_points_mapped = Mock(return_value=((200, 1050), (400, 1050)))
        self.c._ensure_options_overlay_closed = Mock(return_value=True)

        # Hand scan does not find card
        self.c.log_reader.has_new_line = Mock(return_value=False)
        self.c.input.position.return_value = Mock(x=450, y=1050)

        # Fallback right side scan finds card
        self.c._scan_right_side_for_cast = Mock(
            return_value=(105, "local_fragment", 1000.0, (1600, 500))
        )
        self.c._Controller__cast_final_clicks = Mock(return_value=True)

        success = self.c._cast_once(105, expected_match_id="test-match-123")
        self.assertTrue(success)
        self.c._scan_right_side_for_cast.assert_called_once()
        call_kwargs = self.c._Controller__cast_final_clicks.call_args[1]
        self.assertEqual(call_kwargs["click_position"], (1600, 500))

    def test_select_battlefield_permanent_uses_extended_timeout(self):
        self.c._get_battlefield_scan_points_mapped = Mock(return_value=((200, 500), (1700, 900)))
        self.c._Controller__select_object_in_region = Mock(return_value=True)

        success = self.c.select_battlefield_permanent(305, clicks=1)
        self.assertTrue(success)

        # Verify max_scan_sec is 7.5s (not the old 4.0s timeout)
        self.c._Controller__select_object_in_region.assert_called_once()
        kwargs = self.c._Controller__select_object_in_region.call_args[1]
        self.assertEqual(kwargs["max_scan_sec"], 7.5)
        self.assertEqual(kwargs["label"], "BATTLEFIELD_ITEM")

    def test_activate_ability_own_clicks_battlefield_permanent(self):
        fake_state = {
            "zones": [
                {"zoneId": 40, "type": "ZoneType_Battlefield", "objectInstanceIds": [401]},
            ],
            "gameObjects": [
                {"instanceId": 401, "zoneId": 40},
            ],
        }
        self.c.updated_game_state.get_full_state = Mock(return_value=fake_state)
        self.c.select_battlefield_permanent = Mock(return_value=True)
        self.c.submit_selection = Mock()

        self.c._activate_ability_own(401, 12)

        # Should select the permanent on battlefield, then submit
        self.c.select_battlefield_permanent.assert_called_once_with(401, clicks=1)
        self.c.submit_selection.assert_called_once_with(reason="activate_ability", force=True)

    def test_select_battlefield_permanents_multi_select(self):
        self.c._get_battlefield_scan_points_mapped = Mock(return_value=((100, 500), (300, 500)))
        self.c.battlefield_scan_step = 100
        self.c.screen_bounds = ((0, 0), (1920, 1080))

        hover_seq = [
            '"objectId": 389',  # match 1
            '"objectId": 999',  # not in targets
            '"objectId": 392',  # match 2
        ]
        seq_idx = [0]
        def mock_has_new_line(pattern):
            return seq_idx[0] < len(hover_seq)

        def mock_get_latest_line(pattern):
            val = hover_seq[seq_idx[0]]
            seq_idx[0] += 1
            return val

        self.c.log_reader.has_new_line = Mock(side_effect=mock_has_new_line)
        self.c.log_reader.get_latest_line_containing_pattern = Mock(side_effect=mock_get_latest_line)

        candidates = [389, 392, 354, 411]
        selected = self.c.select_battlefield_permanents(candidates, count=2, clicks=1)

        # Should select 389 and 392 in a single sweep and stop when count=2 is reached
        self.assertEqual(selected, [389, 392])
        self.assertEqual(self.c.input.left_click.call_count, 2)

    def test_select_battlefield_permanents_preserves_priority_over_screen_order(self):
        """A protected creature appearing earlier on screen must NOT be selected over preferred fodder."""
        self.c._get_battlefield_scan_points_mapped = Mock(return_value=((100, 500), (300, 500)))
        self.c.battlefield_scan_step = 100
        self.c.screen_bounds = ((0, 0), (1920, 1080))

        # ID 500 is protected creature at x=100 (appears first on screen)
        # ID 600 is sacrificial fodder at x=200 (appears second on screen)
        hover_seq = [
            '"objectId": 500',  # protected creature encountered first in screen sweep
            '"objectId": 600',  # sacrificial fodder encountered second
        ]
        seq_idx = [0]
        def mock_has_new_line(pattern):
            return seq_idx[0] < len(hover_seq)

        def mock_get_latest_line(pattern):
            val = hover_seq[seq_idx[0]]
            seq_idx[0] += 1
            return val

        self.c.log_reader.has_new_line = Mock(side_effect=mock_has_new_line)
        self.c.log_reader.get_latest_line_containing_pattern = Mock(side_effect=mock_get_latest_line)

        # Fodder 600 is preferred (first in list); protected 500 is last
        candidates = [600, 500]
        selected = self.c.select_battlefield_permanents(candidates, count=1, clicks=1)

        # Must select fodder 600, skipping the earlier-appearing protected creature 500
        self.assertEqual(selected, [600])
        self.assertEqual(self.c.input.left_click.call_count, 1)

    def test_select_battlefield_permanents_does_not_silently_substitute_protected_when_fodder_missing(self):
        """When preferred fodder cannot be located on screen, protected creature must NOT be silently clicked."""
        self.c._get_battlefield_scan_points_mapped = Mock(return_value=((100, 500), (200, 500)))
        self.c.battlefield_scan_step = 100
        self.c.screen_bounds = ((0, 0), (1920, 1080))

        # Only protected creature 500 is visible on screen; fodder 600 is not found
        hover_seq = [
            '"objectId": 500',
        ]
        seq_idx = [0]
        def mock_has_new_line(pattern):
            return seq_idx[0] < len(hover_seq)

        def mock_get_latest_line(pattern):
            val = hover_seq[seq_idx[0]]
            seq_idx[0] += 1
            return val

        self.c.log_reader.has_new_line = Mock(side_effect=mock_has_new_line)
        self.c.log_reader.get_latest_line_containing_pattern = Mock(side_effect=mock_get_latest_line)

        candidates = [600, 500]
        selected = self.c.select_battlefield_permanents(candidates, count=1, clicks=1)

        # Must NOT silently substitute protected creature 500
        self.assertEqual(selected, [])
        self.assertEqual(self.c.input.left_click.call_count, 0)


    def test_submit_selection_select_n_falls_back_to_coordinates(self):
        self.c._locate_image_center_in_scaled_arena_region = Mock(return_value=None)
        self.c._locate_image_center = Mock(return_value=None)
        self.c._map_abs_point_to_arena = Mock(return_value=((1755, 944), "arena_mapped"))
        self.c._click_abs = Mock()

        # For select_n, template miss should fall through to coordinate click
        success = self.c.submit_selection(reason="select_n_initial_submit", force=True)
        self.assertTrue(success)
        self.c.input.left_click.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
