"""Unit tests for the reward-popup false-positive guard.

Background (observed live, session 2026-07-25): `claim.png` scores above
threshold on the Starter Deck Duel event landing page's orange "Play" button --
the claim search ROI (1450, 850, 470, 230) covers essentially all of
_EVENT_PLAY_ROI (1400, 900, 520, 180), and both are orange rounded buttons in
the bottom-right corner. The bot therefore "claimed a reward" 18 times in a
session with ZERO wins, i.e. with no reward screen on screen at any point. Each
of those clicks hit Play and started the next match immediately, bypassing the
queue path that swaps in the quest-matched deck -- so the bot replayed the
first deck it ever picked even after the active quest changed colors.

These tests drive _dismiss_reward_popup's decision with the vision layer stubbed
out; MTGA is not scriptable, so the template matching itself is not under test.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


CLAIM_POINT = (3163, 1201)  # where the false positive actually landed, in Play


class RewardPopupGuardTests(unittest.TestCase):
    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.c = Controller(log.name)

        # A buttons dir containing claim.png, so the os.path.exists gate passes.
        self.buttons = tempfile.mkdtemp()
        with open(os.path.join(self.buttons, "claim.png"), "wb") as f:
            f.write(b"not-a-real-png")
        self.c._buttons_dir = lambda: self.buttons

        self.clicks: list[tuple[int, int, str]] = []
        self.c._click_abs = lambda x, y, tag, **kw: self.clicks.append((x, y, tag))
        # Keep the success path's 1s settle sleep out of the test runtime.
        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stub_vision(self, *, claim_at, on_event_page):
        """claim_at: point the claim template 'matched' at, or None for no match.
        on_event_page: what the event-Play-button probe reports."""
        self.located: list[str] = []
        self.locate_kwargs: list[dict] = []

        def locate(image_path, label, **kwargs):
            self.located.append(label)
            self.locate_kwargs.append(kwargs)
            return claim_at

        self.c._locate_image_center_in_scaled_arena_region = locate
        self.probed: list[str] = []

        def on_landing(label):
            self.probed.append(label)
            return on_event_page

        self.c._on_starter_event_landing_page = on_landing

    def test_no_claim_button_means_no_click(self):
        self._stub_vision(claim_at=None, on_event_page=False)

        self.assertFalse(self.c._dismiss_reward_popup())
        self.assertEqual(self.clicks, [])

    def test_claim_on_event_landing_page_is_refused(self):
        """The regression: claim.png matching the event Play button must NOT be
        clicked. Clicking starts a match with whatever deck is selected, so the
        deck-swap step is skipped and the bot replays the wrong-color deck."""
        self._stub_vision(claim_at=CLAIM_POINT, on_event_page=True)

        self.assertFalse(self.c._dismiss_reward_popup())
        self.assertEqual(self.clicks, [], "must not click Play")

    def test_real_reward_popup_is_claimed(self):
        """The guard must not break the actual feature: with no event Play button
        visible, the reward popup is real and Claim gets clicked at the located
        point."""
        self._stub_vision(claim_at=CLAIM_POINT, on_event_page=False)

        self.assertTrue(self.c._dismiss_reward_popup())
        self.assertEqual(self.clicks, [(CLAIM_POINT[0], CLAIM_POINT[1], "REWARD_CLAIM")])

    def test_guard_probe_runs_only_after_a_candidate_match(self):
        """Ordering matters for cost: the event-page probe is an extra template
        search on every navigation attempt, so it must run only once the claim
        template already matched, not before."""
        self._stub_vision(claim_at=None, on_event_page=False)
        self.c._dismiss_reward_popup()
        self.assertEqual(self.probed, [], "no candidate -> no extra probe")

        self._stub_vision(claim_at=CLAIM_POINT, on_event_page=True)
        self.c._dismiss_reward_popup()
        self.assertEqual(len(self.probed), 1, "candidate -> exactly one probe")

    def test_claim_search_uses_the_shared_roi_constant(self):
        """RewardRoiOverlapTests reasons about _REWARD_CLAIM_ROI, which is only
        meaningful if the search actually uses it. Without this, reverting the call
        site to a narrower hardcoded literal would pass the whole suite."""
        self._stub_vision(claim_at=None, on_event_page=False)
        self.c._dismiss_reward_popup()

        self.assertEqual(len(self.locate_kwargs), 1)
        self.assertEqual(
            self.locate_kwargs[0].get("rel_region"), Controller._REWARD_CLAIM_ROI
        )
        self.assertEqual(self.locate_kwargs[0].get("confidence"), 0.80)

    def test_stop_requested_short_circuits(self):
        self._stub_vision(claim_at=CLAIM_POINT, on_event_page=False)
        self.c._stop_requested = True

        self.assertFalse(self.c._dismiss_reward_popup())
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.located, [], "must not even look at the screen")

    def test_missing_template_file_short_circuits(self):
        os.remove(os.path.join(self.buttons, "claim.png"))
        self._stub_vision(claim_at=CLAIM_POINT, on_event_page=False)

        self.assertFalse(self.c._dismiss_reward_popup())
        self.assertEqual(self.clicks, [])
        self.assertEqual(self.located, [])


class DeckSwapRestoredTests(unittest.TestCase):
    """The point of the guard is not the return value -- it is that navigation
    continues far enough to re-pick the deck. This drives _navigate_starter_deck on
    a simulated post-match event landing page (claim.png matches the Play button,
    no Home Play button, no Events tab) and asserts the deck swap really happens.
    Without the guard, _dismiss_reward_popup would click and return True, and
    _navigate_starter_deck would bail before ever reaching the swap."""

    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.c = Controller(log.name)

        self.buttons = tempfile.mkdtemp()
        for name in ("claim.png", "event_play.png", "play_btn.png"):
            with open(os.path.join(self.buttons, name), "wb") as f:
                f.write(b"not-a-real-png")
        self.c._buttons_dir = lambda: self.buttons

        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

        # On the event landing page: the claim template matches (that IS the bug),
        # and the event Play button is genuinely there.
        self.c._locate_image_center_in_scaled_arena_region = (
            lambda image_path, label, **kw: CLAIM_POINT
        )
        self.c._on_starter_event_landing_page = lambda label: True
        # Neither the Home Play button nor the Events tab is present here, so
        # navigation must fall through to the event-landing re-queue.
        self.clicked_templates: list[str] = []

        def click_tpl(image_path, label, **kw):
            self.clicked_templates.append(label)
            return label == "STARTER_EVENT_PLAY"

        self.c._click_image_in_scaled_arena_region = click_tpl
        self.c._click_abs = lambda x, y, tag, **kw: None
        self.c._dismiss_match_end_screen = lambda: False
        self.c._get_state_from_log = lambda: "home"
        self.c._cached_quests = [{"id": "q1"}]
        self.c._cached_active_colors = "GW"
        self.c.refresh_quests_cache = lambda: None

        self.swaps: list[str] = []
        self.c._swap_starter_deck_for_quest = lambda colors: self.swaps.append(colors)

    def test_guarded_reward_claim_still_reaches_the_deck_swap(self):
        self.assertTrue(self.c._navigate_starter_deck())

        self.assertEqual(
            self.swaps, ["GW"],
            "the deck must be re-picked for the current quest colors",
        )
        self.assertIn("STARTER_EVENT_PLAY", self.clicked_templates)

    def test_swap_happens_before_the_queue_click(self):
        """Order matters: pressing Play before swapping queues the old deck."""
        order: list[str] = []
        self.c._swap_starter_deck_for_quest = lambda colors: order.append("swap")

        def click_tpl(image_path, label, **kw):
            if label == "STARTER_EVENT_PLAY":
                order.append("play")
                return True
            return False

        self.c._click_image_in_scaled_arena_region = click_tpl

        self.c._navigate_starter_deck()

        self.assertEqual(order, ["swap", "play"])


class RewardRoiOverlapTests(unittest.TestCase):
    """The guard exists because the two ROIs overlap. Pin that overlap so nobody
    'simplifies' the guard away believing the regions are disjoint. Both values are
    read from the Controller, so shrinking either ROI is caught here rather than
    silently invalidating the reasoning behind the guard."""

    def test_claim_roi_overlaps_the_event_play_button(self):
        cx, cy, cw, ch = Controller._REWARD_CLAIM_ROI
        ex, ey, ew, eh = Controller._EVENT_PLAY_ROI

        overlap_w = min(cx + cw, ex + ew) - max(cx, ex)
        overlap_h = min(cy + ch, ey + eh) - max(cy, ey)

        self.assertGreater(overlap_w, 0)
        self.assertGreater(overlap_h, 0)
        # The event Play button is almost entirely inside the claim search area,
        # so the template gate alone can never discriminate the two screens.
        covered = (overlap_w * overlap_h) / float(ew * eh)
        self.assertGreater(covered, 0.75, f"only {covered:.0%} of Play ROI covered")


if __name__ == "__main__":
    unittest.main()
