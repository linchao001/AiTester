import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app


# 与 test_kb_browse.py 同款：项目端点不碰 reme，注入假 manager 免起真实实例
class _NoopKbManager:
    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})

    def run_job_sync(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})


def _app(tmp_path: Path):
    """与 client fixture 同款装配，但返回 app 本体——用例要摸 application.state.sessions。

    sessions_dir 必须给到 tmp_path：GET 现在会读 SessionStore 数会话，不注入就会读写真实
    backend/data/sessions。
    """
    return create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        projects_path=tmp_path / "projects.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )


def _body(client, **over):
    payload = {
        "name": "订单系统",
        "desc": "交易链路",
        "dir": "D:/work/projects/order",
        "agents": ["case_design"],
    }
    payload.update(over)
    return payload


def _create(application, name: str, dir_: str) -> dict[str, Any]:
    resp = TestClient(application).post("/api/projects", json=_body(None, name=name, dir=dir_))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _get(application) -> dict[str, Any]:
    resp = TestClient(application).get("/api/projects")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _delete(application, pid: str):
    return TestClient(application).delete(f"/api/projects/{pid}")


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(_app(tmp_path))


def test_list_is_empty_json_array(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}


def test_create_returns_201_with_kb_alias_and_zero_sessions(client):
    resp = client.post("/api/projects", json=_body(client))
    assert resp.status_code == 201
    p = resp.json()
    assert p["kb"] == "kb" and p["dir_exists"] is False   # 测试里填的目录不存在
    assert p["session_count"] == 0                        # 测试名承诺的那一钉：新项目零会话
    assert set(p) == {"id", "name", "desc", "dir", "agents", "kb", "session_count", "dir_exists"}
    assert client.get("/api/projects").json()["projects"][0]["id"] == p["id"]


def test_response_body_never_contains_real_kb_identity(client):
    created = client.post("/api/projects", json=_body(client))
    updated = client.put(f"/api/projects/{created.json()['id']}", json=_body(client))
    assert "zhb" not in created.text.lower()
    assert "zhb" not in updated.text.lower()
    assert "zhb" not in client.get("/api/projects").text.lower()


def test_create_persists_to_injected_path_not_data_dir(client, tmp_path):
    client.post("/api/projects", json=_body(client))
    assert (tmp_path / "projects.json").exists()
    assert "订单系统" in (tmp_path / "projects.json").read_text(encoding="utf-8")


def test_validation_maps_to_400_with_chinese_detail(client):
    assert client.post("/api/projects", json=_body(client, name="  ")).status_code == 400
    bad = client.post("/api/projects", json=_body(client, dir="work/order"))
    assert bad.status_code == 400 and "绝对路径" in bad.json()["detail"]
    agents = client.post("/api/projects", json=_body(client, agents=["kb_assistant"]))
    assert agents.status_code == 400 and "不可启用" in agents.json()["detail"]
    kb = client.post("/api/projects", json=_body(client, kb="zhb_kb"))
    assert kb.status_code == 400 and "未知知识库" in kb.json()["detail"]


def test_duplicate_name_rejected(client):
    assert client.post("/api/projects", json=_body(client)).status_code == 201
    dup = client.post("/api/projects", json=_body(client, dir="D:/work/other"))
    assert dup.status_code == 400 and "同名项目" in dup.json()["detail"]


def test_update_immutable_fields_and_unknown_id(client):
    pid = client.post("/api/projects", json=_body(client)).json()["id"]
    ok = client.put(f"/api/projects/{pid}", json=_body(client, name="支付中心"))
    assert ok.status_code == 200 and ok.json()["name"] == "支付中心"
    # 不带 dir/kb 也合法（不传即不改）
    assert client.put(f"/api/projects/{pid}", json={
        "name": "支付中心", "desc": "", "agents": ["case_design"]}).status_code == 200
    clash = client.put(f"/api/projects/{pid}", json=_body(client, dir="E:/x"))
    assert clash.status_code == 400 and "本地文件目录" in clash.json()["detail"]
    kb = client.put(f"/api/projects/{pid}", json=_body(client, kb="other"))
    assert kb.status_code == 400 and "知识库配置" in kb.json()["detail"]
    ghost = client.put("/api/projects/proj_00000000", json=_body(client, name="幽灵"))
    assert ghost.status_code == 404 and "未知项目" in ghost.json()["detail"]


def test_delete_204_then_last_project_protected(client):
    a = client.post("/api/projects", json=_body(client)).json()["id"]
    b = client.post("/api/projects", json=_body(client, name="会员中心", dir="D:/work/m")).json()["id"]
    assert client.delete(f"/api/projects/{b}").status_code == 204
    assert client.delete(f"/api/projects/{b}").status_code == 404
    last = client.delete(f"/api/projects/{a}")
    assert last.status_code == 400 and "至少需要保留 1 个项目" in last.json()["detail"]


def test_unknown_project_id_404(client):
    assert client.delete("/api/projects/proj_deadbeef").status_code == 404
    assert client.put("/api/projects/proj_deadbeef", json=_body(client)).status_code == 404


def test_malformed_project_items_self_heal_on_read(tmp_path):
    (tmp_path / "projects.json").write_text(
        '{"version":1,"projects":[{"id":"evil","name":"坏","dir":"D:/x","agents":["ghost"],"kb":"nope"}]}',
        encoding="utf-8")
    row = TestClient(_app(tmp_path)).get("/api/projects").json()["projects"][0]
    assert row["id"].startswith("proj_") and row["kb"] == "kb"
    assert row["agents"] == ["case_design"]


def test_session_count_reflects_store(tmp_path) -> None:
    application = _app(tmp_path)  # 复用本文件既有的 create_app 助手；须同时给 projects_path 与 sessions_dir
    pid = _create(application, "订单系统", str(tmp_path / "reqs"))["id"]
    store = application.state.sessions
    store.create(store.new_id(), "case_design", pid, "一")
    store.create(store.new_id(), "case_design", pid, "二")
    rows = _get(application)["projects"]
    assert next(p for p in rows if p["id"] == pid)["session_count"] == 2


def test_dir_exists_probe_matches_disk_for_every_project(tmp_path) -> None:
    application = _app(tmp_path)
    real = tmp_path / "reqs"
    real.mkdir()
    ghost = tmp_path / "typed-wrong"
    ok_pid = _create(application, "有目录", str(real))["id"]
    ghost_pid = _create(application, "填错了", str(ghost))["id"]
    rows = {p["id"]: p for p in _get(application)["projects"]}
    assert rows[ok_pid]["dir_exists"] is True
    assert rows[ghost_pid]["dir_exists"] is False
    # 探测只读：GET 一轮后不许替用户把目录建出来
    assert not ghost.exists()


def test_nul_byte_in_stored_dir_yields_false_not_500(tmp_path) -> None:
    # dir 由用户自填，含 NUL 字节的值能让文件系统调用抛 ValueError（不是 OSError）：
    # GET 必须答「不可达」，绝不能把它变成 500（与危险根判据同款坑）
    (tmp_path / "projects.json").write_text(
        json.dumps({"version": 1, "projects": [
            {"id": "proj_aaaaaaaa", "name": "带 NUL", "desc": "", "dir": "D:/work\x00x",
             "agents": ["case_design"], "kb": "kb"}]},
            ensure_ascii=False),
        encoding="utf-8")
    row = _get(_app(tmp_path))["projects"][0]
    assert row["dir_exists"] is False


def test_delete_project_cascades_sessions(tmp_path) -> None:
    application = _app(tmp_path)
    pid = _create(application, "订单系统", str(tmp_path / "reqs"))["id"]
    other = _create(application, "支付中心", str(tmp_path / "pay"))["id"]
    store = application.state.sessions
    sid = store.new_id()
    kept = store.new_id()
    store.create(sid, "case_design", pid, "该删")
    store.create(kept, "case_design", other, "该留")
    assert _delete(application, pid).status_code == 204
    assert store.get(sid) is None
    assert not (tmp_path / "sessions" / f"{sid}.jsonl").exists()
    assert store.get(kept) is not None            # 别的项目一条不少
    assert (tmp_path / "sessions" / f"{kept}.jsonl").exists()


def test_delete_project_last_one_keeps_sessions(tmp_path) -> None:
    # 顺序是刻意的：项目校验没过（剩 1 条禁删）时绝不能先把会话删了
    application = _app(tmp_path)
    pid = _create(application, "唯一项目", str(tmp_path / "only"))["id"]
    store = application.state.sessions
    sid = store.new_id()
    store.create(sid, "case_design", pid, "别跟着死")
    assert _delete(application, pid).status_code == 400
    assert store.get(sid) is not None
