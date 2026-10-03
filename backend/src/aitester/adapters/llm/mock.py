from __future__ import annotations

from typing import Any, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk

# 定长切片只为「条数可断言」，与真实 token 边界无关（spec 风险节登记）
MOCK_CHUNK_CHARS = 4


class MockProvider:
    name = "mock"
    model_ref = "mock/mock"

    def complete(self, messages: list[dict[str, str]]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "user":
                return f"[mock] {msg['content']}"
        return "[mock]"

    def bind_tools(self, tools: list[Any]) -> MockProvider:
        return MockProvider()

    def invoke_messages(self, messages: list[Any]) -> AIMessage:
        for msg in reversed(messages):
            content = getattr(msg, "content", "") or ""
            role = getattr(msg, "type", "")
            if role == "human":
                return AIMessage(content=f"[mock] {content}")
        return AIMessage(content="[mock]")

    def stream_messages(self, messages: list[Any]) -> Iterator[AIMessageChunk]:
        text = str(self.invoke_messages(messages).content or "")
        for i in range(0, len(text), MOCK_CHUNK_CHARS):
            yield AIMessageChunk(content=text[i:i + MOCK_CHUNK_CHARS])
