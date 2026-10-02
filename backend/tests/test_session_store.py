import json

import pytest

from aitester.services.session_store import (
    SessionStore,
    SessionStoreError,
    is_session_id,
)


def _store(tmp_path):
    return SessionStore(tmp_path / "sessions")


def test_new_id_matches_form_and_is_unique(tmp_path) -> None:
    store = _store(tmp_path)
    ids = {store.new_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(is_session_id(i) for i in ids)


def test_is_session_id_rejects_other_forms() -> None:
    assert is_session_id("sess_0a1b2c3d") is True
    for bad in ("sess_0A1B2C3D", "sess_0a1b2c", "kb-console", "default", "", "proj_7e29a494"):
        assert is_session_id(bad) is False, bad


def test_create_derives_title_from_first_message(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    sess = store.create(sid, "case_design", "  第一行很长的问题\n第二行不要进标题  ")
    assert sess.id == sid and sess.agent_id == "case_design"
    # 换行转空格后取前 16 字（spec:2026-10-03-chat-session-design.md:46 与 TITLE_MAX=16 一致）
    assert sess.title == "第一行很长的问题 第二行不要进标"
    assert len(sess.title) == 16
    assert sess.message_count == 0


def test_title_falls_back_when_first_message_blank(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.create(store.new_id(), "case_design", "  \n  ").title == "新会话"


def test_create_is_idempotent_for_existing_id(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    first = store.create(sid, "case_design", "原始问题")
    again = store.create(sid, "case_design", "另一个问题")
    assert again.title == first.title  # 已存在只返回既有，绝不覆盖标题
    assert [s.id for s in store.list("case_design")] == [sid]


def test_append_bumps_count_updates_and_appends_jsonl(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    store.append(sid, "user", "问题")
    store.append(sid, "assistant", "答", steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}])
    msgs = store.messages(sid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].steps == [{"tool": "read", "ok": True, "round": 1, "detail": "{}"}]
    assert msgs[0].steps is None
    got = store.get(sid)
    assert got.message_count == 2 and got.updated_at >= msgs[-1].ts
    lines = (tmp_path / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["content"] == "答"


def test_append_unknown_session_raises_actionable(tmp_path) -> None:
    store = _store(tmp_path)
    with pytest.raises(SessionStoreError) as exc_info:
        store.append("sess_deadbeef", "user", "hi")
    assert exc_info.value.detail == "会话不存在或已被删除"


def test_list_sorts_by_updated_at_desc_and_scopes_by_agent(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create(store.new_id(), "case_design", "A")
    b = store.create(store.new_id(), "case_design", "B")
    store.append(b.id, "user", "B 又说话了")
    assert [s.id for s in store.list("case_design")] == [b.id, a.id]
    assert store.list("ghost") == []


def test_delete_removes_index_entry_and_file(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    store.append(sid, "user", "问题")
    path = tmp_path / "sessions" / f"{sid}.jsonl"
    assert path.exists()
    assert store.delete(sid) is True
    assert store.get(sid) is None and store.messages(sid) == []
    assert not path.exists()
    assert store.delete(sid) is False  # 二次删除明确返回 False，路由据此 404


def test_index_shape_is_versioned(tmp_path) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["version"] == 1
    assert index["sessions"][0]["id"] == sid
    assert set(index["sessions"][0]) == {
        "id", "agent_id", "title", "created_at", "updated_at", "message_count"
    }


def test_index_dirty_entries_dropped_or_healed_on_load(tmp_path) -> None:
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    good = {
        "id": "sess_0a1b2c3d", "agent_id": "case_design", "title": "原始",
        "created_at": 1, "updated_at": 2, "message_count": 1,
    }
    dirty = [
        good,
        {**good, "id": "legacy_001"},
        {**good, "title": "重复"},
        {"id": "sess_11112222", "title": "无智能体"},
        {"id": "sess_33334444", "agent_id": "ghost", "message_count": -5},
    ]
    (root / "index.json").write_text(
        json.dumps({"version": 1, "sessions": dirty}, ensure_ascii=False), encoding="utf-8"
    )

    store = SessionStore(root)
    rows = store.list("case_design")
    assert [(s.id, s.title) for s in rows] == [("sess_0a1b2c3d", "原始")]
    assert store.get("legacy_001") is None
    assert store.get("sess_11112222") is None
    healed = store.list("ghost")
    assert [(s.id, s.message_count) for s in healed] == [("sess_33334444", 0)]

    created = store.create(store.new_id(), "case_design", "新问题")
    saved = json.loads((root / "index.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in saved["sessions"]]
    assert sorted(ids) == sorted(["sess_0a1b2c3d", "sess_33334444", created.id])
    keys = {"id", "agent_id", "title", "created_at", "updated_at", "message_count"}
    assert all(set(item) == keys for item in saved["sessions"])
    ghost = next(item for item in saved["sessions"] if item["id"] == "sess_33334444")
    assert ghost["message_count"] == 0


def test_corrupt_index_self_heals_to_empty(tmp_path) -> None:
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    (root / "index.json").write_text('{"version":1,"sess', encoding="utf-8")

    store = SessionStore(root)
    assert store.list("case_design") == []

    created = store.create(store.new_id(), "case_design", "问题")
    store.append(created.id, "user", "问题")
    reopened = SessionStore(root)
    got = reopened.get(created.id)
    assert got is not None and got.message_count == 1


def test_store_survives_reinstantiation(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    store.append(sid, "user", "问题")
    store.append(sid, "assistant", "答")
    reopened = _store(tmp_path)
    got = reopened.get(sid)
    assert got is not None and got.title == "问题" and got.message_count == 2
    assert [m.content for m in reopened.messages(sid)] == ["问题", "答"]


def test_bad_field_type_drops_only_that_row(tmp_path) -> None:
    """字段类型坏但 JSON 合法的一行只丢该行：坏一行不能拖垮整份 index，更不能让 create_app 在导入期崩。"""
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    good = {
        "id": "sess_0a1b2c3d", "agent_id": "case_design", "title": "好的",
        "created_at": 1, "updated_at": 2, "message_count": 1,
    }
    bad_created = {**good, "id": "sess_11112222", "created_at": "昨天"}
    bad_count = {**good, "id": "sess_33334444", "message_count": ["x"]}
    (root / "index.json").write_text(
        json.dumps({"version": 1, "sessions": [good, bad_created, bad_count]}, ensure_ascii=False),
        encoding="utf-8",
    )

    store = SessionStore(root)  # 构造不抛
    assert [s.id for s in store.list("case_design")] == ["sess_0a1b2c3d"]  # 合法行全在
    assert store.get("sess_11112222") is None  # created_at 坏 → 丢
    assert store.get("sess_33334444") is None  # message_count 坏 → 丢


def test_corrupt_index_archives_and_keeps_jsonl(tmp_path) -> None:
    """坏索引自愈前先留档 index.json.bad-*，正文 .jsonl 原地不动（留档是本期唯一救济手段）。"""
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    (root / "index.json").write_text("{ not json", encoding="utf-8")
    body = root / "sess_0a1b2c3d.jsonl"
    body.write_text('{"role":"user","content":"原始正文","ts":1}\n', encoding="utf-8")
    before = body.read_text(encoding="utf-8")

    store = SessionStore(root)
    assert store.list("case_design") == []
    archived = list(root.glob("index.json.bad-*"))
    assert len(archived) == 1  # 坏文件被改名留档
    assert not (root / "index.json").exists()  # 改名即移走原文件
    assert body.read_text(encoding="utf-8") == before  # 正文文件不受影响
