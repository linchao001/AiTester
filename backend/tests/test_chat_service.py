import pytest

import pytest

from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.services import ChatService
from aitester.services import chat as chat_module


def test_echo_full_chain_trace_and_reply() -> None:
    svc = ChatService()
    result = svc.echo("s1", "生成登录用例")
    assert result["reply"] == "[mock] 生成登录用例"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_echo_remembers_previous_turn() -> None:
    svc = ChatService()
    svc.echo("s1", "第一句")
    svc.echo("s1", "第二句")
    history = svc.memory.recall("s1")
    assert [m["content"] for m in history] == ["第一句", "[mock] 第一句", "第二句", "[mock] 第二句"]
    assert svc.repo.get("session:s1") == {"session_id": "s1", "last_reply": "[mock] 第二句"}


def test_send_uses_injected_provider_and_reports_model() -> None:
    svc = ChatService(provider=MockProvider())
    result = svc.send("s1", "生成用例")
    assert result["reply"] == "[mock] 生成用例"
    assert result["model"] == "mock/mock"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_send_builds_provider_at_call_time_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Settings] = []
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(_env_file=None))

    def fake_build(settings: Settings) -> MockProvider:
        seen.append(settings)
        return MockProvider()

    monkeypatch.setattr(chat_module, "build_provider", fake_build)
    result = ChatService().send("s1", "hello")
    assert result["reply"] == "[mock] hello"
    assert len(seen) == 1
