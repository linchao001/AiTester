"""AutoMemoryTurnTracker：间隔累计 / 关闭 / take_all。"""

from aitester.memory.reme.auto_memory_turns import AutoMemoryTurnTracker


def _msgs(n: int) -> list[dict]:
    return [
        {"role": "user", "content": f"u{n}"},
        {"role": "assistant", "content": f"a{n}"},
    ]


def test_interval_flushes_oldest_n():
    t = AutoMemoryTurnTracker()
    for i in range(1, 5):
        assert t.note_turn(
            session_id="sess_1",
            project_id="p",
            agent_id="a",
            turn_marker=f"m{i}",
            messages=_msgs(i),
            interval=5,
        ) is None
    batch = t.note_turn(
        session_id="sess_1",
        project_id="p",
        agent_id="a",
        turn_marker="m5",
        messages=_msgs(5),
        interval=5,
    )
    assert batch is not None
    assert batch.session_id == "sess_1"
    assert [m["content"] for m in batch.messages] == [
        "u1", "a1", "u2", "a2", "u3", "a3", "u4", "a4", "u5", "a5",
    ]
    assert t.pending_count("sess_1") == 0


def test_interval_one_flushes_every_turn():
    t = AutoMemoryTurnTracker()
    batch = t.note_turn(
        session_id="s",
        project_id="p",
        agent_id="a",
        turn_marker="m1",
        messages=_msgs(1),
        interval=1,
    )
    assert batch is not None
    assert len(batch.messages) == 2


def test_interval_zero_clears_and_never_flushes():
    t = AutoMemoryTurnTracker()
    assert t.note_turn(
        session_id="s",
        project_id="p",
        agent_id="a",
        turn_marker="m1",
        messages=_msgs(1),
        interval=0,
    ) is None
    assert t.pending_count("s") == 0


def test_duplicate_marker_ignored():
    t = AutoMemoryTurnTracker()
    t.note_turn(
        session_id="s", project_id="p", agent_id="a",
        turn_marker="same", messages=_msgs(1), interval=2,
    )
    assert t.note_turn(
        session_id="s", project_id="p", agent_id="a",
        turn_marker="same", messages=_msgs(1), interval=2,
    ) is None
    assert t.pending_count("s") == 1


def test_take_all_returns_pending_and_clears():
    t = AutoMemoryTurnTracker()
    t.note_turn(
        session_id="s", project_id="p", agent_id="a",
        turn_marker="m1", messages=_msgs(1), interval=5,
    )
    t.note_turn(
        session_id="s", project_id="p", agent_id="a",
        turn_marker="m2", messages=_msgs(2), interval=5,
    )
    batch = t.take_all("s")
    assert batch is not None
    assert [m["content"] for m in batch.messages] == ["u1", "a1", "u2", "a2"]
    assert t.pending_count("s") == 0
    assert t.take_all("s") is None
