"""服务层事件流：守门只有一段、终态才落盘、断开也落截断盘、失败一条不落。"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderError
from aitester.config import Settings
from aitester.memory import FileMemoryStore
from aitester.orchestration import build_agent_graph
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl
from aitester.services import ChatService
from aitester.services.agent_runtime import AgentInstance, AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ModelConfigService
from aitester.services.project_config import ProjectConfigError, ProjectService
from aitester.services.session_store import SessionStore
from aitester.storage import FileJsonConfigRepository
from streaming_fakes import CancelAfterProvider, ChunkedStreamMixin, local_tools


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "reqs"
    root.mkdir()
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    return svc, svc.create(name="订单系统", desc="", dir_=str(root), agents=["case_design"])["id"], root


def _runtime(tmp_path: Path) -> AgentRuntime:
    """真实 AgentRuntime + provider_override：零网络，工具面是真注册表。"""
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "m.json"), Settings(_env_file=None))
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "c.json"), model_config)
    return AgentRuntime(capability, model_config)


def _service(tmp_path: Path, svc_proj, provider=None) -> ChatService:
    return ChatService(provider=provider or MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=SessionStore(tmp_path / "sessions"), projects=svc_proj)


class _Scripted(ChunkedStreamMixin):
    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Scripted":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        return self._script.pop(0)


def test_prepare_and_send_guard_share_one_detail(tmp_path, project) -> None:
    """第 2 片消费对称性的直接应用：流式路径不得重写一遍守门文案。"""
    svc_proj, pid, _ = project
    svc = _service(tmp_path, svc_proj)
    with pytest.raises(ProjectConfigError) as a:
        svc.prepare("", "hi", "case_design", "")
    with pytest.raises(ProjectConfigError) as b:
        svc.send("", "hi", "case_design", "")
    assert a.value.detail == b.value.detail == "请先选择项目，再发送消息"


def test_stream_turn_yields_events_then_persists_on_done(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                      sessions=store, projects=svc_proj)
    prepared = svc.prepare("", "生成用例", "case_design", pid)
    events = list(svc.stream_turn(prepared))
    assert events[0]["type"] == "delta"
    done = events[-1]
    assert done["type"] == "done" and done["reply"] == "[mock] 生成用例"
    assert done["steps"] == [] and done["stopped"] is False
    assert done["session_id"] == prepared.session_id
    assert done["title"] == "生成用例"                 # 首条消息建会话，标题取用户第一句
    rows = store.messages(prepared.session_id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[-1].stopped is False
    assert svc.repo.get(f"session:case_design:{prepared.session_id}")[
        "last_reply"] == "[mock] 生成用例"


def test_control_cancel_marks_done_stopped_and_persists_prefix(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    control = RunControl()
    provider = CancelAfterProvider(AIMessage(content="0123456789"), control, after=1)
    svc = _service(tmp_path, svc_proj, provider=provider)
    prepared = svc.prepare("", "说吧", "case_design", pid)
    done = list(svc.stream_turn(prepared, control=control))[-1]
    assert done["type"] == "done" and done["stopped"] is True
    assert done["reply"] == "0123"                     # 留已生成前缀，标注只进 stopped 字段
    row = store.messages(prepared.session_id)[-1]
    assert row.content == "0123" and row.stopped is True


def test_disconnect_persists_truncated_row(tmp_path, project) -> None:
    """浏览器断开 == 生成器 close()：界面不看了，磁盘仍要留下他看到的那半截。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _service(tmp_path, svc_proj)                # MockProvider："[mock] 生成用例" 共 3 块
    prepared = svc.prepare("", "生成用例", "case_design", pid)
    stream = svc.stream_turn(prepared)
    assert next(stream)["type"] == "delta"            # 只消费第一帧就断开
    stream.close()
    rows = store.messages(prepared.session_id)
    assert rows[-1].role == "assistant"
    assert rows[-1].stopped is True
    assert rows[-1].content == "[moc"                 # 停笔点在第二块的检查位：落的就是第一块


def test_disconnect_multi_round_persists_last_round_prefix(tmp_path, project) -> None:
    """多轮断开钉语义：落盘取「末轮已投递前缀」，与 done 路径的 reply 同口径——
    chat.py stream_turn 的 `visible[max(visible)]` 与 agent_graph.py 的 turn 覆盖分支
    （后写的非工具轮 content 覆盖前面的）一致：中间轮文本折成 📝 步骤、不落正文，
    不许被"顺手修成"拼接全部轮次。"""
    svc_proj, pid, root = project
    store = SessionStore(tmp_path / "sessions")
    provider = _Scripted([
        AIMessage(content="核查中", tool_calls=[{"name": "write", "args": {
            "file_path": "t.txt", "content": "v"}, "id": "c1", "type": "tool_call"}]),
        AIMessage(content="已完成清单"),
    ])
    runtime = SimpleNamespace(build=lambda agent_id, session_id, provider_override=None, cwd=".":
                             AgentInstance(agent_id=agent_id,
                                           system_prompt=find_agent("case_design").prompt,
                                           provider=provider, tools=local_tools(root),
                                           build_graph=build_agent_graph))
    svc = ChatService(sessions=store, projects=svc_proj, agent_runtime=runtime)
    prepared = svc.prepare("", "写文件", "case_design", pid)
    stream = svc.stream_turn(prepared)
    # 消费到第二条 delta（round 1 的 delta、round 1 的 step、round 2 首个 delta 都已投递）
    # 才断开，确保 visible 同时含 1、2 两轮，测试才有判别性。
    deltas = 0
    for event in stream:
        if event["type"] == "delta":
            deltas += 1
            if deltas == 2:
                break
    stream.close()
    rows = store.messages(prepared.session_id)
    assert rows[-1].role == "assistant"
    assert rows[-1].stopped is True
    assert rows[-1].content == "已完成清"              # 只落末轮已投递前缀
    assert "核查中" not in rows[-1].content            # 中间轮不落正文：与 done 的 reply 同口径


def test_close_after_done_persists_turn_once(tmp_path, project) -> None:
    """Task 7 的 SSE 路由就是「收到 done 就 break」的消费者：close() 不得二次落盘。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _service(tmp_path, svc_proj)
    prepared = svc.prepare("", "生成用例", "case_design", pid)
    stream = svc.stream_turn(prepared)
    for event in stream:
        if event["type"] == "done":
            break
    stream.close()
    rows = store.messages(prepared.session_id)
    assert [r.role for r in rows] == ["user", "assistant"]   # 至多一写：没有第二对行
    assert rows[-1].content == "[mock] 生成用例"              # finish 全文，不是截断前缀
    assert rows[-1].stopped is False


class _Boom(ChunkedStreamMixin):
    name = "boom"
    model_ref = "boom/model"

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Boom":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        raise ProviderError("调用 boom/model 失败: HTTP 401")


def test_stream_failure_persists_nothing(tmp_path, project) -> None:
    """流中 ProviderError 原样上抛（路由转 error 事件），且一条也不落盘——与迁移前一致。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _service(tmp_path, svc_proj, provider=_Boom())
    prepared = svc.prepare("", "hi", "case_design", pid)
    with pytest.raises(ProviderError):
        list(svc.stream_turn(prepared))
    assert store.list("case_design", pid) == []       # 失败发送不留 0 消息幽灵会话


def test_send_fold_keeps_trace_and_model(tmp_path, project) -> None:
    """一次性壳保留 trace/model（SSE 协议不带它们，服务层与既有断言仍要）。"""
    svc_proj, pid, _ = project
    svc = _service(tmp_path, svc_proj)
    result = svc.send("s1", "生成用例", "case_design", pid)
    assert result["trace"] == [
        "services", "context", "orchestration", "adapters", "memory", "storage"]
    assert result["model"] == "mock/mock"
    assert result["reply"] == "[mock] 生成用例"
    assert result["steps"] == [] and result["drafts"] == []
    assert result["session_id"] == "s1" and result["title"] == ""


def test_send_fold_keeps_tool_trace_entries(tmp_path, project) -> None:
    """工具轮的 trace 追加顺序照旧：adapters → tool:<name> → memory → storage。"""
    svc_proj, pid, root = project

    provider = _Scripted([
        AIMessage(content="", tool_calls=[{"name": "write", "args": {
            "file_path": "t.txt", "content": "v"}, "id": "c1", "type": "tool_call"}]),
        AIMessage(content="已写入"),
    ])
    runtime = SimpleNamespace(build=lambda agent_id, session_id, provider_override=None, cwd=".":
                             AgentInstance(agent_id=agent_id,
                                           system_prompt=find_agent("case_design").prompt,
                                           provider=provider, tools=local_tools(root),
                                           build_graph=build_agent_graph))
    svc = ChatService(sessions=SessionStore(tmp_path / "sessions"),
                      projects=svc_proj, agent_runtime=runtime)
    result = svc.send("s1", "写文件", "case_design", pid)
    assert result["trace"] == ["services", "context", "orchestration", "adapters",
                               "tool:write", "memory", "storage"]
    assert result["steps"] == [{"tool": "write", "ok": True, "round": 1,
                                "detail": '{"file_path": "t.txt", "content": "v"}'}]


def test_node_receives_control_via_config_injected_by_service(tmp_path, project) -> None:
    """服务层注入的 config 形态真能被节点读到（端到端钉，防 run_registry/节点两侧键名漂移）。"""
    seen: list[bool] = []
    control = RunControl()

    class _Spy(ChunkedStreamMixin):
        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages: list) -> str:
            return ""

        def bind_tools(self, tools: list) -> "_Spy":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            return AIMessage(content="ok")

        def stream_messages(self, messages: list):
            cfg: RunnableConfig = __import__(
                "langgraph.config", fromlist=["get_config"]).get_config()
            seen.append((cfg.get("configurable") or {}).get(RUN_CONTROL_KEY) is control)
            return super().stream_messages(messages)

    svc_proj, pid, _ = project
    svc = _service(tmp_path, svc_proj, provider=_Spy())
    prepared = svc.prepare("", "hi", "case_design", pid)
    list(svc.stream_turn(prepared, control=control))
    assert seen and all(seen)
