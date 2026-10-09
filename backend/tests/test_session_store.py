import json
from datetime import datetime

import pytest

from aitester.services.session_store import (
    ChatMessage,
    SessionStore,
    SessionStoreError,
    is_session_id,
)

PROJECT = "proj_11111111"


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
    sess = store.create(sid, "case_design", PROJECT, "  第一行很长的问题\n第二行不要进标题  ")
    assert sess.id == sid and sess.agent_id == "case_design"
    # 换行转空格后取前 16 字（spec:2026-10-03-chat-session-design.md:46 与 TITLE_MAX=16 一致）
    assert sess.title == "第一行很长的问题 第二行不要进标"
    assert len(sess.title) == 16
    assert sess.message_count == 0


def test_title_falls_back_when_first_message_blank(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.create(store.new_id(), "case_design", PROJECT, "  \n  ").title == "新会话"


def test_create_is_idempotent_for_existing_id(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    first = store.create(sid, "case_design", PROJECT, "原始问题")
    again = store.create(sid, "case_design", PROJECT, "另一个问题")
    assert again.title == first.title  # 已存在只返回既有，绝不覆盖标题
    assert [s.id for s in store.list("case_design", PROJECT)] == [sid]


def test_append_bumps_count_updates_and_appends_jsonl(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "问题")
    store.append(sid, "user", "问题")
    store.append(sid, "assistant", "答", steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}])
    msgs = store.messages(sid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].steps == [{"tool": "read", "ok": True, "round": 1, "detail": "{}"}]
    assert msgs[0].steps is None
    assert msgs[0].agent_id == "case_design" and msgs[0].session_id == sid
    assert msgs[1].agent_id == "case_design" and msgs[1].session_id == sid
    assert msgs[0].name == "user" and msgs[1].name == "assistant"
    assert msgs[0].id.startswith("msg_") and msgs[1].id.startswith("msg_")
    assert msgs[0].created_at and msgs[0].ts > 0
    got = store.get(sid)
    assert got.message_count == 2 and got.updated_at >= msgs[-1].ts
    lines = (tmp_path / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    row1 = json.loads(lines[1])
    assert row1["content"] == "答"
    assert row0["agent_id"] == "case_design"
    assert row0["session_id"] == sid
    # Reme 最低可用：磁盘有 name/id/created_at，不再写 ts
    for row in (row0, row1):
        assert "ts" not in row
        assert {"name", "role", "content", "created_at", "id"} <= set(row)
        assert row["name"] == row["role"]
        assert row["id"].startswith("msg_")
        assert "+" in row["created_at"] or row["created_at"].endswith("Z")


def test_open_session_store_puts_files_under_agent_subdir(tmp_path) -> None:
    from aitester.services.session_store import open_session_store

    proj = tmp_path / "myproj"
    proj.mkdir()
    store = open_session_store(proj, "case_design")
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "hi")
    store.append(sid, "user", "hi")
    store.append(
        sid, "assistant", "ok",
        steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}", "result": "FILE_BODY"}],
    )
    root = proj / "session_history" / "case_design"
    assert (root / "index.json").is_file()
    line = json.loads((root / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()[1])
    assert line["agent_id"] == "case_design"
    assert line["session_id"] == sid
    assert line["steps"][0]["result"] == "FILE_BODY"
    assert "ts" not in line
    assert line["name"] == "assistant" and line["id"].startswith("msg_")


def test_messages_skips_rows_missing_agent_or_session_id(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "x")
    path = tmp_path / "sessions" / f"{sid}.jsonl"
    legacy_ts = 1_700_000_000_000
    path.write_text(
        json.dumps({"role": "user", "content": "old", "ts": 1, "steps": None, "stopped": False, "context": None})
        + "\n"
        + json.dumps({
            "agent_id": "case_design", "session_id": sid,
            "role": "user", "content": "new", "ts": legacy_ts,
            "steps": None, "stopped": False, "context": None,
        })
        + "\n",
        encoding="utf-8",
    )
    msgs = store.messages(sid)
    assert [m.content for m in msgs] == ["new"]
    # 老行只有 ts：读侧合成 created_at / name，供 API ts 与 Reme 形态对齐（不回写磁盘）
    assert msgs[0].ts == legacy_ts
    assert msgs[0].created_at
    assert msgs[0].name == "user"


def test_from_dict_reme_minimum_and_legacy_ts() -> None:
    reme = ChatMessage.from_dict({
        "name": "user", "role": "user", "content": "你好",
        "created_at": "2026-03-10T10:00:00+08:00", "id": "msg-001",
    })
    assert reme.name == "user" and reme.id == "msg-001"
    assert reme.ts == int(datetime.fromisoformat("2026-03-10T10:00:00+08:00").timestamp() * 1000)
    assert "ts" not in reme.to_dict()

    legacy = ChatMessage.from_dict({"role": "assistant", "content": "旧", "ts": 1_700_000_000_000})
    assert legacy.name == "assistant"
    assert legacy.created_at
    assert legacy.ts == 1_700_000_000_000
    assert legacy.to_dict()["created_at"] == legacy.created_at


def test_append_unknown_session_raises_actionable(tmp_path) -> None:
    store = _store(tmp_path)
    with pytest.raises(SessionStoreError) as exc_info:
        store.append("sess_deadbeef", "user", "hi")
    assert exc_info.value.detail == "会话不存在或已被删除"


def test_list_sorts_by_updated_at_desc_and_scopes_by_agent(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create(store.new_id(), "case_design", PROJECT, "A")
    b = store.create(store.new_id(), "case_design", PROJECT, "B")
    store.append(b.id, "user", "B 又说话了")
    assert [s.id for s in store.list("case_design", PROJECT)] == [b.id, a.id]
    assert store.list("ghost", PROJECT) == []


def test_delete_removes_index_entry_and_file(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "问题")
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
    store.create(sid, "case_design", PROJECT, "问题")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["version"] == 1
    assert index["sessions"][0]["id"] == sid
    assert set(index["sessions"][0]) == {
        "id", "agent_id", "project_id", "title", "created_at", "updated_at", "message_count"
    }


def test_index_dirty_entries_dropped_or_healed_on_load(tmp_path) -> None:
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    good = {
        "id": "sess_0a1b2c3d", "agent_id": "case_design", "project_id": PROJECT, "title": "原始",
        "created_at": 1, "updated_at": 2, "message_count": 1,
    }
    dirty = [
        good,
        {**good, "id": "legacy_001"},
        {**good, "title": "重复"},
        {"id": "sess_11112222", "title": "无智能体"},
        {"id": "sess_33334444", "agent_id": "ghost", "project_id": PROJECT, "message_count": -5},
    ]
    (root / "index.json").write_text(
        json.dumps({"version": 1, "sessions": dirty}, ensure_ascii=False), encoding="utf-8"
    )

    store = SessionStore(root)
    rows = store.list("case_design", PROJECT)
    assert [(s.id, s.title) for s in rows] == [("sess_0a1b2c3d", "原始")]
    assert store.get("legacy_001") is None
    assert store.get("sess_11112222") is None
    healed = store.list("ghost", PROJECT)
    assert [(s.id, s.message_count) for s in healed] == [("sess_33334444", 0)]

    created = store.create(store.new_id(), "case_design", PROJECT, "新问题")
    saved = json.loads((root / "index.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in saved["sessions"]]
    assert sorted(ids) == sorted(["sess_0a1b2c3d", "sess_33334444", created.id])
    keys = {"id", "agent_id", "project_id", "title", "created_at", "updated_at", "message_count"}
    assert all(set(item) == keys for item in saved["sessions"])
    ghost = next(item for item in saved["sessions"] if item["id"] == "sess_33334444")
    assert ghost["message_count"] == 0


def test_corrupt_index_self_heals_to_empty(tmp_path) -> None:
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    (root / "index.json").write_text('{"version":1,"sess', encoding="utf-8")

    store = SessionStore(root)
    assert store.list("case_design", PROJECT) == []

    created = store.create(store.new_id(), "case_design", PROJECT, "问题")
    store.append(created.id, "user", "问题")
    reopened = SessionStore(root)
    got = reopened.get(created.id)
    assert got is not None and got.message_count == 1


def test_store_survives_reinstantiation(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "问题")
    store.append(sid, "user", "问题")
    store.append(sid, "assistant", "答")
    reopened = _store(tmp_path)
    got = reopened.get(sid)
    assert got is not None and got.title == "问题" and got.message_count == 2
    assert [m.content for m in reopened.messages(sid)] == ["问题", "答"]


def test_bad_field_type_drops_only_that_row(tmp_path, caplog) -> None:
    """字段类型坏但 JSON 合法的一行只丢该行：坏一行不能拖垮整份 index，更不能让 create_app 在导入期崩。"""
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    good = {
        "id": "sess_0a1b2c3d", "agent_id": "case_design", "project_id": PROJECT, "title": "好的",
        "created_at": 1, "updated_at": 2, "message_count": 1,
    }
    bad_created = {**good, "id": "sess_11112222", "created_at": "昨天"}
    bad_count = {**good, "id": "sess_33334444", "message_count": ["x"]}
    (root / "index.json").write_text(
        json.dumps({"version": 1, "sessions": [good, bad_created, bad_count]}, ensure_ascii=False),
        encoding="utf-8",
    )

    store = SessionStore(root)  # 构造不抛
    assert [s.id for s in store.list("case_design", PROJECT)] == ["sess_0a1b2c3d"]  # 合法行全在
    assert store.get("sess_11112222") is None  # created_at 坏 → 丢
    assert store.get("sess_33334444") is None  # message_count 坏 → 丢
    # 丢行必须留话：下一次写盘就把这两行从文件里永久抹掉，无日志就是无迹可查的静默删除
    assert sum("字段类型不可用" in r.getMessage() for r in caplog.records) == 2


def test_corrupt_index_archives_and_keeps_jsonl(tmp_path) -> None:
    """坏索引自愈前先留档 index.json.bad-*，正文 .jsonl 原地不动（留档是本期唯一救济手段）。"""
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    (root / "index.json").write_text("{ not json", encoding="utf-8")
    body = root / "sess_0a1b2c3d.jsonl"
    body.write_text('{"role":"user","content":"原始正文","ts":1}\n', encoding="utf-8")
    before = body.read_text(encoding="utf-8")

    store = SessionStore(root)
    assert store.list("case_design", PROJECT) == []
    archived = list(root.glob("index.json.bad-*"))
    assert len(archived) == 1  # 坏文件被改名留档
    assert not (root / "index.json").exists()  # 改名即移走原文件
    assert body.read_text(encoding="utf-8") == before  # 正文文件不受影响


@pytest.mark.parametrize(
    "broken",
    [
        [{"id": "sess_0a1b2c3d", "agent_id": "case_design"}],  # 顶层不是 dict
        {"version": 1, "sessions": "oops"},  # sessions 不是 list
    ],
    ids=["top-level-list", "sessions-not-list"],
)
def test_structurally_broken_index_also_archives(tmp_path, broken) -> None:
    """合法 JSON 但结构坏也留档：返回空索引后下一次写盘就整文件覆盖，毁数据机制与「不可解析」同类。"""
    root = tmp_path / "sessions"
    root.mkdir(parents=True)
    payload = json.dumps(broken, ensure_ascii=False)
    (root / "index.json").write_text(payload, encoding="utf-8")

    store = SessionStore(root)
    assert store.list("case_design", PROJECT) == []
    archived = list(root.glob("index.json.bad-*"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == payload  # 留档内容原样，还有救
    # 留档不是拖延：随后首次写盘建新索引，但坏文件仍在原地备查
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "新会话第一条")
    assert json.loads((root / "index.json").read_text(encoding="utf-8"))["sessions"][0]["id"] == sid
    assert len(list(root.glob("index.json.bad-*"))) == 1


def test_index_row_requires_project_id_or_row_is_dropped(tmp_path, caplog) -> None:
    # 归属是硬字段：第 2 片之前的旧行（无 project_id）按「丢一行且留话」处理，正文原地不动
    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "问题")
    raw = tmp_path / "sessions" / "index.json"
    data = json.loads(raw.read_text(encoding="utf-8"))
    data["sessions"][0].pop("project_id")
    raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    reloaded = SessionStore(tmp_path / "sessions")
    assert reloaded.list("case_design", PROJECT) == []
    assert "缺少项目归属" in caplog.text
    assert (tmp_path / "sessions" / f"{sid}.jsonl").exists()


def test_list_filters_by_both_agent_and_project(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mine = store.new_id()
    other_agent = store.new_id()
    other_project = store.new_id()
    store.create(mine, "case_design", PROJECT, "我的")
    store.create(other_agent, "kb_assistant", PROJECT, "别的智能体")
    store.create(other_project, "case_design", "proj_22222222", "别的项目")
    assert [s.id for s in store.list("case_design", PROJECT)] == [mine]
    assert store.get(mine).project_id == PROJECT


def test_list_project_id_is_required(tmp_path) -> None:
    # 必填本身就是契约（第 2 片裁定 1/2）：漏传项目必须炸 TypeError，绝不回落到默认值后
    # 静默匹配「空归属」的行——谁给 list 的 project_id 加回默认值，这条就把回归门关上
    store = SessionStore(tmp_path / "sessions")
    with pytest.raises(TypeError):
        store.list("case_design")


def test_count_by_project(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create(store.new_id(), "case_design", PROJECT, "一")
    store.create(store.new_id(), "case_design", PROJECT, "二")
    store.create(store.new_id(), "case_design", "proj_22222222", "三")
    assert store.count_by_project(PROJECT) == 2
    assert store.count_by_project("proj_99999999") == 0


def test_delete_by_project_removes_rows_and_files(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    a = store.new_id()
    b = store.new_id()
    keep = store.new_id()
    store.create(a, "case_design", PROJECT, "一")
    store.create(b, "case_design", PROJECT, "二")
    store.create(keep, "case_design", "proj_22222222", "留")
    assert store.delete_by_project(PROJECT) == 2
    assert not (tmp_path / "sessions" / f"{a}.jsonl").exists()
    assert not (tmp_path / "sessions" / f"{b}.jsonl").exists()
    assert store.get(keep) is not None
    assert store.delete_by_project(PROJECT) == 0  # 幂等：再来一次不炸


def test_stopped_roundtrip_and_old_row_default_false(tmp_path) -> None:
    """零迁移：第 4 片之前落盘的行没有 stopped 字段，读侧必须答 False 而不是炸。"""
    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    store.create(sid, "case_design", "proj_11111111", "退款用例")
    store.append(sid, "assistant", "半截回答",
                 steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}],
                 stopped=True)
    rows = store.messages(sid)
    assert rows[-1].stopped is True
    assert rows[-1].to_dict()["stopped"] is True

    path = tmp_path / "sessions" / f"{sid}.jsonl"
    # 有 agent/session 盖章、无 stopped：零迁移默认 False（缺盖章的行会被 messages 跳过）
    legacy = {
        "agent_id": "case_design", "session_id": sid,
        "role": "assistant", "content": "第 4 片前的行", "ts": 1,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(legacy, ensure_ascii=False) + "\n")
    assert store.messages(sid)[-1].stopped is False
    assert store.messages(sid)[-1].content == "第 4 片前的行"


def test_append_defaults_to_not_stopped(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    store.create(sid, "case_design", "proj_11111111", "订单")
    store.append(sid, "user", "生成用例")
    assert store.messages(sid)[-1].stopped is False
