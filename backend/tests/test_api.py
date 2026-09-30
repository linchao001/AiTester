import json
from pathlib import Path

from fastapi.testclient import TestClient

from aitester.adapters.llm import MockProvider, ProviderError
from aitester.config import Settings
from aitester.main import app, create_app
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


def _isolated_client(
    tmp_path: Path,
    name: str = "model_config.json",
    cap_name: str = "capability_config.json",
) -> TestClient:
    application = create_app(
        model_config_path=tmp_path / name,
        capability_config_path=tmp_path / cap_name,
        settings=Settings(_env_file=None),
    )
    return TestClient(application)


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "aitester-backend"
    assert isinstance(body["llm_provider"], str) and body["llm_provider"]


def test_health_reports_mock_when_no_default_configured(tmp_path: Path) -> None:
    assert _isolated_client(tmp_path).get("/api/health").json()["llm_provider"] == "mock"


def test_chat_echo_traverses_all_seven_layers() -> None:
    resp = client.post("/api/chat/echo", json={"session_id": "s1", "message": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] hello"
    assert body["trace"] == ALL_LAYERS


def test_chat_echo_rejects_empty_message() -> None:
    resp = client.post("/api/chat/echo", json={"message": ""})
    assert resp.status_code == 422


def test_send_uses_injected_provider_and_reports_model(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(provider=MockProvider())
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s2", "message": "生成用例"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] 生成用例"
    assert body["trace"] == ALL_LAYERS
    assert body["model"] == "mock/mock"


def test_send_without_configured_default_returns_400(tmp_path: Path) -> None:
    resp = _isolated_client(tmp_path).post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 400
    assert "设置 · 模型设置" in resp.json()["detail"]


def test_send_upstream_failure_returns_502(tmp_path: Path) -> None:
    class FailingProvider:
        name = "fake"
        model_ref = "fake/model-x"

        def complete(self, messages: list[dict[str, str]]) -> str:
            raise ProviderError("调用 fake/model-x 失败: HTTP 401")

    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(provider=FailingProvider())
    resp = TestClient(application).post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 502
    assert "fake/model-x" in resp.json()["detail"]


def test_chat_service_is_per_app_instance(tmp_path: Path) -> None:
    app_a = create_app(
        model_config_path=tmp_path / "a.json",
        capability_config_path=tmp_path / "a.cap.json",
        settings=Settings(_env_file=None),
    )
    app_b = create_app(
        model_config_path=tmp_path / "b.json",
        capability_config_path=tmp_path / "b.cap.json",
        settings=Settings(_env_file=None),
    )
    client_a, client_b = TestClient(app_a), TestClient(app_b)
    resp = client_a.post("/api/chat/echo", json={"session_id": "iso", "message": "hello"})
    assert resp.status_code == 200
    resp_b = client_b.post("/api/chat/echo", json={"session_id": "other", "message": "hi"})
    assert resp_b.status_code == 200
    assert app_a.state.chat_service.memory.recall("iso")
    assert app_b.state.chat_service.memory.recall("iso") == []


def test_models_view_masks_key(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    resp = c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    assert resp.status_code == 200
    body = c.get("/api/models").json()
    assert "sk-SECRET123456" not in json.dumps(body, ensure_ascii=False)
    provider = body["providers"][0]
    assert provider["has_key"] is True
    assert provider["key_masked"] == "sk-S…3456"
    assert body["default_uid"] == ""


def test_set_default_flow_updates_health(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    resp = c.put("/api/models/default", json={"uid": "deepseek/deepseek-flash"})
    assert resp.status_code == 400
    assert "设置 · 模型设置" in resp.json()["detail"]
    c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    assert c.put("/api/models/default", json={"uid": "nope/x"}).status_code == 404
    assert c.put("/api/models/default", json={"uid": "garbage"}).status_code == 400
    ok = c.put("/api/models/default", json={"uid": "deepseek/deepseek-flash"})
    assert ok.status_code == 200
    assert ok.json()["default_uid"] == "deepseek/deepseek-flash"
    assert c.get("/api/health").json()["llm_provider"] == "deepseek/deepseek-flash"


def test_disable_default_model_cascades(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    c.put("/api/models/default", json={"uid": "deepseek/deepseek-flash"})
    resp = c.put(
        "/api/models/providers/deepseek/models/deepseek-flash/enabled",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["default_uid"] == "deepseek/deepseek-v4-pro"


def test_models_endpoints_404_for_unknown_provider(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    assert c.put("/api/models/providers/glm/key", json={"api_key": "k"}).status_code == 404
    assert (
        c.put("/api/models/providers/deepseek/models/nope/enabled", json={"enabled": True})
    ).status_code == 404
