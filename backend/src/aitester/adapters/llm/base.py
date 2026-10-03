from __future__ import annotations

from typing import Any, Iterator, Protocol

from langchain_core.messages import AIMessage, AIMessageChunk


class LlmProvider(Protocol):
    """模型提供商适配器协议（对齐 provider 化模型配置方向）。"""

    name: str
    model_ref: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...

    def bind_tools(self, tools: list[Any]) -> LlmProvider: ...

    def invoke_messages(self, messages: list[Any]) -> AIMessage: ...

    # 流式通道：节点体逐 chunk 消费。实现者不接受该形参即断链（与 memory.save 的 steps 同口径）
    def stream_messages(self, messages: list[Any]) -> Iterator[AIMessageChunk]: ...
