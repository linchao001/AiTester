"""Agent loop 测试：脚本化 provider 驱动真实工具执行与错误回传。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools.observation import FileObservationStore
from aitester.orchestration import run_agent


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
