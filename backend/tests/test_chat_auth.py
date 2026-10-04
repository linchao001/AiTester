"""第 5 片边界执法：判定矩阵、gate 决策重放、wait 折叠（按任务递进追加）。

判别性要求（spec 测试 1）：三档 × 四类别 × 界内界外 × 记住命中/未命中逐格钉死，
`free` 全 False 单独钉——默认档零行为是本片的红线。
"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from aitester.adapters.llm import MockProvider
from aitester.adapters.tools.file_tools.fs_tool import resolve_path
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
from streaming_fakes import ChunkedStreamMixin


@pytest.fixture
def project(tmp_path: Path) -> str:
    root = tmp_path / "proj"
    root.mkdir()
    return str(resolve_path(str(root), "."))


def _file(path: str) -> dict:
    return {"file_path": path, "content": "x"}


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
    assert len(_thread_state(graph, "runB").values["messages"]) == 2 # 另一条线程不受牵连


def test_threadless_callers_still_work() -> None:
    """带 checkpointer 后缺 thread_id 会 ValueError（实测）：历史入口必须自造线程而不是炸。"""
    events = list(stream_graph(build_agent_graph, MockProvider(), [],
                               [HumanMessage(content="生成用例")]))
    assert events[-1]["type"] == "finish"
