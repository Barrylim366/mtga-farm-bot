"""The starter queue loop must not stall on the event page.

Observed 2026-10-11 (and the same on another user's 1600x900 install): after a
match the bot sat on the Starter Deck Duel page for 47 minutes. MTGA logged no
scene change and no join for ~100 clicks. The chain:

- The event header was visible but its pill was not read, so navigation found
  "no anchor" and pressed ESC -- which on an uncovered screen OPENS Options.
- The undo check asked detect(), which names only the FIRST anchor matching.
  Options sits behind the nav bar's Profile icon in that order, so it stayed
  invisible and Options was never closed.
- Nothing ever left the page: every pass retried the same clicks.

No test here sees the screen: template search, clicks and capture are stubbed.
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import runtime_status
from Controller.MTGAController.Controller import Controller
from tools.session_watchdog import _classify_line
from vision.window_locator import ArenaDetectionResult, ArenaRegionProvider


class _StarterBase(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        self.c = Controller(f.name)

        self.buttons = tempfile.mkdtemp()
        for name in ("play_btn.png", "event_play.png", "event_title.png", "okay_btn.png"):
            with open(os.path.join(self.buttons, name), "wb") as fh:
                fh.write(b"not-a-real-png")
        self.c._buttons_dir = lambda: self.buttons

        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        focus = patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
        focus.start()
        self.addCleanup(focus.stop)
        for name in ("update_status", "set_mode", "clear_intentional_wait", "set_startup_phase"):
            p = patch.object(runtime_status, name, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)

        self.c._get_state_from_log = lambda: "home"
        self.c._dismiss_reward_popup = lambda: False
        self.c._dismiss_match_end_screen = lambda: False
        self.c.refresh_quests_cache = lambda: None
        self.c._cached_quests = [{"id": "q1"}]
        self.c._resolve_starter_target_colors = lambda: ""
        self.c._swap_starter_deck_for_quest = lambda colors: None
        self.c._navigate_to_home = Mock(return_value=True)
        self.c._write_nav_debug_bundle = Mock()
        self.options_open = False
        self.c._options_overlay_visible = lambda: self.options_open

        self.visible: set[str] = set()
        self.clicks: list[str] = []
        self.escapes: list[int] = []

        def locate(image_path, label, **kw):
            return (100, 100) if os.path.basename(image_path) in self.visible else None

        def click_tpl(image_path, label, **kw):
            if os.path.basename(image_path) in self.visible:
                self.clicks.append(label)
                return True
            return False

        self.c._locate_image_center_in_scaled_arena_region = locate
        self.c._click_image_in_scaled_arena_region = click_tpl
        self.c._click_abs = lambda x, y, tag, **kw: self.clicks.append(tag)
        self.c._ensure_arena_region = lambda force_reacquire=False: (0, 0, 1920, 1080)
        self.c.input.tap_escape = lambda: self.escapes.append(1)


class EventHeaderNoEscTests(_StarterBase):
    def test_header_visible_without_pill_presses_no_esc(self):
        """The 2026-10-11 trigger: ESC on the uncovered event page opened Options."""
        self.visible = {"event_title.png"}

        self.assertFalse(self.c._navigate_starter_deck())

        self.assertEqual(self.escapes, [])
        self.assertEqual(self.clicks, [])

    def test_without_header_the_announcement_path_still_runs(self):
        """A post-login promo hides Home AND the header; ESC must still clear it."""
        self.visible = set()
        self.c._find_event_banner_scrolling = lambda *a, **k: False

        self.c._navigate_starter_deck()

        self.assertEqual(len(self.escapes), 1, "the promo ESC fallback must remain")

    def test_second_dead_end_leaves_the_page_via_home(self):
        self.visible = {"event_title.png"}

        self.c._navigate_starter_deck()
        self.c._navigate_to_home.assert_not_called()

        self.c._navigate_starter_deck()
        self.c._navigate_to_home.assert_called_once()
        self.c._write_nav_debug_bundle.assert_called_once()
        self.assertEqual(self.c._starter_dead_ends, 0, "reset once Home was reached")


class DeadEndEscalationTests(_StarterBase):
    def test_escalation_closes_options_before_going_home(self):
        self.options_open = True
        order = []
        self.c.input.tap_escape = lambda: order.append("esc")
        self.c._navigate_to_home = Mock(side_effect=lambda: order.append("home") or True)

        self.c._note_starter_dead_end("t")
        self.c._note_starter_dead_end("t")

        self.assertEqual(order, ["esc", "home"])

    def test_no_esc_when_options_is_closed(self):
        self.c._note_starter_dead_end("t")
        self.c._note_starter_dead_end("t")

        self.assertEqual(self.escapes, [])
        self.c._navigate_to_home.assert_called_once()

    def test_failed_home_keeps_counting_and_retries(self):
        self.c._navigate_to_home = Mock(return_value=False)

        for _ in range(3):
            self.c._note_starter_dead_end("t")

        self.assertEqual(self.c._navigate_to_home.call_count, 2)
        self.assertEqual(self.c._starter_dead_ends, 3)

    def test_debug_bundle_is_rate_limited(self):
        self.c._navigate_to_home = Mock(return_value=False)

        for _ in range(4):
            self.c._note_starter_dead_end("t")

        self.c._write_nav_debug_bundle.assert_called_once()

    def test_reaching_the_chooser_resets_the_count(self):
        self.c._starter_dead_ends = 1
        self.c._detect_starter_screen = lambda label: Controller._STARTER_SCREEN_CHOOSER

        self.assertTrue(self.c._open_starter_deck_chooser())

        self.assertEqual(self.c._starter_dead_ends, 0)

    def test_unreachable_chooser_counts_a_dead_end(self):
        self.c._detect_starter_screen = lambda label: Controller._STARTER_SCREEN_PLAY
        self.c._map_abs_point_to_arena = lambda base, label=None: (base, "test")

        self.assertFalse(self.c._open_starter_deck_chooser())

        self.assertEqual(self.c._starter_dead_ends, 1)

    def test_stop_requested_skips_esc_and_home(self):
        self.options_open = True
        self.c._stop_requested = True

        self.c._note_starter_dead_end("t")
        self.c._note_starter_dead_end("t")

        self.assertEqual(self.escapes, [])
        self.c._navigate_to_home.assert_not_called()


class OptionsOverlayProbeTests(unittest.TestCase):
    """detect() reporting global_anchor must not hide an open Options overlay."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        self.c = Controller(f.name)
        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        focus = patch("Controller.MTGAController.Controller.focus_mtga_window", return_value=True)
        focus.start()
        self.addCleanup(focus.stop)
        self.provider = Mock()
        self.c._arena_region_provider = self.provider

    def _detect(self, anchor, ok=True):
        self.provider.detect.return_value = ArenaDetectionResult(
            ok=ok, region=(0, 0, 1920, 1080), code="ok" if ok else "anchor_not_found",
            message="", matched_anchor=anchor,
        )

    def test_options_behind_the_global_anchor_is_found(self):
        self._detect("global_anchor.png")
        self.provider.anchor_visible.return_value = True

        self.assertTrue(self.c._options_overlay_visible())
        self.provider.anchor_visible.assert_called_once_with((0, 0, 1920, 1080), "options_anchor.png")

    def test_no_options_anywhere(self):
        self._detect("global_anchor.png")
        self.provider.anchor_visible.return_value = False

        self.assertFalse(self.c._options_overlay_visible())

    def test_detect_naming_options_needs_no_second_probe(self):
        self._detect("options_anchor.png")

        self.assertTrue(self.c._options_overlay_visible())
        self.provider.anchor_visible.assert_not_called()

    def test_probe_errors_count_as_not_visible(self):
        self._detect(None, ok=False)
        self.provider.anchor_visible.side_effect = RuntimeError("capture failed")

        self.assertFalse(self.c._options_overlay_visible())

    def test_ensure_closed_escapes_until_gone(self):
        self._detect("global_anchor.png")
        self.provider.anchor_visible.side_effect = [True, False]
        escapes = []
        self.c.input.tap_escape = lambda: escapes.append(1)

        self.assertTrue(self.c._ensure_options_overlay_closed(context="T"))
        self.assertEqual(len(escapes), 1)


class AnchorVisibleTests(unittest.TestCase):
    """window_locator: a single-anchor probe that ignores the first-match order."""

    def _provider(self, scores):
        assets = tempfile.mkdtemp()
        for name in scores:
            with open(os.path.join(assets, name), "wb") as fh:
                fh.write(b"x")
        vision = Mock()
        vision.capture.return_value = np.zeros((10, 10, 3), dtype=np.uint8)
        vision.find_template.side_effect = lambda img, path, threshold=0.0: SimpleNamespace(
            score=scores[os.path.basename(path)]
        )
        return ArenaRegionProvider(vision=vision, assets_dir=assets)

    def test_options_is_visible_even_when_global_matches_first(self):
        p = self._provider({"global_anchor.png": 0.95, "options_anchor.png": 0.90})
        region = (0, 0, 1920, 1080)

        anchor, _checks = p._verify_region_with_any_anchor(region)
        self.assertEqual(anchor, "global_anchor.png", "the first-match blind spot")
        self.assertTrue(p.anchor_visible(region, "options_anchor.png"))

    def test_below_threshold_is_not_visible(self):
        p = self._provider({"options_anchor.png": 0.50})
        self.assertFalse(p.anchor_visible((0, 0, 1920, 1080), "options_anchor.png"))

    def test_unknown_anchor_name(self):
        p = self._provider({})
        self.assertFalse(p.anchor_visible((0, 0, 1920, 1080), "nope.png"))


class WatchdogSignalTests(unittest.TestCase):
    def test_dead_end_lines_are_alerts(self):
        self.assertEqual(
            _classify_line("[ERROR] Starter: could not reach the deck chooser in 5 steps; keeping the current deck."),
            "starter_dead_end",
        )
        self.assertEqual(
            _classify_line("[ERROR] STARTER_DEAD_END: deck chooser unreachable (2 passes in a row); leaving"),
            "starter_dead_end",
        )


if __name__ == "__main__":
    unittest.main()
