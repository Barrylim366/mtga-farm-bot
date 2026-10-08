"""The hook into mtga-farm-bot, exercised with the bot's real AI class and a stand-in advisor.

    python -m unittest forge_tests.test_forge_ai -v

Checks the part that matters most for a live game: Forge's answer is used only when the bot can carry
it out, and every failure ends in the move the bot would have made anyway.
"""
from __future__ import annotations

import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT
sys.path.insert(0, str(ROOT))

from AI.DummyAI import DummyAI  # noqa: E402
from Controller.Utilities.GameState import GameState  # noqa: E402

from forge_bridge import forge_ai  # noqa: E402
from forge_bridge.carddb import CardDb  # noqa: E402
from forge_bridge.client import AdvisorError  # noqa: E402
from forge_bridge.tracker import ArenaTracker  # noqa: E402
from forge_tests import gre  # noqa: E402

CARDS = CardDb({"cards": {str(grp): {"name": name} for grp, name in gre.NAMES.items()}, "counters": {}})

ELVES, BEETLE, STOMPER, UNCLICKABLE, LAND_IN_HAND = 101, 102, 103, 104, 105
# instance id -> Arena card id, which the bot's controller hands to its AI alongside the state
IDS = {
    ELVES: gre.LLANOWAR_ELVES,
    BEETLE: gre.NESSIAN_HORNBEETLE,
    STOMPER: gre.APOTHECARY_STOMPER,
    UNCLICKABLE: gre.INSPIRATION_FROM_BEYOND,
    LAND_IN_HAND: gre.FOREST,
    200: gre.FOREST,
    201: gre.FOREST,
}


class FakeAdvisor:
    """Answers each decision kind with a canned reply, or raises it if it is an exception."""

    running = True

    def __init__(self, **replies):
        self.replies = replies
        self.calls: list[tuple[str, dict, dict]] = []

    def decide(self, kind, state, timeout=10.0, **extra):
        self.calls.append((kind, state, extra))
        reply = self.replies.get(f"{kind}:{state['phase']}", self.replies.get(kind))
        if isinstance(reply, Exception):
            raise reply
        return {"decision": reply if reply is not None else {"action": "none"}, "diag": {}}


def cast_reply(instance_id: int, name: str = "card") -> dict:
    return {"action": "cast", "id": instance_id, "card": name, "targets": {"names": []}}


class ForgeAiTests(unittest.TestCase):
    def setUp(self):
        # the bot looks unknown cards up on Scryfall; a test must never reach the network
        patcher = mock.patch.object(
            urllib.request, "urlopen", side_effect=urllib.error.URLError("no network in tests")
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("MTGA_FORGE", "MTGA_FORGE_ATTACK", "MTGA_FORGE_FALLBACK", "MTGA_FORGE_BLOCKS"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)

    def position(self, hand_actions: list[dict], **message_fields):
        """Two untapped Forests, a hand of green cards, and the given cast/play actions offered."""
        hand = [
            gre.card(ELVES, gre.LLANOWAR_ELVES, 31),
            gre.card(BEETLE, gre.NESSIAN_HORNBEETLE, 31),
            gre.card(STOMPER, gre.APOTHECARY_STOMPER, 31),
            gre.card(UNCLICKABLE, gre.INSPIRATION_FROM_BEYOND, 31),
            gre.card(LAND_IN_HAND, gre.FOREST, 31),
        ]
        actions = hand_actions + [gre.tap_for_green(200), gre.tap_for_green(201)]
        message = gre.state_message(
            hand=hand, battlefield=[gre.land(200), gre.land(201)], actions=actions, **message_fields
        )
        tracker = ArenaTracker()
        tracker.feed_message(gre.connect())
        tracker.feed_message(message)
        return GameState(dict(message["gameStateMessage"])), tracker

    def run_ai(self, advisor: FakeAdvisor, game_state, tracker) -> dict:
        bridge = forge_ai.Bridge(client=advisor, cards=CARDS, tracker=tracker)  # type: ignore[arg-type]
        ai = forge_ai.make_forge_ai_class(DummyAI, bridge)()
        return ai.generate_move(game_state, IDS)

    # -------------------------------------------------------------- the bot alone
    def test_bot_alone_spends_the_most_mana(self):
        game_state, _ = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        self.assertEqual(DummyAI().generate_move(game_state, IDS), {"cast": [BEETLE]})

    # -------------------------------------------------------------- Forge decides
    def test_forges_castable_choice_replaces_the_bots(self):
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=cast_reply(ELVES, "Llanowar Elves"))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [ELVES]})
        kind, state, extra = advisor.calls[0]
        self.assertEqual((kind, extra["castable"], extra["playable"]), ("main", [ELVES, BEETLE], []))
        self.assertEqual([c["name"] for c in state["players"][0]["hand"]][:2], ["Llanowar Elves", "Nessian Hornbeetle"])

    def test_forges_land_choice_is_played(self):
        game_state, tracker = self.position([gre.play(LAND_IN_HAND), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main={"action": "play", "id": LAND_IN_HAND, "card": "Forest"})
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [LAND_IN_HAND]})

    def test_a_creature_forge_would_hold_is_cast_now(self):
        # Forge says "nothing" in the first main phase but would cast after combat; Arena may never stop there.
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(**{"main:MAIN1": {"action": "none"}, "main:MAIN2": cast_reply(ELVES)})
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [ELVES]})
        self.assertEqual([state["phase"] for _, state, _ in advisor.calls], ["MAIN1", "MAIN2"])

    # -------------------------------------------------------------- the bot keeps the last word
    def test_a_card_the_bot_cannot_click_through_is_not_cast(self):
        game_state, tracker = self.position([gre.cast(UNCLICKABLE, 1), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=cast_reply(UNCLICKABLE, "Inspiration from Beyond"))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [BEETLE]})

    def test_a_card_the_bot_cannot_pay_for_is_not_cast(self):
        game_state, tracker = self.position([gre.cast(STOMPER, 6), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=cast_reply(STOMPER, "Apothecary Stomper"))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [BEETLE]})

    def test_only_actions_arena_offers_reach_forge(self):
        game_state, tracker = self.position([gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=cast_reply(BEETLE))
        self.run_ai(advisor, game_state, tracker)
        self.assertEqual(advisor.calls[0][2]["castable"], [BEETLE])

    def test_no_play_from_forge_leaves_the_bots_move(self):
        game_state, tracker = self.position([gre.cast(BEETLE, 2)])
        self.assertEqual(self.run_ai(FakeAdvisor(), game_state, tracker), {"cast": [BEETLE]})

    def test_no_play_from_forge_can_mean_pass(self):
        os.environ["MTGA_FORGE_FALLBACK"] = "pass"
        game_state, tracker = self.position([gre.cast(BEETLE, 2)])
        self.assertEqual(self.run_ai(FakeAdvisor(), game_state, tracker), {"resolve": []})

    # -------------------------------------------------------------- failures and switches
    def test_an_advisor_error_falls_back_to_the_bot(self):
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=AdvisorError("timeout after 4000 ms"))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [BEETLE]})

    def test_a_stopped_advisor_is_not_asked(self):
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        advisor.running = False
        with mock.patch.object(forge_ai.Bridge, "start_async"):
            self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [BEETLE]})
        self.assertEqual(advisor.calls, [])

    def test_shadow_mode_asks_but_plays_the_bots_move(self):
        os.environ["MTGA_FORGE"] = "shadow"
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [BEETLE]})
        self.assertEqual(len(advisor.calls), 1)

    def test_off_never_asks(self):
        os.environ["MTGA_FORGE"] = "off"
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [BEETLE]})
        self.assertEqual(advisor.calls, [])

    def test_not_asked_with_something_on_the_stack(self):
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)], stack=[777])
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        self.run_ai(advisor, game_state, tracker)
        self.assertEqual(advisor.calls, [])

    def test_not_asked_on_the_opponents_turn(self):
        game_state, tracker = self.position([gre.cast(ELVES, 1)], active=gre.OPP)
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"resolve": []})
        self.assertEqual(advisor.calls, [])

    def test_not_asked_when_the_log_is_on_another_turn(self):
        game_state, tracker = self.position([gre.cast(ELVES, 1), gre.cast(BEETLE, 2)])
        tracker.feed_message(gre.state_message(state_id=11, turn=6, active=gre.OPP))
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [BEETLE]})
        self.assertEqual(advisor.calls, [])

    # -------------------------------------------------------------- instants outside our main phase
    def attacked(self, **fields):
        """The opponent has declared an attacker and we hold priority with one castable card."""
        return self.position(
            [gre.cast(ELVES, 1)], active=gre.OPP, phase="Phase_Combat", step="Step_DeclareAttack", **fields
        )

    def test_casts_at_instant_speed_on_the_opponents_turn(self):
        game_state, tracker = self.attacked()
        advisor = FakeAdvisor(main=cast_reply(ELVES, "Llanowar Elves"))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"cast": [ELVES]})
        kind, state, extra = advisor.calls[0]
        self.assertEqual((kind, state["phase"], state["activePlayer"]), ("main", "COMBAT_DECLARE_ATTACKERS", 1))
        self.assertEqual((extra["castable"], extra["playable"]), ([ELVES], []))
        self.assertIn("combat", extra)

    def test_passes_on_the_opponents_turn_when_forge_has_nothing(self):
        game_state, tracker = self.attacked()
        self.assertEqual(self.run_ai(FakeAdvisor(), game_state, tracker), {"resolve": []})

    def test_off_turn_cast_still_needs_the_bots_agreement(self):
        game_state, tracker = self.position(
            [gre.cast(UNCLICKABLE, 1)], active=gre.OPP, phase="Phase_Combat", step="Step_DeclareAttack"
        )
        advisor = FakeAdvisor(main=cast_reply(UNCLICKABLE, "Inspiration from Beyond"))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"resolve": []})

    def test_nothing_is_asked_off_turn_with_a_spell_on_the_stack(self):
        game_state, tracker = self.attacked(stack=[777])
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"resolve": []})
        self.assertEqual(advisor.calls, [])

    def test_instants_can_be_switched_off(self):
        os.environ["MTGA_FORGE_INSTANTS"] = "off"
        self.addCleanup(os.environ.pop, "MTGA_FORGE_INSTANTS", None)
        game_state, tracker = self.attacked()
        advisor = FakeAdvisor(main=cast_reply(ELVES))
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"resolve": []})
        self.assertEqual(advisor.calls, [])

    # -------------------------------------------------------------- attack gate (opt-in)
    def test_gate_passes_the_turn_when_not_attacking_scores_higher(self):
        os.environ["MTGA_FORGE_ATTACK"] = "gate"
        game_state, tracker = self.position([])
        advisor = FakeAdvisor(attack={"attackers": [], "candidates": 2, "scores": {"none": 500, "all": 120}})
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"auto_pass": []})

    def test_gate_attacks_when_attacking_scores_at_least_as_high(self):
        os.environ["MTGA_FORGE_ATTACK"] = "gate"
        game_state, tracker = self.position([])
        advisor = FakeAdvisor(attack={"attackers": [], "candidates": 2, "scores": {"none": 500, "all": 500}})
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"resolve": []})

    def test_gate_does_nothing_without_attackers_or_by_default(self):
        game_state, tracker = self.position([])
        advisor = FakeAdvisor(attack={"attackers": [], "candidates": 2, "scores": {"none": 500, "all": 120}})
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"resolve": []})
        os.environ["MTGA_FORGE_ATTACK"] = "gate"
        advisor = FakeAdvisor(attack={"attackers": [], "candidates": 0, "scores": {"none": 500, "all": 120}})
        self.assertEqual(self.run_ai(advisor, game_state, tracker), {"resolve": []})


class BlockHookTests(unittest.TestCase):
    def setUp(self):
        for name in ("MTGA_FORGE", "MTGA_FORGE_BLOCKS"):
            os.environ.pop(name, None)
            self.addCleanup(os.environ.pop, name, None)
        message = gre.state_message(
            active=gre.OPP, phase="Phase_Combat", step="Step_DeclareBlock",
            battlefield=[
                gre.creature(310, 1, 2, 2),
                gre.creature(320, 2, 3, 3, seat=gre.OPP, attackState="AttackState_Attacking",
                             attackInfo={"targetId": gre.ME}),
            ],
        )
        self.tracker = ArenaTracker()
        self.tracker.feed_message(gre.connect())
        self.tracker.feed_message(message)
        self.legal = [{"blockerInstanceId": 310, "attackerInstanceIds": [320], "maxAttackers": 1}]
        self.bot_answer = {"assignments": [], "detail": [], "reason": "no block worth making", "incoming_damage": 3}

    def choose(self, advisor: FakeAdvisor) -> dict:
        bridge = forge_ai.Bridge(client=advisor, cards=CARDS, tracker=self.tracker)  # type: ignore[arg-type]
        hook = forge_ai.wrap_choose_blocks(lambda *args, **kwargs: dict(self.bot_answer), bridge)
        return hook(self.legal, [], gre.ME, 20, live_instance_ids={310, 320}, attacking_instance_ids={320})

    def test_forges_blocks_replace_the_bots(self):
        advisor = FakeAdvisor(block={"blocks": [{"blocker": 310, "attacker": 320}], "names": ["a blocks b"]})
        result = self.choose(advisor)
        self.assertEqual(result["assignments"], [(310, 320)])
        self.assertEqual(result["bot_assignments"], [])
        self.assertEqual(result["incoming_damage"], 3)
        _, _, extra = advisor.calls[0]
        self.assertEqual((extra["attackers"], extra["legalBlocks"]), ([{"id": 320}], {"310": [320]}))

    def test_forge_choosing_no_blocks_is_an_answer_too(self):
        self.bot_answer["assignments"] = [(310, 320)]
        self.assertEqual(self.choose(FakeAdvisor(block={"blocks": [], "names": []}))["assignments"], [])

    def test_an_error_keeps_the_bots_blocks(self):
        self.bot_answer["assignments"] = [(310, 320)]
        result = self.choose(FakeAdvisor(block=AdvisorError("advisor stopped answering")))
        self.assertEqual(result["assignments"], [(310, 320)])
        self.assertNotIn("bot_assignments", result)

    def test_switched_off_keeps_the_bots_blocks(self):
        os.environ["MTGA_FORGE_BLOCKS"] = "off"
        advisor = FakeAdvisor(block={"blocks": [{"blocker": 310, "attacker": 320}], "names": []})
        self.assertEqual(self.choose(advisor)["assignments"], [])
        self.assertEqual(advisor.calls, [])

    def test_shadow_mode_keeps_the_bots_blocks(self):
        os.environ["MTGA_FORGE"] = "shadow"
        advisor = FakeAdvisor(block={"blocks": [{"blocker": 310, "attacker": 320}], "names": []})
        self.assertEqual(self.choose(advisor)["assignments"], [])
        self.assertEqual(len(advisor.calls), 1)


if __name__ == "__main__":
    unittest.main()
