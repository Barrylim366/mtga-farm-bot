from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Dict, Generator, Optional


class EventType(Enum):
    QUEST_UPDATE = auto()
    QUEST_COMPLETE = auto()
    TURN_START = auto()
    MATCH_START = auto()
    MATCH_END = auto()
    QUEUE_ENTERED = auto()
    QUEUE_EXITED = auto()
    ERROR = auto()
    HAND_UPDATE = auto()
    PRIORITY_UPDATE = auto()


@dataclass
class LogEvent:
    event_type: EventType
    payload: Dict[str, object]


class LogParser:
    """Small helper to tail the MTGA Player.log and surface structured events."""

    def __init__(self, log_path: str) -> None:
        self.log_path = Path(log_path).expanduser()
        # MTGA log lines change often; keep patterns permissive but specific enough to test.
        self._quest_pattern = re.compile(
            r"Quest\s+(?P<quest_id>[\w-]+).*?(?P<progress>\d+)\s*/\s*(?P<goal>\d+)(?:\s*-\s*(?P<description>.+))?",
            re.IGNORECASE,
        )
        self._quest_complete_pattern = re.compile(
            r"Quest\s+(?P<quest_id>[\w-]+).*(complete|completed)", re.IGNORECASE
        )
        self._turn_pattern = re.compile(r"Turn\s+(?P<turn>\d+)\s+(begin|start)", re.IGNORECASE)
        self._queue_pattern = re.compile(r"(Entering|Joined)\s+queue", re.IGNORECASE)
        self._queue_exit_pattern = re.compile(r"(Queue\s+canceled|Match\s+canceled)", re.IGNORECASE)
        self._match_start_pattern = re.compile(
            r"Match\s+(?P<match_id>[\w-]+)\s+(started|start)", re.IGNORECASE
        )
        self._match_end_pattern = re.compile(
            r"Match\s+(?P<match_id>[\w-]+)\s+(ended|complete)", re.IGNORECASE
        )
        self._state_change_pattern = re.compile(
            r"STATE CHANGED.*\"new\":\"(?P<new_state>[^\"]+)\"", re.IGNORECASE
        )
        self._scene_loaded_pattern = re.compile(r"OnSceneLoaded for (?P<scene>\w+)", re.IGNORECASE)
        self._hand_zone_pattern = re.compile(
            r"(Zone_Hand|\"zone\"\\?\":\\?\"hand\\?\"|\"zoneType\"\\?\":\\?\"ZoneType_Hand\\?\"|\"hand\"\\?:)",
            re.IGNORECASE,
        )
        self._grp_id_pattern = re.compile(r'"grpId"\\?":\\?(\d+)')
        self._priority_pattern = re.compile(r'"?priorityPlayer"?\s*:\s*(\d+)', re.IGNORECASE)
        self._active_pattern = re.compile(r'"?activePlayer"?\s*:\s*(\d+)', re.IGNORECASE)
        # Turn info with phase/step, tolerant to long JSON blobs.
        self._turn_info_pattern = re.compile(
            r'"turnInfo"\s*:\s*\{[^}]*?"turnNumber"\s*:\s*(?P<turn>\d+)[^}]*?'
            r'"activePlayer"\s*:\s*(?P<active>\d+)[^}]*?'
            r'"priorityPlayer"\s*:\s*(?P<prio>\d+)?',
            re.IGNORECASE | re.DOTALL,
        )
        # Capture a hand zone for a given owner seat.
        self._hand_zone_full_pattern = re.compile(
            r'"type"\s*:\s*"ZoneType_Hand"[^}]*?'
            r'"ownerSeatId"\s*:\s*(?P<owner>\d+)[^}]*?'
            r'"objectInstanceIds"\s*:\s*\[(?P<ids>[^\]]*)\]',
            re.IGNORECASE,
        )

    def follow(self, poll_interval: float = 1.0, yield_unparsed: bool = False) -> Generator[LogEvent, None, None]:
        """
        Tail the log file and yield LogEvent objects as new lines arrive.

        The generator never ends unless the file is removed. It is intentionally
        lightweight so it can run alongside UI automation without blocking.
        """
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a+", encoding="utf-8", errors="ignore") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if not line:
                    time.sleep(poll_interval)
                    continue

                event = self.parse_line(line)
                if event:
                    yield event
                elif yield_unparsed:
                    yield LogEvent(EventType.ERROR, {"message": line.strip(), "unparsed": True})

    def parse_line(self, line: str) -> Optional[LogEvent]:
        """Convert a raw log line into a LogEvent or None if nothing matched."""
        text = line.strip()
        if not text:
            return None

        # Priority / turn info first, to avoid short-circuit by other patterns.
        if "priority" in text.lower() or "activeplayer" in text.lower() or "turninfo" in text.lower():
            active_match = self._active_pattern.search(text)
            prio_match = self._priority_pattern.search(text)
            turn_match = self._turn_info_pattern.search(text)
            if active_match or prio_match or turn_match:
                payload: Dict[str, object] = {}
                if turn_match:
                    payload["turn"] = int(turn_match.group("turn"))
                    payload["active_player"] = int(turn_match.group("active"))
                    prio = turn_match.group("prio")
                    if prio:
                        payload["priority_player"] = int(prio)
                else:
                    if active_match:
                        payload["active_player"] = int(active_match.group(1))
                    if prio_match:
                        payload["priority_player"] = int(prio_match.group(1))
                return LogEvent(EventType.PRIORITY_UPDATE, payload)

        quest_match = self._quest_pattern.search(text)
        if quest_match:
            payload = {
                "quest_id": quest_match.group("quest_id"),
                "progress": int(quest_match.group("progress")),
                "goal": int(quest_match.group("goal")),
                "description": (quest_match.group("description") or "").strip(),
            }
            payload["kind"] = self._infer_quest_kind(payload["description"])
            return LogEvent(EventType.QUEST_UPDATE, payload)

        quest_complete_match = self._quest_complete_pattern.search(text)
        if quest_complete_match:
            payload = {"quest_id": quest_complete_match.group("quest_id")}
            return LogEvent(EventType.QUEST_COMPLETE, payload)

        # Capture hand composition updates (grpIds inside hand zone payloads).
        if self._hand_zone_pattern.search(text):
            grp_ids = [int(g) for g in self._grp_id_pattern.findall(text)]
            if grp_ids:
                return LogEvent(EventType.HAND_UPDATE, {"grp_ids": grp_ids})

        turn_match = self._turn_pattern.search(text)
        if turn_match:
            payload = {"turn": int(turn_match.group("turn"))}
            return LogEvent(EventType.TURN_START, payload)

        if self._queue_pattern.search(text):
            return LogEvent(EventType.QUEUE_ENTERED, {"message": text})

        if self._queue_exit_pattern.search(text):
            return LogEvent(EventType.QUEUE_EXITED, {"message": text})

        match_start = self._match_start_pattern.search(text)
        if match_start:
            return LogEvent(EventType.MATCH_START, {"match_id": match_start.group("match_id")})

        match_end = self._match_end_pattern.search(text)
        if match_end:
            return LogEvent(EventType.MATCH_END, {"match_id": match_end.group("match_id")})

        state_change = self._state_change_pattern.search(text)
        if state_change:
            new_state = state_change.group("new_state")
            lowered = new_state.lower()
            if "connectedtomatchdoor_connectingtogre" in lowered:
                return LogEvent(EventType.QUEUE_ENTERED, {"state": new_state})
            if "playing" in lowered or "duel" in lowered or "battlefield" in lowered:
                return LogEvent(EventType.MATCH_START, {"state": new_state})
            if "queue" in lowered or "connectingtomatchdoor" in lowered or "waiting" in lowered:
                return LogEvent(EventType.QUEUE_ENTERED, {"state": new_state})
            if "matchcompleted" in lowered or "postmatch" in lowered or "home" in lowered:
                return LogEvent(EventType.MATCH_END, {"state": new_state})

        scene_loaded = self._scene_loaded_pattern.search(text)
        if scene_loaded:
            scene = scene_loaded.group("scene").lower()
            if "duel" in scene or "battlefield" in scene:
                return LogEvent(EventType.MATCH_START, {"scene": scene})
            if "home" in scene or "mainmenu" in scene:
                return LogEvent(EventType.QUEUE_EXITED, {"scene": scene})

        # Priority / active player hints (requires detailed logs).
        if "priority" in text.lower() or "activeplayer" in text.lower():
            active_match = self._active_pattern.search(text)
            prio_match = self._priority_pattern.search(text)
            if active_match or prio_match:
                payload: Dict[str, object] = {}
                if active_match:
                    payload["active_player"] = int(active_match.group(1))
                if prio_match:
                    payload["priority_player"] = int(prio_match.group(1))
                return LogEvent(EventType.PRIORITY_UPDATE, payload)

        # Turn info and priority embedded in turnInfo blocks.
        turn_match = self._turn_info_pattern.search(text)
        if turn_match:
            payload = {
                "turn": int(turn_match.group("turn")),
                "active_player": int(turn_match.group("active")),
            }
            prio = turn_match.group("prio")
            if prio:
                payload["priority_player"] = int(prio)
            return LogEvent(EventType.PRIORITY_UPDATE, payload)

        # Full hand zone update (Detailed logs).
        if "ZoneType_Hand" in text:
            hand_match = self._hand_zone_full_pattern.search(text)
            if hand_match:
                ids_raw = hand_match.group("ids")
                grp_ids = []
                try:
                    for part in ids_raw.split(","):
                        part = part.strip()
                        if part:
                            grp_ids.append(int(part))
                except Exception:
                    grp_ids = []
                return LogEvent(EventType.HAND_UPDATE, {"grp_ids": grp_ids})

        if "error" in text.lower():
            return LogEvent(EventType.ERROR, {"message": text})

        return None

    @staticmethod
    def _infer_quest_kind(description: str) -> str:
        """
        Lightweight heuristic to classify quests.
        Results are used by the QuestAI to pick strategies.
        """
        lowered = description.lower()
        if "spell" in lowered or "zauber" in lowered:
            return "cast_spells"
        if "creature" in lowered or "angreifen" in lowered or "attack" in lowered:
            return "combat"
        return "play_games"
