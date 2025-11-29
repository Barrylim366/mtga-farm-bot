from mtga_bot.log_parser import EventType, LogParser


def test_parse_quest_update():
    parser = LogParser("Player.log")
    event = parser.parse_line("Quest q123 updated 3/10 - Cast 10 red spells")

    assert event is not None
    assert event.event_type == EventType.QUEST_UPDATE
    assert event.payload["quest_id"] == "q123"
    assert event.payload["progress"] == 3
    assert event.payload["goal"] == 10
    assert event.payload["kind"] == "cast_spells"


def test_parse_match_transitions():
    parser = LogParser("Player.log")
    start = parser.parse_line("Match abc started")
    end = parser.parse_line("Match abc ended")
    queue = parser.parse_line("Entering queue for Play")

    assert start and start.event_type == EventType.MATCH_START
    assert end and end.event_type == EventType.MATCH_END
    assert queue and queue.event_type == EventType.QUEUE_ENTERED
