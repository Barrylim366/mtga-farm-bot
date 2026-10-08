"""Scry/surveil advice: reading the prompt and what gets published.

    python -m unittest forge_tests.test_scry_hook -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge_bridge import forge_ai, scry_hook  # noqa: E402
from forge_bridge.carddb import CardDb  # noqa: E402
from forge_bridge.tracker import ArenaTracker  # noqa: E402
from forge_tests import gre  # noqa: E402

CARDS = CardDb({"cards": {str(grp): {"name": name} for grp, name in gre.NAMES.items()}, "counters": {}})


class Advisor:
    running = True

    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def decide(self, kind, state, timeout=10.0, **extra):
        self.calls.append((kind, state, extra))
        return {"decision": self.decision, "diag": {}}


def prompt(context: str, ids: list[int]) -> str:
    return gre.log_line({
        "type": "GREMessageType_GroupReq", "systemSeatIds": [gre.ME],
        "groupReq": {"instanceIds": ids, "context": context, "groupType": "GroupType_Ordered"},
    })


class ScryHookTests(unittest.TestCase):
    def setUp(self):
        self.tracker = ArenaTracker()
        self.tracker.feed_message(gre.connect())
        message = gre.state_message(battlefield=[gre.land(200)])
        # the looked-at card is an object the client can see, still in the library
        message["gameStateMessage"]["gameObjects"].append(gre.card(1000, gre.FOREST, 32))
        self.tracker.feed_message(message)
        forge_ai.FEED.clear()

    def test_reads_scry_and_surveil_prompts_and_ignores_other_groupings(self):
        self.assertEqual(
            scry_hook.group_requests(prompt("GroupingContext_Scry", [1000])),
            [{"mode": "scry", "ids": [1000], "seats": [gre.ME]}],
        )
        self.assertEqual(scry_hook.group_requests(prompt("GroupingContext_Surveil", [1000]))[0]["mode"], "surveil")
        self.assertEqual(scry_hook.group_requests(prompt("GroupingContext_LondonMulligan", [1000])), [])
        self.assertEqual(scry_hook.group_requests("not json at all"), [])

    def test_asks_forge_with_the_card_on_top_of_our_library(self):
        advisor = Advisor({"top": [], "away": [1000], "topNames": [], "awayNames": ["Forest"]})
        bridge = forge_ai.Bridge(client=advisor, cards=CARDS, tracker=self.tracker)  # type: ignore[arg-type]
        request = scry_hook.group_requests(prompt("GroupingContext_Scry", [1000]))[0]
        scry_hook.advise(bridge, request, forge_ai.Config())
        kind, state, extra = advisor.calls[0]
        self.assertEqual((kind, extra["mode"], extra["cards"]), ("scry", "scry", [1000]))
        self.assertEqual(state["players"][0]["library"], [{"id": 1000, "name": "Forest"}])
        _, heading, explanation = forge_ai.FEED[-1]
        self.assertIn("Scry", heading)
        self.assertIn("send Forest to the bottom", explanation)
        self.assertIn("Advice only", explanation)  # no controller was given, so nothing can be dragged

    def test_surveil_sends_to_the_graveyard(self):
        advisor = Advisor({"top": [], "away": [1000], "topNames": [], "awayNames": ["Forest"]})
        bridge = forge_ai.Bridge(client=advisor, cards=CARDS, tracker=self.tracker)  # type: ignore[arg-type]
        request = scry_hook.group_requests(prompt("GroupingContext_Surveil", [1000]))[0]
        scry_hook.advise(bridge, request, forge_ai.Config())
        self.assertIn("to the graveyard", forge_ai.FEED[-1][2])

    def test_a_card_we_cannot_name_is_not_judged(self):
        advisor = Advisor({})
        bridge = forge_ai.Bridge(client=advisor, cards=CARDS, tracker=self.tracker)  # type: ignore[arg-type]
        request = scry_hook.group_requests(prompt("GroupingContext_Scry", [4242]))[0]
        self.assertIsNone(scry_hook.advise(bridge, request, forge_ai.Config()))
        self.assertEqual(advisor.calls, [])

    # -------------------------------------------------------------- dragging
    def overlay(self, cards: dict):
        """The overlay as a row of cards the cursor can find, plus a record of every drag."""
        from forge_tests.test_pointer import FakeBoard

        class Overlay(FakeBoard):
            def __init__(inner, cards):
                super().__init__(cards)
                inner.drags = []
                inner._pressed_at = None

            def _map_abs_point_to_arena(inner, point, **kwargs):
                return point, "test"

            def left_down(inner):
                inner._pressed_at = inner.position

            def left_up(inner):
                inner.drags.append((inner._pressed_at, inner.position))

        board = Overlay(cards)
        # the scry strip sits at y 470-490 in the reference frame; FakeBoard's cards live at y 300-400
        board.move_abs = self._shifted(board.move_abs)
        return board

    @staticmethod
    def _shifted(move_abs):
        def move(x, y):
            return move_abs(x, y - 130 if 440 <= y <= 560 else y)
        return move

    def act(self, board, decision, ids):
        from unittest import mock

        request = {"mode": "scry", "ids": ids, "seats": [gre.ME]}
        with mock.patch.object(scry_hook.time, "sleep"), mock.patch("forge_bridge.pointer.time.sleep"):
            return scry_hook.act(board, request, decision, lambda message: None)

    def test_drags_the_unwanted_card_to_the_bottom_side(self):
        board = self.overlay({1000: (1000, 1110), 1001: (1150, 1260)})
        moved = self.act(board, {"away": [1001]}, [1000, 1001])
        self.assertEqual(moved, [1001])
        (start, end), = board.drags
        self.assertTrue(1150 <= start[0] <= 1260, start)
        self.assertEqual(end[0], scry_hook.LAYOUT["scry"]["away"][0])

    def test_a_lone_card_that_is_not_reported_is_dragged_from_its_usual_place(self):
        board = self.overlay({})  # the client reports no hover at all
        moved = self.act(board, {"away": [1000]}, [1000])
        self.assertEqual(moved, [1000])
        self.assertEqual(board.drags[0][0][0], scry_hook.LAYOUT["scry"]["single"][0])

    def test_one_of_several_cards_that_cannot_be_found_stays_put(self):
        board = self.overlay({1000: (1000, 1110)})
        self.assertEqual(self.act(board, {"away": [1001]}, [1000, 1001]), [])
        self.assertEqual(board.drags, [])

    def test_nothing_is_dragged_when_forge_keeps_everything(self):
        board = self.overlay({1000: (1000, 1110)})
        self.assertEqual(self.act(board, {"away": []}, [1000]), [])
        self.assertEqual(board.drags, [])

    def test_the_drag_happens_before_the_bot_presses_done(self):
        from unittest import mock

        order = []
        wrapped = scry_hook.wrap(lambda controller, line: order.append("done"))
        with mock.patch.object(scry_hook, "advise", side_effect=lambda *a, **k: order.append("drag")):
            wrapped(type("C", (), {})(), prompt("GroupingContext_Scry", [1000]))
            # the same prompt logged again is not acted on twice
            wrapped(type("C", (), {})(), prompt("GroupingContext_Scry", [1000]))
        self.assertEqual(order, ["drag", "done", "done"])

    def test_the_bots_own_handler_always_runs(self):
        seen = []
        wrapped = scry_hook.wrap(lambda controller, line: seen.append(line) or "handled")
        line = prompt("GroupingContext_Scry", [1000])
        self.assertEqual(wrapped(object(), line), "handled")
        self.assertEqual(seen, [line])


if __name__ == "__main__":
    unittest.main()
