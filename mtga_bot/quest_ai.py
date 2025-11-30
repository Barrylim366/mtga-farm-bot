from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from .game_model import GameState, MatchPhase, QuestProgress


class ActionType(str, Enum):
    QUEUE_FOR_MATCH = "queue_for_match"
    KEEP_HAND = "keep_hand"
    CAST_SPELL = "cast_spell"
    ATTACK_ALL = "attack_all"
    SURRENDER = "surrender"
    END_STEP = "end_step"
    WAIT = "wait"
    EXIT = "exit"


@dataclass
class Action:
    action_type: ActionType
    details: Optional[Dict[str, object]] = None
    reason: str = ""


class BaseStrategy:
    quest_kind: str = "generic"

    def get_action(self, state: GameState, quest: Optional[QuestProgress]) -> Optional[Action]:
        raise NotImplementedError


class PlayGamesStrategy(BaseStrategy):
    quest_kind = "play_games"

    def get_action(self, state: GameState, quest: Optional[QuestProgress]) -> Optional[Action]:
        if state.phase == MatchPhase.IDLE:
            return Action(ActionType.QUEUE_FOR_MATCH, reason="Idle: queue for next match")
        if state.phase == MatchPhase.QUEUED:
            return Action(ActionType.WAIT, reason="Waiting for opponent")

        if state.phase == MatchPhase.IN_MATCH:
            # Simple flow: keep hand → try to play/cast → attack → optionally exit later.
            if state.turn == 0:
                return Action(ActionType.KEEP_HAND, reason="Accept starting hand by default")
            if state.turn <= 2:
                return Action(ActionType.CAST_SPELL, reason="Play first card from hand")
            if state.turn >= 4:
                return Action(ActionType.SURRENDER, reason="Exit to speed up quest completion")
            return Action(ActionType.ATTACK_ALL, reason="Progress quest by attacking with all")

        return None


class CastSpellsStrategy(BaseStrategy):
    quest_kind = "cast_spells"

    def __init__(self, preferred_color: str = "red") -> None:
        self.preferred_color = preferred_color

    def get_action(self, state: GameState, quest: Optional[QuestProgress]) -> Optional[Action]:
        if state.phase == MatchPhase.IDLE:
            return Action(ActionType.QUEUE_FOR_MATCH, reason="Idle: queue to cast spells")
        if state.phase == MatchPhase.QUEUED:
            return Action(ActionType.WAIT, reason="Waiting for match start")
        if state.phase == MatchPhase.IN_MATCH:
            if state.turn == 0:
                return Action(ActionType.WAIT, reason="Give client a moment to load the battlefield")
            if quest and quest.progress >= quest.goal - 1:
                return Action(ActionType.SURRENDER, reason="Quest nearly done; exit early to save time")
            return Action(
                ActionType.CAST_SPELL,
                details={"color": self.preferred_color},
                reason="Cast cheap spells to advance color-based quest",
            )
        return None


class CombatStrategy(BaseStrategy):
    quest_kind = "combat"

    def get_action(self, state: GameState, quest: Optional[QuestProgress]) -> Optional[Action]:
        if state.phase == MatchPhase.IDLE:
            return Action(ActionType.QUEUE_FOR_MATCH, reason="Queue for combat quest")
        if state.phase == MatchPhase.IN_MATCH:
            return Action(ActionType.ATTACK_ALL, reason="Aggressive attacks to finish combat quest")
        return Action(ActionType.WAIT, reason="No-op until match state changes")


class QuestAI:
    """
    Maps quest types to strategies and returns an action suggestion
    given the current GameState.
    """

    def __init__(self, default_color: str = "red", default_strategy: str = "play_games") -> None:
        self._strategies: Dict[str, BaseStrategy] = {}
        self._default_strategy = default_strategy
        self.register_strategy("play_games", PlayGamesStrategy())
        self.register_strategy("cast_spells", CastSpellsStrategy(preferred_color=default_color))
        self.register_strategy("combat", CombatStrategy())

    def register_strategy(self, name: str, strategy: BaseStrategy) -> None:
        self._strategies[name] = strategy

    def get_action(self, state: GameState) -> Optional[Action]:
        if state.phase == MatchPhase.IN_MATCH and not state.hand_kept:
            return Action(ActionType.KEEP_HAND, reason="Keep opening hand once per match")

        quest = self._select_quest(state)
        strategy = self._select_strategy(quest)
        return strategy.get_action(state, quest)

    def _select_quest(self, state: GameState) -> Optional[QuestProgress]:
        active = state.active_quests()
        return active[0] if active else None

    def _select_strategy(self, quest: Optional[QuestProgress]) -> BaseStrategy:
        if quest and quest.kind in self._strategies:
            return self._strategies[quest.kind]
        if self._default_strategy in self._strategies:
            return self._strategies[self._default_strategy]
        return self._strategies["play_games"]


def register_strategy(name: str, strategy: BaseStrategy, ai: Optional[QuestAI] = None) -> QuestAI:
    """Convenience hook for plugins."""
    ai = ai or QuestAI()
    ai.register_strategy(name, strategy)
    return ai
