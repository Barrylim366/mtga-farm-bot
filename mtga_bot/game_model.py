from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from .log_parser import EventType, LogEvent


class MatchPhase(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    IN_MATCH = "in_match"
    EXITING = "exiting"


@dataclass
class QuestProgress:
    quest_id: str
    description: str
    progress: int
    goal: int
    kind: str = "play_games"
    reward: Optional[str] = None

    def is_complete(self) -> bool:
        return self.progress >= self.goal

    def update(self, progress: int, goal: int, description: str, kind: Optional[str] = None) -> None:
        self.progress = progress
        self.goal = goal
        if description:
            self.description = description
        if kind:
            self.kind = kind


@dataclass
class GameState:
    """Lightweight state machine to track MTGA session progress."""

    phase: MatchPhase = MatchPhase.IDLE
    quests: Dict[str, QuestProgress] = field(default_factory=dict)
    turn: int = 0
    match_id: Optional[str] = None

    def apply_event(self, event: LogEvent) -> None:
        """Update internal state based on a parsed log event."""
        if event.event_type == EventType.QUEST_UPDATE:
            self._apply_quest_update(event)
        elif event.event_type == EventType.QUEST_COMPLETE:
            quest_id = event.payload.get("quest_id")
            if quest_id and quest_id in self.quests:
                self.quests[quest_id].progress = self.quests[quest_id].goal
        elif event.event_type == EventType.QUEUE_ENTERED:
            self.phase = MatchPhase.QUEUED
        elif event.event_type == EventType.QUEUE_EXITED:
            self.phase = MatchPhase.IDLE
        elif event.event_type == EventType.MATCH_START:
            self.phase = MatchPhase.IN_MATCH
            self.match_id = event.payload.get("match_id")
            self.turn = 0
        elif event.event_type == EventType.TURN_START:
            self.turn = int(event.payload.get("turn", 0))
            if self.phase == MatchPhase.QUEUED:
                self.phase = MatchPhase.IN_MATCH
        elif event.event_type == EventType.MATCH_END:
            self.phase = MatchPhase.IDLE
            self.turn = 0
            self.match_id = None

    def _apply_quest_update(self, event: LogEvent) -> None:
        quest_id = str(event.payload.get("quest_id"))
        description = str(event.payload.get("description", "Quest"))
        progress = int(event.payload.get("progress", 0))
        goal = int(event.payload.get("goal", 0))
        kind = str(event.payload.get("kind") or "play_games")

        existing = self.quests.get(quest_id)
        if existing:
            existing.update(progress=progress, goal=goal, description=description, kind=kind)
        else:
            self.quests[quest_id] = QuestProgress(
                quest_id=quest_id,
                description=description,
                progress=progress,
                goal=goal,
                kind=kind,
            )

    def active_quests(self) -> List[QuestProgress]:
        return [quest for quest in self.quests.values() if not quest.is_complete()]

    def is_idle(self) -> bool:
        return self.phase == MatchPhase.IDLE
