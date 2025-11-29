from mtga_bot.game_model import GameState, MatchPhase
from mtga_bot.log_parser import EventType, LogEvent


def test_apply_quest_update():
    state = GameState()
    event = LogEvent(
        EventType.QUEST_UPDATE,
        {"quest_id": "q123", "progress": 2, "goal": 5, "description": "Play 5 games", "kind": "play_games"},
    )
    state.apply_event(event)

    assert "q123" in state.quests
    quest = state.quests["q123"]
    assert quest.progress == 2
    assert quest.goal == 5
    assert not quest.is_complete()


def test_match_state_transitions():
    state = GameState()
    state.apply_event(LogEvent(EventType.QUEUE_ENTERED, {"message": ""}))
    assert state.phase == MatchPhase.QUEUED

    state.apply_event(LogEvent(EventType.MATCH_START, {"match_id": "abc"}))
    assert state.phase == MatchPhase.IN_MATCH
    assert state.match_id == "abc"

    state.apply_event(LogEvent(EventType.MATCH_END, {"match_id": "abc"}))
    assert state.phase == MatchPhase.IDLE
    assert state.match_id is None
