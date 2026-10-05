import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aitester.agents import find_agent
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
        "grep_search",
        "glob_search",
        "pwsh",
        "bash",
        "web_search",
        "knowledge_search",
        "save_to_knowledge",
        "task",
    ]
    assert [a["id"] for a in body["agents"]] == ["case_design"]
    assert [s["id"] for s in body["subagents"]] == ["general-purpose"]
    agent = body["agents"][0]
    assert agent["name"] == "用例设计智能体"
    assert "## 职责" in agent["prompt"]
    assert agent["prompt"] == find_agent("case_design").prompt
    assert agent["default_uid"] == "" and agent["effective_uid"] == ""
    assert agent["tool_ids"] == [
        "read",
        "write",
        "edit",
        "grep_search",
        "glob_search",
        "web_search",
        "task",
    ]


def test_capabilities_view_reports_availability(tmp_path: Path) -> None:
    tools = {t["id"]: t for t in _client(tmp_path).get("/api/capabilities").json()["tools"]}
    assert tools["read"]["available"] is True
    assert tools["read"]["unavailable_reason"] is None
    web = tools["web_search"]
    assert web["available"] is True
    assert web["unavailable_reason"] is None
    assert web["enabled"] is True


def test_agent_default_model_endpoints(tmp_path: Path) -> None:
    c = _client(tmp_path, deepseek_api_key="sk-x123456789")
    bad = c.put(
        "/api/capabilities/agents/case_design/default-model", json={"uid": "dashscope/qwen3.7-max"}
    )
    assert bad.status_code == 400
    assert "设置 · 模型设置" in bad.json()["detail"]
    ok = c.put(
        "/api/capabilities/agents/case_design/default-model",
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
    assert c.put("/api/capabilities/agents/case_design/default-model", json={"uid": ""}).status_code == 200
    assert c.put("/api/capabilities/agents/a9/default-model", json={"uid": ""}).status_code == 404


def test_disabled_model_falls_back_in_view_without_touching_storage(tmp_path: Path) -> None:
    c = _client(tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789")
    assert c.get("/api/models").json()["default_uid"] == "deepseek/deepseek-flash"
    uid = "dashscope/qwen3.7-max"
    ok = c.put("/api/capabilities/agents/case_design/default-model", json={"uid": uid})
    assert ok.status_code == 200
    before = c.get("/api/capabilities").json()["agents"][0]
    assert before["default_uid"] == uid
    assert before["effective_uid"] == uid
    off = c.put(
        "/api/models/providers/dashscope/models/qwen3.7-max/enabled", json={"enabled": False}
    )
    assert off.status_code == 200
    assert off.json()["default_uid"] == "deepseek/deepseek-flash"
    after = c.get("/api/capabilities").json()["agents"][0]
    assert after["default_uid"] == uid  # 写入期不清理：存储的默认模型原封不动
    assert after["effective_uid"] == "deepseek/deepseek-flash"  # 读取期回落全局默认
    stored = json.loads((tmp_path / "capability_config.json").read_text(encoding="utf-8"))
    assert stored["agents"]["case_design"]["default_uid"] == uid
    assert stored["agents"]["case_design"]["tool_ids"] == [
        "read",
        "write",
        "edit",
        "grep_search",
        "glob_search",
        "web_search",
        "task",
    ]


def test_agent_tools_endpoint(tmp_path: Path) -> None:
    c = _client(tmp_path)
    ok = c.put("/api/capabilities/agents/case_design/tools", json={"tool_ids": ["read", "web_search"]})
    assert ok.status_code == 200
    assert ok.json()["agents"][0]["tool_ids"] == ["read", "web_search"]
    ok = c.put("/api/capabilities/agents/case_design/tools", json={"tool_ids": ["write", "edit"]})
    assert ok.status_code == 200
    assert ok.json()["agents"][0]["tool_ids"] == ["write", "edit"]
    assert c.put("/api/capabilities/agents/case_design/tools", json={"tool_ids": ["nope"]}).status_code == 404


def test_agent_tools_endpoint_rejects_disabled_tool(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.put("/api/capabilities/tools/edit/enabled", json={"enabled": False}).status_code == 200
    bad = c.put("/api/capabilities/agents/case_design/tools", json={"tool_ids": ["read", "edit"]})
    assert bad.status_code == 400
    assert "已禁用" in bad.json()["detail"]


def test_enable_unavailable_tool_endpoint_is_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aitester.services.capability_config.unavailable_reason",
        lambda tool_id: "本机未找到 pwsh 可执行文件" if tool_id == "pwsh" else None,
    )
    c = _client(tmp_path)
    bad = c.put("/api/capabilities/tools/pwsh/enabled", json={"enabled": True})
    assert bad.status_code == 400
    assert "本机未找到 pwsh 可执行文件" in bad.json()["detail"]


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
