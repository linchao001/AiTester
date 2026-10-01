"""模型侧稳定诊断：命令执行工具的入参校验、环境与守卫拒绝文案。

模型可见文案一律英文（对齐 QwenPaw 与业界主流工具定义），面向用户的 UI 文案不在此列。
"""

from typing import NoReturn

from langchain_core.tools import ToolException


def raise_blank_command() -> NoReturn:
    raise ToolException("command must be a non-empty string")


def raise_invalid_timeout() -> NoReturn:
    raise ToolException("timeout must be a positive number of seconds")


def raise_timeout_over_cap(cap: float) -> NoReturn:
    raise ToolException(f"timeout must be less than or equal to {cap:g} seconds")


def raise_cwd_not_found(display_cwd: str) -> NoReturn:
    raise ToolException(f'working directory "{display_cwd}" does not exist')


def raise_cwd_not_dir(display_cwd: str) -> NoReturn:
    raise ToolException(f'working directory "{display_cwd}" is not a directory')


def raise_shell_not_found(names: str) -> NoReturn:
    raise ToolException(
        f"no usable shell executable found (tried: {names}); "
        "install one and make sure it is on PATH"
    )


def raise_self_kill_blocked() -> NoReturn:
    raise ToolException(
        "Blocked: this command would terminate the AiTester process or its parent. "
        "Refusing to execute."
    )


def raise_spawn_failed(reason: object) -> NoReturn:
    raise ToolException(f"failed to start the command: {reason}")
