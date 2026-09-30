from aitester.adapters.llm import MockProvider
from aitester.orchestration import run_echo


def test_run_echo_returns_provider_reply() -> None:
    messages = [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "hello"},
    ]
    assert run_echo(MockProvider(), messages) == "[mock] hello"
