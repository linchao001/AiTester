"""命令执行工具行为测试：对齐 QwenPaw shell 的退出码/流回收、cwd、上限与守卫契约。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.command_tools.shell import MAX_TIMEOUT

_BIG_STDOUT = "'x'*1200000"
_HUGE_STDOUT = "'x'*12000000"


@pytest.fixture
def registry(tmp_path: Path):
    return build_default_registry(cwd=str(tmp_path), session_id="s1")


def _shell(registry) -> object:
    """取本机可用的命令执行工具；都不可用则跳过。"""
    for tool_id in ("pwsh", "bash"):
        tool = registry.get(tool_id)
        try:
            tool._resolve_shell()
        except ToolException:
            continue
        return tool
    pytest.skip("本机没有可用的 pwsh / bash")


def _call(tool, args: dict, call_id: str = "call_1") -> ToolMessage:
    result = tool.invoke(
        {"name": tool.name, "args": args, "id": call_id, "type": "tool_call"}
    )
    assert isinstance(result, ToolMessage)
    return result


def _py(code: str) -> str:
    return f'python -c "{code}"'


def _snippets(tool) -> dict[str, str]:
    """按 shell 家族给出等价命令片段。"""
    if tool.name == "pwsh":
        return {
            "echo": 'Write-Output "hello"',
            "silent": "$null = 1",
            "stderr_ok": 'Write-Output "out"; [Console]::Error.WriteLine("warn")',
            "exit3": "exit 3",
            "pwd": "(Get-Location).Path",
            "multiline": "$x = 41\nWrite-Output ($x + 1)",
            "sleep": "Start-Sleep -Seconds 30",
        }
    return {
        "echo": "echo hello",
        "silent": "true",
        "stderr_ok": "echo out; echo warn 1>&2",
        "exit3": "exit 3",
        "pwd": "pwd",
        "multiline": "x=41\necho $((x+1))",
        "sleep": "sleep 30",
    }


# ---------- 正常路径 ----------


def test_success_returns_stdout_and_artifact(registry) -> None:
    tool = _shell(registry)
    cmds = _snippets(tool)
    msg = _call(tool, {"command": cmds["echo"]})
    assert "hello" in msg.content
    assert "[stderr]" not in msg.content
    assert msg.artifact["exitCode"] == 0
    assert msg.artifact["command"] == cmds["echo"]
    assert msg.artifact["cwd"] == str(Path(registry.get("read").cwd).resolve())
    assert msg.artifact["timedOut"] is False
    assert msg.artifact["durationMs"] >= 0


def test_success_without_output_reports_confirmation(registry) -> None:
    tool = _shell(registry)
    msg = _call(tool, {"command": _snippets(tool)["silent"]})
    assert "Command succeeded with no output." in msg.content


def test_stderr_is_reported_on_success(registry) -> None:
    tool = _shell(registry)
    msg = _call(tool, {"command": _snippets(tool)["stderr_ok"]})
    assert "out" in msg.content
    assert "[stderr]" in msg.content
    assert "warn" in msg.content
    assert msg.artifact["exitCode"] == 0
    assert "warn" in msg.artifact["stderr"]


def test_failure_reports_exit_code_and_streams(registry) -> None:
    tool = _shell(registry)
    cmds = _snippets(tool)
    msg = _call(tool, {"command": cmds["exit3"]})
    assert "Command failed with exit code 3." in msg.content
    assert msg.artifact["exitCode"] == 3


def test_multiline_command_is_preserved(registry) -> None:
    tool = _shell(registry)
    msg = _call(tool, {"command": _snippets(tool)["multiline"]})
    assert "42" in msg.content
    assert msg.artifact["exitCode"] == 0


def test_cwd_relative_and_absolute(registry, tmp_path: Path) -> None:
    tool = _shell(registry)
    sub = tmp_path / "sub"
    sub.mkdir()
    cmds = _snippets(tool)

    relative = _call(tool, {"command": cmds["pwd"], "cwd": "sub"})
    assert str(sub).lower() in relative.content.lower()
    assert relative.artifact["cwd"] == str(sub)

    absolute = _call(tool, {"command": cmds["pwd"], "cwd": str(sub)})
    assert str(sub).lower() in absolute.content.lower()


def test_cwd_missing_or_not_directory(registry, tmp_path: Path) -> None:
    tool = _shell(registry)
    with pytest.raises(ToolException, match="does not exist"):
        _call(tool, {"command": _snippets(tool)["echo"], "cwd": "nope"})

    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ToolException, match="is not a directory"):
        _call(tool, {"command": _snippets(tool)["echo"], "cwd": "a.txt"})


# ---------- 入参校验 ----------


def test_rejects_blank_command_and_bad_timeout(registry) -> None:
    tool = _shell(registry)
    with pytest.raises(ToolException, match="command must be a non-empty string"):
        _call(tool, {"command": "   "})
    with pytest.raises(ToolException, match="must be a positive number of seconds"):
        _call(tool, {"command": _snippets(tool)["echo"], "timeout": 0})
    with pytest.raises(ToolException, match=f"timeout must be less than or equal to {MAX_TIMEOUT:g} seconds"):
        _call(tool, {"command": _snippets(tool)["echo"], "timeout": MAX_TIMEOUT + 1})


# ---------- 自毁守卫 ----------


@pytest.mark.parametrize(
    "command",
    [
        "taskkill /F /IM python.exe",
        "taskkill /PID {pid} /F",
        "kill -9 $$",
        f"kill {os.getpid()}",
    ],
)
def test_self_kill_is_blocked(registry, command: str) -> None:
    tool = _shell(registry)
    with pytest.raises(ToolException, match="Blocked:"):
        _call(tool, {"command": command.format(pid=os.getpid())})


def test_innocent_commands_are_not_blocked(registry) -> None:
    tool = _shell(registry)
    cmds = _snippets(tool)
    assert _call(tool, {"command": cmds["pwd"]}).artifact["exitCode"] == 0
    message = "please do not kill python"
    assert _call(tool, {"command": _py(f"print('{message}')")}).artifact["exitCode"] == 0


# ---------- 超时与输出上限 ----------


def test_timeout_kills_and_reports(registry) -> None:
    tool = _shell(registry)
    started = time.monotonic()
    msg = _call(tool, {"command": _snippets(tool)["sleep"], "timeout": 1.0})
    elapsed = time.monotonic() - started
    assert "Command failed with exit code -1." in msg.content
    assert "exceeded the timeout of 1 seconds" in msg.content
    assert msg.artifact["timedOut"] is True
    assert elapsed < 15


def test_oversized_output_is_truncated_with_notice(registry) -> None:
    tool = _shell(registry)
    msg = _call(tool, {"command": _py(f"import sys;sys.stdout.write({_BIG_STDOUT})")})
    assert "Output truncated" in msg.content
    assert "Output truncated" in msg.artifact["stdout"]
    assert len(msg.artifact["stdout"]) < 1_200_000
    assert msg.artifact["exitCode"] == 0


def test_output_disk_cap_terminates_process_tree(registry) -> None:
    tool = _shell(registry)
    msg = _call(
        tool,
        {
            "command": _py(
                f"import sys,time;sys.stdout.write({_HUGE_STDOUT});"
                "sys.stdout.flush();time.sleep(30)"
            ),
            "timeout": 30.0,
        },
    )
    assert "Command output exceeded the disk cap" in msg.content
    assert msg.artifact["outputExceeded"] is True
    assert msg.artifact["timedOut"] is False
