from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app


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
def kb_root(tmp_path):
    root = tmp_path / "bases" / "zhb_kb"
    (root / "business" / "wiki").mkdir(parents=True)
    (root / "_inbox").mkdir()
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "config").write_text("secret", encoding="utf-8")
    (root / "business" / "wiki" / "a.md").write_text(
        '---\nname: "A"\ndescription: "d"\nbucket: business/wiki\n---\n\n# A\nbody\n', encoding="utf-8")
    (root / "business" / "dbInfo.sql").write_text("select 1;", encoding="utf-8")
    (root / "bin.exe").write_bytes(b"\x00\x01bin")
    return root


def _client(tmp_path, kb_root, **skw):
    # resolve_kb_root 会追加 settings.kb_id，故 kb_id 须与 kb_root 目录名一致；
    # 其余用例 kb_root.name == "zhb_kb"（默认值），行为与简报原案完全相同，
    # 仅 ghost_kb 用例借此让根真正指向不存在的路径。
    s_kw = dict(kb_bases_dir=str(kb_root.parent), kb_id=kb_root.name)
    s_kw.update(skw)
    app = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None, **s_kw),
        kb_manager=_RecordingKbManager(),
    )
    return TestClient(app)


def test_tree_dirs_first_and_hides_git(tmp_path, kb_root):
    with _client(tmp_path, kb_root) as c:
        r = c.get("/api/kb/browse/tree", params={"path": ""})
    assert r.status_code == 200
    j = r.json()
    assert j["root"] == str(kb_root)
    names = [i["name"] for i in j["items"]]
    assert ".git" not in names and set(names) == {"business", "_inbox", "bin.exe"}
    assert j["items"][0]["dir"] is True  # dirs-first


def test_tree_traversal_and_hidden_403(tmp_path, kb_root):
    with _client(tmp_path, kb_root) as c:
        assert c.get("/api/kb/browse/tree", params={"path": "../escape"}).status_code == 403
        assert c.get("/api/kb/browse/tree", params={"path": ".git"}).status_code == 403
        assert c.get("/api/kb/browse/tree", params={"path": "a\0b"}).status_code == 403
        assert c.get("/api/kb/browse/tree", params={"path": "nope"}).status_code == 404
        assert c.get("/api/kb/browse/tree", params={"path": "business/dbInfo.sql"}).status_code == 400


def test_file_read_and_gates(tmp_path, kb_root):
    with _client(tmp_path, kb_root) as c:
        j = c.get("/api/kb/browse/file", params={"path": "business/wiki/a.md"}).json()
        assert j["rel"] == "business/wiki/a.md" and j["ext"] == ".md" and j["editable"] is True
        assert j["mtime"] == int((kb_root / "business" / "wiki" / "a.md").stat().st_mtime_ns // 1_000_000)
        assert c.get("/api/kb/browse/file", params={"path": "bin.exe"}).status_code == 415
        assert c.get("/api/kb/browse/file", params={"path": "nope.md"}).status_code == 404
        assert c.get("/api/kb/browse/file", params={"path": "business"}).status_code == 400
        assert c.get("/api/kb/browse/file", params={"path": "../x.txt"}).status_code == 403


def test_file_too_large_413(tmp_path, kb_root):
    big = kb_root / "big.txt"
    big.write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    with _client(tmp_path, kb_root) as c:
        assert c.get("/api/kb/browse/file", params={"path": "big.txt"}).status_code == 413


def test_search_by_name(tmp_path, kb_root):
    with _client(tmp_path, kb_root) as c:
        j = c.get("/api/kb/browse/search", params={"q": "a.md"}).json()
        assert [h["rel"] for h in j["hits"]] == ["business/wiki/a.md"]
        assert j["total"] == 1 and j["truncated"] is False
        assert c.get("/api/kb/browse/search", params={"q": "  "}).json()["hits"] == []


def test_scan_parses_frontmatter(tmp_path, kb_root):
    with _client(tmp_path, kb_root) as c:
        j = c.get("/api/kb/browse/scan", params={"path": "business"}).json()
        doc = [d for d in j["docs"] if d["name"] == "a.md"][0]
        assert doc["fm"]["name"] == "A" and doc["fm"]["bucket"] == "business/wiki"
        assert all(d["name"].endswith(".md") for d in j["docs"])  # md-only 默认
        j2 = c.get("/api/kb/browse/scan", params={"path": "business", "md": "0"}).json()
        assert any(d["name"] == "dbInfo.sql" for d in j2["docs"])


def test_browse_gate_503_and_missing_root_404(tmp_path, kb_root):
    with _client(tmp_path, kb_root, kb_enabled=False) as c:
        assert c.get("/api/kb/browse/tree").status_code == 503
    with _client(tmp_path, tmp_path / "bases" / "ghost_kb") as c:
        assert c.get("/api/kb/browse/tree").status_code == 404
