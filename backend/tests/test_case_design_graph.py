# backend/tests/test_case_design_graph.py
"""T9 图装配：注册表恒等、catalog 图名、运行时 case_env 三态、直通与 react 逐帧同形、
case_env 注入全链（图级 halted 收敛）、服务层 SSE 携带 case_env。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from streaming_fakes import ScriptedProvider, local_tools
from test_chat_stream_api import _pid, _stream

from aitester.adapters.llm import MockProvider
from aitester.adapters.tools import FileObservationStore
from aitester.agents import find_agent
from aitester.case_design.constants import CASE_DESIGN_KEY
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.graph import build_case_design_graph
from aitester.case_design.ledger import Ledger
from aitester.config import Settings
from aitester.main import create_app
from aitester.orchestration import (
    GRAPH_BUILDERS, build_agent_graph, get_graph_builder, new_thread_id, stream_graph,
)
from aitester.services import ChatService
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository


class _KbOn:
    """带开关的最小 kb：够装配期注册表与平台预热、够 env 构造；不触真 reme。"""

    is_enabled = True

    def __init__(self, root: Path) -> None:
        self.kb_root_dir = root

    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    def workspace_dir(self, project_id: str, agent_id: str) -> Path:
        return self.kb_root_dir / "workspaces" / project_id / agent_id

    def run_job_sync(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})


def _runtime_kb(tmp_path: Path, kb) -> AgentRuntime:
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"), Settings(_env_file=None)
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return AgentRuntime(capability, model_config, FileObservationStore(), kb=kb)


def test_registry_resolves_case_design_loop() -> None:
    assert GRAPH_BUILDERS["case_design_loop"] is build_case_design_graph
    assert get_graph_builder("case_design_loop") is build_case_design_graph


def test_case_design_spec_declares_loop_graph() -> None:
    assert find_agent("case_design").graph_builder == "case_design_loop"


def test_runtime_builds_case_env_for_loop_graph_only(tmp_path: Path) -> None:
    kb = _KbOn(tmp_path / "bases")
    runtime = _runtime_kb(tmp_path, kb)
    project = tmp_path / "reqs"
    project.mkdir()
    instance = runtime.build("case_design", "s1", provider_override=MockProvider(),
                             cwd=str(project))
    assert isinstance(instance.case_env, CaseDesignEnv)
    assert instance.case_env.project_dir == str(project)     # 落点=项目 dir（产物归项目）
    assert instance.case_env.kb is kb
    platform = runtime.build("kb_assistant", "kb-console", provider_override=MockProvider())
    assert platform.case_env is None                          # 非 loop 图不构 env


def test_case_env_passthrough_three_ways(tmp_path: Path) -> None:
    # kb=None：整个装配没有知识库
    inst = _runtime_kb(tmp_path, None).build(
        "case_design", "s1", provider_override=MockProvider())
    assert inst.case_env is None

    # 无开关替身（_NoopKbManager 同形）：缺省 False——存量服务测试零改动的前提（A10）
    class _NoSwitch:
        kb_root_dir = str(tmp_path / "kb-root")

    inst = _runtime_kb(tmp_path, _NoSwitch()).build(
        "case_design", "s1", provider_override=MockProvider())
    assert inst.case_env is None

    # 显式关闭：同样直通
    class _KbOff(_KbOn):
        is_enabled = False

    inst = _runtime_kb(tmp_path, _KbOff(tmp_path / "bases")).build(
        "case_design", "s1", provider_override=MockProvider())
    assert inst.case_env is None


def test_passthrough_frames_match_react(tmp_path: Path) -> None:
    """case_env=None（直通）时事件流与 react 逐帧同形——存量行为零变化的外部证据。"""

    def run(build, root: Path, case_env=None):
        root.mkdir(exist_ok=True)
        (root / "a.txt").write_text("hello", encoding="utf-8")
        provider = ScriptedProvider([
            AIMessage(content="", tool_calls=[
                {"id": "c1", "name": "read", "args": {"file_path": "a.txt"},
                 "type": "tool_call"}]),
            AIMessage(content="读到 hello"),
        ])
        return list(stream_graph(build, provider, local_tools(root),
                                 [HumanMessage(content="读文件")], case_env=case_env))

    # 两次跑必须同落点：read 工具结果内嵌绝对路径，目录名不同则 finish.tool_traces
    # 的 result 永远不等——同形断言钉的是拓扑/帧序，不是临时目录名（简报夹具笔误修正，
    # 断言本体一字未动）
    react = run(build_agent_graph, tmp_path / "work")
    loop = run(build_case_design_graph, tmp_path / "work")
    assert loop == react


def test_case_env_injection_runs_loop_and_halts(tmp_path: Path) -> None:
    """注入 case_env 后走真拓扑：明确任务直开账 → 计划阶段五连激活 → 重试超限 halted。"""
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)
    provider = ScriptedProvider([AIMessage(content=f"第 {i} 稿") for i in range(1, 5)])
    graph = build_case_design_graph(provider, local_tools(tmp_path))
    frames = list(graph.stream(
        {"messages": [HumanMessage(content="按业务信息生成测试设计")], "case": {}},
        config={"configurable": {CASE_DESIGN_KEY: env, "thread_id": new_thread_id()}},
        stream_mode="custom",
    ))
    turns = [f for f in frames if f.get("type") == "turn"]
    assert turns[-1]["text"].startswith("测试设计任务中止：plan/-/- 重试超限")
    assert "现场已保留" in turns[-1]["text"]
    assert turns[-1]["stopped"] is False and turns[-1]["tool_calls"] == []
    assert len(provider.calls) == 4                          # 首问 + 3 次重问（无意向门）
    for k, call in enumerate(provider.calls, start=1):
        instr = call[-1]
        assert isinstance(instr, HumanMessage) and str(instr.id) == f"cdinstr-{k}"
        assert instr.content.startswith("【编排·计划】")
    led = Ledger.load(env.design)
    assert (led is not None and led.status == "halted" and led.cursor["nudge"] == 3
            and led.data["halt"]["kind"] == "artifact_retry" and led.data["halt"]["stage"] == "plan")


def test_chat_first_touch_ends_with_model_words_and_zero_trace(tmp_path: Path) -> None:
    """真拓扑里的「你好」：无工具闲聊 → 静默结束——reply 就是模型那句话，项目零残留。"""
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)
    provider = ScriptedProvider([
        AIMessage(content="你好！我是测试设计助手。"),
    ])
    frames = list(stream_graph(build_case_design_graph, provider, local_tools(tmp_path),
                               [HumanMessage(content="你好")], case_env=env))
    finish = frames[-1]
    assert finish["type"] == "finish"
    assert finish["reply"] == "你好！我是测试设计助手。"     # 静默结束：reply 折叠吃模型原话
    assert len(provider.calls) == 1                          # 仅闲聊一回合，零工具
    chat = provider.calls[0][-1]
    assert isinstance(chat, HumanMessage) and str(chat.id) == "cdchat-1"
    assert chat.content.startswith("【编排·闲聊】")
    assert not (tmp_path / "design").exists()
    assert Ledger.load(env.design) is None


def test_service_stream_carries_case_env_to_graph(tmp_path: Path) -> None:
    """服务层全链：带开关的 kb 装配下 SSE 终帧为编排中止文案（未透传则会是「第 1 稿」）。"""
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        memory_manager=_KbOn(tmp_path / "kb-root"),
    )
    provider = ScriptedProvider([AIMessage(content=f"第 {i} 稿") for i in range(1, 5)])
    application.state.chat_service = ChatService(
        provider=provider,
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
        pending=application.state.pending_registry,
    )
    pid = _pid(application, tmp_path)
    with TestClient(application) as client:
        frames = _stream(client, {"message": "按业务信息生成测试设计",
                                  "agent_id": "case_design", "project_id": pid})
    kinds = [e for e, _ in frames]
    assert kinds[0] == "start" and kinds[-1] == "done"
    done = frames[-1][1]
    assert done["reply"].startswith("测试设计任务中止：plan/-/- 重试超限")
    assert "现场已保留" in done["reply"]
    assert len(provider.calls) == 4
