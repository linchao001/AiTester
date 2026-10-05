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
from aitester.orchestration.auth_rules import face_can_suspend
from aitester.orchestration.gate import GateContext
from aitester.orchestration.subagent import ChildRuntime, drive_child, shape_task_batch
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


def test_face_can_suspend_reads_the_three_tier_sets() -> None:
    """并行判据与三档表同源：写 / 命令 / 知识库写算可挂起，只读六件与 task 不算。"""
    assert face_can_suspend(["read", "grep_search", "glob_search", "web_search"]) is False
    assert face_can_suspend(["read", "knowledge_search", "prepare_kb_write"]) is False
    assert face_can_suspend(["read", "task"]) is False
    assert face_can_suspend(["read", "write"]) is True
    assert face_can_suspend(["edit"]) is True
    assert face_can_suspend(["pwsh"]) is True
    assert face_can_suspend(["save_to_knowledge"]) is True
    assert face_can_suspend([]) is False


def test_shape_task_batch_fans_out_when_every_child_is_read_only() -> None:
    """R4 扇出：整批 task 都可并行 → 全部保留（原对象、原顺序），只延后非 task 兄弟。"""
    calls = [{"id": "c1", "name": "task", "args": {"description": "甲"}},
             {"id": "w1", "name": "read", "args": {"file_path": "a"}},
             {"id": "c2", "name": "task",
              "args": {"description": "乙", "subagent_type": "general-purpose"}}]
    keep, deferred = shape_task_batch(calls, {"general-purpose": True})
    assert len(keep) == 2 and keep[0] is calls[0] and keep[1] is calls[2]
    assert [m.tool_call_id for m in deferred] == ["w1"]
    assert [m.status for m in deferred] == ["error"]
    assert "Re-issue this call in your next round." in str(deferred[0].content)


def test_shape_task_batch_keeps_first_task_when_a_child_can_suspend() -> None:
    """R4 串行回退：表里说这个子面会挂起（开了 write）→ 一轮只跑第一个 task，第二个延后。"""
    calls = [{"id": "c1", "name": "task", "args": {"description": "甲"}},
             {"id": "c2", "name": "task", "args": {"description": "乙"}}]
    keep, deferred = shape_task_batch(calls, {"general-purpose": False})
    assert len(keep) == 1 and keep[0] is calls[0]
    assert [m.tool_call_id for m in deferred] == ["c2"]
    assert [m.name for m in deferred] == ["task"]


def test_shape_task_batch_mixed_batch_falls_back_to_serial() -> None:
    """混合批按串行：只要有一个 task 点名的子不可并行，整批就只留第一个。"""
    calls = [{"id": "c1", "name": "task", "args": {"subagent_type": "general-purpose"}},
             {"id": "c2", "name": "task", "args": {"subagent_type": "runner"}}]
    keep, deferred = shape_task_batch(calls, {"general-purpose": True})   # runner 不在表 = False
    assert [c["id"] for c in keep] == ["c1"]
    assert [m.tool_call_id for m in deferred] == ["c2"]


def test_shape_task_batch_passthrough_without_task() -> None:
    """无 task 的批原样放行：普通多工具轮零行为变化（R4 的边界）。"""
    calls = [{"id": "c1", "name": "read", "args": {"file_path": "a"}},
             {"id": "c2", "name": "grep_search", "args": {"pattern": "x"}}]
    keep, deferred = shape_task_batch(calls, {"general-purpose": True})
    assert keep == calls and deferred == []


def test_two_read_only_children_fan_out_in_one_batch(tmp_path: Path) -> None:
    """R4/R14 图级锁：两个只读子同批都跑——两条摘要各归各的 call_id（同 ns 会顶替成同一条），
    子卡一 call 一张，父步骤两行 task。"""
    providers = [ScriptedProvider([AIMessage(content="甲摘要")]),
                 ScriptedProvider([AIMessage(content="乙摘要")])]
    taken: list[int] = []

    def build_child(_aid: str) -> ChildRuntime:
        taken.append(1)
        return _child_runtime(providers[len(taken) - 1], [])

    tool = build_task_tool({"general-purpose": {"name": SUB_NAME, "desc": "Read-only."}},
                           build_child, drive_child, parallel={"general-purpose": True})
    registry = build_default_registry(cwd=str(tmp_path), session_id="s1",
                                      observed=FileObservationStore(), task=tool)
    parent = ScriptedProvider([
        _calls(("c1", "task", {"description": "查甲"}),
               ("c2", "task", {"description": "查乙"})),
        AIMessage(content="主答")])
    events = list(stream_graph(build_agent_graph, parent, registry.get_many(["task"]),
                               [HumanMessage(content="同时查甲和乙")], thread_id="sg7"))
    subs = {(e["phase"], e["call_id"]) for e in events if e["type"] == "sub"}
    assert subs == {("start", "c1"), ("done", "c1"), ("start", "c2"), ("done", "c2")}
    notes = {m.tool_call_id: str(m.content) for m in parent.calls[1]
             if isinstance(m, ToolMessage)}
    assert sorted(notes.values()) == ["乙摘要", "甲摘要"]        # 两条各归各的：同 ns 会顶替成同一条
    assert len(taken) == 2 and sum(len(p.calls) for p in providers) == 2
    finish = events[-1]
    assert finish["reply"] == "主答" and finish["pending"] is False
    assert [t["tool"] for t in finish["tool_traces"]] == ["task", "task"]


def test_parallel_sibling_is_deferred_until_after_task(tmp_path: Path) -> None:
    """R4/R8 图级锁：子面按串行（parallel 缺省）时 free 档（无 gate）同批 [task, write]
    也只跑 task——write 零执行、tool_traces 只有 task、模型经错误结果看到延后理由。"""
    parent = ScriptedProvider([
        _calls(("c1", "task", {"description": "先派子"}),
               ("c2", "write", {"file_path": "probe.md", "content": "x"})),
        AIMessage(content="完成")])
    child = ScriptedProvider([AIMessage(content="子摘要")])
    tool = build_task_tool({"general-purpose": {"name": SUB_NAME, "desc": "Read-only."}},
                           build_child=lambda _aid: _child_runtime(child, []), drive=drive_child)
    registry = build_default_registry(cwd=str(tmp_path), session_id="s1",
                                      observed=FileObservationStore(), task=tool)
    tools = registry.get_many(["task", "write"])   # write 真在册：延后一旦失效就会落盘
    events = list(stream_graph(build_agent_graph, parent, tools,
                               [HumanMessage(content="派子并写")], thread_id="sg6"))
    assert not (tmp_path / "probe.md").exists()    # 同批 write 未执行：零副作用
    finish = events[-1]
    assert [t["tool"] for t in finish["tool_traces"]] == ["task"]
    notes = [m for m in parent.calls[1]
             if isinstance(m, ToolMessage) and m.tool_call_id == "c2"]
    assert len(notes) == 1                          # 模型看得见延后理由，可下一轮重发
    assert "Re-issue this call in your next round." in str(notes[0].content)
