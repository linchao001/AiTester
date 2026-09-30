from aitester.adapters.llm.base import LlmProvider
from aitester.adapters.llm.errors import ProviderConfigError
from aitester.adapters.llm.mock import MockProvider
from aitester.adapters.llm.openai_compat import OpenAICompatProvider
from aitester.config import Settings


def build_provider(settings: Settings) -> LlmProvider:
    provider = settings.llm_provider.strip().lower()
    if provider == "mock":
        return MockProvider()
    if provider == "deepseek":
        if not settings.deepseek_api_key.strip():
            raise ProviderConfigError(
                "尚未配置 DeepSeek API Key：请在 backend/.env 中设置 DEEPSEEK_API_KEY"
                "（可在 https://platform.deepseek.com 申请）"
            )
        return OpenAICompatProvider(
            name="deepseek",
            api_key=settings.deepseek_api_key.strip(),
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
    if provider == "dashscope":
        if not settings.dashscope_api_key.strip():
            raise ProviderConfigError(
                "尚未配置通义千问 API Key：请在 backend/.env 中设置 DASHSCOPE_API_KEY"
                "（可在 https://bailian.console.aliyun.com 申请）"
            )
        return OpenAICompatProvider(
            name="dashscope",
            api_key=settings.dashscope_api_key.strip(),
            base_url=settings.dashscope_base_url,
            model=settings.dashscope_model,
        )
    raise ProviderConfigError(
        f"未知的 LLM_PROVIDER「{settings.llm_provider}」，合法值：mock / deepseek / dashscope"
    )
