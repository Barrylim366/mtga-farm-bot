"""The Arena state tracker and the position built from it. No advisor process needed.

    python -m unittest forge_tests.test_tracker -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge_bridge import snapshot  # noqa: E402
from forge_bridge.carddb import CardDb  # noqa: E402
from forge_bridge.tracker import ArenaTracker, LogFollower  # noqa: E402
from forge_tests import gre  # noqa: E402

CARDS = CardDb({
    "cards": {
        **{str(grp): {"name": name} for grp, name in gre.NAMES.items()},
        "7": {"name": "Soldier", "token": True, "power": 1, "toughness": 1},
    },
    "counters": {"1": "+1/+1", "7": "Loyalty"},
})


def tracker_with(*messages: dict) -> ArenaTracker:
    tracker = ArenaTracker()
    tracker.feed_message(gre.connect())
    for message in messages:
        tracker.feed_message(message)
    return tracker


class TrackerTests(unittest.TestCase):
    def test_a_resent_object_replaces_the_old_one(self):
        # Arena leaves default values out: the untapped land comes back with no isTapped at all.
        tapped = gre.state_message(battlefield=[gre.land(200, isTapped=True)])
        untapped = gre.state_message(state_id=11, full=False, battlefield=[gre.land(200)])
        tracker = tracker_with(tapped)
        self.assertTrue(tracker.objects[200].get("isTapped"))
        tracker.feed_message(untapped)
        self.assertNotIn("isTapped", tracker.objects[200])

    def test_deleted_objects_and_annotations_go_away(self):
        first = gre.state_message(
            battlefield=[gre.creature(300, 1, 2, 2)],
            extra={"persistentAnnotations": [{"id": 5, "type": ["AnnotationType_Counter"], "affectedIds": [300]}]},
        )
        second = gre.state_message(
            state_id=11, full=False,
            extra={"diffDeletedInstanceIds": [300], "diffDeletedPersistentAnnotationIds": [5]},
        )
        tracker = tracker_with(first, second)
        self.assertNotIn(300, tracker.objects)
        self.assertEqual(tracker.persistent, {})

    def test_a_full_state_starts_over(self):
        tracker = tracker_with(gre.state_message(battlefield=[gre.creature(300, 1, 2, 2)]))
        tracker.feed_message(gre.state_message(state_id=1, battlefield=[gre.land(400)]))
        self.assertEqual(sorted(tracker.objects), [400])

    def test_a_diff_against_an_unseen_state_is_counted(self):
        tracker = tracker_with(gre.state_message(state_id=10))
        tracker.feed_message(gre.state_message(state_id=14, full=False))
        self.assertEqual(tracker.gaps, 1)

    def test_a_request_is_current_only_until_the_next_state(self):
        tracker = tracker_with(gre.state_message(state_id=10))
        tracker.feed_message({
            "type": "GREMessageType_DeclareAttackersReq", "systemSeatIds": [gre.ME], "gameStateId": 10,
            "declareAttackersReq": {"attackers": [{"attackerInstanceId": 300}]},
        })
        self.assertTrue(tracker.pending_is_current("GREMessageType_DeclareAttackersReq"))
        self.assertEqual(snapshot.attack_request(tracker), {"legalAttackers": [300]})
        tracker.feed_message(gre.state_message(state_id=11, full=False))
        self.assertFalse(tracker.pending_is_current("GREMessageType_DeclareAttackersReq"))

    def test_a_torn_line_is_ignored(self):
        tracker = ArenaTracker()
        whole = gre.log_line(gre.connect(), gre.state_message())
        self.assertEqual(tracker.feed_line(whole[: len(whole) // 2]), [])
        self.assertFalse(tracker.in_sync)
        self.assertEqual(len(tracker.feed_line(whole)), 2)
        self.assertTrue(tracker.in_sync)


class LogFollowerTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".log")
        os.close(handle)
        self.addCleanup(os.remove, self.path)

    def append(self, text: str) -> None:
        with open(self.path, "a", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def test_reads_only_what_is_new_and_waits_for_the_end_of_a_line(self):
        tracker = ArenaTracker()
        follower = LogFollower(self.path, tracker)
        line = gre.log_line(gre.connect(), gre.state_message(state_id=10))
        self.append("[UnityCrossThreadLogger] something else\n" + line[:50])
        follower.poll()
        self.assertIsNone(tracker.game_state_id)
        self.append(line[50:])
        follower.poll()
        self.assertEqual(tracker.game_state_id, 10)
        self.append(gre.log_line(gre.state_message(state_id=11, full=False)))
        follower.poll()
        self.assertEqual((tracker.game_state_id, tracker.gaps), (11, 0))

    def test_a_restarted_client_truncates_the_log(self):
        tracker = ArenaTracker()
        follower = LogFollower(self.path, tracker)
        self.append(gre.log_line(gre.connect(), gre.state_message(state_id=10)) * 3)
        follower.poll()
        with open(self.path, "w", encoding="utf-8", newline="") as handle:
            handle.write(gre.log_line(gre.connect(), gre.state_message(state_id=2)))
        follower.poll()
        self.assertEqual(tracker.game_state_id, 2)


class SnapshotTests(unittest.TestCase):
    def test_nothing_before_a_game_state(self):
        self.assertIsNone(snapshot.build_state(ArenaTracker(), CARDS))

    def test_board_is_described_from_our_side(self):
        soldier = gre.creature(
            310, 7, 2, 2, type="GameObjectType_Token", subtypes=["SubType_Soldier"],
            color=["CardColor_White"], hasSummoningSickness=True, uniqueAbilities=[{"id": 1, "grpId": 8}],
        )
        message = gre.state_message(
            turn=6, active=gre.OPP, life=(17, 9),
            hand=[gre.card(100, gre.LLANOWAR_ELVES, 31)],
            battlefield=[
                gre.land(200, isTapped=True),
                soldier,
                gre.creature(320, 2, 3, 3, seat=gre.OPP, damage=1, attackState="AttackState_Attacking",
                             attackInfo={"targetId": gre.ME}),
            ],
            extra={"persistentAnnotations": [{
                "id": 9, "type": ["AnnotationType_Counter"], "affectedIds": [310],
                "details": [{"key": "count", "valueInt32": [1]}, {"key": "counter_type", "valueInt32": [1]}],
            }]},
        )
        state = snapshot.build_state(tracker_with(message), CARDS)
        assert state is not None
        me, opp = state["players"]
        self.assertEqual((state["turn"], state["activePlayer"]), (6, 1))
        self.assertEqual((me["life"], opp["life"]), (17, 9))
        self.assertEqual(me["hand"], [{"id": 100, "name": "Llanowar Elves"}])
        self.assertEqual((opp["hand"], opp["handSize"], opp["librarySize"]), ([], 3, 30))
        land, token = me["battlefield"]
        self.assertEqual((land["name"], land["tapped"], land["types"]), ("Forest", True, ["Land"]))
        self.assertEqual(token, {
            "id": 310, "name": "Soldier", "token": True, "sick": True, "types": ["Creature"],
            "subtypes": ["Soldier"], "colors": "W", "power": 2, "toughness": 2,
            "basePower": 1, "baseToughness": 1, "keywords": ["Flying"], "counters": {"+1/+1": 1},
        })
        self.assertEqual(opp["battlefield"][0]["damage"], 1)

    def test_block_request_lists_attackers_and_arenas_legal_blocks(self):
        message = gre.state_message(
            active=gre.OPP, phase="Phase_Combat", step="Step_DeclareBlock",
            battlefield=[
                gre.creature(310, 1, 2, 2),
                gre.creature(320, 2, 3, 3, seat=gre.OPP, attackState="AttackState_Attacking",
                             attackInfo={"targetId": gre.ME}),
                gre.creature(321, 2, 3, 3, seat=gre.OPP),
            ],
        )
        tracker = tracker_with(message, {
            "type": "GREMessageType_DeclareBlockersReq", "systemSeatIds": [gre.ME], "gameStateId": 10,
            "declareBlockersReq": {"blockers": [{"blockerInstanceId": 310, "attackerInstanceIds": [320]}]},
        })
        self.assertEqual(
            snapshot.block_request(tracker), {"attackers": [{"id": 320}], "legalBlocks": {"310": [320]}}
        )

    def test_land_drop_is_used_when_arena_stops_offering_one(self):
        hand = [gre.card(100, gre.FOREST, 31), gre.card(101, gre.LLANOWAR_ELVES, 31)]
        offered = gre.state_message(hand=hand, actions=[gre.play(100), gre.cast(101)])
        not_offered = gre.state_message(hand=hand, actions=[gre.cast(101)])
        self.assertEqual(snapshot.build_state(tracker_with(offered), CARDS)["players"][0]["landsPlayed"], 0)
        self.assertEqual(snapshot.build_state(tracker_with(not_offered), CARDS)["players"][0]["landsPlayed"], 1)
        self.assertEqual(snapshot.main_request(tracker_with(offered)), {"castable": [101], "playable": [100]})


if __name__ == "__main__":
    unittest.main()
