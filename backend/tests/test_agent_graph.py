"""Agent loop 测试：脚本化 provider 驱动真实工具执行与错误回传。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from aitester.adapters.llm import MockProvider
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools.observation import FileObservationStore
from aitester.agents import AGENT_CATALOG
from aitester.orchestration import (
    GRAPH_BUILDERS,
    build_agent_graph,
    get_graph_builder,
    run_agent,
    run_graph,
)


class ScriptedProvider:
    """按剧本逐条返回 AIMessage：先工具调用，后最终回复。"""

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def bind_tools(self, tools: list[Any]) -> ScriptedProvider:
        return self

    def invoke_messages(self, messages: list[BaseMessage]) -> AIMessage:
        if self._script:
            return self._script.pop(0)
        return AIMessage(content="done")


def _tools(tmp_path: Path):
    registry = build_default_registry(
        cwd=str(tmp_path), session_id="s1", observed=FileObservationStore()
    )
    return registry.as_langchain_tools()


def test_agent_loop_executes_tool_and_replies(tmp_path: Path) -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write",
                    "args": {"file_path": "out.txt", "content": "done!"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="文件已写入"),
    ]
    result = run_agent(
        ScriptedProvider(script), _tools(tmp_path), [HumanMessage(content="写文件")]
    )
    assert result["reply"] == "文件已写入"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "done!"
    assert len(result["tool_traces"]) == 1
    assert result["tool_traces"][0]["tool"] == "write"
    assert "Created file" in result["tool_traces"][0]["result"]


def test_agent_loop_surfaces_tool_errors(tmp_path: Path) -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read",
                    "args": {"file_path": "missing.txt"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="文件不存在，无法读取"),
    ]
    result = run_agent(
        ScriptedProvider(script), _tools(tmp_path), [HumanMessage(content="读文件")]
    )
    assert result["reply"] == "文件不存在，无法读取"
    assert "not found" in result["tool_traces"][0]["result"]


def test_agent_loop_surfaces_shell_guard_error(tmp_path: Path) -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "pwsh",
                    "args": {"command": "taskkill /F /IM python.exe"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="该命令会杀掉后端，已放弃"),
    ]
    result = run_agent(
        ScriptedProvider(script), _tools(tmp_path), [HumanMessage(content="清理进程")]
    )
    assert result["reply"] == "该命令会杀掉后端，已放弃"
    assert result["tool_traces"][0]["tool"] == "pwsh"
    assert "Blocked" in result["tool_traces"][0]["result"]


def test_agent_loop_executes_grep_search(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("alpha\nP0 用例缺失\n", encoding="utf-8")
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "grep_search",
                    "args": {"pattern": "P0"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="找到一处 P0 标记"),
    ]
    result = run_agent(
        ScriptedProvider(script), _tools(tmp_path), [HumanMessage(content="找 P0")]
    )
    assert result["reply"] == "找到一处 P0 标记"
    assert len(result["tool_traces"]) == 1
    assert result["tool_traces"][0]["tool"] == "grep_search"
    assert "notes.md:2:> P0 用例缺失" in result["tool_traces"][0]["result"]


def test_agent_loop_multiple_tool_rounds(tmp_path: Path) -> None:
    script = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write",
                    "args": {"file_path": "a.txt", "content": "v1"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "edit",
                    "args": {"file_path": "a.txt", "old_string": "v1", "new_string": "v2"},
                    "id": "c2",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="两步完成"),
    ]
    result = run_agent(
        ScriptedProvider(script), _tools(tmp_path), [HumanMessage(content="写并改")]
    )
    assert result["reply"] == "两步完成"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v2"
    names = [trace["tool"] for trace in result["tool_traces"]]
    assert names == ["write", "edit"]


def test_registry_resolves_react_to_build_agent_graph() -> None:
    assert get_graph_builder("react") is build_agent_graph


def test_registry_rejects_unregistered_builder_name() -> None:
    with pytest.raises(ValueError) as exc_info:
        get_graph_builder("plan_execute")
    assert "未注册" in str(exc_info.value)


def test_every_catalog_agent_has_a_registered_builder() -> None:
    for spec in AGENT_CATALOG:
        assert GRAPH_BUILDERS[spec.graph_builder] is get_graph_builder(spec.graph_builder)


def test_graph_without_tools_answers_in_single_node() -> None:
    result = run_graph(build_agent_graph, MockProvider(), [], [HumanMessage(content="生成用例")])
    assert result["reply"] == "[mock] 生成用例"
    assert result["tool_traces"] == []


def test_run_graph_on_react_matches_run_agent(tmp_path: Path) -> None:
    def script() -> list[AIMessage]:
        return [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write",
                        "args": {"file_path": "h.txt", "content": "v"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已写入"),
        ]

    tools = _tools(tmp_path)
    by_helper = run_agent(ScriptedProvider(script()), tools, [HumanMessage(content="写")])
    by_graph = run_graph(
        get_graph_builder("react"),
        ScriptedProvider(script()),
        tools,
        [HumanMessage(content="写")],
    )
    assert by_graph["reply"] == by_helper["reply"] == "已写入"
    assert [t["tool"] for t in by_graph["tool_traces"]] == ["write"]
