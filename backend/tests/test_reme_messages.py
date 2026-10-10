from aitester.memory.reme.messages import to_reme_messages, to_reme_session_id


def test_to_reme_session_id_stable():
    a = to_reme_session_id("sess_abc", agent_id="agent_x")
    assert a == to_reme_session_id("sess_abc", agent_id="agent_x")
    assert a.startswith("aitsid_sha256_")
    assert a != to_reme_session_id("sess_other", agent_id="agent_x")
    assert len(a) == len("aitsid_sha256_") + 64


def test_to_reme_session_id_scoped_by_agent():
    same_sess = "sess_abc"
    a = to_reme_session_id(same_sess, agent_id="agent_a")
    b = to_reme_session_id(same_sess, agent_id="agent_b")
    assert a != b
    # 无 agent 与有 agent 不得撞车（材料始终带冒号前缀）
    assert to_reme_session_id(same_sess) != a


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
