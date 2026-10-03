import pytest

from aitester.memory import FileMemoryStore, InMemoryMemoryStore
from aitester.services.session_store import SessionStore

PROJECT = "proj_11111111"


def _new_sid(tmp_path) -> str:
    return SessionStore(tmp_path / "sessions").new_id()


def test_first_user_save_creates_session_with_title(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, PROJECT)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "生成登录用例")
    got = store.get(sid)
    assert got is not None and got.title == "生成登录用例" and got.agent_id == "case_design"
    assert [m.content for m in store.messages(sid)] == ["生成登录用例"]


def test_second_save_appends_and_steps_persist(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, PROJECT)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "问")
    mem.save(f"case_design:{sid}", "assistant", "答", steps=[{"tool": "read", "ok": True}])
    msgs = store.messages(sid)
    assert [(m.role, m.content) for m in msgs] == [("user", "问"), ("assistant", "答")]
    assert msgs[1].steps == [{"tool": "read", "ok": True}]


def test_recall_returns_role_content_in_order(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, PROJECT)
    sid = _new_sid(tmp_path)
    for role, content in [("user", "一"), ("assistant", "二"), ("user", "三")]:
        mem.save(f"case_design:{sid}", role, content)
    assert mem.recall(f"case_design:{sid}") == [
        {"role": "user", "content": "一"},
        {"role": "assistant", "content": "二"},
        {"role": "user", "content": "三"},
    ]


def test_unregistered_or_temporary_key_recalls_empty(tmp_path) -> None:
    mem = FileMemoryStore(SessionStore(tmp_path / "sessions"), PROJECT)
    assert mem.recall("case_design:sess_00000000") == []
    assert mem.recall("kb_assistant:kb-console") == []


def test_key_split_uses_first_colon_only(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, PROJECT)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "含:冒号的内容")
    assert store.messages(sid)[0].content == "含:冒号的内容"


def test_in_memory_store_accepts_and_ignores_steps() -> None:
    mem = InMemoryMemoryStore()
    mem.save("s1", "assistant", "答", steps=[{"tool": "read"}])
    assert mem.recall("s1") == [{"role": "assistant", "content": "答"}]


def test_lazy_created_session_carries_project(tmp_path) -> None:
    # 延迟建会话的裁定不变，但建出来的行必须带上归属——否则第一次刷新列表就把它丢了
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, PROJECT)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "生成登录用例")
    assert store.get(sid).project_id == PROJECT
    assert [s.id for s in store.list("case_design", PROJECT)] == [sid]


def test_project_id_is_required_at_construction(tmp_path) -> None:
    # 必填本身就是契约：单参构造必须 TypeError。给了默认值就会静默把会话落成空归属，
    # 而空归属的行在双条件列表里永远认不出来——延迟 create 的那条尤其没人报错
    store = SessionStore(tmp_path / "sessions")
    with pytest.raises(TypeError):
        FileMemoryStore(store)


def test_stopped_flag_persists_on_assistant_row(tmp_path) -> None:
    """标注进得了磁盘：memory.save 漏传 stopped 就是静默丢标注（四层链任一环都锁一条）。"""
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, "proj_11111111")
    mem.save("case_design:sess_abcdef01", "user", "生成用例")
    mem.save("case_design:sess_abcdef01", "assistant", "半截回答",
             steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}],
             stopped=True)
    rows = store.messages("sess_abcdef01")
    assert rows[-1].stopped is True
    assert rows[-1].steps == [{"tool": "read", "ok": True, "round": 1, "detail": "{}"}]


def test_recall_returns_truncated_text_verbatim(tmp_path) -> None:
    """被停止的那条进下一轮 prompt 时是截断原文——不加「（已停止）」，标注只进 UI。"""
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, "proj_11111111")
    mem.save("case_design:sess_11112222", "assistant", "已生成的前缀", stopped=True)
    assert mem.recall("case_design:sess_11112222") == [
        {"role": "assistant", "content": "已生成的前缀"}]
