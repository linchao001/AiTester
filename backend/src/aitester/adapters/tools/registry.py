"""工具注册表：tool_id → AiTooler 实例，按能力配置装配给智能体。"""

from __future__ import annotations

from aitester.adapters.tools.base import AiTooler


class ToolRegistry:
    """全局工具注册表，维护 tool_id 到工具实例的映射。"""

    def __init__(self) -> None:
        self._tools: dict[str, AiTooler] = {}

    def register(self, tool: AiTooler) -> None:
        self._tools[tool.tool_id()] = tool

    def get(self, tool_id: str) -> AiTooler | None:
        return self._tools.get(tool_id)

    def get_many(self, tool_ids: list[str]) -> list[AiTooler]:
        return [t for tid in tool_ids if (t := self.get(tid)) is not None]

    def all_ids(self) -> list[str]:
        return list(self._tools.keys())

    def as_langchain_tools(self) -> list[AiTooler]:
        return list(self._tools.values())
