"""Phase 1 combat logic: attack selection and block assignment.

All offline: CombatLogic is pure functions over a synthetic game state, and it
reads card data through CardInfo.get_card_info_local, which never touches the
network. Keywords are stubbed per test by pinning a grpId into the local card
DB, which is exactly how the real lookup resolves them in Starter Deck Duel.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import AI.Utilities.CardInfo as CardInfo
import AI.Utilities.CombatLogic as CombatLogic

MY_SEAT = 1
OPP_SEAT = 2
BATTLEFIELD_ZONE = 28

# grpIds used to attach keywords/mana cost to a test creature.
PLAIN = 900001
DEATHTOUCH = 900002
FIRST_STRIKE = 900003
TRAMPLE = 900004
VIGILANCE = 900005
INDESTRUCTIBLE = 900006
EXPENSIVE = 900007

_TEST_CARDS = {
    PLAIN: {"name": "Plain Bear", "manaCost": "{2}{G}", "keywords": []},
    DEATHTOUCH: {"name": "Deathtouch Snake", "manaCost": "{1}{B}", "keywords": ["Deathtouch"]},
    FIRST_STRIKE: {"name": "First Striker", "manaCost": "{1}{W}", "keywords": ["First strike"]},
    TRAMPLE: {"name": "Trampler", "manaCost": "{3}{G}", "keywords": ["Trample"]},
    VIGILANCE: {"name": "Watchful Knight", "manaCost": "{2}{W}", "keywords": ["Vigilance"]},
    INDESTRUCTIBLE: {"name": "Unkillable", "manaCost": "{4}{W}", "keywords": ["Indestructible"]},
    EXPENSIVE: {"name": "Big Finisher", "manaCost": "{6}{G}{G}", "keywords": []},
}


def creature(instance_id, seat, power=2, toughness=2, *, grp_id=PLAIN,
             tapped=False, damage=0, attacking_seat=None):
    obj = {
        "instanceId": instance_id,
        "grpId": grp_id,
        "zoneId": BATTLEFIELD_ZONE,
        "controllerSeatId": seat,
        "ownerSeatId": seat,
        "cardTypes": ["CardType_Creature"],
        "power": {"value": power},
        "toughness": {"value": toughness},
    }
    if tapped:
        obj["isTapped"] = True
    if damage:
        obj["damage"] = damage
    if attacking_seat is not None:
        obj["attackState"] = "AttackState_Attacking"
        obj["attackInfo"] = {"targetId": attacking_seat}
    return obj


def players(my_life=20, opp_life=20):
    return [
        {"systemSeatNumber": MY_SEAT, "lifeTotal": my_life},
        {"systemSeatNumber": OPP_SEAT, "lifeTotal": opp_life},
    ]


def blockers_req(*pairs):
    """`declareBlockersReq.blockers` from (blocker_id, [attacker_ids]) pairs."""
    return [
        {"blockerInstanceId": blocker, "attackerInstanceIds": list(attackers), "maxAttackers": 1}
        for blocker, attackers in pairs
    ]


def attackers_req(*instance_ids):
    """`declareAttackersReq.attackers` from instance ids."""
    return [
        {
            "attackerInstanceId": instance_id,
            "legalDamageRecipients": [
                {"type": "DamageRecType_Player", "playerSystemSeatId": OPP_SEAT}
            ],
        }
        for instance_id in instance_ids
    ]


def live(*objects):
    return {obj["instanceId"] for obj in objects}


class CombatLogicTestCase(unittest.TestCase):
    """Pins the synthetic cards into the local card DB for the whole class."""

    @classmethod
    def setUpClass(cls):
        cls._saved = {}
        for grp_id, card in _TEST_CARDS.items():
            key = str(grp_id)
            cls._saved[key] = CardInfo._starter_cards.get(key)
            CardInfo._starter_cards[key] = dict(card, grpId=grp_id)

    @classmethod
    def tearDownClass(cls):
        for key, previous in cls._saved.items():
            if previous is None:
                CardInfo._starter_cards.pop(key, None)
            else:
                CardInfo._starter_cards[key] = previous


class TestCombatPairs(CombatLogicTestCase):
    def test_bigger_creature_kills_and_survives(self):
        attacker = creature(10, OPP_SEAT, 2, 2)
        blocker = creature(20, MY_SEAT, 3, 3)
        result = CombatLogic.resolve_combat_pair(attacker, blocker)
        self.assertEqual(result["outcome"], CombatLogic.KILL_AND_SURVIVE)
        self.assertEqual(result["damage_prevented"], 2)

    def test_equal_creatures_trade(self):
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 2, 2), creature(20, MY_SEAT, 2, 2)
        )
        self.assertEqual(result["outcome"], CombatLogic.TRADE)

    def test_small_blocker_chumps(self):
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 5, 5), creature(20, MY_SEAT, 1, 1)
        )
        self.assertEqual(result["outcome"], CombatLogic.CHUMP)

    def test_wall_absorbs_without_killing(self):
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 2, 2), creature(20, MY_SEAT, 0, 5)
        )
        self.assertEqual(result["outcome"], CombatLogic.ABSORB)

    def test_deathtouch_kills_anything(self):
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 6, 6),
            creature(20, MY_SEAT, 1, 1, grp_id=DEATHTOUCH),
        )
        self.assertTrue(result["attacker_dies"])
        self.assertEqual(result["outcome"], CombatLogic.TRADE)

    def test_marked_damage_lowers_effective_toughness(self):
        # A 3/3 that has already taken 2 damage dies to a 1/1.
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 3, 3, damage=2), creature(20, MY_SEAT, 1, 1)
        )
        self.assertTrue(result["attacker_dies"])

    def test_first_strike_kills_before_taking_damage(self):
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 2, 2),
            creature(20, MY_SEAT, 2, 2, grp_id=FIRST_STRIKE),
        )
        self.assertEqual(result["outcome"], CombatLogic.KILL_AND_SURVIVE)
        self.assertFalse(result["blocker_dies"])

    def test_indestructible_survives_lethal_damage(self):
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 9, 9),
            creature(20, MY_SEAT, 1, 1, grp_id=INDESTRUCTIBLE),
        )
        self.assertFalse(result["blocker_dies"])

    def test_trample_only_prevents_the_blockers_toughness(self):
        result = CombatLogic.resolve_combat_pair(
            creature(10, OPP_SEAT, 5, 5, grp_id=TRAMPLE),
            creature(20, MY_SEAT, 1, 1),
        )
        self.assertEqual(result["damage_prevented"], 1)


class TestChooseBlocks(CombatLogicTestCase):
    def test_takes_a_free_kill(self):
        attacker = creature(10, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 3, 3)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, blocker], MY_SEAT, 20,
            live_instance_ids=live(attacker, blocker),
        )
        self.assertEqual(decision["assignments"], [(20, 10)])
        self.assertEqual(decision["detail"][0]["outcome"], CombatLogic.KILL_AND_SURVIVE)

    def test_refuses_to_chump_at_healthy_life(self):
        attacker = creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 1, 1)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, blocker], MY_SEAT, 20,
            live_instance_ids=live(attacker, blocker),
        )
        self.assertEqual(decision["assignments"], [])
        self.assertEqual(decision["unblocked_damage"], 5)

    def test_chumps_to_survive_lethal(self):
        # 5 power incoming at 4 life: the 1/1 has to get in the way.
        attacker = creature(10, OPP_SEAT, 5, 5, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 1, 1)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, blocker], MY_SEAT, 4,
            live_instance_ids=live(attacker, blocker),
        )
        self.assertEqual(decision["assignments"], [(20, 10)])
        self.assertTrue(decision["lethal_without_blocks"])
        self.assertEqual(decision["unblocked_damage"], 0)

    def test_refuses_to_trade_down_when_not_dying(self):
        # Our 8-drop would trade with their 2-drop: not worth it at 20 life.
        attacker = creature(10, OPP_SEAT, 4, 4, grp_id=PLAIN, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 4, 4, grp_id=EXPENSIVE)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, blocker], MY_SEAT, 20,
            live_instance_ids=live(attacker, blocker),
        )
        self.assertEqual(decision["assignments"], [])

    def test_accepts_the_same_trade_when_it_is_lethal(self):
        attacker = creature(10, OPP_SEAT, 4, 4, grp_id=PLAIN, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 4, 4, grp_id=EXPENSIVE)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, blocker], MY_SEAT, 4,
            live_instance_ids=live(attacker, blocker),
        )
        self.assertEqual(decision["assignments"], [(20, 10)])

    def test_spends_the_cheapest_body_that_does_the_job(self):
        attacker = creature(10, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)
        cheap = creature(20, MY_SEAT, 3, 3, grp_id=PLAIN)
        pricey = creature(21, MY_SEAT, 3, 3, grp_id=EXPENSIVE)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10]), (21, [10])),
            [attacker, cheap, pricey], MY_SEAT, 20,
            live_instance_ids=live(attacker, cheap, pricey),
        )
        self.assertEqual(decision["assignments"], [(20, 10)])

    def test_only_legal_pairs_are_considered(self):
        # The flier is not in our blocker's attackerInstanceIds, so it is
        # untouchable no matter how good the block would look on paper.
        flier = creature(10, OPP_SEAT, 3, 3, attacking_seat=MY_SEAT)
        ground = creature(11, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 4, 4)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [11])), [flier, ground, blocker], MY_SEAT, 20,
            live_instance_ids=live(flier, ground, blocker),
        )
        self.assertEqual(decision["assignments"], [(20, 11)])

    def test_dead_creatures_are_not_blockers(self):
        # A creature missing from the battlefield membership list died; blocking
        # with it would send the clicker hunting a card that is not on screen.
        attacker = creature(10, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)
        ghost = creature(20, MY_SEAT, 5, 5)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, ghost], MY_SEAT, 20,
            live_instance_ids={10},
        )
        self.assertEqual(decision["assignments"], [])

    def test_attacker_aimed_at_a_planeswalker_is_not_lethal_pressure(self):
        attacker = creature(10, OPP_SEAT, 9, 9, attacking_seat=99)
        blocker = creature(20, MY_SEAT, 1, 1)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, blocker], MY_SEAT, 3,
            live_instance_ids=live(attacker, blocker),
        )
        self.assertEqual(decision["incoming_damage"], 0)
        self.assertEqual(decision["assignments"], [])

    def test_blocks_several_attackers_to_survive(self):
        # 6 damage incoming at 3 life: one chump still leaves exactly lethal, so
        # both 1/1s have to get in the way.
        a1 = creature(10, OPP_SEAT, 3, 3, attacking_seat=MY_SEAT)
        a2 = creature(11, OPP_SEAT, 3, 3, attacking_seat=MY_SEAT)
        b1 = creature(20, MY_SEAT, 1, 1)
        b2 = creature(21, MY_SEAT, 1, 1)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10, 11]), (21, [10, 11])),
            [a1, a2, b1, b2], MY_SEAT, 3,
            live_instance_ids=live(a1, a2, b1, b2),
        )
        self.assertEqual(len(decision["assignments"]), 2)
        self.assertEqual(decision["unblocked_damage"], 0)

    def test_unblockable_attacker_counts_towards_lethal(self):
        """The block graph only lists attackers we can block. A flier nothing of
        ours can reach appears nowhere in it, so counting damage from the graph
        alone understates it -- and understating it is exactly the case where we
        fail to chump and die."""
        flier = creature(10, OPP_SEAT, 4, 4, attacking_seat=MY_SEAT)
        ground = creature(11, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)
        blocker = creature(20, MY_SEAT, 1, 1)
        board = [flier, ground, blocker]

        # Without the flier: 2 damage at 5 life, no block needed.
        graph_only = CombatLogic.choose_blocks(
            blockers_req((20, [11])), board, MY_SEAT, 5,
            live_instance_ids=live(flier, ground, blocker),
        )
        self.assertEqual(graph_only["incoming_damage"], 2)
        self.assertEqual(graph_only["assignments"], [])

        # With the flier known to be attacking: 6 damage at 5 life is lethal, so
        # the 1/1 chumps the one attacker it can actually reach.
        with_flier = CombatLogic.choose_blocks(
            blockers_req((20, [11])), board, MY_SEAT, 5,
            live_instance_ids=live(flier, ground, blocker),
            attacking_instance_ids={10, 11},
        )
        self.assertEqual(with_flier["incoming_damage"], 6)
        self.assertTrue(with_flier["lethal_without_blocks"])
        self.assertEqual(with_flier["assignments"], [(20, 11)])
        self.assertEqual(with_flier["unblocked_damage"], 4)  # the flier still connects

    def test_unblockable_lethal_with_no_legal_blocks_at_all(self):
        flier = creature(10, OPP_SEAT, 9, 9, attacking_seat=MY_SEAT)
        decision = CombatLogic.choose_blocks(
            [], [flier], MY_SEAT, 5,
            live_instance_ids=live(flier),
            attacking_instance_ids={10},
        )
        self.assertEqual(decision["incoming_damage"], 9)
        self.assertTrue(decision["lethal_without_blocks"])
        self.assertEqual(decision["assignments"], [])

    def test_our_own_creatures_are_never_counted_as_attackers(self):
        attacker = creature(10, OPP_SEAT, 2, 2, attacking_seat=MY_SEAT)
        mine = creature(20, MY_SEAT, 3, 3)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10])), [attacker, mine], MY_SEAT, 20,
            live_instance_ids=live(attacker, mine),
            attacking_instance_ids={10, 20},
        )
        self.assertEqual(decision["incoming_damage"], 2)

    def test_spends_no_more_creatures_than_survival_needs(self):
        # Same board at 5 life: one chump drops it to 3 damage and we live, so
        # the second 1/1 is kept rather than thrown away for nothing.
        a1 = creature(10, OPP_SEAT, 3, 3, attacking_seat=MY_SEAT)
        a2 = creature(11, OPP_SEAT, 3, 3, attacking_seat=MY_SEAT)
        b1 = creature(20, MY_SEAT, 1, 1)
        b2 = creature(21, MY_SEAT, 1, 1)
        decision = CombatLogic.choose_blocks(
            blockers_req((20, [10, 11]), (21, [10, 11])),
            [a1, a2, b1, b2], MY_SEAT, 5,
            live_instance_ids=live(a1, a2, b1, b2),
        )
        self.assertEqual(len(decision["assignments"]), 1)
        self.assertEqual(decision["unblocked_damage"], 3)


class TestChooseAttackers(CombatLogicTestCase):
    def test_attacks_freely_into_an_empty_board(self):
        mine = creature(20, MY_SEAT, 2, 2)
        decision = CombatLogic.choose_attackers(
            attackers_req(20), [mine], MY_SEAT, 20, 20,
            live_instance_ids=live(mine),
        )
        self.assertEqual(decision["attackers"], [20])

    def test_holds_back_a_creature_that_dies_for_nothing(self):
        mine = creature(20, MY_SEAT, 2, 2)
        theirs = creature(10, OPP_SEAT, 3, 3)
        decision = CombatLogic.choose_attackers(
            attackers_req(20), [mine, theirs], MY_SEAT, 20, 20,
            live_instance_ids=live(mine, theirs),
        )
        self.assertEqual(decision["attackers"], [])
        self.assertEqual(decision["hold_back"], [20])
        self.assertIn("dies to a block", decision["skipped"][20])

    def test_attacks_past_a_tapped_defender(self):
        mine = creature(20, MY_SEAT, 2, 2)
        theirs = creature(10, OPP_SEAT, 3, 3, tapped=True)
        decision = CombatLogic.choose_attackers(
            attackers_req(20), [mine, theirs], MY_SEAT, 20, 20,
            live_instance_ids=live(mine, theirs),
        )
        self.assertEqual(decision["attackers"], [20])

    def test_refuses_to_trade_down(self):
        mine = creature(20, MY_SEAT, 3, 3, grp_id=EXPENSIVE)
        theirs = creature(10, OPP_SEAT, 3, 3, grp_id=PLAIN)
        decision = CombatLogic.choose_attackers(
            attackers_req(20), [mine, theirs], MY_SEAT, 20, 20,
            live_instance_ids=live(mine, theirs),
        )
        self.assertEqual(decision["attackers"], [])
        self.assertIn("trade down", decision["skipped"][20])

    def test_alpha_strikes_for_lethal_through_blockers(self):
        # Three 3/3s into one untapped blocker: 6 damage gets through, opponent
        # is at 5. Two of ours would die on a block -- irrelevant, the game ends.
        mine = [creature(20 + i, MY_SEAT, 3, 3) for i in range(3)]
        theirs = creature(10, OPP_SEAT, 4, 4)
        decision = CombatLogic.choose_attackers(
            attackers_req(20, 21, 22), mine + [theirs], MY_SEAT, 20, 5,
            live_instance_ids=live(*mine, theirs),
        )
        self.assertTrue(decision["lethal"])
        self.assertEqual(decision["attackers"], [20, 21, 22])

    def test_holds_back_defenders_when_the_counter_attack_is_lethal(self):
        # We are at 6; they have three 3/3s (tapped, so they cannot block, but
        # they untap and swing for 9 next turn). Attacking with everything
        # leaves nothing home and we die.
        mine = [creature(20 + i, MY_SEAT, 2, 2) for i in range(3)]
        theirs = [creature(10 + i, OPP_SEAT, 3, 3, tapped=True) for i in range(3)]
        decision = CombatLogic.choose_attackers(
            attackers_req(20, 21, 22), mine + theirs, MY_SEAT, 6, 20,
            live_instance_ids=live(*mine, *theirs),
        )
        self.assertTrue(decision["hold_back"], "expected some creatures kept home")
        self.assertGreater(decision["projected_life_after"], 0)

    def test_vigilant_creatures_attack_and_still_defend(self):
        mine = [creature(20 + i, MY_SEAT, 2, 2, grp_id=VIGILANCE) for i in range(3)]
        theirs = [creature(10 + i, OPP_SEAT, 3, 3, tapped=True) for i in range(3)]
        decision = CombatLogic.choose_attackers(
            attackers_req(20, 21, 22), mine + theirs, MY_SEAT, 6, 20,
            live_instance_ids=live(*mine, *theirs),
        )
        self.assertEqual(decision["attackers"], [20, 21, 22])
        self.assertEqual(decision["hold_back"], [])

    def test_zero_power_creatures_stay_home(self):
        mine = creature(20, MY_SEAT, 0, 4)
        decision = CombatLogic.choose_attackers(
            attackers_req(20), [mine], MY_SEAT, 20, 20,
            live_instance_ids=live(mine),
        )
        self.assertEqual(decision["attackers"], [])
        self.assertEqual(decision["skipped"][20], "no power")

    def test_no_legal_attackers(self):
        decision = CombatLogic.choose_attackers(
            [], [], MY_SEAT, 20, 20, live_instance_ids=set()
        )
        self.assertEqual(decision["attackers"], [])
        self.assertEqual(decision["reason"], "no legal attackers")


if __name__ == "__main__":
    unittest.main()
