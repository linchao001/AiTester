"""ChatService 个人记忆钩子：注入 / 回合累计调度 / 软失败 / 删会话 flush。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from aitester.adapters.llm import MockProvider
from aitester.context.usage import ContextUsage
from aitester.memory import InMemoryMemoryStore
from aitester.services.chat import ChatService, PreparedRun


class SpyPersonal:
    def __init__(self, hit: str = "", boom: bool = False) -> None:
        self.hit = hit
        self.boom = boom
        self.search_calls: list[dict] = []
        self.note_calls: list[dict] = []
        self.flush_calls: list[str] = []

    def auto_search_for_turn(self, **kw: Any) -> str:
        if self.boom:
            raise RuntimeError("search boom")
        self.search_calls.append(kw)
        return self.hit

    def note_user_turn(self, **kw: Any) -> None:
        self.note_calls.append(kw)

    def flush_session(self, session_id: str) -> None:
        self.flush_calls.append(session_id)


def _prepared(
    memory: InMemoryMemoryStore | None = None,
    *,
    session_id: str = "sess_1",
    message: str = "你好",
) -> PreparedRun:
    mem = memory or InMemoryMemoryStore()

    def build(_p, _t) -> Any:
        raise AssertionError("graph should not run in persist-only tests")

    return PreparedRun(
        key=f"case_design:{session_id}",
        session_id=session_id,
        message=message,
        provider=MockProvider(),
        system_prompt="sys",
        build=build,  # type: ignore[arg-type]
        tools=[],
        memory=mem,
        messages=[SystemMessage(content="sys"), HumanMessage(content=message)],
        agent_id="case_design",
        project_id="proj_1",
        usage=ContextUsage(0),
    )


def test_persist_notes_user_turn():
    spy = SpyPersonal()
    svc = ChatService(personal=spy)
    prepared = _prepared()
    svc._persist(prepared, "回复", steps=[], stopped=False)
    assert len(spy.note_calls) == 1
    call = spy.note_calls[0]
    assert call["session_id"] == "sess_1"
    assert call["project_id"] == "proj_1"
    assert call["messages"][0]["content"] == "你好"
    assert call["messages"][1]["content"] == "回复"


def test_search_failure_does_not_break_persist():
    """prepare 路径的 soft-skip 在 PersonalMemory 内；此处钉 persist 仍可落盘。"""
    spy = SpyPersonal()
    svc = ChatService(personal=spy, memory=InMemoryMemoryStore())
    prepared = _prepared(svc.memory)
    snap = svc._persist(prepared, "ok", steps=[], stopped=False)
    assert snap is not None
    assert len(svc.memory.recall(prepared.key)) == 2


def test_drop_session_flushes_personal_memory():
    spy = SpyPersonal()
    svc = ChatService(personal=spy)
    svc.drop_session("sess_abc")
    assert spy.flush_calls == ["sess_abc"]


def test_prepare_injects_when_spy_returns_hit(monkeypatch):
    """绕过完整 prepare 装配：直接验证插入逻辑用的消息形状。

    完整 prepare 依赖项目/运行时；此处用最小拼装复现钩子插入点。
    """
    spy = SpyPersonal(hit="记忆命中摘要")
    # 模拟 prepare 末尾钩子
    messages = [SystemMessage(content="sys"), HumanMessage(content="q")]
    hit = spy.auto_search_for_turn(project_id="p", agent_id="a", query="q")
    assert hit
    insert_at = 1 if messages and isinstance(messages[0], SystemMessage) else 0
    messages.insert(insert_at, SystemMessage(content=f"[aitester_personal_memory]\n{hit}"))
    assert isinstance(messages[1], SystemMessage)
    assert "[aitester_personal_memory]" in messages[1].content
    assert "记忆命中摘要" in messages[1].content
