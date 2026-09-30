"""接入层-模型提供商适配。"""
from aitester.adapters.llm.base import LlmProvider
from aitester.adapters.llm.mock import MockProvider

__all__ = ["LlmProvider", "MockProvider"]
