"""The bot must never press another event's Play button.

Reported 2026-10 (Discord): with a Draft as the last thing played on the
account, the Starter Deck Duel bot entered that Draft. Every Play press is found
by template, and the orange Play pill is the same widget on Home and on every
event's page -- measured on 53 captures, play_btn.png and event_play.png score
0.92-0.99 at the same spot on Home and on Starter Deck Duel's page alike. So
each press now needs proof of WHICH screen it is on:

- the Home Play click needs Home's own nav anchor,
- every event Play press needs the "Starter Deck Duel" header, and Player.log
  must not name a different event as the open EventLanding page,
- and if MTGA is still told to join/queue a foreign event, the bot stops.

No test here sees the screen: template search and clicks are stubbed.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import runtime_status
from Controller.MTGAController.Controller import Controller


def scene_change(to_scene: str, context: str) -> str:
    payload = {"fromSceneName": "Home", "toSceneName": to_scene, "initiator": "User", "context": context}
    return "[UnityCrossThreadLogger]Client.SceneChange " + json.dumps(payload, separators=(",", ":")) + "\n"


def event_request(request: str, event_name: str) -> str:
    """The way Player.log writes it: the request is a JSON string inside JSON."""
    inner = json.dumps({"EventName": event_name, "EventCode": None}, separators=(",", ":"))
    outer = json.dumps({"id": "7cca88af", "request": inner}, separators=(",", ":"))
    return f"[UnityCrossThreadLogger]==> {request} {outer}\n"


class _GuardTestBase(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        f.close()
        self.log_path = f.name
        self.addCleanup(lambda: os.path.exists(self.log_path) and os.unlink(self.log_path))
        self.c = Controller(self.log_path)

        self.buttons = tempfile.mkdtemp()
        for name in ("play_btn.png", "event_play.png", "event_title.png"):
            with open(os.path.join(self.buttons, name), "wb") as fh:
                fh.write(b"not-a-real-png")
        self.c._buttons_dir = lambda: self.buttons

        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
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

        # Which templates are "on screen"; everything else does not match.
        self.visible: set[str] = set()
        self.clicks: list[str] = []

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
        # Coordinate fallbacks (e.g. the All-filter row) read the arena region,
        # which would capture the real monitor.
        self.c._ensure_arena_region = lambda force_reacquire=False: (0, 0, 1920, 1080)
        self.c._options_overlay_visible = lambda: False

    def append(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(text)


class SceneParsingTests(_GuardTestBase):
    def test_a_draft_event_page_is_foreign(self):
        self.append(scene_change("EventLanding", "QuickDraft_EOE_20261001"))
        self.assertEqual(self.c._foreign_event_landing_name(), "QuickDraft_EOE_20261001")

    def test_starter_deck_duel_page_is_not_foreign(self):
        self.append(scene_change("EventLanding", Controller._STARTER_EVENT_NAME))
        self.assertIsNone(self.c._foreign_event_landing_name())

    def test_leaving_the_event_page_clears_it(self):
        self.append(scene_change("EventLanding", "QuickDraft_EOE_20261001"))
        self.append(scene_change("Home", "Landing Page 'Home'"))
        self.assertIsNone(self.c._foreign_event_landing_name())
        self.assertEqual(self.c._get_last_scene_name(), "Home")

    def test_no_scene_logged_yet(self):
        self.assertIsNone(self.c._foreign_event_landing_name())


class StarterNavigationGateTests(_GuardTestBase):
    def test_foreign_event_page_presses_nothing_and_goes_home(self):
        """The reported bug: parked on the Draft's page, step 1 pressed its Play."""
        self.append(scene_change("EventLanding", "QuickDraft_EOE_20261001"))
        # Everything a pill template could match is on screen -- as it is there.
        self.visible = {"play_btn.png", "event_play.png", "home_anchor.png"}

        self.assertFalse(self.c._navigate_starter_deck())

        self.assertEqual(self.clicks, [])
        self.c._navigate_to_home.assert_called_once()

    def test_home_play_is_not_clicked_without_the_home_anchor(self):
        """The pill alone is any event's Play button."""
        self.visible = {"play_btn.png", "event_play.png"}
        self.c._find_event_banner_scrolling = lambda *a, **k: False

        self.c._navigate_starter_deck()

        self.assertNotIn("STARTER_PLAY", self.clicks)

    def test_home_play_is_clicked_on_a_proven_home(self):
        self.visible = {"play_btn.png", "home_anchor.png", "events_tab.png"}
        self.c._find_event_banner_scrolling = lambda *a, **k: False

        self.c._navigate_starter_deck()

        self.assertIn("STARTER_PLAY", self.clicks)

    def test_banner_to_a_page_without_the_starter_header_is_not_played(self):
        """The banner match is only 0.72 and In Progress also lists a Draft."""
        self.visible = {"play_btn.png", "home_anchor.png", "events_tab.png", "event_play.png"}
        self.c._find_event_banner_scrolling = lambda *a, **k: True

        self.assertFalse(self.c._navigate_starter_deck())

        self.assertNotIn("STARTER_EVENT_PLAY", self.clicks)
        self.assertNotIn("STARTER_PLAY_CONFIRM", self.clicks)

    def test_banner_to_starter_deck_duel_is_played(self):
        self.visible = {
            "play_btn.png", "home_anchor.png", "events_tab.png",
            "event_play.png", "event_title.png",
        }
        self.c._find_event_banner_scrolling = lambda *a, **k: True

        self.assertTrue(self.c._navigate_starter_deck())

        self.assertIn("STARTER_EVENT_PLAY", self.clicks)

    def test_header_does_not_outvote_the_log(self):
        """A foreign EventLanding in Player.log refuses even with the header seen."""
        self.append(scene_change("EventLanding", "QuickDraft_EOE_20261001"))
        self.visible = {"event_play.png", "event_title.png"}

        self.assertFalse(self.c._press_starter_event_play())
        self.assertNotIn("STARTER_EVENT_PLAY", self.clicks)

    def test_event_landing_requeue_reproves_the_page_after_the_swap(self):
        self.c._on_starter_event_landing_page = lambda label: True
        # The swap ended somewhere that is not Starter Deck Duel's page.
        self.visible = {"event_play.png"}

        self.assertFalse(self.c._queue_from_event_landing(""))
        self.assertNotIn("STARTER_EVENT_PLAY", self.clicks)


class HistoricQueueGateTests(_GuardTestBase):
    def test_queue_click_is_skipped_on_a_foreign_event_page(self):
        self.c._game_mode = "historic"
        self.c.reroll_quest_on_landing = lambda: True
        self.c._account_switch_due = lambda: False
        self.c._ensure_historic_selection = lambda: True
        self.c._historic_selection_key = "cached"
        self.c.input = Mock()
        self.append(scene_change("EventLanding", "QuickDraft_EOE_20261001"))

        self.c.start_game_from_home_screen()

        self.c.input.left_down.assert_not_called()
        self.assertIsNone(self.c._historic_selection_key)
        self.c._navigate_to_home.assert_called_once()


class ForeignEventEntryTests(_GuardTestBase):
    def setUp(self):
        super().setUp()
        self.stops: list[str] = []
        self.c.set_stop_bot_callback(self.stops.append)
        self.c._game_mode = "starter"

    def arm(self):
        self.c._event_entry_scan_offset = os.path.getsize(self.log_path)

    def test_starter_deck_duel_pairing_is_allowed(self):
        self.arm()
        self.append(event_request("EventEnterPairing", Controller._STARTER_EVENT_NAME))
        self.assertFalse(self.c._check_foreign_event_entry())
        self.assertEqual(self.stops, [])

    def test_joining_a_draft_stops_the_bot(self):
        self.arm()
        self.append(event_request("EventJoin", "QuickDraft_EOE_20261001"))
        self.assertTrue(self.c._check_foreign_event_entry())
        self.assertEqual(len(self.stops), 1)
        self.assertTrue(self.c._stop_requested)

    def test_entries_from_before_start_are_not_the_bots(self):
        self.append(event_request("EventJoin", "QuickDraft_EOE_20261001"))
        self.arm()
        self.assertFalse(self.c._check_foreign_event_entry())

    def test_unarmed_guard_is_idle(self):
        self.append(event_request("EventJoin", "QuickDraft_EOE_20261001"))
        self.assertIsNone(self.c._event_entry_scan_offset)
        self.assertFalse(self.c._check_foreign_event_entry())

    def test_a_half_written_line_is_read_once_complete(self):
        self.arm()
        line = event_request("EventJoin", "QuickDraft_EOE_20261001")
        self.append(line[:-30])
        self.assertFalse(self.c._check_foreign_event_entry())
        self.append(line[-30:])
        self.assertTrue(self.c._check_foreign_event_entry())

    def test_each_entry_is_reported_once(self):
        self.arm()
        self.append(event_request("EventEnterPairing", Controller._STARTER_EVENT_NAME))
        self.c._check_foreign_event_entry()
        self.append(scene_change("Home", "Landing Page 'Home'"))
        self.assertFalse(self.c._check_foreign_event_entry())

    def test_historic_mode_forbids_limited_events_only(self):
        self.c._game_mode = "historic"
        self.assertTrue(self.c._event_entry_forbidden("QuickDraft_EOE_20261001"))
        self.assertTrue(self.c._event_entry_forbidden("Sealed_EOE_20261001"))
        self.assertFalse(self.c._event_entry_forbidden("Play_Historic"))

    def test_begin_session_arms_the_guard_at_the_current_end_of_log(self):
        self.append(event_request("EventJoin", "QuickDraft_EOE_20261001"))
        self.c._start_popup_recovery = lambda: None
        self.c.begin_session()
        self.assertEqual(self.c._event_entry_scan_offset, os.path.getsize(self.log_path))


class UnconfiguredAccountWarningTests(_GuardTestBase):
    def test_warns_once_for_an_account_not_in_the_config(self):
        self.c._load_accounts_from_dirs = lambda: [{"name": "Barrylim", "screen_name": "Barrylim#08112"}]
        self.c._current_account_config = lambda: None
        self.c._current_account_screen_name = "Stranger#12345"
        with patch("Controller.MTGAController.Controller.bot_logger.log_error") as log_error:
            self.c._warn_if_unconfigured_account()
            self.c._warn_if_unconfigured_account()
        self.assertEqual(log_error.call_count, 1)
        self.assertIn("Stranger#12345", log_error.call_args[0][0])

    def test_configured_account_is_silent(self):
        account = {"name": "Barrylim", "screen_name": "Barrylim#08112"}
        self.c._load_accounts_from_dirs = lambda: [account]
        self.c._current_account_config = lambda: account
        self.c._current_account_screen_name = "Barrylim#08112"
        with patch("Controller.MTGAController.Controller.bot_logger.log_error") as log_error:
            self.c._warn_if_unconfigured_account()
        log_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
