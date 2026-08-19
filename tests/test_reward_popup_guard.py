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


class StarterDeckNavigationFilterFallbackTests(unittest.TestCase):
    """Tests fallback to the 'All' filter when Starter Deck Duel is not listed
    under 'In Progress' (e.g. on new accounts that have never entered the event)."""

    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.c = Controller(log.name)

        self.buttons = tempfile.mkdtemp()
        for name in ("play_btn.png",):
            with open(os.path.join(self.buttons, name), "wb") as f:
                f.write(b"not-a-real-png")
        self.c._buttons_dir = lambda: self.buttons

        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

        self.c._dismiss_reward_popup = lambda: False
        self.c._dismiss_match_end_screen = lambda: False
        self.c._get_state_from_log = lambda: "home"
        self.c.refresh_quests_cache = lambda: None
        self.c._resolve_starter_target_colors = lambda: ""
        self.c._swap_starter_deck_for_quest = lambda colors: None

        self.clicked_tags: list[str] = []
        self.c._click_abs = lambda x, y, tag, **kw: self.clicked_tags.append(tag)
        self.c._locate_image_center_in_scaled_arena_region = lambda *a, **k: (1600, 310)

    def test_fallback_to_all_filter_when_not_in_progress(self):
        """When the event banner is not found under 'In Progress', the bot switches to 'All' filter."""
        banner_searches = {"count": 0}

        def mock_banner_scrolling(*args, **kwargs):
            banner_searches["count"] += 1
            # First search (under In Progress) returns False; second search (under All) returns True.
            return banner_searches["count"] >= 2

        self.c._find_event_banner_scrolling = mock_banner_scrolling

        def click_tpl(image_path, label, **kw):
            return label in ("STARTER_PLAY", "STARTER_EVENTS", "STARTER_PLAY_CONFIRM")

        self.c._click_image_in_scaled_arena_region = click_tpl

        result = self.c._navigate_starter_deck()

        self.assertTrue(result)
        self.assertEqual(banner_searches["count"], 2)
        self.assertIn("STARTER_ALL_FILTER_FALLBACK", self.clicked_tags)

    def test_no_fallback_needed_when_found_in_progress(self):
        """When found on the first pass under 'In Progress', fallback to 'All' is not triggered."""
        self.c._find_event_banner_scrolling = lambda *a, **k: True

        def click_tpl(image_path, label, **kw):
            return label in ("STARTER_PLAY", "STARTER_EVENTS", "STARTER_PLAY_CONFIRM")

        self.c._click_image_in_scaled_arena_region = click_tpl

        result = self.c._navigate_starter_deck()

        self.assertTrue(result)
        self.assertNotIn("STARTER_ALL_FILTER_FALLBACK", self.clicked_tags)

class StarterFirstTimeEntryTests(unittest.TestCase):
    """The Starter Deck Duel event has four look-alike screens, and on an account
    that has never entered it the deck grid is TWO presses away:

        "start"       event not joined      -> green "Start"
        "choose_deck" joined, no deck yet   -> "Choose Your Deck" (no Play button!)
        "chooser"     the 10-deck grid      -> "View Deck" + "Submit Deck"
        "play"        deck picked           -> orange "Play"

    Verified live on a fresh account (2026-08-19). The bug these tests pin: the
    old code inferred "not the Play page" => "already on the grid", so on the
    first two screens it clicked the grid coordinates over the event artwork and
    clicked the deck-box coordinate, which on the first-time page is the "Inspect
    Event Decks" thumbnail -- opening the read-only card list, an anchorless
    screen the bot could not navigate out of.
    """

    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.c = Controller(log.name)

        self.buttons = tempfile.mkdtemp()
        for name in (
            "event_play.png", "event_start.png", "choose_your_deck.png",
            "view_deck.png", "view_deck_active.png", "submit_deck.PNG",
        ):
            with open(os.path.join(self.buttons, name), "wb") as f:
                f.write(b"not-a-real-png")
        self.c._buttons_dir = lambda: self.buttons

        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

        self.c._ensure_arena_region = lambda force_reacquire=False: (0, 0, 1920, 1080)
        self.c._choose_starter_deck_template = lambda colors: "assets/assert/starter_decks/WG.PNG"

        self.clicks: list[str] = []
        self.c._click_abs = lambda x, y, tag, **kw: self.clicks.append(tag)

        def click_tpl(image_path, label, **kw):
            self.clicks.append(label)
            return True

        self.c._click_image_in_scaled_arena_region = click_tpl
        # Nothing matches by image, so the deck pick uses the fixed grid point.
        self.c._locate_image_center_in_scaled_arena_region = lambda *a, **k: None

    def _screens(self, sequence):
        """Feed _detect_starter_screen a scripted sequence of screens."""
        self.seen = list(sequence)
        remaining = list(sequence)

        def detect(label):
            return remaining.pop(0) if remaining else Controller._STARTER_SCREEN_UNKNOWN

        self.c._detect_starter_screen = detect

    def test_first_time_entry_presses_start_then_choose_your_deck(self):
        """The regression. From the un-joined page the bot must press Start, then
        Choose Your Deck, and only pick a deck once the grid is actually up -- and
        it must NOT click the deck-box coordinate on the way (that is the Inspect
        Event Decks thumbnail there, i.e. the deck-viewer trap)."""
        self._screens([
            Controller._STARTER_SCREEN_START,
            Controller._STARTER_SCREEN_CHOOSE_DECK,
            Controller._STARTER_SCREEN_CHOOSER,
            Controller._STARTER_SCREEN_PLAY,   # post-submit verify
        ])

        self.c._swap_starter_deck_for_quest("GW")

        self.assertEqual(
            self.clicks,
            [
                "STARTER_EVENT_START",
                "STARTER_CHOOSE_YOUR_DECK",
                "STARTER_DECK_PICK_WG",
                "STARTER_SUBMIT_DECK",
            ],
        )
        self.assertNotIn("STARTER_DECK_BOX", self.clicks)
        self.assertNotIn("STARTER_CHOOSER_BACK_ARROW", self.clicks)

    def test_play_page_opens_the_chooser_via_the_deck_box(self):
        """With a deck already selected the deck box is the way onto the grid."""
        self._screens([
            Controller._STARTER_SCREEN_PLAY,
            Controller._STARTER_SCREEN_CHOOSER,
            Controller._STARTER_SCREEN_PLAY,
        ])

        self.c._swap_starter_deck_for_quest("GW")

        self.assertEqual(
            self.clicks,
            ["STARTER_DECK_BOX", "STARTER_DECK_PICK_WG", "STARTER_SUBMIT_DECK"],
        )

    def test_chooser_already_open_skips_straight_to_the_pick(self):
        self._screens([
            Controller._STARTER_SCREEN_CHOOSER,
            Controller._STARTER_SCREEN_PLAY,
        ])

        self.c._swap_starter_deck_for_quest("GW")

        self.assertEqual(self.clicks, ["STARTER_DECK_PICK_WG", "STARTER_SUBMIT_DECK"])

    def test_unknown_screen_backs_out_instead_of_clicking_blind(self):
        """The deck card list has no anchor. Clicking grid coordinates there is
        what stranded the bot, so an unknown screen must back out."""
        self._screens([
            Controller._STARTER_SCREEN_UNKNOWN,
            Controller._STARTER_SCREEN_PLAY,
            Controller._STARTER_SCREEN_CHOOSER,
            Controller._STARTER_SCREEN_PLAY,
        ])

        self.c._swap_starter_deck_for_quest("GW")

        self.assertEqual(
            self.clicks,
            [
                "STARTER_CHOOSER_BACK_ARROW",
                "STARTER_DECK_BOX",
                "STARTER_DECK_PICK_WG",
                "STARTER_SUBMIT_DECK",
            ],
        )

    def test_never_picks_a_deck_without_reaching_the_chooser(self):
        """If the grid never appears, keep the current deck rather than clicking
        grid coordinates over whatever screen happens to be up."""
        self._screens([Controller._STARTER_SCREEN_UNKNOWN] * 8)

        self.c._swap_starter_deck_for_quest("GW")

        self.assertNotIn("STARTER_DECK_PICK_WG", self.clicks)
        self.assertNotIn("STARTER_SUBMIT_DECK", self.clicks)

    def test_unknown_screen_gets_only_one_backout(self):
        """Each detection sweep on an unknown screen costs five template probes
        (~12s live). The back arrow only helps on the event's own card list, so on
        anything else -- Decks, Store, a load transition -- retrying just burns
        queue time; hand back to the caller instead."""
        self._screens([Controller._STARTER_SCREEN_UNKNOWN] * 8)

        self.c._swap_starter_deck_for_quest("GW")

        self.assertEqual(
            self.clicks.count("STARTER_CHOOSER_BACK_ARROW"),
            Controller._STARTER_CHOOSER_MAX_BACKOUTS,
        )

    def test_submit_deck_search_is_confined_to_the_bottom_right(self):
        """The chooser's bottom-LEFT pill is "View Deck", which opens the card
        list. Searching the whole arena for submit_deck.PNG matched it live, so
        the submit click must be region-restricted to the bottom-right corner."""
        self._screens([
            Controller._STARTER_SCREEN_CHOOSER,
            Controller._STARTER_SCREEN_PLAY,
        ])
        regions: dict[str, object] = {}

        def click_tpl(image_path, label, **kw):
            regions[label] = kw.get("rel_region")
            self.clicks.append(label)
            return True

        self.c._click_image_in_scaled_arena_region = click_tpl

        self.c._swap_starter_deck_for_quest("GW")

        self.assertEqual(regions.get("STARTER_SUBMIT_DECK"), Controller._EVENT_PLAY_ROI)
        vx, vy, vw, vh = Controller._STARTER_VIEW_DECK_ROI
        ex, ey, ew, eh = Controller._EVENT_PLAY_ROI
        self.assertLess(vx + vw, ex, "View Deck ROI must not overlap the submit ROI")


class StarterScreenDetectionTests(unittest.TestCase):
    """_detect_starter_screen must probe the chooser FIRST and via the bottom-left
    "View Deck" pill. Measured live: at 0.80 event_play.png matches all four of
    the event's pill buttons and submit_deck.PNG matches the three landing pages,
    so an orange-pill probe cannot discriminate. "View Deck" is blue and exists on
    no other screen."""

    def setUp(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        self.c = Controller(log.name)
        self.buttons = tempfile.mkdtemp()
        for name in (
            "event_play.png", "event_start.png", "choose_your_deck.png",
            "view_deck.png", "view_deck_active.png", "event_title.png",
        ):
            with open(os.path.join(self.buttons, name), "wb") as f:
                f.write(b"not-a-real-png")
        self.c._buttons_dir = lambda: self.buttons

    def _only(self, visible):
        """Only template basenames in `visible` match."""
        self.probes: list[tuple[str, object, float]] = []

        def locate(image_path, label, **kw):
            name = os.path.basename(image_path)
            self.probes.append((name, kw.get("rel_region"), kw.get("confidence")))
            return (100, 100) if name in visible else None

        self.c._locate_image_center_in_scaled_arena_region = locate

    def test_each_screen_is_identified_by_its_own_button(self):
        for visible, expected in (
            ({"view_deck.png"}, Controller._STARTER_SCREEN_CHOOSER),
            ({"view_deck_active.png"}, Controller._STARTER_SCREEN_CHOOSER),
            ({"event_title.png", "event_play.png"}, Controller._STARTER_SCREEN_PLAY),
            ({"event_title.png", "event_start.png"}, Controller._STARTER_SCREEN_START),
            ({"event_title.png", "choose_your_deck.png"}, Controller._STARTER_SCREEN_CHOOSE_DECK),
            ({"event_title.png"}, Controller._STARTER_SCREEN_UNKNOWN),
            (set(), Controller._STARTER_SCREEN_UNKNOWN),
        ):
            with self.subTest(visible=visible):
                self._only(visible)
                self.assertEqual(self.c._detect_starter_screen("T"), expected)

    def test_chooser_wins_over_an_orange_pill_false_positive(self):
        """On the chooser, submit_deck/event_play style false positives must not
        win: the View Deck anchor is checked first and decides."""
        self._only({"view_deck.png", "event_play.png", "event_title.png"})
        self.assertEqual(self.c._detect_starter_screen("T"), Controller._STARTER_SCREEN_CHOOSER)

    def test_probes_use_the_right_regions_and_a_strict_confidence(self):
        self._only(set())
        self.c._detect_starter_screen("T")

        by_name = {name: (roi, conf) for name, roi, conf in self.probes}
        for tpl in ("view_deck.png", "view_deck_active.png"):
            self.assertEqual(by_name[tpl][0], Controller._STARTER_VIEW_DECK_ROI)
        self.assertEqual(by_name["event_title.png"][0], Controller._STARTER_TITLE_ROI)
        for tpl, (_roi, conf) in by_name.items():
            self.assertEqual(conf, Controller._STARTER_SCREEN_CONFIDENCE, tpl)
        self.assertGreaterEqual(
            Controller._STARTER_SCREEN_CONFIDENCE, 0.90,
            "below 0.90 the event's pill buttons are not separable (measured live)",
        )

    def test_first_time_pages_also_count_as_landing_pages(self):
        """The reward-popup guard keys off _on_starter_event_landing_page. Start
        and Choose Your Deck are one click from joining/queueing too, so a blind
        'claim' there is just as harmful as on the Play page."""
        for tpl in ("event_play.png", "event_start.png", "choose_your_deck.png"):
            with self.subTest(tpl=tpl):
                self._only({"event_title.png", tpl})
                self.assertTrue(self.c._on_starter_event_landing_page("T"))
        self._only({"view_deck.png"})
        self.assertFalse(self.c._on_starter_event_landing_page("T"))
        self._only(set())
        self.assertFalse(self.c._on_starter_event_landing_page("T"))


class StarterDeckGridGeometryTests(unittest.TestCase):
    """Grid coordinates measured live (2026-08-19) by locating all 10 deck-art
    templates in a screenshot of the chooser. The previous row values were the
    art's bottom EDGE, so the fixed-grid fallback clicked the seam between the
    card and its name plate and selected nothing."""

    # name -> measured art center in the 1920x1080 arena frame.
    MEASURED = {
        "WU": (180, 385), "WG": (472, 388), "UB": (769, 387), "UG": (1061, 384),
        "WR": (1353, 389), "BG": (1643, 389),
        "RG": (185, 703), "BR": (478, 701), "WB": (764, 699), "UR": (1057, 696),
    }

    def test_grid_points_land_on_the_measured_card_centers(self):
        log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log.close()
        c = Controller(log.name)
        for code, (mx, my) in self.MEASURED.items():
            with self.subTest(deck=code):
                point = c._starter_deck_grid_point(code)
                self.assertIsNotNone(point)
                # Well inside a card: the art is ~293x249 in this frame.
                self.assertLess(abs(point[0] - mx), 60, f"{code} x off")
                self.assertLess(abs(point[1] - my), 60, f"{code} y off")


if __name__ == "__main__":
    unittest.main()


