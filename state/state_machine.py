from __future__ import annotations

import re
from collections import deque
from enum import Enum


class BotState(str, Enum):
    HOME = "HOME"
    PLAY_MENU = "PLAY_MENU"
    FIND_MATCH = "FIND_MATCH"
    HISTORIC = "HISTORIC"
    MY_DECKS = "MY_DECKS"
    IN_GAME = "IN_GAME"
    OPTIONS = "OPTIONS"
    STORE = "STORE"
    UNKNOWN = "UNKNOWN"


_SCENE_MAP = {
    "home": BotState.HOME,
    "frontdoor": BotState.HOME,
    "mainmenu": BotState.HOME,
    "play": BotState.PLAY_MENU,
    "playblade": BotState.PLAY_MENU,
    "matchmaking": BotState.FIND_MATCH,
    "decks": BotState.MY_DECKS,
    "collection": BotState.MY_DECKS,
    "store": BotState.STORE,
    "options": BotState.OPTIONS,
}


def get_state_from_playerlog(log_tail: str) -> BotState:
    text = str(log_tail or "")
    if not text:
        return BotState.UNKNOWN

    lowered = text.lower()
    if "gremessagetype_gamestatemessage" in lowered:
        return BotState.IN_GAME

    scene_matches = re.findall(r'"toSceneName"\s*:\s*"([^"]+)"', text)
    if scene_matches:
        scene = scene_matches[-1].strip().lower()
        for key, value in _SCENE_MAP.items():
            if key in scene:
                return value

    if "my decks" in lowered:
        return BotState.MY_DECKS
    if "historic" in lowered:
        return BotState.HISTORIC
    if "find match" in lowered:
        return BotState.FIND_MATCH
    if "mainnav load in" in lowered:
        return BotState.HOME
    return BotState.UNKNOWN


def should_act(
    state: BotState,
    pending_message_count: int,
    decision_player_ok: bool,
    stack_present: bool,
) -> bool:
    if state == BotState.UNKNOWN:
        return False
    if int(pending_message_count or 0) > 0:
        return False
    if stack_present and not decision_player_ok:
        return False
    return True


class PlayerLogStateTracker:
    def __init__(self, max_lines: int = 400) -> None:
        self._lines: deque[str] = deque(maxlen=max(50, int(max_lines)))
        self._last_state: BotState = BotState.UNKNOWN

    def push_line(self, line: str) -> None:
        text = str(line or "").strip()
        if not text:
            return
        self._lines.append(text)
        self._last_state = get_state_from_playerlog("\n".join(self._lines))

    def get_state(self) -> BotState:
        return self._last_state

    def get_tail(self, max_lines: int = 120) -> str:
        count = max(1, int(max_lines))
        return "\n".join(list(self._lines)[-count:])
