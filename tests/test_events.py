from tu_shell_agent.opencode_adapter.events import (
    SseParser,
    delta_text,
    event_text,
    is_permission_ask,
)


def test_parser_emits_single_line_event_immediately():
    parser = SseParser()
    assert parser.push('data: {"type": "server.connected"}\n') == [{"type": "server.connected"}]


def test_parser_merges_multi_line_data_frames():
    parser = SseParser()
    assert parser.push('data: {"type": "message.part.updated",\n') == []
    events = parser.push('data: "properties": {"x": 1}}\n')
    assert events == [{"type": "message.part.updated", "properties": {"x": 1}}]


def test_parser_ignores_comments_and_blank_lines():
    parser = SseParser()
    assert parser.push(": keep-alive\n\n") == []


def test_event_text_extracts_whole_part_text():
    event = {"type": "message.part.updated", "properties": {"part": {"type": "text", "text": "你好"}}}
    assert event_text(event) == "你好"


def test_event_text_returns_none_for_non_text_parts():
    event = {"type": "message.part.updated", "properties": {"part": {"type": "tool", "tool": "read"}}}
    assert event_text(event) is None


def test_delta_text_extracts_incremental_text():
    event = {
        "type": "message.part.delta",
        "properties": {
            "sessionID": "ses_1", "messageID": "msg_1", "partID": "prt_1",
            "field": "text", "delta": "你好",
        },
    }
    assert delta_text(event) == "你好"


def test_delta_text_ignores_non_text_fields():
    event = {"type": "message.part.delta", "properties": {"field": "reasoning", "delta": "x"}}
    assert delta_text(event) is None


def test_is_permission_ask_recognizes_real_event_name():
    event = {"type": "permission.asked", "properties": {"id": "per_1", "sessionID": "ses_1"}}
    assert is_permission_ask(event) == ("ses_1", "per_1")


def test_is_permission_ask_also_accepts_legacy_event_name():
    event = {"type": "permission.updated", "properties": {"id": "per_2", "sessionID": "ses_2"}}
    assert is_permission_ask(event) == ("ses_2", "per_2")


def test_is_permission_ask_ignores_other_events():
    assert is_permission_ask({"type": "session.idle", "properties": {}}) is None
