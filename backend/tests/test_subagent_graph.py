"""子智能体编排：同线程驱动、受控帧契约、挂起续跑与失败路径（T4/T5）。

判别性要求：子体独立入参（System/Human 各恰好 1）、子挂起经父审批通道两连配对、
子步骤带 subagent 标注且不进父步骤——三件都是 T1 探针实测过的机制，这里钉成回归锁。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools.observation import FileObservationStore
from aitester.adapters.tools.subagent_tools import build_task_tool
from aitester.orchestration import build_agent_graph, stream_graph
from aitester.orchestration.gate import GateContext
from aitester.orchestration.subagent import ChildRuntime, drive_child
from streaming_fakes import CancelAfterProvider, ScriptedProvider

SUB_NAME = "通用子智能体"


def _calls(*items) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"id": cid, "name": name, "args": args, "type": "tool_call"}
        for cid, name, args in items])


def _task_call(cid: str = "c1") -> AIMessage:
    return _calls((cid, "task", {"description": "落两个文件"}))


def _child_runtime(provider, tools) -> ChildRuntime:
    return ChildRuntime(agent_id="general-purpose", system_prompt="You are a subagent.",
                        provider=provider, tools=tools, build_graph=build_agent_graph)


def _parent_tools(child: ChildRuntime, tmp_path: Path) -> list:
    tool = build_task_tool({"general-purpose": {"name": SUB_NAME, "desc": "Read-only investigator."}},
                           build_child=lambda _aid: child, drive=drive_child)
    registry = build_default_registry(cwd=str(tmp_path), session_id="s1",
                                      observed=FileObservationStore(), task=tool)
    return registry.get_many(["task"])


def _gate(tmp_path: Path) -> GateContext:
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    return GateContext(perm_mode="boundary", project_dir=str(proj),
                       session_key="case_design:s1", remembered=set())


def test_child_runs_and_parent_only_receives_summary(tmp_path: Path) -> None:
    """快乐路径：子独立跑完（System/Human 恰好各 1），父上下文只收回一条摘要。"""
    parent = ScriptedProvider([_task_call(), AIMessage(content="主答")])
    child = ScriptedProvider([AIMessage(content="子摘要")])
    read_tool = build_default_registry(cwd=str(tmp_path)).get_many(["read"])
    tools = _parent_tools(_child_runtime(child, read_tool), tmp_path)
    events = list(stream_graph(build_agent_graph, parent, tools,
                               [HumanMessage(content="派子调查")], thread_id="sg1"))
    subs = [e for e in events if e["type"] == "sub"]
    assert [s["phase"] for s in subs] == ["start", "done"]
    assert subs[0]["call_id"] == "c1" and subs[0]["name"] == SUB_NAME
    assert subs[0]["title"] == SUB_NAME and subs[1]["ok"] is True
    assert subs[1]["tools"] == 0 and subs[1]["elapsed_ms"] >= 0
    finish = events[-1]
    assert finish["reply"] == "主答" and finish["stopped"] is False
    assert [s["tool"] for s in finish["tool_traces"]] == ["task"]
    assert finish["tool_traces"][0]["result"] == "子摘要"          # 主上下文只收回摘要
    parent_round2 = parent.calls[1]                              # 父第二回合入参（验收 2 隔离）
    assert len(parent_round2) == 3                               # 子内消息一条都不进父：恰增一条摘要
    assert isinstance(parent_round2[-1], ToolMessage)
    assert parent_round2[-1].content == "子摘要"
    assert all("You are a subagent." not in str(m.content) for m in parent_round2)  # 子提示词不进父
    child_call = child.calls[0]                                  # 子体独立入参（R2）
    assert isinstance(child_call[0], SystemMessage)
    assert child_call[0].content == "You are a subagent."
    assert child_call[1].content == "落两个文件" and len(child_call) == 2
    assert all("派子调查" not in str(m.content) for m in child_call)


def test_child_boundary_write_waits_via_parent_and_resumes(tmp_path: Path) -> None:
    """子写界外 → 父通道弹卡（wait 带 subagent 来源）→ 批准续跑 → 落盘、主答、子步骤标注。"""
    parent = ScriptedProvider([_task_call(), AIMessage(content="主答")])
    child = ScriptedProvider([
        _calls(("w1", "write", {"file_path": "../out.md", "content": "x"})),
        AIMessage(content="子写完了")])
    write_tool = build_default_registry(cwd=str(tmp_path / "proj")).get_many(["write"])
    tools = _parent_tools(_child_runtime(child, write_tool), tmp_path)
    args = dict(build=build_agent_graph, provider=parent, tools=tools,
                messages=[HumanMessage(content="派子写文件")], thread_id="sg2",
                gate=_gate(tmp_path))
    first = list(stream_graph(**args))
    wait = [e for e in first if e["type"] == "wait"][0]
    assert wait["call_id"] == "w1" and wait["tool"] == "write"
    assert wait["subagent"] == {"call_id": "c1", "name": SUB_NAME, "title": SUB_NAME}
    assert first[-1]["pending"] is True
    assert not (tmp_path / "out.md").exists()                    # 挂起即零副作用
    second = list(stream_graph(resume={"decision": "approve", "remember": False}, **args))
    assert [s["phase"] for s in second if s["type"] == "sub"] == ["start", "done"]
    assert second[-1]["pending"] is False and second[-1]["reply"] == "主答"
    assert (tmp_path / "out.md").read_text(encoding="utf-8") == "x"
    child_steps = [e for e in second if e["type"] == "step" and e.get("subagent")]
    parent_steps = [e for e in second if e["type"] == "step" and e.get("subagent") is None]
    assert [(s["tool"], s["ok"]) for s in child_steps] == [("write", True)]
    assert [s["tool"] for s in parent_steps] == ["task"]         # 子步骤不进父过程（R10）


def test_child_two_waits_pair_positionally_across_resumes(tmp_path: Path) -> None:
    """同轮两条界外写：两次 resume 依位置配对（w1→w2），重入不重复投喂输入。"""
    parent = ScriptedProvider([_task_call(), AIMessage(content="主答")])
    child = ScriptedProvider([
        _calls(("w1", "write", {"file_path": "../a.md", "content": "a"}),
               ("w2", "write", {"file_path": "../b.md", "content": "b"})),
        AIMessage(content="子写完了")])
    write_tool = build_default_registry(cwd=str(tmp_path / "proj")).get_many(["write"])
    tools = _parent_tools(_child_runtime(child, write_tool), tmp_path)
    args = dict(build=build_agent_graph, provider=parent, tools=tools,
                messages=[HumanMessage(content="派子写两个")], thread_id="sg3",
                gate=_gate(tmp_path))
    first = list(stream_graph(**args))
    assert [w["call_id"] for w in (e for e in first if e["type"] == "wait")] == ["w1"]
    second = list(stream_graph(resume={"decision": "approve", "remember": False}, **args))
    assert [w["call_id"] for w in (e for e in second if e["type"] == "wait")] == ["w2"]
    third = list(stream_graph(resume={"decision": "approve", "remember": False}, **args))
    assert third[-1]["pending"] is False and third[-1]["reply"] == "主答"
    assert (tmp_path / "a.md").exists() and (tmp_path / "b.md").exists()
    resume_call = child.calls[1]                                 # 重入那次子回合
    assert sum(isinstance(m, SystemMessage) for m in resume_call) == 1
    assert sum(isinstance(m, HumanMessage) for m in resume_call) == 1


def test_child_failure_surfaces_as_english_tool_error(tmp_path: Path) -> None:
    """子跑挂 → fail 帧 + 英文 ToolException，模型收到错误结果后照常接续作答。"""
    parent = ScriptedProvider([_task_call(), AIMessage(content="改用直答")])
    child = ScriptedProvider([RuntimeError("provider down")])
    tools = _parent_tools(_child_runtime(child, []), tmp_path)
    events = list(stream_graph(build_agent_graph, parent, tools,
                               [HumanMessage(content="派子调查")], thread_id="sg4"))
    subs = [e for e in events if e["type"] == "sub"]
    assert [s["phase"] for s in subs] == ["start", "fail"] and subs[1]["ok"] is False
    finish = events[-1]
    assert finish["reply"] == "改用直答"
    assert finish["tool_traces"][0]["ok"] is False
    assert "Subagent 'general-purpose' failed: provider down" in finish["tool_traces"][0]["result"]


def test_child_stop_cascades_to_parent(tmp_path: Path) -> None:
    """停止位级联：子轮停笔即收（无第二回合）、父 finish.stopped=True、无孤儿挂起。"""
    from aitester.orchestration.run_control import RunControl

    control = RunControl()
    parent = ScriptedProvider([_task_call(), AIMessage(content="主答")])
    child = CancelAfterProvider(AIMessage(content="子正在算……"), control, after=1)
    tools = _parent_tools(_child_runtime(child, []), tmp_path)
    events = list(stream_graph(build_agent_graph, parent, tools,
                               [HumanMessage(content="派子调查")], thread_id="sg5",
                               control=control))
    assert not any(e["type"] == "wait" for e in events)
    assert events[-1]["stopped"] is True and events[-1]["pending"] is False


def test_drive_guard_reuses_finished_child_without_streaming() -> None:
    """守卫其一（R5）：子已完结的重入直接复用摘要；writer 在守卫之后才取，
    所以无流上下文（config=None）也能裸调本函数（P1b 实测口径）。"""
    class _FinishedGraph:
        def get_state(self, _cfg):
            return SimpleNamespace(next=(), values={"messages": [AIMessage(content="旧摘要")]})

        def stream(self, *_a, **_k):
            raise AssertionError("已完结的子不该重跑")

    runtime = ChildRuntime(agent_id="general-purpose", system_prompt="S", provider=None,
                           tools=[], build_graph=lambda _p, _t: _FinishedGraph())
    assert drive_child(runtime, "brief", call_id="c1", name="n", title="t",
                       config=None) == "旧摘要"
