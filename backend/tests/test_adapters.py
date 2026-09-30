from aitester.adapters.llm import LlmProvider, MockProvider


def test_mock_provider_echoes_last_user_message() -> None:
    provider: LlmProvider = MockProvider()
    messages = [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "生成用例"},
        {"role": "assistant", "content": "上一轮回复"},
        {"role": "user", "content": "再来一条"},
    ]
    assert provider.name == "mock"
    assert provider.complete(messages) == "[mock] 再来一条"


def test_mock_provider_without_user_message() -> None:
    assert MockProvider().complete([{"role": "system", "content": "hi"}]) == "[mock]"
