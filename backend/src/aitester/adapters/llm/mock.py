from __future__ import annotations

from typing import Any, Iterator, Sequence

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

# 定长切片只为「条数可断言」，与真实 token 边界无关（spec 风险节登记）
MOCK_CHUNK_CHARS = 4


class MockChatModel(BaseChatModel):
    """create_agent 路径用的可流式假聊天模型：语义与 MockProvider 对齐——回显最近一句 human。

    create_agent 会 `model.bind_tools(tools)` 再流式驱动；FakeListChatModel 之类通用 fake
    要么不支持 bind_tools，要么空剧本触发「No generations found in stream」。这里自带
    bind_tools（原样返回自身，不真调工具，故一轮即收尾）与逐片 _stream，无 API key 的 mock
    兜底与单测都能跑通 create_agent 全链路。
    """

    @property
    def _llm_type(self) -> str:
        return "mock"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "MockChatModel":
        return self

    @staticmethod
    def _last_human(messages: Sequence[BaseMessage]) -> str:
        for msg in reversed(list(messages)):
            if getattr(msg, "type", "") == "human":
                return str(getattr(msg, "content", "") or "")
        return ""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = f"[mock] {self._last_human(messages)}"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        text = f"[mock] {self._last_human(messages)}"
        for i in range(0, len(text), MOCK_CHUNK_CHARS):
            chunk = ChatGenerationChunk(
                message=AIMessageChunk(content=text[i:i + MOCK_CHUNK_CHARS])
            )
            if run_manager:
                run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            yield chunk


class MockProvider:
    name = "mock"
    model_ref = "mock/mock"

    @property
    def base_chat_model(self) -> MockChatModel:
        """供 create_agent 路径使用：可流式、自带 bind_tools 的假聊天模型。"""
        return MockChatModel()

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
