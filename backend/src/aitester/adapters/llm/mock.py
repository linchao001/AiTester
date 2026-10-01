from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


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
