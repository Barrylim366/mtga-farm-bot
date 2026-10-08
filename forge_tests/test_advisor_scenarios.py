"""Hand-built positions with an obvious right answer, run through the real advisor process.

    python -m unittest forge_tests.test_advisor_scenarios -v

Starting the advisor loads Forge's whole card database, so the process is shared by every test here.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge_bridge.client import AdvisorClient  # noqa: E402

_client: AdvisorClient | None = None


def setUpModule() -> None:
    global _client
    _client = AdvisorClient()
    info = _client.start()
    print(f"\nadvisor ready: {info}")


def tearDownModule() -> None:
    if _client is not None:
        _client.close()


_next_id = [100]


def card(name: str, **fields) -> dict:
    _next_id[0] += 1
    out = {"id": _next_id[0], "name": name}
    out.update(fields)
    return out


def player(life=20, hand=(), battlefield=(), graveyard=(), library=30, lands_played=0, hand_size=None) -> dict:
    return {
        "life": life,
        "landsPlayed": lands_played,
        "librarySize": library,
        "handSize": len(hand) if hand_size is None else hand_size,
        "hand": list(hand),
        "battlefield": list(battlefield),
        "graveyard": list(graveyard),
        "exile": [],
    }


def state(me: dict, opp: dict, active=0, phase="MAIN1", turn=5) -> dict:
    return {"turn": turn, "phase": phase, "activePlayer": active, "players": [me, opp]}


def creature(name: str, power: int, toughness: int, **fields) -> dict:
    return card(name, power=power, toughness=toughness, types=["Creature"], **fields)


class AdvisorScenarios(unittest.TestCase):
    def decide(self, kind: str, st: dict, **extra) -> dict:
        assert _client is not None
        reply = _client.decide(kind, st, timeout=20, include=["board"], **extra)
        self.assertFalse(reply["diag"]["dropped"], reply["diag"])
        self.assertFalse(reply["diag"]["lost"], reply["diag"])
        return reply

    def test_ping(self):
        assert _client is not None
        self.assertTrue(_client.request({"op": "ping"})["pong"])

    def test_names(self):
        assert _client is not None
        known = _client.request(
            {"op": "names", "names": ["Grizzly Bears", "Serra Angel", "No Such Card Exists", "A-Luminarch Aspirant"]}
        )["known"]
        self.assertEqual(known["Grizzly Bears"], "Grizzly Bears")
        self.assertIsNone(known["No Such Card Exists"])

    def test_attack_for_lethal(self):
        bears = [creature("Grizzly Bears", 2, 2), creature("Grizzly Bears", 2, 2)]
        reply = self.decide(
            "attack",
            state(player(battlefield=bears), player(life=4)),
            legalAttackers=[c["id"] for c in bears],
        )
        self.assertEqual(
            sorted(a["id"] for a in reply["decision"]["attackers"]), sorted(c["id"] for c in bears), reply
        )

    def test_does_not_attack_into_a_bigger_blocker(self):
        bear = creature("Grizzly Bears", 2, 2)
        wall = creature("Hill Giant", 3, 3)
        reply = self.decide(
            "attack", state(player(battlefield=[bear]), player(battlefield=[wall])), legalAttackers=[bear["id"]]
        )
        self.assertEqual(reply["decision"]["attackers"], [], reply)

    def test_summoning_sick_creature_is_not_declared(self):
        bear = creature("Grizzly Bears", 2, 2, sick=True)
        reply = self.decide("attack", state(player(battlefield=[bear]), player(life=1)), legalAttackers=[])
        self.assertEqual(reply["decision"]["attackers"], [], reply)

    def test_blocks_and_kills_a_smaller_attacker(self):
        giant = creature("Hill Giant", 3, 3)
        bear = creature("Grizzly Bears", 2, 2, tapped=True)
        reply = self.decide(
            "block",
            state(player(battlefield=[giant]), player(battlefield=[bear]), active=1),
            attackers=[{"id": bear["id"]}],
            legalBlocks={str(giant["id"]): [bear["id"]]},
        )
        self.assertEqual(reply["decision"]["blocks"], [{"blocker": giant["id"], "attacker": bear["id"]}], reply)

    def test_chump_blocks_to_survive_lethal(self):
        bear = creature("Grizzly Bears", 2, 2)
        giant = creature("Hill Giant", 3, 3, tapped=True)
        reply = self.decide(
            "block",
            state(player(life=3, battlefield=[bear]), player(battlefield=[giant]), active=1),
            attackers=[{"id": giant["id"]}],
            legalBlocks={str(bear["id"]): [giant["id"]]},
        )
        self.assertEqual(reply["decision"]["blocks"], [{"blocker": bear["id"], "attacker": giant["id"]}], reply)

    def test_block_respects_arenas_legal_graph(self):
        giant = creature("Hill Giant", 3, 3)
        bear = creature("Grizzly Bears", 2, 2, tapped=True)
        reply = self.decide(
            "block",
            state(player(battlefield=[giant]), player(battlefield=[bear]), active=1),
            attackers=[{"id": bear["id"]}],
            legalBlocks={},
        )
        self.assertEqual(reply["decision"]["blocks"], [], reply)
        self.assertTrue(reply["decision"]["notLegalInArena"], reply)

    def test_plays_a_land_then_casts(self):
        forest_in_play = card("Forest", types=["Land"])
        forest = card("Forest", types=["Land"])
        bears = card("Grizzly Bears")
        me = player(hand=[forest, bears], battlefield=[forest_in_play])
        reply = self.decide("main", state(me, player()), castable=[bears["id"]], playable=[forest["id"]])
        self.assertEqual((reply["decision"]["action"], reply["decision"]["id"]), ("play", forest["id"]), reply)

        second_forest = card("Forest", types=["Land"])
        me = player(hand=[bears], battlefield=[forest_in_play, second_forest], lands_played=1)
        # Forge's AI keeps a creature that cannot affect this combat until the second main phase
        reply = self.decide("main", state(me, player(), phase="MAIN1"), castable=[bears["id"]], playable=[])
        self.assertEqual(reply["decision"]["action"], "none", reply)
        reply = self.decide("main", state(me, player(), phase="MAIN2"), castable=[bears["id"]], playable=[])
        self.assertEqual((reply["decision"]["action"], reply["decision"]["id"]), ("cast", bears["id"]), reply)

    def test_casts_a_haste_creature_before_combat(self):
        mountains = [card("Mountain", types=["Land"]), card("Mountain", types=["Land"])]
        guide = card("Goblin Guide")
        me = player(hand=[guide], battlefield=mountains, lands_played=1)
        reply = self.decide("main", state(me, player(), phase="MAIN1"), castable=[guide["id"]], playable=[])
        self.assertEqual((reply["decision"]["action"], reply["decision"]["id"]), ("cast", guide["id"]), reply)

    def test_does_not_cast_what_arena_is_not_offering(self):
        forests = [card("Forest", types=["Land"]), card("Forest", types=["Land"])]
        bears = card("Grizzly Bears")
        me = player(hand=[bears], battlefield=forests, lands_played=1)
        reply = self.decide("main", state(me, player()), castable=[], playable=[])
        self.assertEqual(reply["decision"]["action"], "none", reply)

    def test_removal_targets_the_opposing_creature(self):
        swamps = [card("Swamp", types=["Land"]), card("Swamp", types=["Land"])]
        murder = card("Doom Blade")
        angel = creature("Serra Angel", 4, 4)
        me = player(hand=[murder], battlefield=swamps, lands_played=1)
        reply = self.decide("main", state(me, player(battlefield=[angel])), castable=[murder["id"]], playable=[])
        decision = reply["decision"]
        self.assertEqual((decision["action"], decision["id"]), ("cast", murder["id"]), reply)
        self.assertEqual(decision["targets"]["cards"], [angel["id"]], reply)

    def test_arena_power_and_toughness_override_forge(self):
        # Arena shows this bear as 5/5 (say, a pump Forge never saw): it must be treated as a 5/5.
        bear = creature("Grizzly Bears", 5, 5)
        giant = creature("Hill Giant", 3, 3)
        reply = self.decide(
            "attack", state(player(battlefield=[bear]), player(battlefield=[giant])), legalAttackers=[bear["id"]]
        )
        self.assertEqual([a["id"] for a in reply["decision"]["attackers"]], [bear["id"]], reply)
        self.assertEqual(reply["diag"]["adjusted"][0]["power"], 3, reply)

    def test_unknown_permanent_becomes_a_stand_in(self):
        mystery = creature("Totally Unknown Arena Card", 6, 6, subtypes=["Horror"], colors="B")
        bear = creature("Grizzly Bears", 2, 2, tapped=True)
        reply = _client.decide(  # type: ignore[union-attr]
            "block",
            state(player(battlefield=[mystery]), player(battlefield=[bear]), active=1),
            timeout=20,
            include=["board"],
            attackers=[{"id": bear["id"]}],
            legalBlocks={str(mystery["id"]): [bear["id"]]},
        )
        self.assertEqual(reply["diag"]["standIns"][0]["id"], mystery["id"], reply)
        self.assertIn("Totally Unknown Arena Card 6/6", reply["board"]["me"]["battlefield"], reply)
        self.assertEqual(reply["decision"]["blocks"], [{"blocker": mystery["id"], "attacker": bear["id"]}], reply)

    def test_counters_and_auras_are_applied(self):
        bear = creature("Grizzly Bears", 5, 5, counters={"+1/+1": 1})
        aura = card("Rancor", attachedTo=bear["id"], types=["Enchantment"])
        reply = self.decide("load", state(player(battlefield=[bear, aura]), player()))
        # 2/2 base, +1/+1 counter, Rancor gives +2/+0 -> 5/3; Arena said 5/5, so toughness is corrected by +2
        self.assertEqual(reply["diag"]["adjusted"], [
            {"id": bear["id"], "name": "Grizzly Bears", "power": 0, "toughness": 2}
        ], reply)

    def test_back_face_land_stays_on_the_battlefield(self):
        # Jwari Disruption // Jwari Ruins, played as the land on its back
        ruins = card("Jwari Ruins", types=["Land"], tapped=True)
        swamp = card("Swamp", types=["Land"])
        reply = self.decide("load", state(player(battlefield=[ruins, swamp]), player()))
        self.assertIn("Jwari Ruins (T)", reply["board"]["me"]["battlefield"], reply)
        self.assertEqual(reply["board"]["me"]["hand"], [], reply)

    def test_playout_prefers_the_lethal_attack(self):
        bears = [creature("Grizzly Bears", 2, 2), creature("Grizzly Bears", 2, 2)]
        reply = self.decide(
            "attack",
            state(player(battlefield=bears), player(life=4)),
            legalAttackers=[c["id"] for c in bears],
            evaluate=True,
        )
        scores = reply["decision"]["scores"]
        self.assertGreater(scores["all"], scores["none"], reply)

    def test_playout_sees_the_counter_attack(self):
        # Attacking taps our only blocker; the two giants then swing back for exactly lethal.
        bear = creature("Grizzly Bears", 2, 2)
        giants = [creature("Hill Giant", 3, 3), creature("Hill Giant", 3, 3)]
        reply = self.decide(
            "attack",
            state(player(life=6, battlefield=[bear]), player(battlefield=giants)),
            legalAttackers=[bear["id"]],
            evaluate=True,
        )
        scores = reply["decision"]["scores"]
        self.assertGreater(scores["none"], scores["all"], reply)

    def test_removal_is_cast_on_an_attacker_during_the_opponents_turn(self):
        swamps = [card("Swamp", types=["Land"]), card("Swamp", types=["Land"])]
        blade = card("Doom Blade")
        angel = creature("Serra Angel", 4, 4)
        st = state(
            player(life=10, hand=[blade], battlefield=swamps), player(battlefield=[angel]),
            active=1, phase="COMBAT_DECLARE_ATTACKERS",
        )
        reply = self.decide(
            "main", st, castable=[blade["id"]], playable=[], combat={"attackers": [{"id": angel["id"]}]}
        )
        decision = reply["decision"]
        self.assertEqual((decision["action"], decision["id"]), ("cast", blade["id"]), reply)
        self.assertEqual(decision["targets"]["cards"], [angel["id"]], reply)

    def test_pump_spell_is_cast_on_our_blocked_attacker(self):
        growth = card("Giant Growth")
        bear = creature("Grizzly Bears", 2, 2, tapped=True)
        giant = creature("Hill Giant", 3, 3)
        st = state(
            player(hand=[growth], battlefield=[card("Forest", types=["Land"]), bear]),
            player(battlefield=[giant]), active=0, phase="COMBAT_DECLARE_BLOCKERS",
        )
        reply = self.decide(
            "main", st, castable=[growth["id"]], playable=[],
            combat={"attackers": [{"id": bear["id"]}], "blocks": [{"blocker": giant["id"], "attacker": bear["id"]}]},
        )
        decision = reply["decision"]
        self.assertEqual((decision["action"], decision["targets"]["cards"]), ("cast", [bear["id"]]), reply)

    def test_scry_keeps_a_land_when_short_and_bottoms_it_when_flooded(self):
        def scry(lands_in_play: int, mode: str = "scry") -> dict:
            top = card("Island")
            me = player(hand=[card("Opt")], battlefield=[card("Island", types=["Land"]) for _ in range(lands_in_play)])
            me["library"] = [top]
            reply = self.decide("scry", state(me, player()), mode=mode, cards=[top["id"]])
            return {"kept": reply["decision"]["top"] == [top["id"]], "away": reply["decision"]["away"] == [top["id"]]}

        self.assertTrue(scry(2)["kept"])
        self.assertTrue(scry(7)["away"])
        self.assertTrue(scry(7, "surveil")["away"])

    def test_token_uses_forge_token_script(self):
        soldier = creature("Soldier", 1, 1, token=True, subtypes=["Soldier"], colors="W", basePower=1, baseToughness=1)
        reply = self.decide("load", state(player(battlefield=[soldier]), player()))
        self.assertFalse(reply["diag"]["standIns"], reply)
        self.assertIn("Soldier Token 1/1", reply["board"]["me"]["battlefield"], reply)


if __name__ == "__main__":
    unittest.main()
