from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app
from aitester.project_runtime import knowledge_mount


# tests/ 无 __init__.py，`from tests.test_kb_api import ...` 路径不通，
# 按简报指示原样复制 test_kb_api.py:10-25 的 _RecordingKbManager。
class _RecordingKbManager:
    def __init__(self, exc=None):
        self.calls: list[tuple[str, dict]] = []
        self.exc = exc

    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        if self.exc is not None:
            raise self.exc
        self.calls.append((name, kwargs))
        return SimpleNamespace(success=True, answer="ok", metadata={"echo": name})


@pytest.fixture()
def kb_content(tmp_path):
    """模拟项目挂载点内的知识内容（与 Reme junction 目标同结构）。"""
    root = tmp_path / "proj" / ".AiTester" / "knowledge"
    (root / "business" / "wiki").mkdir(parents=True)
    (root / "_inbox").mkdir()
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "config").write_text("secret", encoding="utf-8")
    (root / "business" / "wiki" / "a.md").write_text(
        '---\nname: "A"\ndescription: "d"\nbucket: business/wiki\n---\n\n# A\nbody\n', encoding="utf-8")
    (root / "business" / "dbInfo.sql").write_text("select 1;", encoding="utf-8")
    (root / "bin.exe").write_bytes(b"\x00\x01bin")
    return root


def _client(tmp_path, **skw):
    s_kw = dict(kb_bases_dir=str(tmp_path / "bases"), kb_id="zhb_kb")
    s_kw.update(skw)
    app = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        projects_path=tmp_path / "projects.json",
        settings=Settings(_env_file=None, **s_kw),
        memory_manager=_RecordingKbManager(),
    )
    return TestClient(app)


def _mk_project(client: TestClient, proj_dir: Path, name: str = "订单系统") -> str:
    proj_dir.mkdir(parents=True, exist_ok=True)
    resp = client.post("/api/projects", json={
        "name": name, "desc": "", "dir": str(proj_dir), "agents": ["case_design"],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _p(pid: str, **extra) -> dict:
    return {"project_id": pid, **extra}


def test_tree_dirs_first_and_hides_git(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        r = c.get("/api/kb/browse/tree", params=_p(pid, path=""))
    assert r.status_code == 200
    j = r.json()
    assert Path(j["root"]) == kb_content.resolve()
    names = [i["name"] for i in j["items"]]
    assert ".git" not in names and set(names) == {"business", "_inbox", "bin.exe"}
    assert j["items"][0]["dir"] is True  # dirs-first


def test_tree_requires_project_id(tmp_path, kb_content):
    del kb_content  # 内容无关：缺 project_id 即 400
    with _client(tmp_path) as c:
        r = c.get("/api/kb/browse/tree", params={"path": ""})
    assert r.status_code == 400
    assert r.json()["detail"] == "请先选择项目"


def test_tree_not_mounted_friendly(tmp_path):
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj, name="空项目")
        r = c.get("/api/kb/browse/tree", params=_p(pid))
    assert r.status_code == 404
    assert "尚未挂载知识库" in r.json()["detail"]


def test_tree_empty_mounted(tmp_path):
    proj = tmp_path / "empty_kb"
    knowledge_mount(proj).mkdir(parents=True)
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj, name="空库")
        j = c.get("/api/kb/browse/tree", params=_p(pid)).json()
    assert j["items"] == []


def test_tree_through_windows_junction(tmp_path):
    """挂载点是 junction 时：resolve 跟到实体后 listing/rel_of 不得再用挂载点路径。"""
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("junction 回归仅在 Windows 复现")
    proj = tmp_path / "jproj"
    entity = tmp_path / "entity_kb"
    (entity / "_inbox").mkdir(parents=True)
    (entity / "_inbox" / "n.md").write_text("# n\n", encoding="utf-8")
    mount = knowledge_mount(proj)
    mount.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(mount), str(entity)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        pytest.skip(f"无法创建 junction: {r.stderr or r.stdout}")
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj, name="junction项目")
        j = c.get("/api/kb/browse/tree", params=_p(pid)).json()
        assert [i["name"] for i in j["items"]] == ["_inbox"]
        f = c.get("/api/kb/browse/file", params=_p(pid, path="_inbox/n.md")).json()
        assert f["content"] == "# n\n"
        assert Path(j["root"]) == entity.resolve()


def test_tree_traversal_and_hidden_403(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        assert c.get("/api/kb/browse/tree", params=_p(pid, path="../escape")).status_code == 403
        assert c.get("/api/kb/browse/tree", params=_p(pid, path=".git")).status_code == 403
        assert c.get("/api/kb/browse/tree", params=_p(pid, path="a\0b")).status_code == 403
        assert c.get("/api/kb/browse/tree", params=_p(pid, path="nope")).status_code == 404
        assert c.get("/api/kb/browse/tree", params=_p(pid, path="business/dbInfo.sql")).status_code == 400


def test_file_read_and_gates(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        j = c.get("/api/kb/browse/file", params=_p(pid, path="business/wiki/a.md")).json()
        assert j["rel"] == "business/wiki/a.md" and j["ext"] == ".md" and j["editable"] is True
        assert j["mtime"] == int((kb_content / "business" / "wiki" / "a.md").stat().st_mtime_ns // 1_000_000)
        assert c.get("/api/kb/browse/file", params=_p(pid, path="bin.exe")).status_code == 415
        assert c.get("/api/kb/browse/file", params=_p(pid, path="nope.md")).status_code == 404
        assert c.get("/api/kb/browse/file", params=_p(pid, path="business")).status_code == 400
        assert c.get("/api/kb/browse/file", params=_p(pid, path="../x.txt")).status_code == 403


def test_file_too_large_413(tmp_path, kb_content):
    big = kb_content / "big.txt"
    big.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        assert c.get("/api/kb/browse/file", params=_p(pid, path="big.txt")).status_code == 413


def test_search_by_name(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        j = c.get("/api/kb/browse/search", params=_p(pid, q="a.md")).json()
        assert [h["rel"] for h in j["hits"]] == ["business/wiki/a.md"]
        assert j["total"] == 1 and j["truncated"] is False
        assert c.get("/api/kb/browse/search", params=_p(pid, q="  ")).json()["hits"] == []


def test_scan_parses_frontmatter(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        j = c.get("/api/kb/browse/scan", params=_p(pid, path="business")).json()
        doc = [d for d in j["docs"] if d["name"] == "a.md"][0]
        assert doc["fm"]["name"] == "A" and doc["fm"]["bucket"] == "business/wiki"
        assert all(d["name"].endswith(".md") for d in j["docs"])  # md-only 默认
        j2 = c.get("/api/kb/browse/scan", params=_p(pid, path="business", md="0")).json()
        assert any(d["name"] == "dbInfo.sql" for d in j2["docs"])


def test_browse_gate_503_and_missing_project_404(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path, kb_enabled=False) as c:
        pid = _mk_project(c, proj)
        assert c.get("/api/kb/browse/tree", params=_p(pid)).status_code == 503
    with _client(tmp_path) as c:
        r = c.get("/api/kb/browse/tree", params=_p("proj_deadbeef"))
        assert r.status_code == 404 and "未知项目" in r.json()["detail"]


def test_put_roundtrip_and_mtime(tmp_path, kb_content):
    f = kb_content / "_inbox" / "n.md"
    f.write_text("v1", encoding="utf-8")
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        cur = int(f.stat().st_mtime_ns // 1_000_000)
        r = c.put("/api/kb/browse/file", params=_p(pid, path="_inbox/n.md", mtime=cur),
                  json={"content": "v2"})
        assert r.status_code == 200 and f.read_text(encoding="utf-8") == "v2"
        assert r.json()["mtime"] >= cur and r.json()["size"] == 2


def test_put_conflict_409_keeps_disk(tmp_path, kb_content):
    f = kb_content / "_inbox" / "n.md"
    f.write_text("disk", encoding="utf-8")
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        r = c.put("/api/kb/browse/file", params=_p(pid, path="_inbox/n.md", mtime=1),
                  json={"content": "mine"})
        assert r.status_code == 409
        assert r.json()["mtime"] == int(f.stat().st_mtime_ns // 1_000_000)
        assert f.read_text(encoding="utf-8") == "disk"


def test_put_guards(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        assert c.put("/api/kb/browse/file", params=_p(pid, path="nope.md"),
                     json={"content": "x"}).status_code == 404
        assert c.put("/api/kb/browse/file", params=_p(pid, path="bin.exe"),
                     json={"content": "x"}).status_code == 415
        assert c.put("/api/kb/browse/file", params=_p(pid, path="business"),
                     json={"content": "x"}).status_code == 400
        assert c.put("/api/kb/browse/file", params=_p(pid, path="../x.md"),
                     json={"content": "x"}).status_code == 403


def test_post_create(tmp_path, kb_content):
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        r = c.post("/api/kb/browse/file", params=_p(pid, path="_inbox/new.md"),
                   json={"content": "# N\n"})
        assert r.status_code == 201
        assert (kb_content / "_inbox" / "new.md").read_text(encoding="utf-8") == "# N\n"
        assert c.post("/api/kb/browse/file", params=_p(pid, path="_inbox/new.md"),
                      json={"content": "x"}).status_code == 409
        assert c.post("/api/kb/browse/file", params=_p(pid, path="ghost/x.md"),
                      json={"content": "x"}).status_code == 400


def test_put_post_without_body_422(tmp_path, kb_content):
    # 终审项 7：body 必传——无请求体由 FastAPI 校验层 422，而非 None 解引用 500
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        assert c.put("/api/kb/browse/file",
                     params=_p(pid, path="business/wiki/a.md")).status_code == 422
        assert c.post("/api/kb/browse/file",
                      params=_p(pid, path="_inbox/x.md")).status_code == 422


def test_error_bodies_carry_editable_false(tmp_path, kb_content):
    # 终审项 10：spec §A 错误表——415/413 JSON 体附 editable:false，detail 文案逐字不变
    big = kb_content / "big.txt"
    big.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    over = "x" * (2 * 1024 * 1024 + 1)
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        r = c.get("/api/kb/browse/file", params=_p(pid, path="bin.exe"))
        assert r.status_code == 415
        assert r.json() == {"detail": "暂不支持预览该文件类型", "editable": False}
        r = c.get("/api/kb/browse/file", params=_p(pid, path="big.txt"))
        assert r.status_code == 413 and r.json()["editable"] is False
        assert r.json()["detail"] == "文件超过 2MB，只读不加载"
        r = c.put("/api/kb/browse/file", params=_p(pid, path="bin.exe"), json={"content": "x"})
        assert r.status_code == 415 and r.json()["editable"] is False
        r = c.put("/api/kb/browse/file", params=_p(pid, path="business/wiki/a.md"),
                  json={"content": over})
        assert r.status_code == 413 and r.json()["editable"] is False
        r = c.post("/api/kb/browse/file", params=_p(pid, path="_inbox/bin2.exe"), json={"content": "x"})
        assert r.status_code == 415 and r.json()["editable"] is False
        r = c.post("/api/kb/browse/file", params=_p(pid, path="_inbox/big.md"), json={"content": over})
        assert r.status_code == 413 and r.json()["editable"] is False


def test_browse_still_hides_dot_dirs(tmp_path, kb_content):
    # 中栏 browse 仍直读直写并隐藏点目录；助手草案已改走 Reme 桶，不再做路径隐藏共判
    (kb_content / ".scratch").mkdir()
    proj = tmp_path / "proj"
    with _client(tmp_path) as c:
        pid = _mk_project(c, proj)
        assert c.get("/api/kb/browse/tree", params=_p(pid, path=".scratch")).status_code == 403
