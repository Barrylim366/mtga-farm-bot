from __future__ import annotations

from dataclasses import dataclass, field
import logging
from enum import Enum
from typing import Dict, List, Optional

from .log_parser import EventType, LogEvent
try:  # Optional import to avoid cycles when running minimal tests without cards.
    from .card_db import CardDatabase
except Exception:  # pragma: no cover
    CardDatabase = None  # type: ignore[misc,assignment]


logger = logging.getLogger(__name__)


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
    player_seat_id: int = 1
    active_player: Optional[int] = None
    priority_player: Optional[int] = None
    quests: Dict[str, QuestProgress] = field(default_factory=dict)
    turn: int = 0
    match_id: Optional[str] = None
    hand_kept: bool = False
    hand_cards: List[int] = field(default_factory=list)
    hand_info: Dict[str, object] = field(default_factory=dict)
    card_db: Optional["CardDatabase"] = None
    last_play_land_turn: int = -1

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
            incoming_match_id = event.payload.get("match_id")
            is_new_match = self.phase != MatchPhase.IN_MATCH or (
                incoming_match_id and self.match_id and incoming_match_id != self.match_id
            )
            self.phase = MatchPhase.IN_MATCH
            if is_new_match:
                self.turn = 0
                self.hand_kept = False
                self.last_play_land_turn = -1
                self.active_player = None
                self.priority_player = None
                logger.debug("GameState: MATCH_START -> reset hand_kept (match_id=%s)", incoming_match_id)
            if incoming_match_id:
                self.match_id = incoming_match_id
        elif event.event_type == EventType.TURN_START:
            self.turn = int(event.payload.get("turn", 0))
            if self.phase in (MatchPhase.QUEUED, MatchPhase.IDLE):
                self.phase = MatchPhase.IN_MATCH
                self.hand_kept = False
                self.last_play_land_turn = -1
                logger.debug("GameState: TURN_START while not in match -> set IN_MATCH and reset hand_kept")
        elif event.event_type == EventType.MATCH_END:
            self.phase = MatchPhase.IDLE
            self.turn = 0
            self.match_id = None
            self.hand_kept = False
            self.last_play_land_turn = -1
            self.active_player = None
            self.priority_player = None
            logger.debug("GameState: MATCH_END -> reset hand_kept")
        elif event.event_type == EventType.HAND_UPDATE:
            grp_ids = event.payload.get("grp_ids")
            if grp_ids:
                self.hand_cards = list(map(int, grp_ids))
                self._summarize_hand()
                # If we start the bot mid-match, a hand update implies we're in a game.
                if self.phase == MatchPhase.IDLE:
                    self.phase = MatchPhase.IN_MATCH
        elif event.event_type == EventType.PRIORITY_UPDATE:
            active = event.payload.get("active_player")
            prio = event.payload.get("priority_player")
            turn_hint = event.payload.get("turn")
            changed = (active is not None and active != self.active_player) or (
                prio is not None and prio != self.priority_player
            )
            if active is not None:
                self.active_player = int(active)
            if prio is not None:
                self.priority_player = int(prio)
            if turn_hint is not None:
                try:
                    self.turn = int(turn_hint)
                except Exception:
                    pass
            if changed:
                logger.debug(
                    "Priority update: active=%s priority=%s turn=%s (mine=%s)",
                    self.active_player,
                    self.priority_player,
                    self.turn,
                    self.player_seat_id,
                )

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

    def has_priority(self) -> bool:
        """Return True if we believe the local player has priority."""
        if self.priority_player is None:
            return True
        return self.priority_player == self.player_seat_id

    def _summarize_hand(self) -> None:
        """Enrich hand_info using the optional card database."""
        if not self.card_db or not hasattr(self.card_db, "summarize_hand"):
            self.hand_info = {"total_cards": len(self.hand_cards)}
            return
        try:
            self.hand_info = self.card_db.summarize_hand(self.hand_cards)
        except Exception:
            self.hand_info = {"total_cards": len(self.hand_cards)}
