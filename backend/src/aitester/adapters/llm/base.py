from typing import Protocol


class LlmProvider(Protocol):
    """模型提供商适配器协议（对齐 provider 化模型配置方向）。"""

    name: str
    model_ref: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...
