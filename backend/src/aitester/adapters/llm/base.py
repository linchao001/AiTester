from __future__ import annotations

from typing import Any, Protocol

from langchain_core.messages import AIMessage


class LlmProvider(Protocol):
    """模型提供商适配器协议（对齐 provider 化模型配置方向）。"""

    name: str
    model_ref: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...

    def bind_tools(self, tools: list[Any]) -> LlmProvider: ...

    def invoke_messages(self, messages: list[Any]) -> AIMessage: ...
