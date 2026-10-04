"""第 5 片边界执法：判定矩阵、gate 决策重放、wait 折叠（按任务递进追加）。

判别性要求（spec 测试 1）：三档 × 四类别 × 界内界外 × 记住命中/未命中逐格钉死，
`free` 全 False 单独钉——默认档零行为是本片的红线。
"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from aitester.adapters.llm import MockProvider
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools.fs_tool import resolve_path
from aitester.adapters.tools.file_tools.observation import FileObservationStore
from aitester.orchestration import build_agent_graph, stream_graph
from aitester.orchestration.auth_rules import (
    PERM_MODE_DETAIL,
    PERM_MODES,
    PermModeError,
    needs_approval,
    plan_target,
    tool_ids_for,
    validate_perm_mode,
)
from aitester.orchestration.checkpoint import drop_thread, get_checkpointer
from aitester.orchestration.gate import (
    APPROVE,
    REJECT,
    build_gate_context,
    decision_from,
    plan_items,
)
from streaming_fakes import ChunkedStreamMixin


@pytest.fixture
def project(tmp_path: Path) -> str:
    root = tmp_path / "proj"
    root.mkdir()
    return str(resolve_path(str(root), "."))


def _file(path: str) -> dict:
    return {"file_path": path, "content": "x"}


class _Counting(FileObservationStore):
    """写执行计数器：只挂真工具的 mark 钩子（write.py:65 每次成功写入必过这里）。

    不造第二个工具实现——MagicMock 过不了 ToolNode 的 schema 校验，而自写假工具会绕开
    「判定与执行认同一个解析口」这条真正要验的东西。
    """

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def mark(self, session_id: str, path: str, version: str) -> None:
        self.writes.append(path)
        super().mark(session_id, path, version)


def _project_tools(tmp_path: Path, counter: _Counting) -> list:
    """cwd 落在项目目录里：界外文件就是 tmp_path 根，工具真写得动、计数器抓得到。"""
    registry = build_default_registry(
        cwd=str(tmp_path / "proj"), session_id="s1", observed=counter)
    return registry.get_many(["write"])


def _calls(*items) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"id": cid, "name": name, "args": args, "type": "tool_call"}
        for cid, name, args in items])


def _outside(name: str = "out.md") -> dict:
    return {"file_path": f"../{name}", "content": "x"}


def _gate_ctx(tmp_path: Path, perm_mode: str, remembered: set[str] | None = None) -> "GateContext":
    from aitester.orchestration.gate import GateContext
    # 传进来的集合必须原样挂上（哪怕还是空的）：记住表靠同一对象身份被 gate 回填
    return GateContext(perm_mode=perm_mode,
                       project_dir=str(resolve_path(str(tmp_path / "proj"), ".")),
                       session_key="case_design:sess_1",
                       remembered=set() if remembered is None else remembered)


def test_free_mode_never_asks(project: str) -> None:
    for tool, args in (("write", _file("D:/tmp/a.md")), ("edit", _file("D:/tmp/a.md")),
                        ("pwsh", {"command": "del x"}), ("bash", {"command": "rm x"}),
                        ("save_to_knowledge", {"title": "t", "content": "c"})):
        assert needs_approval(tool, args, "free", project, set()) is False
    assert tool_ids_for("free") == ()


def test_boundary_write_only_out_of_bounds(project: str) -> None:
    inside = str(resolve_path(project, "cases/a.md"))
    outside = str(resolve_path(project, "../out.md"))
    assert needs_approval("write", _file(inside), "boundary", project, set()) is False
    assert needs_approval("write", _file(outside), "boundary", project, set()) is True
    assert needs_approval("edit", {"file_path": inside, "old_string": "a", "new_string": "b"},
                          "boundary", project, set()) is False
    assert needs_approval("edit", {"file_path": outside, "old_string": "a", "new_string": "b"},
                          "boundary", project, set()) is True


def test_boundary_shell_always_asks_even_with_cwd_inside(project: str) -> None:
    # 裁定 6：命令的影响范围静态不可判，且 cwd 能把工作目录改到界外 → boundary 档一律挂
    for tool in ("pwsh", "bash"):
        assert needs_approval(tool, {"command": "pytest"}, "boundary", project, set()) is True
        assert needs_approval(tool, {"command": "pytest", "cwd": project},
                              "boundary", project, set()) is True
        assert needs_approval(tool, {"command": "pytest"},
                              "boundary", project, {f"{tool}|pytest"}) is False


def test_boundary_kb_write_counts_as_in_bounds(project: str) -> None:
    assert needs_approval("save_to_knowledge", {"title": "t", "content": "c"},
                          "boundary", project, set()) is False


def test_strict_asks_every_mutation_tool(project: str) -> None:
    for tool in tool_ids_for("strict"):
        args = {"command": "ls"} if tool in ("pwsh", "bash") else (
            {"title": "t", "content": "c"} if tool == "save_to_knowledge" else _file("a.md"))
        assert needs_approval(tool, args, "strict", project, set()) is True


def test_read_only_and_unknown_tools_never_ask(project: str) -> None:
    for tool in ("read", "grep_search", "glob_search", "web_search", "knowledge_search",
                 "prepare_kb_write", "not_a_real_tool"):
        for mode in ("boundary", "strict"):
            assert needs_approval(tool, _file("D:/tmp/a.md"), mode, project, set()) is False


def test_remembered_key_blocks_next_time(project: str) -> None:
    outside = str(resolve_path(project, "../out.md"))
    plan = plan_target("write", _file(outside), project)
    assert plan is not None and plan.remember_key == f"write|{outside}"
    assert needs_approval("write", _file(outside), "boundary", project, {plan.remember_key}) is False


def test_relative_and_absolute_forms_agree(project: str) -> None:
    # spec 三档判据硬要求①：判定用 resolve 后的真实路径，与 fs_tool._resolve 同一个口
    assert plan_target("write", _file("cases/a.md"), project).target == str(Path("cases") / "a.md")
    assert plan_target("write", _file("../escape.md"), project).action == "写入项目目录外的文件"
    assert plan_target("edit", {"file_path": "../e.md", "old_string": "a", "new_string": "b"},
                       project).action == "修改项目目录外的文件"


def test_perm_mode_validation_words() -> None:
    assert PERM_MODES == ("free", "boundary", "strict")
    assert validate_perm_mode("strict") == "strict"
    assert validate_perm_mode("") == "free"                 # 缺省即自由权限（/kb 根本不发这字段）
    with pytest.raises(PermModeError) as exc:
        validate_perm_mode("yolo")
    assert exc.value.detail == PERM_MODE_DETAIL
    assert PERM_MODE_DETAIL == "无效的权限模式，请选择自由权限、只批界外或严格权限"


class ScriptProvider(ChunkedStreamMixin):
    """剧本逐条给 AIMessage；bind_tools 返回自己（与 test_agent_graph.ScriptedProvider 同形）。"""

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke_messages(self, messages):
        return self._script.pop(0) if self._script else AIMessage(content="done")


def _thread_state(graph, thread_id: str):
    """StateSnapshot 是纯 NamedTuple（实测 4.2.0：字符串下标会 TypeError），必须走 .values。"""
    return graph.get_state({"configurable": {"thread_id": thread_id}})


def test_graph_compiles_with_the_shared_checkpointer(tmp_path: Path) -> None:
    """P3：无 checkpointer 时 interrupt 静默通过 = 假执法。「装了、而且装的是单例」钉成回归锁。"""
    graph = build_agent_graph(MockProvider(), [])
    assert graph.checkpointer is get_checkpointer()      # 跨请求存活才谈得上续跑
    graph2 = build_agent_graph(MockProvider(), [])
    assert graph2.checkpointer is graph.checkpointer


def test_thread_id_isolates_two_runs(tmp_path: Path) -> None:
    """P4：thread_id 每轮新建；同会话连发两条线程的消息互不累积。"""
    graph = build_agent_graph(MockProvider(), [])
    for tid in ("runA", "runB"):
        list(stream_graph(build_agent_graph, MockProvider(), [],
                          [HumanMessage(content="生成用例")], thread_id=tid))
        assert len(_thread_state(graph, tid).values["messages"]) == 2      # human + ai
    drop_thread("runA")
    assert _thread_state(graph, "runA").next == ()                   # 摘干净：不留残断
    assert not _thread_state(graph, "runA").values                   # 删除判据：只有真删了才空
    assert len(_thread_state(graph, "runB").values["messages"]) == 2 # 另一条线程不受牵连


def test_threadless_callers_still_work() -> None:
    """带 checkpointer 后缺 thread_id 会 ValueError（实测）：历史入口必须自造线程而不是炸。"""
    events = list(stream_graph(build_agent_graph, MockProvider(), [],
                               [HumanMessage(content="生成用例")]))
    assert events[-1]["type"] == "finish"


def test_free_context_never_touches_the_gate(tmp_path: Path) -> None:
    """默认档零行为：gate 在场但直通，工具照旧立刻执行。"""
    counter = _Counting()
    provider = ScriptProvider([_calls(("c1", "write", _outside())), AIMessage(content="完成")])
    events = list(stream_graph(build_agent_graph, provider, _project_tools(tmp_path, counter),
                               [HumanMessage(content="写界外")],
                               gate=build_gate_context("free", "anything", "k", set())))
    assert [e["type"] for e in events].count("wait") == 0
    assert counter.writes == [str(resolve_path(str(tmp_path), "out.md"))]


def test_build_gate_context_short_circuits_free_and_platform(tmp_path: Path) -> None:
    assert build_gate_context("free", str(tmp_path), "k", set()) is None
    assert build_gate_context("strict", "", "k", set()) is None       # 平台智能体无项目落点


@pytest.mark.xfail(strict=True, reason="wait 事件要到 Task 4 从 __interrupt__ 折出来；Task 4 Step 4 删掉本行")
def test_boundary_out_of_bounds_waits_before_executing(tmp_path: Path) -> None:
    counter = _Counting()
    provider = ScriptProvider([_calls(("c1", "write", _outside()))])
    events = list(stream_graph(build_agent_graph, provider, _project_tools(tmp_path, counter),
                               [HumanMessage(content="写界外")],
                               thread_id="w1", gate=_gate_ctx(tmp_path, "boundary")))
    assert counter.writes == []                                  # 走查项 4 的单测锁：挂起即零副作用
    wait = [e for e in events if e["type"] == "wait"]
    assert len(wait) == 1 and wait[0]["call_id"] == "c1"
    assert wait[0]["tool"] == "write"
    assert wait[0]["action"] == "写入项目目录外的文件"


@pytest.mark.xfail(strict=True, reason="wait 事件要到 Task 4 从 __interrupt__ 折出来；Task 4 Step 4 删掉本行")
def test_two_parallel_calls_execute_exactly_once_each(tmp_path: Path) -> None:
    """P5 否决形态的正面锁：两个都批 → 真执行恰为两次，gate 重跑不重复副作用。"""
    counter = _Counting()
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("a.md")), ("c2", "write", _outside("b.md"))),
        AIMessage(content="两个都写了"),
    ])
    ctx = _gate_ctx(tmp_path, "strict")
    args = dict(build=build_agent_graph, provider=provider,
                tools=_project_tools(tmp_path, counter),
                messages=[HumanMessage(content="并行写两个")], thread_id="r2", gate=ctx)
    first = list(stream_graph(**args))
    assert [e["call_id"] for e in first if e["type"] == "wait"] == ["c1"]
    assert counter.writes == []                                  # 批第一条之后仍未开跑（spec 风险节）
    second = list(stream_graph(resume=decision_from({"decision": APPROVE}), **args))
    assert [e["call_id"] for e in second if e["type"] == "wait"] == ["c2"]
    assert counter.writes == []
    third = list(stream_graph(resume=decision_from({"decision": APPROVE}), **args))
    assert counter.writes == [str(resolve_path(str(tmp_path), "a.md")),
                              str(resolve_path(str(tmp_path), "b.md"))]        # 各恰好一次
    assert third[-1]["reply"] == "两个都写了"


def test_reject_keeps_the_round_alive_and_asks_the_model(tmp_path: Path) -> None:
    """裁定 3：拒绝单条、本轮继续。被拒的不执行，模型收到中文拒绝结果后接着作答。"""
    counter = _Counting()
    provider = ScriptProvider([_calls(("c1", "write", _outside())), AIMessage(content="好的，我不写了")])
    args = dict(build=build_agent_graph, provider=provider,
                tools=_project_tools(tmp_path, counter),
                messages=[HumanMessage(content="写文件")], thread_id="r3",
                gate=_gate_ctx(tmp_path, "strict"))
    list(stream_graph(**args))
    out = list(stream_graph(resume=decision_from({"decision": REJECT}), **args))
    assert counter.writes == []
    assert out[-1]["reply"] == "好的，我不写了"
    assert out[-1]["tool_traces"] == []                          # R9：拒绝不进过程行


def test_rejected_call_is_rewritten_out_of_the_pending_list(tmp_path: Path) -> None:
    """P6 的两条硬形状：同 id 覆盖那条 AIMessage（tool_calls 只剩批准的）；合成拒绝三字段对齐。"""
    from langchain_core.messages import ToolMessage
    counter = _Counting()
    # 一条批准 + 一条拒绝：批准的 c2 真跑，拒绝的 c1 被剔出清单并收到 error
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("bad.md")), ("c2", "write", _outside("good.md"))),
        AIMessage(content="一个写了一个没写"),
    ])
    ctx = _gate_ctx(tmp_path, "strict")
    tools = _project_tools(tmp_path, counter)
    # 另装一个图只为了读状态：checkpointer 是进程级单例（Task 2），同 thread_id 读得到同一份
    graph = build_agent_graph(provider, tools)
    args = dict(build=build_agent_graph, provider=provider, tools=tools,
                messages=[HumanMessage(content="并行两个")], thread_id="r7", gate=ctx)
    list(stream_graph(**args))                                   # 挂起在 c1
    list(stream_graph(resume=decision_from({"decision": REJECT}), **args))    # 拒 c1 → 挂起在 c2
    list(stream_graph(resume=decision_from({"decision": APPROVE}), **args))   # 批 c2 → 执行
    msgs = _thread_state(graph, "r7").values["messages"]
    original = msgs[1]                                           # 脚本第一轮那条 AIMessage
    assert isinstance(original, AIMessage)
    rewritten = [m for m in msgs if isinstance(m, AIMessage) and m.id == original.id]
    assert len(rewritten) == 1                                   # 同 id 覆盖，不是新追加一条
    assert [c["id"] for c in rewritten[0].tool_calls] == ["c2"]  # 只留批准的
    # 批准的 c2 执行后也有 ToolMessage（成功结果）：合成拒绝按 status=="error" 筛，
    # 与 spec 测试 3「拒绝的形状」的判据一致。
    rejected = [m for m in msgs if isinstance(m, ToolMessage) and m.status == "error"]
    assert [m.tool_call_id for m in rejected] == ["c1"]
    assert rejected[0].status == "error" and rejected[0].name == "write"
    assert rejected[0].content.startswith("用户拒绝了此操作：")
    assert counter.writes == [str(resolve_path(str(tmp_path), "good.md"))]


def test_remember_lands_only_after_the_whole_loop(tmp_path: Path) -> None:
    """位置匹配的下标钉死：中途落记住表会让后一条继承前一条的决策（Critical 回归锁）。"""
    from langchain_core.messages import ToolMessage
    counter = _Counting()
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("a.md")), ("c2", "write", _outside("b.md"))),
        AIMessage(content="一个记住一个拒绝"),
    ])
    ctx = _gate_ctx(tmp_path, "strict")
    tools = _project_tools(tmp_path, counter)
    graph = build_agent_graph(provider, tools)
    args = dict(build=build_agent_graph, provider=provider, tools=tools,
                messages=[HumanMessage(content="并行两个")], thread_id="r9", gate=ctx)
    list(stream_graph(**args))                              # 挂起在 c1
    list(stream_graph(resume=decision_from({"decision": APPROVE, "remember": True}), **args))
    assert counter.writes == []                              # 批 c1 并记住 → 挂起在 c2，一条都没执行
    list(stream_graph(resume=decision_from({"decision": REJECT}), **args))
    msgs = _thread_state(graph, "r9").values["messages"]
    rejected = [m for m in msgs if isinstance(m, ToolMessage) and m.status == "error"]
    assert [m.tool_call_id for m in rejected] == ["c2"]      # 拒绝必须落在 c2 自己头上
    assert counter.writes == [str(resolve_path(str(tmp_path), "a.md"))]   # b.md 一个字都没写
    assert ctx.remembered == {plan_target("write", _outside("a.md"),
                                          ctx.project_dir).remember_key}


def test_all_rejected_routes_back_to_agent(tmp_path: Path) -> None:
    """全拒不经过 tools 节点：断言路由函数本身，不吃 ToolNode 拿到空清单时「恰好不报错」的巧合。"""
    from aitester.orchestration.agent_graph import route_after_gate
    assert route_after_gate({"messages": [AIMessage(content="", tool_calls=[])]}) == "agent"
    call = {"id": "c1", "name": "write", "args": {}, "type": "tool_call"}
    assert route_after_gate({"messages": [AIMessage(content="", tool_calls=[call])]}) == "tools"


def test_remembered_set_reaps_the_next_identical_call(tmp_path: Path) -> None:
    """裁定 4：勾选记住后同一路径不再问，且记住的是规范化后的绝对路径"""
    counter = _Counting()
    remembered: set[str] = set()
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("a.md"))),
        _calls(("c2", "write", _outside("a.md"))),
        AIMessage(content="好了"),
    ])
    ctx = _gate_ctx(tmp_path, "strict", remembered)
    tools = _project_tools(tmp_path, counter)
    list(stream_graph(build_agent_graph, provider, tools,
                      [HumanMessage(content="先写")], thread_id="r4a", gate=ctx))
    list(stream_graph(build_agent_graph, provider, tools, [HumanMessage(content="先写")],
                      thread_id="r4a", gate=ctx,
                      resume=decision_from({"decision": APPROVE, "remember": True})))
    assert remembered == {f"write|{resolve_path(str(tmp_path / 'proj'), '../a.md')}"}
    tail = list(stream_graph(build_agent_graph, provider, tools,
                             [HumanMessage(content="再写同一文件")], thread_id="r4b", gate=ctx))
    assert [e["type"] for e in tail].count("wait") == 0          # 新线程也不问了
    assert counter.writes.count(str(resolve_path(str(tmp_path), "a.md"))) == 2


def test_decision_from_rejects_garbage(tmp_path: Path) -> None:
    from aitester.orchestration.gate import GateAuthError
    assert decision_from({"decision": "approve", "remember": None}) == {"decision": "approve", "remember": False}
    for bad in (None, "approve", {"decision": "yes"}, {}):
        with pytest.raises(GateAuthError):
            decision_from(bad)
