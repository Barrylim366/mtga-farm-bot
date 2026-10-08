"""Follows the game state Arena's rules engine (the GRE) writes into Player.log.

The GRE sends one full state per game and diffs after that. A diff re-sends every changed object,
zone and player *whole*, with default values left out: an untapped permanent simply has no
`isTapped`, a player at 0 life has no `lifeTotal`. So each one replaces what was known before --
patching field by field would keep a creature tapped (or attacking, or damaged) forever. Checked
against real logs: 288 of 288 objects re-sent without `isTapped` came with an explicit untap.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

_GSM_TYPES = ("GREMessageType_GameStateMessage", "GREMessageType_QueuedGameStateMessage")


class ArenaTracker:
    def __init__(self) -> None:
        self.my_seat: Optional[int] = None
        self.messages_seen = 0
        self.gaps = 0
        self._reset_game()

    def _reset_game(self) -> None:
        self.zones: dict[int, dict] = {}
        self.objects: dict[int, dict] = {}
        self.players: dict[int, dict] = {}
        self.turn_info: dict = {}
        self.persistent: dict[int, dict] = {}
        self.actions: list[dict] = []
        self.game_state_id: Optional[int] = None
        self.game_info: dict = {}
        # the request the GRE is currently waiting on us to answer, if any
        self.pending: Optional[dict] = None
        self.pending_type: Optional[str] = None
        self.in_sync = False

    # ------------------------------------------------------------------ input
    def feed_line(self, line: str) -> list[dict]:
        """Apply one Player.log line. Returns the GRE messages it carried (empty for any other line)."""
        if "greToClientEvent" not in line:
            return []
        start = line.find("{")
        if start < 0:
            return []
        try:
            payload = json.loads(line[start:])
        except ValueError:
            # a torn line: MTGA was still writing it
            return []
        messages = (payload.get("greToClientEvent") or {}).get("greToClientMessages") or []
        for message in messages:
            self.feed_message(message)
        return messages

    def feed_message(self, message: dict) -> None:
        self.messages_seen += 1
        kind = message.get("type")
        seats = message.get("systemSeatIds") or []
        if kind == "GREMessageType_ConnectResp" and len(seats) == 1:
            self.my_seat = seats[0]
        if kind in _GSM_TYPES:
            gsm = message.get("gameStateMessage")
            if gsm:
                self._apply_state(gsm)
            return
        if kind in (
            "GREMessageType_ActionsAvailableReq",
            "GREMessageType_DeclareAttackersReq",
            "GREMessageType_DeclareBlockersReq",
            "GREMessageType_SelectTargetsReq",
            "GREMessageType_SelectNReq",
            "GREMessageType_MulliganReq",
            "GREMessageType_AssignDamageReq",
            "GREMessageType_OrderReq",
            "GREMessageType_GroupReq",
            "GREMessageType_PromptReq",
            "GREMessageType_CastingTimeOptionsReq",
        ):
            if self.my_seat is None and len(seats) == 1:
                self.my_seat = seats[0]
            self.pending = message
            self.pending_type = kind

    def _apply_state(self, gsm: dict) -> None:
        if gsm.get("type") == "GameStateType_Full":
            self._reset_game()
            self.in_sync = True
        else:
            previous = gsm.get("prevGameStateId")
            if self.game_state_id is not None and previous is not None and previous != self.game_state_id:
                # a diff against a state we never saw; what we hold may be wrong until the next full state
                self.gaps += 1
        if "gameStateId" in gsm:
            self.game_state_id = gsm["gameStateId"]
        if "gameInfo" in gsm:
            self.game_info = gsm["gameInfo"]
        if "turnInfo" in gsm:
            self.turn_info = gsm["turnInfo"]
        for zone in gsm.get("zones") or []:
            self.zones[zone["zoneId"]] = zone
        for obj in gsm.get("gameObjects") or []:
            self.objects[obj["instanceId"]] = obj
        for instance_id in gsm.get("diffDeletedInstanceIds") or []:
            self.objects.pop(instance_id, None)
        for player in gsm.get("players") or []:
            seat = player.get("systemSeatNumber")
            if seat is not None:
                self.players[seat] = player
        for annotation in gsm.get("persistentAnnotations") or []:
            if "id" in annotation:
                self.persistent[annotation["id"]] = annotation
        for annotation_id in gsm.get("diffDeletedPersistentAnnotationIds") or []:
            self.persistent.pop(annotation_id, None)
        # sent whole with every state; absent means there is nothing we can do
        self.actions = gsm.get("actions") or []

    # ------------------------------------------------------------------ queries
    def pending_is_current(self, kind: str) -> bool:
        """True while the GRE is still waiting for our answer to a request of this kind.

        A request names the state it was asked in; once a newer state has arrived it has been answered.
        """
        if self.pending is None or self.pending_type != kind:
            return False
        asked_in = self.pending.get("gameStateId")
        return asked_in is None or asked_in == self.game_state_id

    @property
    def opp_seat(self) -> Optional[int]:
        if self.my_seat is None:
            return None
        for seat in self.players:
            if seat != self.my_seat:
                return seat
        return 2 if self.my_seat == 1 else 1

    def zone_objects(self, zone_type: str, owner: Optional[int] = None) -> list[dict]:
        """Cards and tokens in a zone, in the zone's own order. Sub-objects (adventure halves, abilities) are skipped."""
        out = []
        for zone in self.zones.values():
            if zone.get("type") != zone_type:
                continue
            if owner is not None and zone.get("ownerSeatId") not in (None, owner):
                continue
            for instance_id in zone.get("objectInstanceIds") or []:
                obj = self.objects.get(instance_id)
                if obj is not None and obj.get("type") in ("GameObjectType_Card", "GameObjectType_Token"):
                    out.append(obj)
        return out

    def zone_size(self, zone_type: str, owner: int) -> int:
        for zone in self.zones.values():
            if zone.get("type") == zone_type and zone.get("ownerSeatId") == owner:
                return len(zone.get("objectInstanceIds") or [])
        return 0

    def stack_is_empty(self) -> bool:
        for zone in self.zones.values():
            if zone.get("type") == "ZoneType_Stack":
                return not (zone.get("objectInstanceIds") or [])
        return True

    def annotations_on(self, instance_id: int, annotation_type: str) -> list[dict]:
        return [
            a for a in self.persistent.values()
            if annotation_type in (a.get("type") or []) and instance_id in (a.get("affectedIds") or [])
        ]

    def my_actions(self) -> list[dict]:
        """What we may do right now: the pending request's list when there is one, else the state's."""
        if self.pending_type == "GREMessageType_ActionsAvailableReq" and self.pending:
            return (self.pending.get("actionsAvailableReq") or {}).get("actions") or []
        return [
            (wrapper.get("action") or {}) for wrapper in self.actions
            if wrapper.get("seatId") in (None, self.my_seat)
        ]


class LogFollower:
    """Feeds a tracker from Player.log, picking up where it left off each time `poll` is called.

    The client truncates Player.log when it starts, so a file that got shorter is read from the top.
    A last line without its newline is still being written and is left for the next poll.
    """

    def __init__(self, path: str, tracker: ArenaTracker) -> None:
        self.path = path
        self.tracker = tracker
        self._offset = 0
        self._tail = b""

    def poll(self) -> list[dict[str, Any]]:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self._offset:
            self._offset = 0
            self._tail = b""
        if size == self._offset:
            return []
        with open(self.path, "rb") as handle:
            handle.seek(self._offset)
            data = handle.read(size - self._offset)
        self._offset += len(data)
        data = self._tail + data
        lines = data.split(b"\n")
        self._tail = lines.pop()
        seen: list[dict[str, Any]] = []
        for raw in lines:
            if b"greToClientEvent" in raw:
                seen.extend(self.tracker.feed_line(raw.decode("utf-8", errors="replace")))
        return seen
