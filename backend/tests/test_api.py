import pytest
from fastapi.testclient import TestClient

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError, ProviderError
from aitester.config import Settings
from aitester.main import app, create_app
from aitester.services import chat as chat_module
from aitester.services.chat import ChatService

client = TestClient(app)

ALL_LAYERS = [
    "interaction",
    "services",
    "context",
    "orchestration",
    "adapters",
    "memory",
    "storage",
]


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "aitester-backend"
    assert isinstance(body["llm_provider"], str) and body["llm_provider"]


def test_health_reports_current_llm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_module, "get_settings", lambda: Settings(llm_provider="deepseek", _env_file=None))
    body = client.get("/api/health").json()
    assert body["llm_provider"] == "deepseek"


def test_chat_echo_traverses_all_seven_layers() -> None:
    resp = client.post("/api/chat/echo", json={"session_id": "s1", "message": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] hello"
    assert body["trace"] == ALL_LAYERS


def test_chat_echo_rejects_empty_message() -> None:
    resp = client.post("/api/chat/echo", json={"message": ""})
    assert resp.status_code == 422


def test_send_uses_configured_provider_and_reports_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_module, "build_provider", lambda settings: MockProvider())
    resp = client.post("/api/chat/send", json={"session_id": "s2", "message": "生成用例"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] 生成用例"
    assert body["trace"] == ALL_LAYERS
    assert body["model"] == "mock/mock"


def test_send_config_error_returns_400_with_actionable_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_config(settings: Settings) -> LlmProvider:
        raise ProviderConfigError("尚未配置 DeepSeek API Key：请在 backend/.env 中设置 DEEPSEEK_API_KEY")

    monkeypatch.setattr(chat_module, "build_provider", raise_config)
    resp = client.post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 400
    assert "DEEPSEEK_API_KEY" in resp.json()["detail"]


def test_send_upstream_failure_returns_502() -> None:
    class FailingProvider:
        name = "fake"
        model_ref = "fake/model-x"

        def complete(self, messages: list[dict[str, str]]) -> str:
            raise ProviderError("调用 fake/model-x 失败: HTTP 401")

    app2 = create_app()
    app2.state.chat_service = ChatService(provider=FailingProvider())
    resp = TestClient(app2).post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 502
    assert "fake/model-x" in resp.json()["detail"]


def test_chat_service_is_per_app_instance() -> None:
    app_a, app_b = create_app(), create_app()
    client_a, client_b = TestClient(app_a), TestClient(app_b)
    resp = client_a.post("/api/chat/echo", json={"session_id": "iso", "message": "hello"})
    assert resp.status_code == 200
    resp_b = client_b.post("/api/chat/echo", json={"session_id": "other", "message": "hi"})
    assert resp_b.status_code == 200
    assert app_a.state.chat_service.memory.recall("iso")
    assert app_b.state.chat_service.memory.recall("iso") == []
