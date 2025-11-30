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
    assert not state.hand_kept

    # Duplicate match start should not reset keep flag once set.
    state.hand_kept = True
    state.apply_event(LogEvent(EventType.MATCH_START, {"match_id": "abc"}))
    assert state.hand_kept

    state.apply_event(LogEvent(EventType.MATCH_END, {"match_id": "abc"}))
    assert state.phase == MatchPhase.IDLE
    assert state.match_id is None


def test_hand_kept_resets_on_turn_start_without_match_start():
    state = GameState()
    state.hand_kept = True  # stale flag from previous match

    # If the client emits turn events before a match start event, the flag should reset.
    state.apply_event(LogEvent(EventType.TURN_START, {"turn": 1}))
    assert state.phase == MatchPhase.IN_MATCH
    assert not state.hand_kept


def test_hand_kept_persists_during_match_turns():
    state = GameState(phase=MatchPhase.IN_MATCH, hand_kept=True, turn=1)
    state.apply_event(LogEvent(EventType.TURN_START, {"turn": 2}))

    assert state.phase == MatchPhase.IN_MATCH
    assert state.hand_kept
