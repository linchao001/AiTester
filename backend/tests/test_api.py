import json
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from aitester.adapters.llm import MockProvider, ProviderError
from aitester.adapters.llm.probe import ProbeError
from aitester.agents import find_agent
from aitester.config import Settings
from aitester.main import app, create_app
from aitester.services import model_config
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.chat import ChatService
from streaming_fakes import ChunkedStreamMixin, sse_frames
from test_chat_stream_api import _NoopKbManager

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


def _stream_reply(client, payload: dict) -> dict:
    """POST /api/chat/send/stream 折成「终态载荷 dict」：迁移期让既有断言只改一行。"""
    with client.stream("POST", "/api/chat/send/stream", json=payload) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        frames = sse_frames(resp)
        done = dict(frames[-1][1]) if frames and frames[-1][0] == "done" else {}
        done["deltas"] = [data["text"] for event, data in frames if event == "delta"]
        return done


def _isolated_client(
    tmp_path: Path,
    name: str = "model_config.json",
    cap_name: str = "capability_config.json",
) -> TestClient:
    application = create_app(
        model_config_path=tmp_path / name,
        capability_config_path=tmp_path / cap_name,
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None),
    )
    return TestClient(application)


def _seed_project(application, tmp_path: Path, name: str = "订单系统") -> str:
    """Task 6：可见智能体的 send 必须有真实可达的项目，测试经 create_app 缝建真项目。"""
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    return application.state.project_config.create(
        name=name, desc="", dir_=str(root), agents=["case_design"]
    )["id"]


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


def test_send_uses_injected_provider_and_deltas_match_reply(tmp_path: Path) -> None:
    # kb_manager 显式给无开关替身（同 test_chat_stream_api._app 口径）：不传则 create_app
    # 现装真 RemeKbManager（kb_enabled 缺省 True）→ case_design 翻进 loop 而非直通，
    # 本用例锁的「注入 provider 的纯回合」文案会变（T9 接线实测）
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None),
        kb_manager=_NoopKbManager(),
    )
    assert isinstance(application.state.agent_runtime, AgentRuntime)
    application.state.chat_service = ChatService(
        provider=MockProvider(),
        agent_runtime=application.state.agent_runtime,
        projects=application.state.project_config,
    )
    pid = _seed_project(application, tmp_path)
    body = _stream_reply(TestClient(application),
                         {"session_id": "s2", "message": "生成用例", "project_id": pid})
    assert body["reply"] == "[mock] 生成用例"
    # trace/model 随一次性端点一起消失（spec 裁定 1 与偏离登记 3）：
    # 七层穿透的验收物仍由 echo 用例锁（test_chat_echo_traverses_all_seven_layers）
    assert body["deltas"] and "".join(body["deltas"]) == "[mock] 生成用例"


def test_send_with_unknown_agent_returns_404(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=MockProvider(),
        agent_runtime=application.state.agent_runtime,
        projects=application.state.project_config,
    )
    # 项目段先于装配：给个真项目让流程走到 build，才能验「未知智能体」的 404
    pid = _seed_project(application, tmp_path)
    resp = TestClient(application).post(
        "/api/chat/send/stream",
        json={"session_id": "s3", "message": "hi", "agent_id": "ghost", "project_id": pid},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "未知智能体「ghost」"


def test_send_with_legacy_agent_id_returns_404(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=MockProvider(),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
    )
    pid = _seed_project(application, tmp_path)
    resp = TestClient(application).post(
        "/api/chat/send/stream",
        json={"message": "hi", "agent_id": "a1", "project_id": pid},
    )
    assert resp.status_code == 404
    assert "未知智能体「a1」" in resp.json()["detail"]


def test_send_uses_agent_prompt_and_default_agent_id(tmp_path: Path) -> None:
    seen: list[list[object]] = []

    class _SpyProvider(ChunkedStreamMixin):
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
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None),
        kb_manager=_NoopKbManager(),   # 直通缝：同上（不传则真 manager 把本回合翻进 loop）
    )
    application.state.chat_service = ChatService(
        provider=_SpyProvider(),
        agent_runtime=application.state.agent_runtime,
        projects=application.state.project_config,
    )
    pid = _seed_project(application, tmp_path)
    # 请求体不带 agent_id 时取 SendRequest 默认值 case_design；系统提示词来自 md
    assert _stream_reply(TestClient(application),
                         {"session_id": "s4", "message": "生成登录用例", "project_id": pid})["reply"] == "[spy] 收到"
    assert str(seen[0][0].content) == find_agent("case_design").prompt


def test_send_without_configured_default_returns_400(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    # 项目段先于 build：项目齐备才能走到「未配置默认模型」那条 400，而非「请先选择项目」
    pid = _seed_project(c.app, tmp_path)
    resp = c.post("/api/chat/send/stream", json={"message": "hi", "project_id": pid})
    assert resp.status_code == 400
    assert "设置 · 模型设置" in resp.json()["detail"]


def test_send_upstream_failure_travels_as_error_event(tmp_path: Path) -> None:
    class FailingProvider(ChunkedStreamMixin):
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
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=FailingProvider(),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
    )
    pid = _seed_project(application, tmp_path)
    with TestClient(application).stream(
        "POST", "/api/chat/send/stream", json={"message": "hi", "project_id": pid}
    ) as resp:
        assert resp.status_code == 200          # 流已开始，失败只能走事件
        # brief 原码对每条 data 行取 ["detail"] 会先撞 start 帧（它没有这个键）；改取末条
        last_data = [line for line in resp.iter_lines() if line.startswith("data: ")][-1]
    detail = json.loads(last_data.split(":", 1)[1].strip())["detail"]
    assert "fake/model-x" in detail


def test_chat_service_is_per_app_instance(tmp_path: Path) -> None:
    app_a = create_app(
        model_config_path=tmp_path / "a.json",
        capability_config_path=tmp_path / "a.cap.json",
        sessions_dir=tmp_path / "sessions-a",
        settings=Settings(_env_file=None),
    )
    app_b = create_app(
        model_config_path=tmp_path / "b.json",
        capability_config_path=tmp_path / "b.cap.json",
        sessions_dir=tmp_path / "sessions-b",
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
