"""Unit tests for the modal card-prompt gate (SearchReq / OrderReq).

Background (diagnosed from live artifacts, 2026-07-25): casting Circuitous Route
("search your library for up to two basic land cards and/or Gate cards") makes
MTGA open a modal card browser. The bot had no pattern for
GREMessageType_SearchReq, so it kept treating the state as a normal main phase --
`Stack present, no pass action, but 11 actions available. Proceeding with
decision.` -- decided "play a land from hand", and then swept the hand row for a
card it could not reach: `SCAN_STOPPED: No hover update before bounds`, retried,
and idled until the inactivity timer conceded the game.

The real message sequence, taken from the Player.log:

    GREMessageType_SearchReq   maxFind=2, itemsSought=[14 legal candidates]
    ClientMessageType_SearchResp   itemsFound=[173, 206]
    GREMessageType_OrderReq    ids=[325, 326]
    ClientMessageType_OrderResp

Both Req types are modal, hence both are gated here. These tests cover the
recognition and the pause; physically answering the prompt is separate work.
"""
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Controller.MTGAController.Controller import Controller


MY_SEAT = 2
SOURCE_ID = 310
SOUGHT = [278, 275, 267, 246, 233, 238, 268, 262, 239, 258, 242, 255, 261, 240]


def search_req_line(*, seats=(MY_SEAT,), max_find=2, sought=None, source_id=SOURCE_ID) -> str:
    """A SearchReq log line shaped like the one MTGA emitted for Circuitous Route."""
    return "some log prefix " + json.dumps({
        "greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_SearchReq",
            "systemSeatIds": list(seats),
            "msgId": 165,
            "prompt": {"promptId": 2209, "parameters": [
                {"parameterName": "CardId", "type": "ParameterType_Number",
                 "numberValue": source_id},
            ]},
            "searchReq": {
                "maxFind": max_find,
                "zonesToSearch": [36],
                "itemsToSearch": list(range(229, 279)),
                "itemsSought": list(SOUGHT if sought is None else sought),
                "sourceId": source_id,
            },
            "allowCancel": "AllowCancel_No",
        }]}
    })


def order_req_line(*, seats=(MY_SEAT,), ids=(325, 326), source_id=320) -> str:
    """An OrderReq line -- the follow-up prompt after a search is answered. Note
    it carries no sourceId; the source card is in the prompt parameters."""
    return "some log prefix " + json.dumps({
        "greToClientEvent": {"greToClientMessages": [{
            "type": "GREMessageType_OrderReq",
            "systemSeatIds": list(seats),
            "msgId": 230,
            "prompt": {"promptId": 91, "parameters": [
                {"parameterName": "CardId", "type": "ParameterType_Number",
                 "numberValue": source_id},
            ]},
            "orderReq": {"ids": list(ids)},
            "allowCancel": "AllowCancel_No",
        }]}
    })


def make_controller() -> Controller:
    f = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
    f.close()
    c = Controller(f.name)
    c._Controller__system_seat_id = MY_SEAT
    return c


def set_stack(c: Controller, ids) -> None:
    """Point the controller's game state at a stack containing `ids`."""
    c.updated_game_state.get_zone = lambda name: (
        {"objectInstanceIds": list(ids)} if name == "ZoneType_Stack" else None
    )


class SearchReqRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        set_stack(self.c, [SOURCE_ID])

    def pending(self):
        return self.c._Controller__pending_card_prompt

    def test_search_req_is_recorded_with_what_mtga_told_us(self):
        self.c._Controller__handle_search_req(search_req_line())

        p = self.pending()
        self.assertIsNotNone(p)
        self.assertEqual(p["kind"], "search")
        self.assertEqual(p["max_find"], 2)
        self.assertEqual(p["candidates"], SOUGHT)
        self.assertEqual(p["zones"], [36])
        self.assertEqual(p["source_id"], SOURCE_ID)
        self.assertEqual(p["allow_cancel"], "AllowCancel_No")

    def test_open_search_prompt_pauses_decisions(self):
        self.assertFalse(self.c._Controller__should_pause_for_card_prompt())

        self.c._Controller__handle_search_req(search_req_line())

        self.assertTrue(self.c._Controller__should_pause_for_card_prompt())

    def test_opponents_prompt_is_ignored(self):
        """systemSeatIds names who must answer; another seat's prompt must not
        pause us or we would stall on every opponent search."""
        self.c._Controller__handle_search_req(search_req_line(seats=(1,)))

        self.assertIsNone(self.pending())
        self.assertFalse(self.c._Controller__should_pause_for_card_prompt())

    def test_prompt_without_a_seat_list_is_taken_as_ours(self):
        self.c._Controller__handle_search_req(search_req_line(seats=()))

        self.assertIsNotNone(self.pending())

    def test_malformed_line_is_survived(self):
        self.c._Controller__handle_search_req("no json here at all")
        self.c._Controller__handle_search_req("prefix {not valid json")

        self.assertIsNone(self.pending())

    def test_stop_requested_records_nothing(self):
        self.c._stop_requested = True

        self.c._Controller__handle_search_req(search_req_line())

        self.assertIsNone(self.pending())


class OrderReqRecognitionTests(unittest.TestCase):
    """The follow-up prompt. Handling only the search would move the stall one
    step later rather than removing it."""

    def setUp(self):
        self.c = make_controller()
        set_stack(self.c, [320])

    def test_order_req_is_recorded(self):
        self.c._Controller__handle_order_req(order_req_line())

        p = self.c._Controller__pending_card_prompt
        self.assertIsNotNone(p)
        self.assertEqual(p["kind"], "order")
        self.assertEqual(p["candidates"], [325, 326])

    def test_order_source_is_read_from_the_prompt_parameters(self):
        """OrderReq has no sourceId field, so the stack-based self-clear only
        works if the source is picked up from prompt.parameters."""
        self.c._Controller__handle_order_req(order_req_line(source_id=320))

        self.assertEqual(self.c._Controller__pending_card_prompt["source_id"], 320)

    def test_open_order_prompt_pauses_decisions(self):
        self.c._Controller__handle_order_req(order_req_line())

        self.assertTrue(self.c._Controller__should_pause_for_card_prompt())


class CardPromptSelfClearTests(unittest.TestCase):
    """The gate must never be able to wedge the bot: a prompt we somehow miss the
    end of has to clear itself."""

    def setUp(self):
        self.c = make_controller()
        set_stack(self.c, [SOURCE_ID])
        self.c._Controller__handle_search_req(search_req_line())
        self.assertTrue(self.c._Controller__should_pause_for_card_prompt())

    def test_clears_when_the_source_spell_leaves_the_stack(self):
        set_stack(self.c, [999])  # something else is on the stack now

        self.assertFalse(self.c._Controller__should_pause_for_card_prompt())
        self.assertIsNone(self.c._Controller__pending_card_prompt)

    def test_an_empty_stack_read_does_not_clear_it(self):
        """A game-state update without the stack zone must not be mistaken for
        'the spell resolved' -- that would drop the gate on the first such
        message, which is most of them."""
        set_stack(self.c, [])

        self.assertTrue(self.c._Controller__should_pause_for_card_prompt())

    def test_missing_stack_zone_does_not_clear_it(self):
        self.c.updated_game_state.get_zone = lambda name: None

        self.assertTrue(self.c._Controller__should_pause_for_card_prompt())

    def test_clears_after_the_timeout(self):
        self.c._Controller__pending_card_prompt["ts"] = (
            time.time() - self.c._Controller__card_prompt_timeout_sec - 1.0
        )

        self.assertFalse(self.c._Controller__should_pause_for_card_prompt())
        self.assertIsNone(self.c._Controller__pending_card_prompt)

    def test_reset_for_new_game_drops_it(self):
        """Its source id belongs to a game that no longer exists, so the
        stack-based clear cannot fire -- only the reset saves the next match."""
        self.c.reset_for_new_game()

        self.assertIsNone(self.c._Controller__pending_card_prompt)
        self.assertFalse(self.c._Controller__should_pause_for_card_prompt())


class RedriveGuardTests(unittest.TestCase):
    """The decision heartbeat re-drives stalled decisions. Into an open modal
    prompt that just repeats the move that cannot land -- which is what kept
    re-triggering the hand-row sweep."""

    def setUp(self):
        self.c = make_controller()
        self.c._Controller__has_mulled_keep = True
        self.c.updated_game_state.get_turn_info = lambda: {"decisionPlayer": MY_SEAT}
        self.c.updated_game_state.is_complete = lambda: True
        set_stack(self.c, [SOURCE_ID])

    def test_heartbeat_refuses_while_a_prompt_is_open(self):
        self.assertTrue(self.c._Controller__safe_to_redrive_decision())

        self.c._Controller__handle_search_req(search_req_line())

        self.assertFalse(self.c._Controller__safe_to_redrive_decision())

    def test_heartbeat_resumes_once_the_prompt_is_gone(self):
        self.c._Controller__handle_search_req(search_req_line())
        self.assertFalse(self.c._Controller__safe_to_redrive_decision())

        set_stack(self.c, [999])  # spell resolved

        self.assertTrue(self.c._Controller__safe_to_redrive_decision())


class DecisionPathGateTests(unittest.TestCase):
    """The primary gate, driven end-to-end through __update_game_state -- the path
    that actually produced the bug. A game-state update arriving while the browser
    is open must not arm a decision; the same update with no prompt open must."""

    def setUp(self):
        from unittest.mock import patch

        from tests.test_controller_decision_guards import make_raw_dict, seed_state

        self.make_raw_dict = make_raw_dict
        self.c = make_controller()
        self.c._Controller__system_seat_id = 1  # raw_dict helper decides for seat 1
        self.c._Controller__has_mulled_keep = True
        seed_state(self.c, decision_player=1, active_player=1, stack_object_ids=[SOURCE_ID])

        # The decision is armed inline via threading.Timer, not through a named
        # method, so the observable effect of the gate is "no decision timer got
        # created". Recording Timer construction also keeps real timers from
        # firing a decision during the test.
        self.timers = []
        outer = self

        class _FakeTimer:
            def __init__(self, interval, function, *a, **k):
                self.interval = interval
                self.function = function
                outer.timers.append(self)

            def start(self):
                pass

            def cancel(self):
                pass

            def is_alive(self):
                return False

        patcher = patch(
            "Controller.MTGAController.Controller.threading.Timer", _FakeTimer
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _decision_timers(self):
        return [
            t for t in self.timers
            if "decision" in getattr(t.function, "__name__", "").lower()
        ]

    def _push_update(self):
        """Reproduce the real state: our spell on the stack, no Pass action, but
        other actions still listed -- the combination that made the controller log
        "Stack present, no pass action, but N actions available. Proceeding with
        decision." and then act into the modal window."""
        raw = self.make_raw_dict(
            decision_player=1, active_player=1, stack_object_ids=[SOURCE_ID],
            game_state_id=101, prev_game_state_id=100,
        )
        msg = raw["greToClientEvent"]["greToClientMessages"][0]["gameStateMessage"]
        msg["actions"] = [
            {"type": "ActionType_Play", "instanceId": 308},
            {"type": "ActionType_Cast", "instanceId": 293},
        ]
        self.c._Controller__update_game_state(raw)

    def test_a_decision_is_armed_when_nothing_blocks(self):
        """Positive control: without it, the test below would pass even if the
        update never reached the decision path at all."""
        self._push_update()

        self.assertTrue(
            self._decision_timers(),
            "precondition: this update must normally arm a decision",
        )

    def test_no_decision_is_armed_while_a_search_prompt_is_open(self):
        self.c._Controller__handle_search_req(search_req_line(seats=(1,)))

        self._push_update()

        self.assertEqual(
            self._decision_timers(), [],
            "a decision armed here is exactly the 'play a land' move that then "
            "swept the hand row for a card behind a modal window",
        )

    def test_armed_decision_is_cancelled_when_the_prompt_arrives(self):
        """A decision already armed when the prompt opens must be dropped, not
        left to fire into the browser a moment later."""
        class _Timer:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        timer = _Timer()
        self.c._Controller__decision_execution_thread = timer

        self.c._Controller__handle_search_req(search_req_line(seats=(1,)))

        self.assertTrue(timer.cancelled)
        self.assertIsNone(self.c._Controller__decision_execution_thread)


class PatternWiringTests(unittest.TestCase):
    def test_both_request_types_are_watched(self):
        c = make_controller()
        self.assertEqual(
            c.patterns["search_req"], '"type": "GREMessageType_SearchReq"'
        )
        self.assertEqual(
            c.patterns["order_req"], '"type": "GREMessageType_OrderReq"'
        )


class ClickPointGeometryTests(unittest.TestCase):
    """The click spread. Its one hard requirement: never hit the same card twice --
    a second click on a chosen card toggles it back off, which is the only way this
    can silently take fewer cards than asked for."""

    def setUp(self):
        self.c = make_controller()

    def points(self, candidates, picks):
        return self.c._Controller__card_prompt_click_points(candidates, picks)

    def card_step(self, candidates):
        return min(
            float(Controller._CARD_PROMPT_CARD_MAX_W),
            float(Controller._CARD_PROMPT_FAN_FULL_W) / float(candidates),
        )

    def test_returns_one_point_per_pick(self):
        self.assertEqual(len(self.points(14, 2)), 2)
        self.assertEqual(len(self.points(14, 1)), 1)

    def test_picks_are_clamped_to_the_candidate_count(self):
        """"Up to two" with a single legal card must click once, not twice on it."""
        self.assertEqual(len(self.points(1, 2)), 1)

    def test_degenerate_inputs_yield_nothing(self):
        self.assertEqual(self.points(0, 2), [])
        self.assertEqual(self.points(5, 0), [])

    def test_points_never_share_a_card(self):
        for candidates in range(2, 21):
            for picks in (2, 3):
                pts = self.points(candidates, picks)
                if len(pts) < 2:
                    continue
                xs = sorted(x for x, _ in pts)
                gaps = [b - a for a, b in zip(xs, xs[1:])]
                self.assertGreaterEqual(
                    min(gaps), self.card_step(candidates) * 0.9,
                    f"candidates={candidates} picks={picks} pts={pts} would toggle a card off",
                )

    def test_points_stay_inside_the_fan(self):
        for candidates in range(1, 21):
            step = self.card_step(candidates)
            half = candidates * step / 2.0
            lo = Controller._CARD_PROMPT_FAN_CENTER_X - half
            hi = Controller._CARD_PROMPT_FAN_CENTER_X + half
            for x, _y in self.points(candidates, 2):
                self.assertGreaterEqual(x, lo - 1)
                self.assertLessEqual(x, hi + 1)

    def test_click_row_avoids_the_other_controls(self):
        """"View Battlefield" sits at y<=115 and the pager near y~780; the click row
        must be clear of both, so a horizontal miss only hits dead background."""
        y = Controller._CARD_PROMPT_FAN_CLICK_Y
        self.assertGreater(y, 150)
        self.assertLess(y, 700)

    def test_points_are_on_screen_within_the_reference_frame(self):
        for candidates in (1, 2, 3, 7, 14, 20):
            for x, y in self.points(candidates, 2):
                self.assertGreater(x, 0)
                self.assertLess(x, 1920)
                self.assertGreater(y, 0)
                self.assertLess(y, 1080)


class AnswerCardPromptTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch

        self.c = make_controller()
        set_stack(self.c, [SOURCE_ID])
        patcher = patch("Controller.MTGAController.Controller.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        # Record clicks; never touch a real mouse or the screen.
        self.clicks = []
        self.c._click_abs = lambda x, y, tag, **kw: self.clicks.append((x, y, tag))
        self.c._map_abs_point_to_arena = lambda p, label=None, **kw: (p, "test")
        # No Submit template on disk in the test env -> measured-point fallback.
        self.c._click_image_in_scaled_arena_region = lambda *a, **k: False
        # Answer immediately instead of after the settle delay.
        self.timers = []
        outer = self

        class _T:
            def __init__(self, interval, function, *a, **k):
                self.function = function
                outer.timers.append(self)
                self.daemon = False

            def start(self):
                pass

            def cancel(self):
                pass

        tp = patch("Controller.MTGAController.Controller.threading.Timer", _T)
        tp.start()
        self.addCleanup(tp.stop)

    def open_search(self, **kw):
        self.c._Controller__handle_search_req(search_req_line(**kw))
        return self.c._Controller__pending_card_prompt["token"]

    def picks(self):
        return [c for c in self.clicks if c[2] == "CARD_PROMPT_PICK"]

    def submits(self):
        return [c for c in self.clicks if c[2] == "CARD_PROMPT_SUBMIT"]

    def test_answers_a_search_with_max_find_clicks_then_submit(self):
        token = self.open_search()

        self.c._Controller__answer_card_prompt(token)

        self.assertEqual(len(self.picks()), 2, "maxFind=2 -> two cards")
        self.assertEqual(len(self.submits()), 1)
        self.assertEqual(self.clicks[-1][2], "CARD_PROMPT_SUBMIT", "Submit must come last")

    def test_a_stale_token_clicks_nothing(self):
        """A prompt that closed before the settle delay elapsed must not be
        clicked into -- by then the window may be gone and the board live."""
        stale = self.open_search()
        self.c._Controller__clear_pending_card_prompt("answered by hand")

        self.c._Controller__answer_card_prompt(stale)

        self.assertEqual(self.clicks, [])

    def test_token_from_a_previous_prompt_clicks_nothing(self):
        first = self.open_search()
        self.c._Controller__clear_pending_card_prompt("resolved")
        self.open_search()  # a second, different prompt is now open

        self.c._Controller__answer_card_prompt(first)

        self.assertEqual(self.clicks, [])

    def test_stop_requested_clicks_nothing(self):
        token = self.open_search()
        self.c._stop_requested = True

        self.c._Controller__answer_card_prompt(token)

        self.assertEqual(self.clicks, [])

    def test_suppressed_selections_click_nothing(self):
        token = self.open_search()
        self.c._suppress_selections = True

        self.c._Controller__answer_card_prompt(token)

        self.assertEqual(self.clicks, [])

    def test_attempts_are_capped(self):
        token = self.open_search()

        self.c._Controller__answer_card_prompt(token)
        self.c._Controller__answer_card_prompt(token)
        self.c._Controller__answer_card_prompt(token)  # must be refused

        self.assertEqual(len(self.submits()), 2, "at most two attempts")

    def test_retry_does_not_repeat_the_same_points(self):
        """Repeating the identical spread would click the same cards again and
        toggle the first attempt's selection back off."""
        token = self.open_search()

        self.c._Controller__answer_card_prompt(token)
        first = [(x, y) for x, y, _ in self.picks()]
        self.clicks.clear()
        self.c._Controller__answer_card_prompt(token)
        second = [(x, y) for x, y, _ in self.picks()]

        self.assertTrue(second)
        self.assertNotEqual(first, second)

    def test_order_prompt_only_confirms(self):
        """Ordering decides a sequence that does not affect play, so no card is
        clicked -- only the default is confirmed."""
        self.c._Controller__handle_order_req(order_req_line())
        token = self.c._Controller__pending_card_prompt["token"]

        self.c._Controller__answer_card_prompt(token)

        self.assertEqual(self.picks(), [])
        self.assertEqual(len(self.submits()), 1)

    def test_a_retry_is_scheduled_so_the_retry_path_can_run(self):
        """The shifted-spread retry is only reachable if something re-invokes the
        answer. Nothing else does, so the attempt has to schedule its own re-check."""
        token = self.open_search()
        self.timers.clear()

        self.c._Controller__answer_card_prompt(token)

        self.assertTrue(self.timers, "no re-check scheduled -> retry is dead code")
        # Firing it must go through the same entry point with the same token.
        self.clicks.clear()
        self.timers[-1].function()
        self.assertTrue(self.picks(), "the scheduled call must run a second attempt")

    def test_the_scheduled_recheck_is_a_no_op_once_answered(self):
        token = self.open_search()
        self.c._Controller__answer_card_prompt(token)
        recheck = self.timers[-1].function
        self.c._Controller__clear_pending_card_prompt("search answered")
        self.clicks.clear()

        recheck()

        self.assertEqual(self.clicks, [])

    def test_single_candidate_search_clicks_once(self):
        token = self.open_search(sought=[278])

        self.c._Controller__answer_card_prompt(token)

        self.assertEqual(len(self.picks()), 1)


class SearchRespTests(unittest.TestCase):
    def setUp(self):
        self.c = make_controller()
        set_stack(self.c, [SOURCE_ID])
        self.c._Controller__handle_search_req(search_req_line())

    @staticmethod
    def resp_line(items):
        return "prefix " + json.dumps({
            "type": "ClientMessageType_SearchResp",
            "gameStateId": 172, "respId": 227,
            "searchResp": {"itemsFound": list(items)},
        })

    def test_full_answer_clears_the_prompt(self):
        self.c._Controller__handle_search_resp(self.resp_line([173, 206]))

        self.assertIsNone(self.c._Controller__pending_card_prompt)

    def test_partial_answer_is_still_recognised_and_clears(self):
        """One of two is a worse answer but a resolved prompt -- the gate must not
        keep blocking decisions afterwards."""
        self.c._Controller__handle_search_resp(self.resp_line([173]))

        self.assertIsNone(self.c._Controller__pending_card_prompt)

    def test_a_response_without_items_is_ignored(self):
        self.c._Controller__handle_search_resp("prefix " + json.dumps({"searchResp": {}}))

        self.assertIsNotNone(self.c._Controller__pending_card_prompt)

    def test_malformed_response_is_survived(self):
        self.c._Controller__handle_search_resp("not json")

        self.assertIsNotNone(self.c._Controller__pending_card_prompt)


class RealPlayerLogTests(unittest.TestCase):
    """Replay of the prompt messages captured from a real Player.log
    (tests/fixtures/card_prompts_player_log.json): the two Circuitous Route
    searches -- the game the bot broke on, and a hand-played repeat -- plus the
    OrderReq that followed.

    Synthetic lines cannot catch two things these do: that the watched pattern
    string is spelled the way MTGA actually writes it, and that nothing in the
    parser quietly assumes values that happen to be constant in a hand-written
    sample (the two real searches use DIFFERENT library zone ids, 36 and 32)."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "fixtures",
                            "card_prompts_player_log.json")
        with open(path, encoding="utf-8") as f:
            cls.messages = json.load(f)

    @staticmethod
    def as_log_line(message: dict) -> str:
        """Wrap a captured message the way it arrives from the log monitor."""
        return "[UnityCrossThreadLogger]GreToClientEvent " + json.dumps(
            {"greToClientEvent": {"greToClientMessages": [message]}}
        )

    def messages_of(self, type_name: str):
        return [m for m in self.messages if m.get("type") == type_name]

    def test_fixture_has_the_captured_prompts(self):
        self.assertEqual(len(self.messages_of("GREMessageType_SearchReq")), 2)
        self.assertEqual(len(self.messages_of("GREMessageType_OrderReq")), 1)

    def test_watched_patterns_match_how_mtga_spells_the_type(self):
        """If MTGA's spacing differed from the pattern string, the whole gate
        would be dead code -- the handler would simply never be called."""
        c = make_controller()
        for msg, key in (
            (self.messages_of("GREMessageType_SearchReq")[0], "search_req"),
            (self.messages_of("GREMessageType_OrderReq")[0], "order_req"),
        ):
            self.assertIn(c.patterns[key], self.as_log_line(msg))

    def test_every_real_search_is_parsed(self):
        for msg in self.messages_of("GREMessageType_SearchReq"):
            c = make_controller()
            c._Controller__system_seat_id = msg["systemSeatIds"][0]

            c._Controller__handle_search_req(self.as_log_line(msg))

            p = c._Controller__pending_card_prompt
            self.assertIsNotNone(p, "real SearchReq must be recorded")
            self.assertEqual(p["kind"], "search")
            self.assertEqual(p["max_find"], 2)
            self.assertEqual(p["candidates"], msg["searchReq"]["itemsSought"])
            self.assertEqual(p["source_id"], msg["searchReq"]["sourceId"])
            self.assertEqual(p["allow_cancel"], "AllowCancel_No")
            # Read from the message, never assumed: see the zone test below.
            self.assertEqual(p["zones"], msg["searchReq"]["zonesToSearch"])

    def test_the_zone_is_read_and_not_assumed(self):
        """The two real searches use different library zone ids (36 and 32), so a
        hardcoded zone would be wrong half the time. Asserting the fixture differs
        proves nothing on its own -- the parsed value has to track it."""
        zones = [m["searchReq"]["zonesToSearch"] for m
                 in self.messages_of("GREMessageType_SearchReq")]
        self.assertNotEqual(zones[0], zones[1], "fixture precondition")

        parsed = []
        for msg in self.messages_of("GREMessageType_SearchReq"):
            c = make_controller()
            c._Controller__system_seat_id = msg["systemSeatIds"][0]
            c._Controller__handle_search_req(self.as_log_line(msg))
            parsed.append(c._Controller__pending_card_prompt["zones"])

        self.assertEqual(parsed, zones)

    def test_real_order_req_source_comes_from_prompt_parameters(self):
        msg = self.messages_of("GREMessageType_OrderReq")[0]
        self.assertNotIn("sourceId", msg["orderReq"])
        c = make_controller()
        c._Controller__system_seat_id = msg["systemSeatIds"][0]

        c._Controller__handle_order_req(self.as_log_line(msg))

        p = c._Controller__pending_card_prompt
        self.assertIsNotNone(p)
        self.assertEqual(p["kind"], "order")
        self.assertEqual(p["candidates"], msg["orderReq"]["ids"])
        expected_source = msg["prompt"]["parameters"][0]["numberValue"]
        self.assertEqual(p["source_id"], expected_source)

    def test_real_search_resp_is_parsed_and_clears_the_prompt(self):
        resp = [m for m in self.messages
                if m.get("type") == "ClientMessageType_SearchResp"]
        self.assertEqual(len(resp), 1, "fixture precondition")
        req = self.messages_of("GREMessageType_SearchReq")[0]
        c = make_controller()
        c._Controller__system_seat_id = req["systemSeatIds"][0]
        set_stack(c, [req["searchReq"]["sourceId"]])
        c._Controller__handle_search_req(self.as_log_line(req))
        self.assertIsNotNone(c._Controller__pending_card_prompt)

        # The response is a plain client message, not wrapped in greToClientEvent.
        c._Controller__handle_search_resp("prefix " + json.dumps(resp[0]))

        self.assertIsNone(c._Controller__pending_card_prompt)

    def test_real_prompts_pause_decisions(self):
        for msg in (self.messages_of("GREMessageType_SearchReq")
                    + self.messages_of("GREMessageType_OrderReq")):
            c = make_controller()
            c._Controller__system_seat_id = msg["systemSeatIds"][0]
            handler = (
                c._Controller__handle_search_req
                if msg["type"].endswith("SearchReq")
                else c._Controller__handle_order_req
            )
            # Source still on the stack, i.e. the prompt is genuinely open.
            source = msg.get("searchReq", {}).get("sourceId") or \
                msg["prompt"]["parameters"][0]["numberValue"]
            set_stack(c, [source])

            handler(self.as_log_line(msg))

            self.assertTrue(
                c._Controller__should_pause_for_card_prompt(),
                f"{msg['type']} must pause decisions",
            )


if __name__ == "__main__":
    unittest.main()
