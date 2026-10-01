import json
from pathlib import Path

from fastapi.testclient import TestClient

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderError
from aitester.adapters.llm.probe import ProbeError
from aitester.config import Settings
from aitester.main import app, create_app
from aitester.services import model_config
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.chat import ChatService
from langchain_core.messages import AIMessage

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
    assert isinstance(application.state.agent_runtime, AgentRuntime)
    application.state.chat_service = ChatService(
        provider=MockProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s2", "message": "生成用例"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] 生成用例"
    assert body["trace"] == ALL_LAYERS
    assert body["model"] == "mock/mock"


def test_send_with_unknown_agent_returns_404(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=MockProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s3", "message": "hi", "agent_id": "ghost"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "未知智能体「ghost」"


def test_send_with_legacy_agent_id_returns_404(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=MockProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"message": "hi", "agent_id": "a1"}
    )
    assert resp.status_code == 404
    assert "未知智能体「a1」" in resp.json()["detail"]


def test_send_uses_agent_prompt_and_default_agent_id(tmp_path: Path) -> None:
    seen: list[list[object]] = []

    class _SpyProvider:
        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            return "[spy]"

        def bind_tools(self, tools: list) -> "_SpyProvider":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=_SpyProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s4", "message": "生成登录用例"}
    )
    assert resp.status_code == 200
    # 请求体不带 agent_id 时取 SendRequest 默认值 case_design；系统提示词来自 md
    assert str(seen[0][0].content) == find_agent("case_design").prompt
    assert resp.json()["reply"] == "[spy] 收到"


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

        def bind_tools(self, tools: list) -> "FailingProvider":
            return FailingProvider()

        def invoke_messages(self, messages: list) -> object:
            raise ProviderError("调用 fake/model-x 失败: HTTP 401")

    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=FailingProvider(), agent_runtime=application.state.agent_runtime
    )
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


def _stub_probe(monkeypatch, *, latency: int = 42, error: str | None = None) -> list:
    calls: list[dict] = []

    def fake_probe_model(**kwargs: object) -> int:
        calls.append(kwargs)
        if error is not None:
            raise ProbeError(error)
        return latency

    monkeypatch.setattr(model_config, "probe_model", fake_probe_model)
    return calls


def test_provider_test_endpoint_reports_success_latency(tmp_path: Path, monkeypatch) -> None:
    calls = _stub_probe(monkeypatch, latency=42)
    c = _isolated_client(tmp_path)
    c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    resp = c.post("/api/models/providers/deepseek/test", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["latency_ms"] == 42
    assert calls[0]["model"] == "deepseek-flash"


def test_provider_test_endpoint_draft_key_is_not_persisted(tmp_path: Path, monkeypatch) -> None:
    calls = _stub_probe(monkeypatch)
    c = _isolated_client(tmp_path)
    resp = c.post("/api/models/providers/deepseek/test", json={"api_key": "sk-DRAFT123456"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls[0]["api_key"] == "sk-DRAFT123456"
    body = c.get("/api/models").json()
    assert body["providers"][0]["has_key"] is False
    assert "sk-DRAFT123456" not in json.dumps(body, ensure_ascii=False)


def test_provider_test_endpoint_returns_200_with_failure_copy(
    tmp_path: Path, monkeypatch
) -> None:
    _stub_probe(monkeypatch, error="地址不可达，请检查 Base URL 与网络")
    c = _isolated_client(tmp_path)
    c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    resp = c.post("/api/models/providers/deepseek/test", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "地址不可达" in resp.json()["reason"]


def test_provider_test_endpoint_without_key_fails_without_requesting(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _stub_probe(monkeypatch)
    c = _isolated_client(tmp_path)
    resp = c.post("/api/models/providers/deepseek/test", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "未配置 API Key" in resp.json()["reason"]
    assert calls == []


def test_provider_test_endpoint_404_for_unknown_provider(tmp_path: Path, monkeypatch) -> None:
    _stub_probe(monkeypatch)
    c = _isolated_client(tmp_path)
    assert c.post("/api/models/providers/glm/test", json={}).status_code == 404
