"""Follows the game state Arena's rules engine (the GRE) writes into Player.log.

Kept apart from the controller's own merged GameState on purpose. The GRE sends
one full state per game and diffs after that, and a diff re-sends every changed
object, zone and player *whole*, with default values left out: an untapped
permanent simply has no ``isTapped``. So each one replaces what was known
before -- a field-by-field merge would keep a creature tapped (or attacking, or
damaged) for ever, which is fine for the bot's own heuristics but would hand
Forge a wrong board.
"""
from __future__ import annotations

import json
import os
from typing import Any

GSM_TYPES = ("GREMessageType_GameStateMessage", "GREMessageType_QueuedGameStateMessage")

# The requests the GRE waits on a player to answer.
REQUEST_TYPES = (
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
    "GREMessageType_PayCostsReq",
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


class ArenaTracker:
    """The game as the log has it, kept in the order the GRE introduced things."""

    def __init__(self) -> None:
        self.my_seat: int | None = None
        self.messages_seen = 0
        self.gaps = 0
        self._reset_game()

    def _reset_game(self) -> None:
        self._activation_observer: dict | None = None
        self.zones: dict[int, dict] = {}
        self.objects: dict[int, dict] = {}
        self.players: dict[int, dict] = {}
        self.turn_info: dict = {}
        self.persistent: dict[int, dict] = {}
        self.actions: list = []
        self.game_state_id: int | None = None
        self.game_info: dict = {}
        self.pending: dict | None = None
        self.pending_type: str | None = None
        self.in_sync = False

    # -- input ----------------------------------------------------------------

    def feed_line(self, line: str) -> list:
        """Apply one Player.log line; returns the GRE messages it carried."""
        if "greToClientEvent" not in line:
            return []
        start = line.find("{")
        if start == -1:
            return []
        try:
            payload = json.loads(line[start:])
        except ValueError:
            # A torn line: MTGA was still writing it.
            return []
        event = payload.get("greToClientEvent") if isinstance(payload, dict) else None
        messages = (event or {}).get("greToClientMessages") or []
        for message in messages:
            if isinstance(message, dict):
                self.feed_message(message)
        return messages

    def feed_message(self, message: dict) -> None:
        self.messages_seen += 1
        kind = message.get("type") or ""
        seats = message.get("systemSeatIds") or []
        only_seat = _as_int(seats[0]) if len(seats) == 1 else None
        if kind == "GREMessageType_ConnectResp" and only_seat is not None:
            self.my_seat = only_seat
        if kind in GSM_TYPES:
            state = message.get("gameStateMessage")
            if state:
                watch = self._activation_observer
                self._apply_state(state)
                if (watch is not None and self._activation_observer is None
                        and watch["match_id"] == self.game_info.get("matchID")):
                    self._activation_observer = watch
                self._observe_activation(message, state)
            return
        if kind in REQUEST_TYPES:
            if self.my_seat is None and only_seat is not None:
                self.my_seat = only_seat
            self.pending = message
            self.pending_type = kind

    def _apply_state(self, gsm: dict) -> None:
        if gsm.get("type") == "GameStateType_Full":
            self._reset_game()
            self.in_sync = True
        else:
            previous = _as_int(gsm.get("prevGameStateId"))
            if self.game_state_id is not None and previous is not None and previous != self.game_state_id:
                # A diff against a state we never saw; what we hold may be
                # wrong until the next full state.
                self.gaps += 1
        if "gameStateId" in gsm:
            self.game_state_id = _as_int(gsm.get("gameStateId"))
        if "gameInfo" in gsm:
            self.game_info = gsm.get("gameInfo") or {}
        if "turnInfo" in gsm:
            self.turn_info = gsm.get("turnInfo") or {}
        for zone in gsm.get("zones") or []:
            zone_id = _as_int(zone.get("zoneId"))
            if zone_id is not None:
                self.zones[zone_id] = zone
        for obj in gsm.get("gameObjects") or []:
            instance_id = _as_int(obj.get("instanceId"))
            if instance_id is not None:
                self.objects[instance_id] = obj
        for instance_id in gsm.get("diffDeletedInstanceIds") or []:
            self.objects.pop(instance_id, None)
        for player in gsm.get("players") or []:
            seat = _as_int(player.get("systemSeatNumber"))
            if seat is not None:
                self.players[seat] = player
        for annotation in gsm.get("persistentAnnotations") or []:
            annotation_id = _as_int(annotation.get("id"))
            if annotation_id is not None:
                self.persistent[annotation_id] = annotation
        for annotation_id in gsm.get("diffDeletedPersistentAnnotationIds") or []:
            self.persistent.pop(annotation_id, None)
        # Sent whole with every state; absent means there is nothing to do.
        self.actions = list(gsm.get("actions") or [])

    # -- queries --------------------------------------------------------------

    def begin_activation_observation(self, sources: set, ability_id: int) -> dict:
        """Watch only new evidence received after this attempt was armed."""
        watch = {
            "match_id": self.game_info.get("matchID"), "sources": set(sources),
            "ability_id": ability_id, "seat": self.my_seat,
            "existing": set(self.objects), "created": {}, "objects": {},
            "evidence": None, "armed_message": self.messages_seen,
            "armed_state": self.game_state_id,
        }
        self._activation_observer = watch
        return watch

    def end_activation_observation(self, watch: dict) -> None:
        if self._activation_observer is watch:
            self._activation_observer = None

    def activation_evidence(self, watch: dict) -> dict | None:
        if (self._activation_observer is not watch
                or watch["match_id"] != self.game_info.get("matchID")):
            return None
        evidence = watch["evidence"]
        return dict(evidence) if evidence else None

    def _observe_activation(self, message: dict, state: dict) -> None:
        watch = self._activation_observer
        if watch is None:
            return
        if watch["match_id"] != self.game_info.get("matchID"):
            self._activation_observer = None
            return
        if watch["evidence"] is not None:
            return
        state_id = _as_int(state.get("gameStateId"))
        if (state_id is not None and watch["armed_state"] is not None
                and state_id <= watch["armed_state"]):
            return
        for annotation in state.get("annotations") or []:
            if ("AnnotationType_AbilityInstanceCreated" in (annotation.get("type") or [])
                    and annotation.get("affectorId") in watch["sources"]):
                for instance_id in annotation.get("affectedIds") or []:
                    if instance_id not in watch["existing"]:
                        watch["created"][instance_id] = annotation["affectorId"]
        for obj in state.get("gameObjects") or []:
            instance_id = obj.get("instanceId")
            if (instance_id not in watch["existing"]
                    and obj.get("type") == "GameObjectType_Ability"):
                watch["objects"][instance_id] = dict(obj)
        for instance_id, obj in watch["objects"].items():
            source = obj.get("parentId", watch["created"].get(instance_id))
            if (source in watch["sources"] and obj.get("grpId") == watch["ability_id"]
                    and obj.get("controllerSeatId") == watch["seat"]):
                watch["evidence"] = {
                    "kind": "source_ability_created", "source_id": source,
                    "ability_id": watch["ability_id"], "instance_id": instance_id,
                    "msg_id": message.get("msgId"), "game_state_id": state.get("gameStateId"),
                }
                return
        # One attempt lasts seconds; cap unmatched split-message evidence.
        for key in ("created", "objects"):
            while len(watch[key]) > 128:
                watch[key].pop(next(iter(watch[key])))

    def pending_is_current(self, kind: str) -> bool:
        """True while the GRE still waits for our answer to a request of this kind.

        A request names the state it was asked in; once a newer state has
        arrived it has been answered.
        """
        if self.pending is None or self.pending_type != kind:
            return False
        asked_in = _as_int(self.pending.get("gameStateId"))
        return asked_in is None or asked_in == self.game_state_id

    def opp_seat(self) -> int | None:
        if self.my_seat is None:
            return None
        for seat in self.players:
            if seat != self.my_seat:
                return seat
        return 2 if self.my_seat == 1 else 1

    def object(self, instance_id: int | None) -> dict | None:
        if instance_id is None:
            return None
        return self.objects.get(instance_id)

    def zone_objects(self, zone_type: str, owner: int | None = None) -> list[dict]:
        """Cards and tokens in a zone, in the zone's order. Sub-objects are skipped."""
        found = []
        for zone in self.zones.values():
            if zone.get("type") != zone_type:
                continue
            zone_owner = zone.get("ownerSeatId")
            if owner is not None and zone_owner is not None and zone_owner != owner:
                continue
            for instance_id in zone.get("objectInstanceIds") or []:
                obj = self.objects.get(instance_id)
                if obj and obj.get("type") in ("GameObjectType_Card", "GameObjectType_Token"):
                    found.append(obj)
        return found

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

    def abilities_on_stack(self) -> list[dict]:
        """Activated and triggered abilities on the stack; each names its source in parentId."""
        found = []
        for zone in self.zones.values():
            if zone.get("type") != "ZoneType_Stack":
                continue
            for instance_id in zone.get("objectInstanceIds") or []:
                obj = self.objects.get(instance_id)
                if obj and obj.get("type") == "GameObjectType_Ability":
                    found.append(obj)
        return found

    @staticmethod
    def _annotation_is(annotation: dict, annotation_type: str) -> bool:
        return annotation_type in (annotation.get("type") or [])

    def attached(self) -> dict[int, int]:
        """Auras and equipment that are on something: attachment -> what it is on."""
        attached = {}
        for annotation in self.persistent.values():
            affected = annotation.get("affectedIds") or []
            if self._annotation_is(annotation, "AnnotationType_Attachment") and affected:
                attachment = _as_int(annotation.get("affectorId"))
                target = _as_int(affected[0])
                if attachment is not None and target is not None:
                    attached[attachment] = target
        return attached

    def annotations_on(self, instance_id: int, annotation_type: str) -> list[dict]:
        return [
            annotation
            for annotation in self.persistent.values()
            if self._annotation_is(annotation, annotation_type)
            and instance_id in (annotation.get("affectedIds") or [])
        ]

    def my_actions(self) -> list[dict]:
        """What we may do right now: the pending request's list, else the state's."""
        if self.pending_is_current("GREMessageType_ActionsAvailableReq") and self.pending is not None:
            return list((self.pending.get("actionsAvailableReq") or {}).get("actions") or [])
        out = []
        for wrapper in self.actions:
            seat = wrapper.get("seatId")
            if seat is None or seat == self.my_seat:
                out.append(wrapper.get("action") or {})
        return out


class LogFollower:
    """Feeds a tracker from Player.log, picking up where it left off each poll.

    The client truncates Player.log when it starts, so a file that got shorter
    is read from the top. A last line without its newline is still being
    written and is left for the next poll. Bytes are decoded leniently: a card
    or player name in a non-Latin script must never stop the reading.
    """

    def __init__(self, path) -> None:
        self.path = str(path)
        self.offset = 0
        self.tail = b""

    def poll(self, tracker: ArenaTracker) -> list:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0
            self.tail = b""
        if size == self.offset:
            return []
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                data = handle.read(size - self.offset)
        except OSError:
            return []
        self.offset += len(data)
        whole = self.tail + data
        lines = whole.split(b"\n")
        self.tail = lines.pop()
        seen = []
        for raw in lines:
            if b"greToClientEvent" in raw:
                seen.extend(tracker.feed_line(raw.decode("utf-8", errors="replace")))
        return seen
