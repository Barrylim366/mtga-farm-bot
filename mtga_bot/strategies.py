from __future__ import annotations

from typing import Optional

from .game_model import GameState, MatchPhase, QuestProgress
from .quest_ai import Action, ActionType, BaseStrategy


class DeckStrategy(BaseStrategy):
    """
    Base class for deck-specific behaviour.
    Override `get_action` and optionally `supports` to provide tailored logic.
    """

    deck_name: str = "generic"

    def supports(self, deck_id: str) -> bool:
        return deck_id == self.deck_name

    def get_action(self, state: GameState, quest: Optional[QuestProgress]) -> Optional[Action]:
        return None


class MonoRedAggroStrategy(DeckStrategy):
    deck_name = "mono_red"

    def get_action(self, state: GameState, quest: Optional[QuestProgress]) -> Optional[Action]:
        if state.phase == MatchPhase.IDLE:
            return Action(ActionType.QUEUE_FOR_MATCH, reason="Mono-red: queue quickly")
        if state.phase == MatchPhase.IN_MATCH:
            # Cast spells aggressively, then attack.
            if quest and quest.kind == "cast_spells":
                return Action(ActionType.CAST_SPELL, details={"color": "red"}, reason="Mono-red spell cast")
            return Action(ActionType.ATTACK_ALL, reason="Mono-red aggro attack")
        return None
