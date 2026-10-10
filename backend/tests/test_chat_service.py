from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.config import Settings
from aitester.orchestration.auth_rules import DEFAULT_PERM_MODE
from aitester.services import ChatService
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ModelConfigService
from aitester.services.project_config import ProjectConfigError, ProjectService
from aitester.services.session_locator import SessionLocator
from aitester.services.session_store import SessionStoreError, open_session_store
from aitester.storage import FileJsonConfigRepository
from streaming_fakes import CHUNK_CHARS, ChunkedStreamMixin


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


@pytest.fixture
def project(tmp_path):
    """真实 ProjectService + 真实存在的目录：本片第一次消费 dir，测试不能给个字符串了事。"""
    root = tmp_path / "reqs"
    root.mkdir()
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    created = svc.create(name="订单系统", desc="", dir_=str(root), agents=["case_design"])
    return svc, created["id"], root


def _locator(svc_proj) -> SessionLocator:
    return SessionLocator(svc_proj)


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


def test_send_uses_injected_provider_and_reports_model(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                      projects=svc_proj)
    result = svc.send("s1", "生成用例", "case_design", pid)
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


def test_send_scopes_history_by_agent_and_session(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                      projects=svc_proj)
    svc.send("s1", "生成用例", "case_design", pid)
    assert svc.memory.recall("case_design:s1")
    assert svc.memory.recall("s1") == []  # 与 echo 的裸键互不串
    assert svc.repo.get("session:case_design:s1") == {
        "session_id": "case_design:s1",
        "last_reply": "[mock] 生成用例",
    }
    svc.echo("s1", "echo 一句")
    assert [m["content"] for m in svc.memory.recall("s1")] == ["echo 一句", "[mock] echo 一句"]


def test_send_requires_agent_runtime(project) -> None:
    # 运行时守卫在项目段之前：未装配 runtime 的报错形态与第 1 片一致，不传项目也要响亮
    _, pid, _ = project
    svc = ChatService(provider=MockProvider())
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design", pid)
    assert "服务未装配智能体运行时，请通过 create_app 启动后端" in exc_info.value.detail


def test_send_uses_agent_system_prompt_from_md(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    seen: list[list[object]] = []

    class _SpyProvider(ChunkedStreamMixin):
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

    svc = ChatService(provider=_SpyProvider(), agent_runtime=_runtime(tmp_path),
                      projects=svc_proj)
    result = svc.send("s1", "生成用例", "case_design", pid)
    assert result["reply"] == "[spy] 收到"
    assert type(seen[0][0]).__name__ == "SystemMessage"
    assert str(seen[0][0].content) == find_agent("case_design").prompt


def test_send_resolves_default_from_model_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch, project
) -> None:
    svc_proj, pid, _ = project
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def bind_tools(self, tools: list) -> "FakeChatOpenAI":
            return self

        def invoke(self, messages: list[dict[str, str]]) -> object:
            return type("R", (), {"content": "真实回复"})()

        def stream(self, messages: list[dict[str, str]]):
            # 节点体现在走 stream_messages → client.stream；与 invoke 同一份回复，切成 chunk
            text = "真实回复"
            for i in range(0, len(text), CHUNK_CHARS):
                yield AIMessageChunk(content=text[i:i + CHUNK_CHARS])

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = ChatService(
        agent_runtime=_runtime(tmp_path, deepseek_api_key="sk-x123456789"),
        projects=svc_proj,
    )
    result = svc.send("s1", "hi", "case_design", pid)
    assert result["reply"] == "真实回复"
    assert result["model"] == "deepseek/deepseek-flash"
    assert recorded["model"] == "deepseek-flash"


def test_send_without_usable_default_raises_actionable_config_error(
    tmp_path, project
) -> None:
    svc_proj, pid, _ = project
    svc = ChatService(agent_runtime=_runtime(tmp_path), projects=svc_proj)
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design", pid)
    assert "设置 · 模型设置" in exc_info.value.detail


def test_send_passes_drafts_through(tmp_path, project):
    from types import SimpleNamespace
    from langchain_core.messages import ToolMessage

    svc_proj, pid, _ = project
    draft = {"op": "create", "path": "_inbox/n.md", "abs_display": "P", "summary": "s",
             "content": "c", "base": None, "mtime": 0}

    class _FakeGraph:
        # run_graph 驱动翻到 graph.stream：假图改吐 custom turn + tools updates，
        # draft 仍从 prepare_kb_write 的 ToolMessage.artifact 走草案通道。下面断言一字未动。
        def stream(self, state, *, config=None, stream_mode=None):
            yield ("custom", {"type": "turn", "round": 1, "text": "", "stopped": False,
                              "tool_calls": [{"id": "c1", "name": "prepare_kb_write",
                                              "args": {}}]})
            yield ("updates", {"tools": {"messages": [
                ToolMessage(content="Draft ready", tool_call_id="c1",
                            name="prepare_kb_write", artifact=draft)]}})
            yield ("custom", {"type": "turn", "round": 2, "text": "草案已生成",
                              "stopped": False, "tool_calls": []})

    from aitester.services.agent_runtime import AgentInstance
    runtime = SimpleNamespace(build=lambda aid, sid, provider_override=None, cwd=".", **_: AgentInstance(
        agent_id=aid, system_prompt="p", provider=MockProvider(), tools=[],
        build_graph=lambda provider, tools: _FakeGraph()))
    service = ChatService(agent_runtime=runtime, projects=svc_proj)
    out = service.send("kb-console", "记一笔", "case_design", pid)
    assert out["drafts"] == [draft]
    assert out["reply"] == "草案已生成"


from types import SimpleNamespace

from aitester.memory import FileMemoryStore, InMemoryMemoryStore
from aitester.orchestration.agent_graph import build_agent_graph
from aitester.services.agent_runtime import AgentInstance


def _sentinel_runtime():
    """provider 解析与记忆选型互不相关：假 runtime 只把注入的 provider 原样回出来。

    build_graph 用真实单节点拓扑（tools=[] 退化为直答）：裁剪断言要看进 prompt 的
    langchain 消息对象，echo 路径（provider.complete，收 dict）抓不到。
    """
    return SimpleNamespace(build=lambda agent_id, session_id, provider_override=None, cwd=".", **_:
                           AgentInstance(agent_id=agent_id, system_prompt="p",
                                         provider=provider_override or MockProvider(),
                                         tools=[], build_graph=build_agent_graph))


def _recording_runtime():
    """记 kwargs 的 runtime 替身：build 调用点的 cwd 与延迟建会话的归属只能在此验。"""
    calls: list[dict] = []

    def _build(agent_id, session_id, provider_override=None, cwd=".", **_):
        calls.append({"agent_id": agent_id, "session_id": session_id, "cwd": cwd})
        return AgentInstance(agent_id=agent_id, system_prompt="p",
                             provider=provider_override or MockProvider(),
                             tools=[], build_graph=build_agent_graph)

    return SimpleNamespace(build=_build, calls=calls)


class _RecordingService(ChatService):
    """抓 prepare 产出的 memory 实例：装配裁定（文件/进程内）只能在此处验。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seen_memory: list[object] = []

    def prepare(self, session_id: str, message: str, agent_id: str,
                project_id: str = "", perm_mode: str = DEFAULT_PERM_MODE):
        prepared = super().prepare(session_id, message, agent_id, project_id, perm_mode)
        self.seen_memory.append(prepared.memory)
        return prepared


def test_send_with_empty_session_id_generates_sess_id(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    svc = _RecordingService(provider=MockProvider(),
                            sessions=_locator(svc_proj),
                            projects=svc_proj)
    svc.agent_runtime = _sentinel_runtime()
    result = svc.send("", "生成登录用例", "case_design", pid)
    assert result["session_id"].startswith("sess_")
    assert result["title"] == "生成登录用例"
    assert isinstance(svc.seen_memory[0], FileMemoryStore)  # 新会话走文件记忆


def test_failed_send_leaves_no_session_on_disk(tmp_path, project) -> None:
    svc_proj, pid, root = project
    loc = _locator(svc_proj)
    svc = ChatService(provider=MockProvider(), sessions=loc, projects=svc_proj)

    def _boom(agent_id, session_id, provider_override=None, cwd=".", **_):
        raise ProviderConfigError("未配置模型")

    svc.agent_runtime = SimpleNamespace(build=_boom)
    with pytest.raises(ProviderConfigError):
        svc.send("", "hi", "case_design", pid)
    assert loc.for_agent(pid, "case_design").list("case_design", pid) == []
    assert not (root / ".AiTester" / "session_history").exists() or \
        loc.for_agent(pid, "case_design").list("case_design", pid) == []


def test_send_with_temporary_key_stays_in_memory(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    loc = _locator(svc_proj)
    svc = _RecordingService(provider=MockProvider(), sessions=loc, projects=svc_proj)
    svc.agent_runtime = _sentinel_runtime()
    result = svc.send("kb-console", "hi", "case_design", pid)  # 非 sess_ 形态：临时键
    assert result["session_id"] == "kb-console"
    assert result["title"] == ""                          # 临时键不在索引里
    assert isinstance(svc.seen_memory[0], InMemoryMemoryStore)
    assert loc.for_agent(pid, "case_design").list("case_design", pid) == []


def test_send_with_unknown_sess_id_raises(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    svc = ChatService(provider=MockProvider(), sessions=_locator(svc_proj),
                      projects=svc_proj)
    svc.agent_runtime = _sentinel_runtime()
    with pytest.raises(SessionStoreError) as exc_info:
        svc.send("sess_deadbeef", "hi", "case_design", pid)
    assert exc_info.value.detail == "会话不存在或已被删除"


def test_platform_agent_never_uses_file_memory(tmp_path, project) -> None:
    svc_proj, _pid, _ = project
    svc = _RecordingService(provider=MockProvider(), sessions=_locator(svc_proj),
                            projects=svc_proj)
    svc.agent_runtime = _sentinel_runtime()
    svc.send("kb-console", "hi", "kb_assistant")
    assert isinstance(svc.seen_memory[0], InMemoryMemoryStore)


def test_history_is_trimmed_to_last_history_max(tmp_path, project) -> None:
    svc_proj, pid, root = project
    seen: list[list] = []

    class _SpyProvider(ChunkedStreamMixin):
        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages):
            return "[spy]"

        def bind_tools(self, tools):
            return self

        def invoke_messages(self, messages):
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    store = open_session_store(root, "case_design")
    sid = store.new_id()
    store.create(sid, "case_design", pid, "标题")
    for i in range(45):
        store.append(sid, "user", f"问{i}")
        store.append(sid, "assistant", f"答{i}")
    svc = ChatService(provider=_SpyProvider(), sessions=_locator(svc_proj), projects=svc_proj)
    svc.agent_runtime = _sentinel_runtime()
    svc.send(sid, "新问题", "case_design", pid)
    contents = [m.content for m in seen[0]]
    # 90 条历史只取最近 40 条进 prompt：[system] + 40 + [本轮 user]
    assert len(contents) == 42
    assert contents[0] == "p"          # AgentInstance.system_prompt
    assert contents[1] == "问25"        # 90 条里的第 51 条，更早的 50 条进不了 prompt
    assert contents[-2] == "答44"
    assert contents[-1] == "新问题"
    assert len(open_session_store(root, "case_design").messages(sid)) == 92


def test_send_stores_failed_tool_step(tmp_path, project) -> None:
    """ok=False 必须原样落到持久化的 steps：缺省兜底会把失败工具伪装成成功。"""
    from langchain_core.messages import ToolMessage

    svc_proj, pid, root = project
    expected = {"tool": "read", "ok": False, "round": 1,
                "detail": '{"file_path": "missing.txt"}'}

    class _FakeGraph:
        # run_graph 驱动翻到 graph.stream：假图改吐 custom turn + tools updates。
        # status=error 折出的 ok=False 仍原样进 steps。下面断言一字未动。
        def stream(self, state, *, config=None, stream_mode=None):
            yield ("custom", {"type": "turn", "round": 1, "text": "", "stopped": False,
                              "tool_calls": [{"id": "c1", "name": "read",
                                              "args": {"file_path": "missing.txt"}}]})
            yield ("updates", {"tools": {"messages": [
                ToolMessage(content="工具执行失败", tool_call_id="c1", name="read",
                            status="error")]}})
            yield ("custom", {"type": "turn", "round": 2, "text": "读取失败",
                              "stopped": False, "tool_calls": []})

    svc = ChatService(provider=MockProvider(), sessions=_locator(svc_proj), projects=svc_proj)
    svc.agent_runtime = SimpleNamespace(
        build=lambda agent_id, session_id, provider_override=None, cwd=".", **_: AgentInstance(
            agent_id=agent_id, system_prompt="p", provider=MockProvider(), tools=[],
            build_graph=lambda provider, tools: _FakeGraph()))
    result = svc.send("", "读文件", "case_design", pid)
    assert result["steps"] == [expected]  # API/done 无 result
    stored = open_session_store(root, "case_design").messages(result["session_id"])
    disk = stored[1].steps[0]
    assert {k: disk[k] for k in ("tool", "ok", "round", "detail")} == expected
    assert disk["result"] == "工具执行失败"  # 全量落盘


# ── 第 2 片：项目解析、目录验真与双归属校验（Task 6）──────────────────────────


def test_send_requires_project_for_visible_agent(tmp_path, project) -> None:
    svc, _pid, _ = project
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=_locator(svc), projects=svc)
    with pytest.raises(ProjectConfigError) as exc:
        chat.send("", "生成用例", "case_design", "")         # 空 project_id：显式传，不靠签名默认值
    assert "项目" in exc.value.detail


def test_send_rejects_unreachable_project_dir(tmp_path, project) -> None:
    svc, pid, root = project
    root.rmdir()                                            # 目录被移走/删掉：发送必须响亮失败
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=_locator(svc), projects=svc)
    with pytest.raises(ProjectConfigError) as exc:
        chat.send("", "生成用例", "case_design", pid)
    assert str(root) in exc.value.detail and "项目页" in exc.value.detail


def test_send_rejects_malformed_project_dir_as_400_not_500(tmp_path, project) -> None:
    # 裁定 3：含 NUL 的畸形落盘 dir 会让文件系统调用抛 ValueError（不是 OSError），
    # 必须走同一只吞 (OSError, ValueError) 的 dir_exists 探测答「不可达」→ 中文 400，绝不 500。
    # create 校验不吃 NUL 输入，只能绕道改盘后重建服务实例（读侧自愈路径会原样放行 dir）。
    _svc, pid, root = project
    repo = FileJsonConfigRepository(tmp_path / "projects.json")
    data = repo.load()
    for p in data["projects"]:
        if p["id"] == pid:
            p["dir"] = f"{root}\x00"
    repo.save(data)
    broken = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=_locator(broken), projects=broken)
    with pytest.raises(ProjectConfigError) as exc:
        chat.send("", "生成用例", "case_design", pid)
    assert "项目页" in exc.value.detail


def test_send_builds_instance_with_project_cwd(tmp_path, project) -> None:
    svc, pid, root = project
    runtime = _recording_runtime()                          # 记 kwargs 的 runtime 替身
    chat = ChatService(agent_runtime=runtime,
                       sessions=_locator(svc), projects=svc)
    sid = chat.send("", "生成用例", "case_design", pid)["session_id"]
    assert runtime.calls[-1]["cwd"] == str(root)
    assert chat.sessions.for_agent(pid, "case_design").get(sid).project_id == pid


def test_persist_writes_tool_result_under_project_session_history(tmp_path, project) -> None:
    svc_proj, pid, root = project
    svc = ChatService(provider=MockProvider(), sessions=_locator(svc_proj), projects=svc_proj)
    svc.agent_runtime = _sentinel_runtime()
    prepared = svc.prepare("", "hi", "case_design", pid)
    svc._persist(
        prepared, "reply",
        [{"tool": "read", "ok": True, "round": 1, "detail": "{}", "result": "BODY"}],
        False,
    )
    hist = root / ".AiTester" / "session_history" / "case_design"
    assert (hist / "index.json").is_file()
    rows = open_session_store(root, "case_design").messages(prepared.session_id)
    assert rows[0].agent_id == "case_design" and rows[0].session_id == prepared.session_id
    assert rows[1].steps[0]["result"] == "BODY"


def test_send_expands_tilde_project_dir_before_handing_over_cwd(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`~` 项目：验真与落点必须认同一个展开结果，否则裁定 5 的验真落空。

    `dir_exists` 与 `dangerous_root_reason` 都先 expanduser 再 stat，所以 `~/work/reqs` 答「可达」；
    而 `fs_tool._resolve` 只把 `Path(cwd) / target` 拼起来、从不展 `~`，未展开的 dir 会被当相对片段
    拼进后端 cwd，write 的 `mkdir(parents=True)` 就地建出一棵字面 `~` 目录树——用户以为写进了项目。
    平台差异：POSIX 的 `os.path.expanduser` 读 HOME，Windows 读 USERPROFILE（3.11 的
    `Path.expanduser` 内部就是调 `os.path.expanduser("~")`），所以两个变量都设，断言在任何平台上
    都指向同一个真实目录；测试侧同样用 `Path(dir).expanduser()` 求期望值，与被测代码认同一个入口。
    """
    home = tmp_path / "fakehome"
    (home / "work" / "reqs").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    created = svc.create(name="波浪号项目", desc="", dir_="~/work/reqs", agents=["case_design"])
    runtime = _recording_runtime()
    chat = ChatService(provider=MockProvider(), agent_runtime=runtime,
                       sessions=_locator(svc), projects=svc)
    chat.send("", "生成用例", "case_design", created["id"])
    cwd = str(runtime.calls[-1]["cwd"])
    assert "~" not in cwd                                    # 字面 `~` 绝不进落点
    assert Path(cwd).is_absolute()
    assert cwd == str(Path("~/work/reqs").expanduser())      # 与验真同一次展开


def test_send_rejects_project_mismatch(tmp_path, project) -> None:
    svc, pid, _ = project
    # 目录验真在项目段第一关（早于归属比对）：other 的 dir 必须真实存在，
    # 否则先撞 dir_exists 的 ProjectConfigError，永远走不到归属不匹配那条断言
    (tmp_path / "pay").mkdir()
    other = svc.create(name="支付中心", desc="", dir_=str(tmp_path / "pay"),
                       agents=["case_design"])["id"]
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=_locator(svc), projects=svc)
    sid = chat.send("", "一", "case_design", pid)["session_id"]
    with pytest.raises(SessionStoreError) as exc:
        chat.send(sid, "二", "case_design", other)           # 别的项目目录下查无此 sid
    # 会话按项目 dir 隔离后，跨项目续写表现为「不存在」而非全局索引上的归属不符
    assert exc.value.detail == "会话不存在或已被删除"


def test_platform_agent_ignores_project(tmp_path, project) -> None:
    svc, _pid, _ = project
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=_locator(svc), projects=svc)
    result = chat.send("kb-console", "记一笔", "kb_assistant", "")  # 空 project_id：/kb 链路不破（spec 裁定 7）
    assert result["session_id"] == "kb-console"
