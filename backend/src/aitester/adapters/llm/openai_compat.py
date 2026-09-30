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
    ) -> None:
        self.name = name
        self.model_ref = f"{name}/{model}"
        self._api_key = api_key
        self._client = ChatOpenAI(
            model=model, api_key=api_key, base_url=base_url, timeout=timeout
        )

    def complete(self, messages: list[dict[str, str]]) -> str:
        try:
            result = self._client.invoke(messages)
        except Exception as exc:
            raw = f"调用 {self.model_ref} 失败: {exc}".replace(self._api_key, "***")
            raise ProviderError(raw) from exc
        return str(result.content)
