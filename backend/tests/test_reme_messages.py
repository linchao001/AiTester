from aitester.memory.reme.messages import to_reme_messages, to_reme_session_id


def test_to_reme_session_id_stable():
    a = to_reme_session_id("sess_abc")
    assert a == to_reme_session_id("sess_abc")
    assert a.startswith("aitsid_sha256_")
    assert a != to_reme_session_id("sess_other")
    assert len(a) == len("aitsid_sha256_") + 64


def test_to_reme_messages_skips_empty_and_fills_defaults():
    rows = to_reme_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "  "},
        {"role": "assistant", "content": "ok", "id": "msg_1", "name": "bot"},
    ])
    assert len(rows) == 2
    assert rows[0]["role"] == "user" and rows[0]["name"] == "user"
    assert rows[0]["id"].startswith("msg_")
    assert rows[0]["created_at"]
    assert rows[1]["id"] == "msg_1" and rows[1]["name"] == "bot"
