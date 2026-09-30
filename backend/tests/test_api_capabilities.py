from pathlib import Path

from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app


def _client(tmp_path: Path, **settings_kwargs: object) -> TestClient:
    application = create_app(
        model_config_path=tmp_path / "model_config.json",
        capability_config_path=tmp_path / "capability_config.json",
        settings=Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )
    return TestClient(application)


def test_capabilities_seed_view(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/capabilities").json()
    assert [t["id"] for t in body["tools"]] == [
        "read",
        "write",
        "edit",
        "pwsh",
        "bash",
        "web_search",
    ]
    assert [a["id"] for a in body["agents"]] == ["a1"]
    agent = body["agents"][0]
    assert agent["name"] == "用例设计智能体"
    assert agent["default_uid"] == "" and agent["effective_uid"] == ""
    assert agent["tool_ids"] == ["read", "write", "edit", "web_search"]


def test_agent_default_model_endpoints(tmp_path: Path) -> None:
    c = _client(tmp_path, deepseek_api_key="sk-x123456789")
    bad = c.put(
        "/api/capabilities/agents/a1/default-model", json={"uid": "dashscope/qwen3.7-max"}
    )
    assert bad.status_code == 400
    assert "设置 · 模型设置" in bad.json()["detail"]
    ok = c.put(
        "/api/capabilities/agents/a1/default-model",
        json={"uid": "deepseek/deepseek-flash"},
    )
    assert ok.status_code == 200
    assert ok.json()["agents"][0]["default_uid"] == "deepseek/deepseek-flash"
    assert ok.json()["agents"][0]["effective_uid"] == "deepseek/deepseek-flash"
    restarted = _client(tmp_path)
    assert (
        restarted.get("/api/capabilities").json()["agents"][0]["default_uid"]
        == "deepseek/deepseek-flash"
    )
    assert c.put("/api/capabilities/agents/a1/default-model", json={"uid": ""}).status_code == 200
    assert c.put("/api/capabilities/agents/a9/default-model", json={"uid": ""}).status_code == 404


def test_agent_tools_endpoint(tmp_path: Path) -> None:
    c = _client(tmp_path)
    bad = c.put("/api/capabilities/agents/a1/tools", json={"tool_ids": ["read", "bash"]})
    assert bad.status_code == 400 and "工具" in bad.json()["detail"]
    ok = c.put("/api/capabilities/agents/a1/tools", json={"tool_ids": ["read", "pwsh"]})
    assert ok.status_code == 200
    assert ok.json()["agents"][0]["tool_ids"] == ["read", "pwsh"]
    assert c.put("/api/capabilities/agents/a1/tools", json={"tool_ids": ["nope"]}).status_code == 404


def test_tool_enabled_endpoint_cascades(tmp_path: Path) -> None:
    c = _client(tmp_path)
    off = c.put("/api/capabilities/tools/read/enabled", json={"enabled": False})
    assert off.status_code == 200
    read = next(t for t in off.json()["tools"] if t["id"] == "read")
    assert read["enabled"] is False and read["carried_by"] == []
    assert "read" not in off.json()["agents"][0]["tool_ids"]
    on = c.put("/api/capabilities/tools/read/enabled", json={"enabled": True})
    assert "read" not in on.json()["agents"][0]["tool_ids"]  # 不自动补回
    assert c.put("/api/capabilities/tools/nope/enabled", json={"enabled": True}).status_code == 404


def test_capabilities_body_has_no_secrets(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    assert "sk-SECRET123456" not in c.get("/api/capabilities").text
