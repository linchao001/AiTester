from __future__ import annotations

from typing import Any, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, get_buffer_string
from langchain_openai import ChatOpenAI

from aitester.adapters.llm.errors import ProviderError


class OpenAICompatProvider:
    """OpenAI 兼容协议（DeepSeek / DashScope 兼容模式等）的真实调用适配器。"""

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 60,
        _client: ChatOpenAI | None = None,
    ) -> None:
        self.name = name
        self.model_ref = f"{name}/{model}"
        self._api_key = api_key
        self._client = _client or ChatOpenAI(
            model=model, api_key=api_key, base_url=base_url, timeout=timeout
        )

    def complete(self, messages: list[dict[str, str]]) -> str:
        try:
            result = self._client.invoke(messages)
        except Exception as exc:
            raw = f"调用 {self.model_ref} 失败: {exc}".replace(self._api_key, "***")
            raise ProviderError(raw) from exc
        return str(result.content)

    def bind_tools(self, tools: list[Any]) -> OpenAICompatProvider:
        bound_client = self._client.bind_tools(tools)
        return OpenAICompatProvider(
            name=self.name,
            api_key=self._api_key,
            base_url="",
            model="",
            _client=bound_client,
        )

    def invoke_messages(self, messages: list[Any]) -> AIMessage:
        try:
            result = self._client.invoke(messages)
        except Exception as exc:
            raw = f"调用 {self.model_ref} 失败: {exc}".replace(self._api_key, "***")
            raise ProviderError(raw) from exc
        if not isinstance(result, AIMessage):
            return AIMessage(content=str(result.content))
        return result

    def stream_messages(self, messages: list[Any]) -> Iterator[AIMessageChunk]:
        # 失败包法与 invoke_messages 逐字相同：同一条「调用 {model_ref} 失败: …」+ key 打星
        try:
            for chunk in self._client.stream(messages):
                yield chunk
        except Exception as exc:
            raw = f"调用 {self.model_ref} 失败: {exc}".replace(self._api_key, "***")
            raise ProviderError(raw) from exc
