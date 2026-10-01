"""命令执行工具：pwsh / bash，宿主执行语义对齐 QwenPaw ``agents/tools/shell.py``。

每个工具对应一种 shell 家族，由能力配置的工具 id（``pwsh`` / ``bash``）选择：
``pwsh`` 在 Windows 上优先 pwsh、回退 Windows PowerShell，``bash`` 走 POSIX shell。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.command_tools import errors, runner
from aitester.adapters.tools.command_tools.runner import CommandResult

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 600.0

_PWSH_CANDIDATES = ("pwsh", "pwsh.exe", "powershell", "powershell.exe")
_BASH_CANDIDATES = ("bash", "bash.exe", "/bin/bash", "/bin/sh", "sh")

_POWERSHELL_NAMES = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe"})
_CMD_NAMES = frozenset({"cmd", "cmd.exe"})

# 模型可能自己套一层 ``powershell -Command "…"``，运行时再套一层会引入引号歧义。
_PS_CMD_RE = re.compile(
    r"^(powershell(?:\.exe)?|pwsh(?:\.exe)?)"
    r"((?:\s+-(?:NoProfile|NonInteractive|NoLogo))*)"
    r"(?:\s+-ExecutionPolicy\s+\S+)?"
    r"\s+-Command\s+",
    re.IGNORECASE,
)


class ShellInput(BaseModel):
    command: str = Field(
        description="The command to run; chain multiple statements with ; or &&, newlines allowed."
    )
    timeout: float = Field(
        default=DEFAULT_TIMEOUT,
        description=f"Maximum seconds to run. Defaults to {DEFAULT_TIMEOUT:g}, maximum {MAX_TIMEOUT:g}.",
    )
    cwd: str | None = Field(
        default=None,
        description="Working directory (absolute, or relative to the current directory). Defaults to the current directory.",
    )


def _shell_basename(executable: str) -> str:
    """取小写 basename，兼容 / 与 \\ 两种分隔符。"""
    return executable.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _is_powershell(executable: str) -> bool:
    return _shell_basename(executable) in _POWERSHELL_NAMES


def _strip_powershell_wrapper(command: str) -> str:
    """剥掉命令里冗余的 powershell/pwsh -Command 外壳，只留内层脚本体。"""
    match = _PS_CMD_RE.match(command)
    if not match:
        return command
    inner = command[match.end() :]
    if len(inner) >= 2 and inner[0] == '"' and inner[-1] == '"':
        inner = inner[1:-1]
    return inner


def build_shell_argv(shell: str, command: str) -> list[str]:
    """按 shell 家族组装 argv：PowerShell 非交互执行，POSIX shell 走 -c。"""
    name = _shell_basename(shell)
    if name in _POWERSHELL_NAMES:
        return [shell, "-NoProfile", "-NonInteractive", "-Command", command]
    if name in _CMD_NAMES:
        return [shell, "/D", "/S", "/C", command]
    return [shell, "-c", command]


def format_shell_output(result: CommandResult) -> str:
    """命令执行结果文本：成功给 stdout，失败给退出码 + stdout/stderr。"""
    if result.exit_code == 0:
        text = result.stdout or "Command succeeded with no output."
        if result.stderr:
            text += f"\n[stderr]\n{result.stderr}"
        return text

    parts = [f"Command failed with exit code {result.exit_code}."]
    if result.stdout:
        parts.append(f"\n[stdout]\n{result.stdout}")
    if result.stderr:
        parts.append(f"\n[stderr]\n{result.stderr}")
    return "".join(parts)


class ShellTool(AiTooler):
    """命令执行工具公共实现：解析 shell、装配 cwd/env、执行并回收结果。"""

    cwd: str = "."
    args_schema: type[BaseModel] = ShellInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _candidates(self) -> tuple[str, ...]:
        raise NotImplementedError

    def find_shell(self) -> str | None:
        """按候选顺序解析可执行文件（绝对路径直接校验，裸名走 PATH）；找不到返回 None。

        可用性探测复用此方法，故不抛错。
        """
        for candidate in self._candidates():
            if os.path.isabs(candidate):
                if os.path.exists(candidate):
                    return candidate
                continue
            found = shutil.which(candidate)
            if found:
                return found
        return None

    def _resolve_shell(self) -> str:
        shell = self.find_shell()
        if shell is None:
            errors.raise_shell_not_found(", ".join(self._candidates()))
        return shell

    def _resolve_cwd(self, cwd: str | None) -> Path:
        if cwd is None or not str(cwd).strip():
            return Path(self.cwd).resolve()
        candidate = Path(cwd).expanduser()
        if not candidate.is_absolute():
            candidate = Path(self.cwd) / candidate
        candidate = candidate.resolve()
        if not candidate.exists():
            errors.raise_cwd_not_found(str(candidate))
        if not candidate.is_dir():
            errors.raise_cwd_not_dir(str(candidate))
        return candidate

    def _build_command(self, command: str) -> str:
        return command

    def _run(
        self, command: str, timeout: float = DEFAULT_TIMEOUT, cwd: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        command = (command or "").strip()
        if not command:
            errors.raise_blank_command()
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.raise_invalid_timeout()
        if timeout > MAX_TIMEOUT:
            errors.raise_timeout_over_cap(MAX_TIMEOUT)
        if runner.is_dangerous_self_kill(command):
            errors.raise_self_kill_blocked()

        working_dir = self._resolve_cwd(cwd)
        shell = self._resolve_shell()
        argv = build_shell_argv(shell, self._build_command(command))
        result = runner.run_command(
            argv, cwd=str(working_dir), timeout=float(timeout), env=runner.build_env()
        )

        artifact = {
            "command": command,
            "shell": shell,
            "cwd": str(working_dir),
            "exitCode": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timedOut": result.timed_out,
            "outputExceeded": result.output_exceeded,
            "durationMs": result.duration_ms,
        }
        return format_shell_output(result), artifact


class PwshTool(ShellTool):
    name: str = "pwsh"
    description: str = (
        "Run a PowerShell command and return stdout, stderr and the exit code. "
        "Use it to run tests, install dependencies and call CLIs. "
        "Every call is a fresh subprocess: `cd`, `$env:` and other settings do NOT persist, "
        'so chain them in one command (e.g. "cd D:/repo; pytest") or pass cwd explicitly. '
        "Do not start long-lived background processes; raise timeout for genuinely long tasks."
    )

    def _candidates(self) -> tuple[str, ...]:
        return _PWSH_CANDIDATES

    def _build_command(self, command: str) -> str:
        return _strip_powershell_wrapper(command)


class BashTool(ShellTool):
    name: str = "bash"
    description: str = (
        "Run a Bash command and return stdout, stderr and the exit code; "
        "same purpose as pwsh (used on macOS / Linux). "
        "Every call is a fresh subprocess: `cd`, `export`, `source` and other settings do NOT persist, "
        "so chain them in one command (e.g. `cd /repo && pytest`) or pass cwd explicitly. "
        "Do not start long-lived background processes; raise timeout for genuinely long tasks."
    )

    def _candidates(self) -> tuple[str, ...]:
        return _BASH_CANDIDATES
