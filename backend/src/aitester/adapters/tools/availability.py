"""工具可用性探测：让能力配置里的工具与「实现有没有、本机跑不跑得起来」一致。

当前唯一判据：shell 类工具能在本机解析到可执行文件。可用性只影响配置界面与
携带校验，真正调用时仍由工具自身报错兜底（如 web_search 的网络失败）。
"""

from __future__ import annotations

from aitester.adapters.tools.command_tools.shell import BashTool, PwshTool

_SHELL_PROBES: dict[str, PwshTool | BashTool] = {
    "pwsh": PwshTool(cwd="."),
    "bash": BashTool(cwd="."),
}


def unavailable_reason(tool_id: str) -> str | None:
    """返回工具在当机不可用的原因；可用（含无外部依赖的文件工具）返回 None。"""
    probe = _SHELL_PROBES.get(tool_id)
    if probe is None:
        return None
    if probe.find_shell() is None:
        return f"本机未找到 {probe.name} 可执行文件"
    return None
