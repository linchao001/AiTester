import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app


# 装配蓝本 = test_api_projects.py 的 _app：项目端点不碰 reme，注入假 manager 免起真实实例
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
    return create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        projects_path=tmp_path / "projects.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )


def _mk_project(client: TestClient, root: Path, name: str) -> str:
    root.mkdir(parents=True, exist_ok=True)   # 创建口已拦「不存在」，测试给真实目录
    resp = client.post("/api/projects", json={
        "name": name, "desc": "", "dir": str(root), "agents": ["case_design"]})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_tree_lists_dirs_first_and_hides_hidden(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        root = tmp_path / "reqs"
        (root / "cases").mkdir(parents=True)
        (root / "README.md").write_text("# R", encoding="utf-8")
        (root / ".git" / "objects").mkdir(parents=True)
        (root / ".scratch").mkdir()
        pid = _mk_project(c, root, "订单系统")
        j = c.get(f"/api/projects/{pid}/browse/tree").json()
        assert [i["name"] for i in j["items"]] == ["cases", "README.md"]  # dirs-first、隐藏不可见
        assert j["items"][0]["dir"] is True
        assert j["items"][0]["rel"] == "cases"
        sub = c.get(f"/api/projects/{pid}/browse/tree", params={"path": "cases"}).json()
        assert sub["rel"] == "cases" and sub["items"] == []


def test_tree_path_guards(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        root = tmp_path / "reqs"
        (root / "a.md").parent.mkdir(parents=True, exist_ok=True)
        (root / "a.md").write_text("x", encoding="utf-8")
        pid = _mk_project(c, root, "订单系统")
        base = f"/api/projects/{pid}/browse/tree"
        assert c.get(base, params={"path": "../escape"}).status_code == 403
        assert c.get(base, params={"path": ".git"}).status_code == 403
        assert c.get(base, params={"path": "a\0b"}).status_code == 403
        assert "路径超出项目目录范围" in c.get(base, params={"path": ".git"}).json()["detail"]
        assert c.get(base, params={"path": "nope"}).status_code == 404
        assert c.get(base, params={"path": "a.md"}).status_code == 400   # 文件当目录


def test_unknown_project_404_detail(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/projects/proj_deadbeef/browse/tree")
        assert r.status_code == 404 and "未知项目" in r.json()["detail"]


def test_dir_removed_after_create_points_to_projects_page(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        root = tmp_path / "reqs"
        pid = _mk_project(c, root, "订单系统")
        root.rmdir()
        r = c.get(f"/api/projects/{pid}/browse/tree")
        assert r.status_code == 404 and "项目页" in r.json()["detail"]


def test_nul_dir_answers_404_not_500(tmp_path):
    # dir 含 NUL 字节：Path.resolve() 抛 ValueError（不是 OSError）——必须答 404 可读文案，不能 500
    (tmp_path / "projects.json").write_text(
        json.dumps({"version": 1, "projects": [
            {"id": "proj_aaaaaaaa", "name": "带 NUL", "desc": "", "dir": "D:/work\x00x",
             "agents": ["case_design"], "kb": "kb"}]}, ensure_ascii=False),
        encoding="utf-8")
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/projects/proj_aaaaaaaa/browse/tree")
        assert r.status_code == 404 and "项目页" in r.json()["detail"]


def test_tilde_dir_consumed_at_same_place_as_send(tmp_path, monkeypatch):
    # 第 2 片的 `~` 教训：创建口按 expanduser 验真，工作区必须以同一展开结果落根；
    # 把家目录指到 tmp，锁死「浏览到的就是 expanduser 后的目录」这条消费对称性
    home = tmp_path / "fakehome"
    (home / "wsprobe").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    with TestClient(_app(tmp_path)) as c:
        resp = c.post("/api/projects", json={
            "name": "波浪号", "desc": "", "dir": "~/wsprobe", "agents": ["case_design"]})
        assert resp.status_code == 201, resp.text
        pid = resp.json()["id"]
        (home / "wsprobe" / "made-by-agent.md").write_text("v", encoding="utf-8")
        j = c.get(f"/api/projects/{pid}/browse/tree").json()
        assert j["root"] == str((home / "wsprobe").resolve())
        assert [i["name"] for i in j["items"]] == ["made-by-agent.md"]


def test_file_read_and_gates(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        root = tmp_path / "reqs"
        pid = _mk_project(c, root, "订单系统")
        (root / "a.md").write_text("# A\n", encoding="utf-8")
        (root / "bin.exe").write_bytes(b"\x00\x01")
        r = c.get(f"/api/projects/{pid}/browse/file", params={"path": "a.md"})
        assert r.status_code == 200
        j = r.json()
        assert j["content"] == "# A\n" and j["ext"] == ".md" and j["editable"] is True
        assert j["mtime"] == int((root / "a.md").stat().st_mtime_ns // 1_000_000)
        r = c.get(f"/api/projects/{pid}/browse/file", params={"path": "bin.exe"})
        assert r.status_code == 415
        assert r.json() == {"detail": "暂不支持预览该文件类型", "editable": False}
        assert c.get(f"/api/projects/{pid}/browse/file", params={"path": "nope.md"}).status_code == 404
        assert c.get(f"/api/projects/{pid}/browse/file", params={"path": ""}).status_code == 400  # 根是目录
        assert c.get(f"/api/projects/{pid}/browse/file", params={"path": "../x.txt"}).status_code == 403


def test_file_too_large_413_editable_false(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        root = tmp_path / "reqs"
        pid = _mk_project(c, root, "订单系统")
        (root / "big.txt").write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
        r = c.get(f"/api/projects/{pid}/browse/file", params={"path": "big.txt"})
        assert r.status_code == 413
        assert r.json() == {"detail": "文件超过 2MB，无法打开", "editable": False}


def test_put_roundtrip_and_conflict_409(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        root = tmp_path / "reqs"
        pid = _mk_project(c, root, "订单系统")
        f = root / "cases" / "n.md"
        f.parent.mkdir()
        f.write_text("v1", encoding="utf-8")
        url = f"/api/projects/{pid}/browse/file"
        cur = int(f.stat().st_mtime_ns // 1_000_000)
        r = c.put(url, params={"path": "cases/n.md", "mtime": cur}, json={"content": "v2"})
        assert r.status_code == 200 and f.read_text(encoding="utf-8") == "v2"
        assert r.json()["mtime"] >= cur and r.json()["size"] == 2
        r = c.put(url, params={"path": "cases/n.md", "mtime": 1}, json={"content": "bad"})
        assert r.status_code == 409 and f.read_text(encoding="utf-8") == "v2"   # 冲突不覆盖
        assert r.json()["mtime"] == int(f.stat().st_mtime_ns // 1_000_000)      # 回传磁盘当前值


def test_put_guards(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        root = tmp_path / "reqs"
        pid = _mk_project(c, root, "订单系统")
        (root / "cases").mkdir()
        (root / "gone.md").write_text("x", encoding="utf-8")
        (root / "bin.exe").write_bytes(b"\x00")
        url = f"/api/projects/{pid}/browse/file"
        (root / "gone.md").unlink()
        r = c.put(url, params={"path": "gone.md"}, json={"content": "x"})
        assert r.status_code == 404 and "已不存在" in r.json()["detail"]        # 文案不指引不存在的「新建接口」
        assert c.put(url, params={"path": "bin.exe"}, json={"content": "x"}).status_code == 415
        assert c.put(url, params={"path": ".git/cfg"}, json={"content": "x"}).status_code == 403
        assert c.put(url, params={"path": "cases"}, json={"content": "x"}).status_code == 400
        big = root / "big.md"
        big.write_text("g", encoding="utf-8")
        r = c.put(url, params={"path": "big.md"}, json={"content": "x" * (2 * 1024 * 1024 + 1)})
        assert r.status_code == 413 and r.json()["editable"] is False


def test_post_not_offered_405(tmp_path):
    # 裁定 2：无「新建文件」消费方 ⇒ 后端不做 POST；该路径只有 GET/PUT，POST 由 FastAPI 答 405
    with TestClient(_app(tmp_path)) as c:
        pid = _mk_project(c, tmp_path / "reqs", "订单系统")
        r = c.post(f"/api/projects/{pid}/browse/file", params={"path": "new.md"}, json={"content": "x"})
        assert r.status_code == 405
