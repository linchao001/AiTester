import pytest
from langchain_core.messages import AIMessage

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.config import Settings
from aitester.services import ChatService
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _model_config(tmp_path, **settings_kwargs: object) -> ModelConfigService:
    return ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )


def _runtime(tmp_path, **settings_kwargs: object) -> AgentRuntime:
    model_config = _model_config(tmp_path, **settings_kwargs)
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return AgentRuntime(capability, model_config)


def test_echo_full_chain_trace_and_reply() -> None:
    svc = ChatService()
    result = svc.echo("s1", "生成登录用例")
    assert result["reply"] == "[mock] 生成登录用例"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_echo_remembers_previous_turn() -> None:
    svc = ChatService()
    svc.echo("s1", "第一句")
    svc.echo("s1", "第二句")
    history = svc.memory.recall("s1")
    assert [m["content"] for m in history] == ["第一句", "[mock] 第一句", "第二句", "[mock] 第二句"]
    assert svc.repo.get("session:s1") == {"session_id": "s1", "last_reply": "[mock] 第二句"}


def test_send_uses_injected_provider_and_reports_model(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path))
    result = svc.send("s1", "生成用例", "case_design")
    assert result["reply"] == "[mock] 生成用例"
    assert result["model"] == "mock/mock"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_send_scopes_history_by_agent_and_session(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path))
    svc.send("s1", "生成用例", "case_design")
    assert svc.memory.recall("case_design:s1")
    assert svc.memory.recall("s1") == []  # 与 echo 的裸键互不串
    assert svc.repo.get("session:case_design:s1") == {
        "session_id": "case_design:s1",
        "last_reply": "[mock] 生成用例",
    }
    svc.echo("s1", "echo 一句")
    assert [m["content"] for m in svc.memory.recall("s1")] == ["echo 一句", "[mock] echo 一句"]


def test_send_requires_agent_runtime() -> None:
    svc = ChatService(provider=MockProvider())
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design")
    assert "服务未装配智能体运行时，请通过 create_app 启动后端" in exc_info.value.detail


def test_send_uses_agent_system_prompt_from_md(tmp_path) -> None:
    seen: list[list[object]] = []

    class _SpyProvider:
        """独立假 provider，bind_tools 返回自身——MockProvider.bind_tools 会返回新的
        MockProvider()，用它做子类会把 spy 丢掉，断言永远抓不到消息。"""

        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            return "[spy]"

        def bind_tools(self, tools: list) -> "_SpyProvider":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    svc = ChatService(provider=_SpyProvider(), agent_runtime=_runtime(tmp_path))
    result = svc.send("s1", "生成用例", "case_design")
    assert result["reply"] == "[spy] 收到"
    assert type(seen[0][0]).__name__ == "SystemMessage"
    assert str(seen[0][0].content) == find_agent("case_design").prompt


def test_send_resolves_default_from_model_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def bind_tools(self, tools: list) -> "FakeChatOpenAI":
            return self

        def invoke(self, messages: list[dict[str, str]]) -> object:
            return type("R", (), {"content": "真实回复"})()

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = ChatService(
        agent_runtime=_runtime(tmp_path, deepseek_api_key="sk-x123456789")
    )
    result = svc.send("s1", "hi", "case_design")
    assert result["reply"] == "真实回复"
    assert result["model"] == "deepseek/deepseek-flash"
    assert recorded["model"] == "deepseek-flash"


def test_send_without_usable_default_raises_actionable_config_error(tmp_path) -> None:
    svc = ChatService(agent_runtime=_runtime(tmp_path))
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design")
    assert "设置 · 模型设置" in exc_info.value.detail


def test_send_passes_drafts_through():
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage, ToolMessage

    draft = {"op": "create", "path": "_inbox/n.md", "abs_display": "P", "summary": "s",
             "content": "c", "base": None, "mtime": 0}

    class _FakeGraph:
        def invoke(self, state):
            return {"messages": [
                AIMessage(content="", tool_calls=[{"name": "prepare_kb_write", "args": {}, "id": "c1", "type": "tool_call"}]),
                ToolMessage(content="Draft ready", tool_call_id="c1", name="prepare_kb_write", artifact=draft),
                AIMessage(content="草案已生成"),
            ]}

    from aitester.services.agent_runtime import AgentInstance
    runtime = SimpleNamespace(build=lambda aid, sid, provider_override=None: AgentInstance(
        agent_id=aid, system_prompt="p", provider=MockProvider(), tools=[],
        build_graph=lambda provider, tools: _FakeGraph()))
    service = ChatService(agent_runtime=runtime)
    out = service.send("kb-console", "记一笔", "case_design")
    assert out["drafts"] == [draft]
    assert out["reply"] == "草案已生成"


from types import SimpleNamespace

from aitester.memory import FileMemoryStore, InMemoryMemoryStore
from aitester.orchestration.agent_graph import build_agent_graph
from aitester.services.agent_runtime import AgentInstance
from aitester.services.session_store import SessionStore, SessionStoreError


def _sentinel_runtime():
    """provider 解析与记忆选型互不相关：假 runtime 只把注入的 provider 原样回出来。

    build_graph 用真实单节点拓扑（tools=[] 退化为直答）：裁剪断言要看进 prompt 的
    langchain 消息对象，echo 路径（provider.complete，收 dict）抓不到。
    """
    return SimpleNamespace(build=lambda agent_id, session_id, provider_override=None:
                           AgentInstance(agent_id=agent_id, system_prompt="p",
                                         provider=provider_override or MockProvider(),
                                         tools=[], build_graph=build_agent_graph))


class _RecordingService(ChatService):
    """抓 _complete 收到的 memory 实例：装配裁定（文件/进程内）只能在此处验。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seen_memory: list[object] = []

    def _complete(self, key, message, provider, system_prompt, build=None,
                  tools=None, memory=None):
        self.seen_memory.append(memory if memory is not None else self.memory)
        return super()._complete(key, message, provider, system_prompt,
                                 build=build, tools=tools, memory=memory)


def test_send_with_empty_session_id_generates_sess_id(tmp_path) -> None:
    svc = _RecordingService(provider=MockProvider(), sessions=SessionStore(tmp_path / "sessions"))
    svc.agent_runtime = _sentinel_runtime()
    result = svc.send("", "生成登录用例", "case_design")
    assert result["session_id"].startswith("sess_")
    assert result["title"] == "生成登录用例"
    assert isinstance(svc.seen_memory[0], FileMemoryStore)  # 新会话走文件记忆


def test_failed_send_leaves_no_session_on_disk(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    svc = ChatService(provider=MockProvider(), sessions=store)

    def _boom(agent_id, session_id, provider_override=None):
        raise ProviderConfigError("未配置模型")

    svc.agent_runtime = SimpleNamespace(build=_boom)
    with pytest.raises(ProviderConfigError):
        svc.send("", "hi", "case_design")
    assert store.list("case_design") == []  # 400 一次不得留 0 消息幽灵会话


def test_send_with_temporary_key_stays_in_memory(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    svc = _RecordingService(provider=MockProvider(), sessions=store)
    svc.agent_runtime = _sentinel_runtime()
    result = svc.send("kb-console", "hi", "case_design")  # 非 sess_ 形态：临时键
    assert result["session_id"] == "kb-console"
    assert result["title"] == ""                          # 临时键不在索引里
    assert isinstance(svc.seen_memory[0], InMemoryMemoryStore)
    assert store.list("case_design") == []


def test_send_with_unknown_sess_id_raises(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), sessions=SessionStore(tmp_path / "sessions"))
    svc.agent_runtime = _sentinel_runtime()
    with pytest.raises(SessionStoreError) as exc_info:
        svc.send("sess_deadbeef", "hi", "case_design")
    assert exc_info.value.detail == "会话不存在或已被删除"


def test_platform_agent_never_uses_file_memory(tmp_path) -> None:
    svc = _RecordingService(provider=MockProvider(), sessions=SessionStore(tmp_path / "sessions"))
    svc.agent_runtime = _sentinel_runtime()
    svc.send("kb-console", "hi", "kb_assistant")
    assert isinstance(svc.seen_memory[0], InMemoryMemoryStore)


def test_history_is_trimmed_to_last_history_max(tmp_path) -> None:
    seen: list[list] = []

    class _SpyProvider:
        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages):
            return "[spy]"

        def bind_tools(self, tools):
            return self

        def invoke_messages(self, messages):
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    # 过渡：project_id 先传空串，Task 6 收紧本文件时注入真实项目
    store.create(sid, "case_design", "", "标题")
    for i in range(45):
        store.append(sid, "user", f"问{i}")
        store.append(sid, "assistant", f"答{i}")
    svc = ChatService(provider=_SpyProvider(), sessions=store)
    svc.agent_runtime = _sentinel_runtime()
    svc.send(sid, "新问题", "case_design")
    contents = [m.content for m in seen[0]]
    # 90 条历史只取最近 40 条进 prompt：[system] + 40 + [本轮 user]
    assert len(contents) == 42
    assert contents[0] == "p"          # AgentInstance.system_prompt
    assert contents[1] == "问25"        # 90 条里的第 51 条，更早的 50 条进不了 prompt
    assert contents[-2] == "答44"
    assert contents[-1] == "新问题"
    assert len(store.messages(sid)) == 92  # 磁盘保留全量（本轮 user+assistant 已追加）


def test_send_stores_failed_tool_step(tmp_path) -> None:
    """ok=False 必须原样落到持久化的 steps：缺省兜底会把失败工具伪装成成功。"""
    from langchain_core.messages import ToolMessage

    expected = {"tool": "read", "ok": False, "round": 1,
                "detail": '{"file_path": "missing.txt"}'}

    class _FakeGraph:
        def invoke(self, state):
            return {"messages": [
                AIMessage(content="", tool_calls=[{"name": "read", "args": {
                    "file_path": "missing.txt"}, "id": "c1", "type": "tool_call"}]),
                ToolMessage(content="工具执行失败", tool_call_id="c1", name="read",
                            status="error"),
                AIMessage(content="读取失败"),
            ]}

    store = SessionStore(tmp_path / "sessions")
    svc = ChatService(provider=MockProvider(), sessions=store)
    svc.agent_runtime = SimpleNamespace(
        build=lambda agent_id, session_id, provider_override=None: AgentInstance(
            agent_id=agent_id, system_prompt="p", provider=MockProvider(), tools=[],
            build_graph=lambda provider, tools: _FakeGraph()))
    result = svc.send("", "读文件", "case_design")
    assert result["steps"] == [expected]
    stored = store.messages(result["session_id"])
    assert stored[1].steps == [expected]  # 落盘的不只是内存返回值
