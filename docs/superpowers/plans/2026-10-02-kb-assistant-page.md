# /kb 三栏改版（browse 文件面 + prepare_kb_write 草案工具 + kb_assistant 助手）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 /kb 页面重做成与原型一致的三栏（目录树 + 预览/编辑 + 聊天助手），助手为新增内置智能体 `kb_assistant`，一切写盘经「待写入草案卡」用户确认。

**Architecture:** 文件面走新增 `/api/kb/browse/*` 六端点直读直写 KB 实体目录（逐条移植 `prototype/serve.js` 语义，索引收敛仍靠 reme 后台 watch）；助手面复用同步 `POST /api/chat/send`，新工具 `prepare_kb_write` 零写盘、草案走 ToolMessage artifact（模型不可见）经 `SendResponse.drafts` 透传给前端渲染草案卡。

**Tech Stack:** FastAPI + pydantic v2 + LangChain BaseTool + LangGraph（后端）；React 18 + TS + Vite（前端，零新增依赖）。

**Spec:** `docs/superpowers/specs/2026-10-02-kb-assistant-page-design.md`（四条用户裁定 + 关键事实修正，本计划是其唯一实施依据）

**原型真相源（转写蓝本，实施前先读）：**
- `prototype/serve.js:93-198` —— 六 handler 语义（browse 端点逐条对齐）。
- `prototype/index.html:472-547`（KB 三栏 CSS 族）、`:696-760`（三栏+新建笔记弹窗 markup）、`:1448`（escapeHtml）、`:1512`（toast）、`:1619` 起（mdRender，转到函数结束）、`:2342-2741`（KB 全部 JS 逻辑：树/编辑器/草案卡/助手）。

## Global Constraints

- 依赖冻结：`reme-ai==0.4.1.8` 不动；后端零新增依赖（stdlib + 现有 fastapi/pydantic/langchain-core）；前端零新增 npm 包（md 渲染手转写原型）。
- 文案分域（README 既有规约）：工具 `description` / `Field(description=…)` / ToolException / 工具输出 **英文**；UI、API 面向用户的 detail/提示 **中文**。工具层注释与 docstring 中文。
- 测试零真实 LLM/向量调用，零真实 `~/.reme` 触碰：browse/工具/装配测试一律 `tmp_path` 假根 + `Settings(_env_file=None, …)`；`backend/tests/conftest.py` 的环境清理表加 `REME_KNOWLEDGE_BASES_DIR`。
- `kb_assistant` 不可见性：不改 `capability_config.py` 的 `TOOL_CATALOG`/`AGENT_CATALOG`/`DEFAULT_AGENT_STATE` 任何条目；`/api/capabilities` 响应、设置页、聊天页智能体下拉维持只显示 `case_design`；不改 `backend/data/*.json`。
- 不加同步 reindex（终审既有裁定）：browse 写文件后只提示「索引自动收敛（约数十秒）」，不调任何 reindex job。
- browse 错误码与文案逐条对齐 serve.js；允许且仅允许三处偏差：① 错误体键用 FastAPI `detail`（409 额外并带 `mtime` 字段）；② `zh` 本地化排序 Python 侧用 `name.lower()` 近似（浏览器 ICU 无等价物，仅影响显示序）；③ 读文件 `errors="replace"`（serve.js 遇坏字节直接 500，我们不学）。
- 服务边界：不重启/杀掉/占用用户正在跑的 8000（后端）与 5173（vite）；真机走查用 `8001/5174` 或经用户同意复用。
- 每任务完成即提交（中文 conventional commit，参照 `git log` 现有风格，如 `feat(kb): …`）；不推远端。
- 后端测试命令统一 `uv run pytest`（在 `backend/` 下），前端门禁 `npm run build`（tsc 0 错误）。

---

### Task 1: KB 实体根路径解析 + manager 访问器

**Files:**
- Create: `backend/src/aitester/services/kb/paths.py`
- Modify: `backend/src/aitester/services/kb/manager.py`（加 `kb_root_dir` 属性与 `workspace_dir()`，`_kb_config` 改用后者）
- Modify: `backend/tests/conftest.py:8`（`_SETTINGS_ENV_VARS` 追加 `"REME_KNOWLEDGE_BASES_DIR"`）
- Test: `backend/tests/test_kb_paths.py`（新建）+ `backend/tests/test_kb_manager.py`（追加两测）

**Interfaces:**
- Consumes: `Settings.kb_bases_dir: str`、`Settings.kb_id: str`（`backend/src/aitester/config.py:36-37`，已存在）。
- Produces: `resolve_kb_bases_dir(settings) -> Path`、`resolve_kb_root(settings) -> Path`（Task 2 browse 路由、Task 4 prepare 工具都从这里拿根）；`RemeKbManager.kb_root_dir -> Path`、`RemeKbManager.workspace_dir(project_id: str, agent_id: str) -> Path`（Task 4/6）。

- [ ] **Step 1: 写失败测试** `backend/tests/test_kb_paths.py`

```python
"""KB 实体根解析：与 reme store.py:57-70 的三级口径一致（配置 > 环境变量 > 家目录默认）。"""

from pathlib import Path

from aitester.config import Settings
from aitester.services.kb.paths import resolve_kb_bases_dir, resolve_kb_root


def _s(**kw):
    return Settings(_env_file=None, **kw)


def test_explicit_config_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REME_KNOWLEDGE_BASES_DIR", str(tmp_path / "env"))
    got = resolve_kb_bases_dir(_s(kb_bases_dir=str(tmp_path / "cfg")))
    assert got == (tmp_path / "cfg").resolve()


def test_env_beats_default(tmp_path, monkeypatch):
    monkeypatch.setenv("REME_KNOWLEDGE_BASES_DIR", str(tmp_path / "env"))
    assert resolve_kb_bases_dir(_s()) == (tmp_path / "env").resolve()


def test_default_is_home_reme(tmp_path, monkeypatch):
    monkeypatch.delenv("REME_KNOWLEDGE_BASES_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_kb_bases_dir(_s()) == tmp_path / ".reme" / "knowledge_bases"


def test_root_joins_kb_id(tmp_path, monkeypatch):
    monkeypatch.setenv("REME_KNOWLEDGE_BASES_DIR", str(tmp_path / "env"))
    assert resolve_kb_root(_s(kb_id="zhb_kb")) == (tmp_path / "env" / "zhb_kb").resolve()
```

追加到 `backend/tests/test_kb_manager.py`（构造 manager 的方式照该文件既有用例）：

```python
def test_kb_root_dir_and_workspace_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("REME_KNOWLEDGE_BASES_DIR", raising=False)
    m = RemeKbManager(
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"), kb_id="zhb_kb"),
        data_dir=tmp_path,
    )
    assert m.kb_root_dir == (tmp_path / "bases" / "zhb_kb").resolve()
    assert m.workspace_dir("default", "kb_assistant") == tmp_path / "workspaces" / "default" / "kb_assistant"
```

- [ ] **Step 2: 跑测试确认失败** — `uv run pytest tests/test_kb_paths.py -v` → ImportError/AttributeError。
- [ ] **Step 3: 实现**

`backend/src/aitester/services/kb/paths.py`：

```python
"""KB 实体目录解析：不依赖 reme 导入的纯标准库实现。

判据逐条对齐 reme/knowledge/store.py:57-70（resolve_knowledge_bases_dir）：
配置 kb_bases_dir > 进程环境变量 REME_KNOWLEDGE_BASES_DIR > ~/.reme/knowledge_bases。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ENV_DEFAULT = "REME_KNOWLEDGE_BASES_DIR"


def resolve_kb_bases_dir(settings: Any) -> Path:
    raw = (getattr(settings, "kb_bases_dir", "") or "").strip()
    if not raw:
        raw = (os.environ.get(_ENV_DEFAULT) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".reme" / "knowledge_bases"


def resolve_kb_root(settings: Any) -> Path:
    return resolve_kb_bases_dir(settings) / settings.kb_id
```

`manager.py` 追加（放在 `is_enabled` 属性附近；顶部补 `from aitester.services.kb.paths import resolve_kb_root`）：

```python
    @property
    def kb_root_dir(self) -> Path:
        """共享 KB 实体目录（browse 接口与草案工具共用的唯一真相根）。"""
        return resolve_kb_root(self._settings)

    def workspace_dir(self, project_id: str, agent_id: str) -> Path:
        return self._data_dir / "workspaces" / project_id / agent_id
```

并把 `_kb_config()` 里 `workspace_dir=str(self._data_dir / "workspaces" / project_id / agent_id)` 改为 `workspace_dir=str(self.workspace_dir(project_id, agent_id))`。`conftest.py` 元组加 `"REME_KNOWLEDGE_BASES_DIR"`（注释同步说明：browse/paths 判据会读该环境变量）。

- [ ] **Step 4: 跑绿** — `uv run pytest tests/test_kb_paths.py tests/test_kb_manager.py -v`；再 `uv run pytest -q` 全量回归。
- [ ] **Step 5: Commit** — `feat(kb): KB 实体根三级解析 paths.py + manager kb_root_dir/workspace_dir 访问器`

---

### Task 2: browse 只读四端点（tree / file / search / scan）

**Files:**
- Create: `backend/src/aitester/interaction/kb_browse.py`
- Modify: `backend/src/aitester/main.py`（`application.state.settings = s`；`include_router(kb_browse_router)`）
- Test: `backend/tests/test_kb_browse.py`（新建，本任务先建读侧用例）

**Interfaces:**
- Consumes: Task 1 `resolve_kb_root(settings)`；`Settings.kb_enabled`。
- Produces: HTTP 端点（前端 Task 7-10 消费，响应体形状逐条对齐 serve.js handler，见各步）；模块级 `router`（APIRouter, prefix=`/api/kb/browse`）、`_resolve(root, rel) -> Path`（Task 3 复用）、`_is_text(p)`、`_hidden(name)`、`_parse_fm(text)`、`_stat_dict(p)`、`_walk_abs(root, depth=12)`。

- [ ] **Step 1: 写失败测试** `backend/tests/test_kb_browse.py`——fixture 与假 manager 复用 `test_kb_api.py:10-35` 的 `_RecordingKbManager`/`_client` 形态（复制过来，settings 传 `kb_bases_dir=str(kb_root.parent)`）：

```python
import pytest
from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app
from tests.test_kb_api import _RecordingKbManager  # 若 import 路径不通则原样复制该类


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
    s_kw = dict(kb_bases_dir=str(kb_root.parent))
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
```

- [ ] **Step 2: 跑测试确认失败** — `uv run pytest tests/test_kb_browse.py -v`（404 no route）。
- [ ] **Step 3: 实现 `kb_browse.py`（本任务只写读侧 + 守卫；写侧在 Task 3 补进同文件）**

```python
"""知识库文件浏览接口：逐条移植 prototype/serve.js:93-198 的六 handler 语义。

错误体统一 FastAPI 的 detail 键（serve.js 用 error，这是唯一键名偏差）；
状态码、文案、限额与 serve.js 一致。安全：路径锁死 KB 实体根内、隐藏目录/点文件
不可见、仅白名单文本类型、2MB 上限。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from aitester.services.kb.paths import resolve_kb_root

router = APIRouter(prefix="/api/kb/browse")

HIDDEN_DIRS = {".git", ".idea", ".vscode", ".locks", "__pycache__", "node_modules", ".obsidian"}
HIDDEN_FILES = {".ds_store", "thumbs.db"}
TEXT_EXT = {".md", ".markdown", ".txt", ".json", ".jsonl", ".py", ".js", ".ts", ".yaml", ".yml",
            ".sql", ".sh", ".bat", ".ini", ".cfg", ".csv", ".html", ".css", ".xml", ".toml",
            ".gitignore", ".log"}
MAX_TEXT = 2 * 1024 * 1024
SCAN_MD_MAX = 512 * 1024
WALK_DEPTH = 12

_FM_RE = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---")
_KV_RE = re.compile(r"^([A-Za-z0-9_\-.]+):\s*(.*)$")
_NOISE_RE = re.compile(r"\.(pyc|pyo|pack|idx|rev|sample)$")


def _root(request: Request) -> Path:
    settings = request.app.state.settings
    if not settings.kb_enabled:
        raise HTTPException(status_code=503, detail="知识库未启用或未启动")
    root = resolve_kb_root(settings)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="知识库实体目录不存在")
    return root


def _hidden(name: str) -> bool:
    return (name in HIDDEN_DIRS or name.lower() in HIDDEN_FILES or name.startswith(".")
            or bool(_NOISE_RE.search(name.lower())))


def _resolve(root: Path, rel: str) -> Path:
    """serve.js kbPath 平移：拒 NUL、越界（resolve 后必须锁在根内）、隐藏段命中一律 403。"""
    if rel and "\0" in rel:
        raise HTTPException(status_code=403, detail="路径超出知识库范围")
    clean = rel.replace("\\", "/").lstrip("/")
    root_r = root.resolve()
    cand = (root_r / clean).resolve() if clean else root_r
    if cand != root_r and root_r not in cand.parents:
        raise HTTPException(status_code=403, detail="路径超出知识库范围")
    if any(_hidden(seg) for seg in cand.relative_to(root_r).parts):
        raise HTTPException(status_code=403, detail="路径超出知识库范围")
    return cand


def _rel_of(root: Path, abs: Path) -> str:
    return abs.relative_to(root).as_posix()


def _is_text(abs: Path) -> bool:
    ext = abs.suffix.lower()
    return ext in TEXT_EXT or ext == ""


def _stat_d(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"size": st.st_size, "mtime": int(st.st_mtime_ns // 1_000_000)}


def _parse_fm(text: str) -> dict[str, str] | None:
    """只解析 frontmatter 顶层 key: value（serve.js parseFm 同款，够原型用）。"""
    m = _FM_RE.match(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        kv = _KV_RE.match(line)
        if not kv:
            continue
        v = kv.group(2).strip()
        if len(v) > 1 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        out[kv.group(1)] = v
    return out


def _walk_abs(root: Path, depth: int = 0) -> list[Path]:
    out: list[Path] = []
    stack = [(root, depth)]
    while stack:
        cur, d = stack.pop()
        for e in sorted(cur.iterdir(), key=lambda e: e.name.lower()):
            if _hidden(e.name):
                continue
            out.append(e)
            if e.is_dir() and d + 1 < WALK_DEPTH:
                stack.append((e, d + 1))
    return out


@router.get("/tree")
def browse_tree(request: Request, path: str = "") -> dict[str, Any]:
    root = _root(request)
    target = _resolve(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    items = []
    for e in target.iterdir():
        if _hidden(e.name):
            continue
        info: dict[str, Any] = {"name": e.name, "rel": _rel_of(root, e), "dir": e.is_dir()}
        try:
            info.update(_stat_d(e))
        except OSError:
            info.update({"size": 0, "mtime": 0})
        items.append(info)
    # dirs-first；zh 序用 lower() 近似（Global Constraints 偏差②）
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return {"root": str(root), "rel": _rel_of(root, target), "items": items}


@router.get("/file")
def browse_file(request: Request, path: str = "") -> dict[str, Any]:
    root = _root(request)
    target = _resolve(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录，不是文件")
    if not _is_text(target):
        raise HTTPException(status_code=415, detail="暂不支持预览该文件类型")
    st = target.stat()
    if st.st_size > MAX_TEXT:
        raise HTTPException(status_code=413, detail="文件超过 2MB，只读不加载")
    return {
        "rel": _rel_of(root, target), "name": target.name, "ext": target.suffix.lower(),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "size": st.st_size, "mtime": int(st.st_mtime_ns // 1_000_000), "editable": True,
    }


@router.get("/search")
def browse_search(request: Request, q: str = "", limit: int = 120) -> dict[str, Any]:
    root = _root(request)
    kw = q.strip().lower()
    if not kw:
        return {"root": str(root), "total": 0, "truncated": False, "hits": []}
    limit = max(1, min(limit, 400))
    hits = []
    for abs in _walk_abs(root):
        if kw not in abs.name.lower():
            continue
        try:
            info = {"name": abs.name, "rel": _rel_of(root, abs), "dir": abs.is_dir()}
            info.update(_stat_d(abs))
        except OSError:
            continue
        hits.append(info)
        if len(hits) >= limit:
            break
    hits.sort(key=lambda h: h["rel"].lower())
    return {"root": str(root), "total": len(hits), "truncated": len(hits) >= limit, "hits": hits}


@router.get("/scan")
def browse_scan(request: Request, path: str = "", limit: int = 800, md: str = "1") -> dict[str, Any]:
    root = _root(request)
    target = _resolve(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    limit = max(1, min(limit, 3000))
    only_md = md != "0"
    docs = []
    for abs in _walk_abs(target):
        if not abs.is_file():
            continue
        if only_md and abs.suffix.lower() != ".md":
            continue
        try:
            st = abs.stat()
        except OSError:
            continue
        if st.st_size > SCAN_MD_MAX:
            continue
        head = abs.read_text(encoding="utf-8", errors="replace")[:4000]
        docs.append({"rel": _rel_of(root, abs), "name": abs.name, "size": st.st_size,
                     "mtime": int(st.st_mtime_ns // 1_000_000), "fm": _parse_fm(head)})
        if len(docs) >= limit:
            break
    return {"root": str(root), "scanned": len(docs), "truncated": len(docs) >= limit, "docs": docs}
```

`main.py`：`from aitester.interaction.kb_browse import router as kb_browse_router`；`create_app` 内 `application.state.settings = s`（紧跟 `application.state.kb_manager = kb`）；`application.include_router(kb_browse_router)`（与现有 router 并列）。

- [ ] **Step 4: 跑绿** — `uv run pytest tests/test_kb_browse.py -v`；全量 `uv run pytest -q`。
- [ ] **Step 5: Commit** — `feat(kb): browse 只读四端点——tree/file/search/scan 逐条平移 serve.js 语义与防护`

---

### Task 3: browse 写两端点（PUT 带 mtime 冲突 / POST 新建）

**Files:**
- Modify: `backend/src/aitester/interaction/kb_browse.py`（追加两端点 + 请求体模型）
- Test: `backend/tests/test_kb_browse.py`（追加写侧用例）

**Interfaces:**
- Consumes: Task 2 的 `_root/_resolve/_is_text/_rel_of/MAX_TEXT`。
- Produces: `PUT /api/kb/browse/file?path=&mtime=`（`mtime` 查询参：int，缺省 0=跳过冲突检查；body `{"content": str}`；成功 200 `{rel,size,mtime}`；冲突 409 `{"detail":…, "mtime":<磁盘当前毫秒>}`）；`POST /api/kb/browse/file?path=`（成功 201 同形状；serve.js 的裸文本 body 不兼容——前后端都走 JSON，此为裁定内偏差）。

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_put_roundtrip_and_mtime(tmp_path, kb_root):
    f = kb_root / "_inbox" / "n.md"
    f.write_text("v1", encoding="utf-8")
    with _client(tmp_path, kb_root) as c:
        cur = int(f.stat().st_mtime_ns // 1_000_000)
        r = c.put("/api/kb/browse/file", params={"path": "_inbox/n.md", "mtime": cur},
                  json={"content": "v2"})
        assert r.status_code == 200 and f.read_text(encoding="utf-8") == "v2"
        assert r.json()["mtime"] >= cur and r.json()["size"] == 2


def test_put_conflict_409_keeps_disk(tmp_path, kb_root):
    f = kb_root / "_inbox" / "n.md"
    f.write_text("disk", encoding="utf-8")
    with _client(tmp_path, kb_root) as c:
        r = c.put("/api/kb/browse/file", params={"path": "_inbox/n.md", "mtime": 1},
                  json={"content": "mine"})
        assert r.status_code == 409
        assert r.json()["mtime"] == int(f.stat().st_mtime_ns // 1_000_000)
        assert f.read_text(encoding="utf-8") == "disk"


def test_put_guards(tmp_path, kb_root):
    with _client(tmp_path, kb_root) as c:
        assert c.put("/api/kb/browse/file", params={"path": "nope.md"},
                     json={"content": "x"}).status_code == 404
        assert c.put("/api/kb/browse/file", params={"path": "bin.exe"},
                     json={"content": "x"}).status_code == 415
        assert c.put("/api/kb/browse/file", params={"path": "business"},
                     json={"content": "x"}).status_code == 400
        assert c.put("/api/kb/browse/file", params={"path": "../x.md"},
                     json={"content": "x"}).status_code == 403


def test_post_create(tmp_path, kb_root):
    with _client(tmp_path, kb_root) as c:
        r = c.post("/api/kb/browse/file", params={"path": "_inbox/new.md"},
                   json={"content": "# N\n"})
        assert r.status_code == 201
        assert (kb_root / "_inbox" / "new.md").read_text(encoding="utf-8") == "# N\n"
        assert c.post("/api/kb/browse/file", params={"path": "_inbox/new.md"},
                      json={"content": "x"}).status_code == 409
        assert c.post("/api/kb/browse/file", params={"path": "ghost/x.md"},
                      json={"content": "x"}).status_code == 400
```

- [ ] **Step 2: 跑测试确认失败** — 405/404。
- [ ] **Step 3: 实现**（`kb_browse.py` 追加；顶部补 `from fastapi.responses import JSONResponse`、`from fastapi import Query`、`from pydantic import BaseModel, Field`）

```python
class KbBrowseWriteBody(BaseModel):
    content: str = Field(description="文件全文")


@router.put("/file")
def browse_put(request: Request, path: str = Query(""), mtime: int = Query(0),
               body: KbBrowseWriteBody = None) -> Any:
    root = _root(request)
    target = _resolve(root, path)
    if not _is_text(target):
        raise HTTPException(status_code=415, detail="只允许写入文本文件")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在，请用新建接口")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录")
    cur = int(target.stat().st_mtime_ns // 1_000_000)
    if mtime and cur != mtime:
        # 对齐 serve.js：乐观锁冲突不覆盖，回磁盘当前 mtime 供前端重载
        return JSONResponse(status_code=409,
                            content={"detail": "文件在页面打开后被外部修改，未覆盖", "mtime": cur})
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        raise HTTPException(status_code=413, detail="内容过大")
    target.write_bytes(data)
    return {"rel": _rel_of(root, target), "size": len(data),
            "mtime": int(target.stat().st_mtime_ns // 1_000_000)}


@router.post("/file")
def browse_post(request: Request, path: str = Query(""),
                body: KbBrowseWriteBody = None) -> Any:
    root = _root(request)
    target = _resolve(root, path)
    if not _is_text(target):
        raise HTTPException(status_code=415, detail="只允许写入文本文件")
    if target.exists():
        raise HTTPException(status_code=409, detail="同名文件已存在，请换个名字")
    if not target.parent.is_dir():
        raise HTTPException(status_code=400, detail="父目录不存在")
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        raise HTTPException(status_code=413, detail="内容过大")
    target.write_bytes(data)
    return JSONResponse(status_code=201, content={
        "rel": _rel_of(root, target), "size": len(data),
        "mtime": int(target.stat().st_mtime_ns // 1_000_000),
    })
```

（`body: KbBrowseWriteBody = None` 形参 FastAPI 自动按 JSON body 解析；若 strict 类型检查要求，可改 `body: KbBrowseWriteBody` 不带默认值——契约形状不变。）

- [ ] **Step 4: 跑绿** — `uv run pytest tests/test_kb_browse.py -v` + 全量。
- [ ] **Step 5: Commit** — `feat(kb): browse 写两端点——PUT mtime 乐观锁 409 不覆盖、POST 新建 201/409 族`

---

### Task 4: prepare_kb_write 草案工具（零写盘）

**Files:**
- Modify: `backend/src/aitester/adapters/tools/kb_tools.py`（追加输入模型 + 工具类）
- Modify: `backend/src/aitester/adapters/tools/__init__.py`（同一 kb 门内注册；`__all__` 补 `PrepareKbWriteTool`）
- Test: `backend/tests/test_kb_tools.py`（追加）

**Interfaces:**
- Consumes: Task 1 `kb.kb_root_dir`（注册方以 `PrepareKbWriteTool(kb_root=Path(kb.kb_root_dir))` 注入）。
- Produces: `PrepareKbWriteTool`（`name="prepare_kb_write"`，`response_format="content_and_artifact"`）；artifact 精确形状 `{op, path, abs_display, summary, content, base, mtime}`（`base: str|None`、`mtime: int` 毫秒，create 时 `base=None, mtime=0`）——Task 5/10 消费。

- [ ] **Step 1: 写失败测试**（追加到 `test_kb_tools.py`）

```python
from pathlib import Path

import pytest
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.kb_tools import PrepareKbWriteTool


class _StubRootKb:
    is_enabled = True

    def __init__(self, root: Path):
        self.kb_root_dir = root

    def run_job_sync(self, name, **kwargs):  # pragma: no cover
        raise AssertionError("prepare_kb_write 不应触发任何 job")


def _tool(tmp_path):
    (tmp_path / "business" / "wiki").mkdir(parents=True)
    (tmp_path / "business" / "wiki" / "x.md").write_text("old", encoding="utf-8")
    return PrepareKbWriteTool(kb_root=tmp_path)


def test_prepare_modify_draft_zero_write(tmp_path):
    tool = _tool(tmp_path)
    x = tmp_path / "business" / "wiki" / "x.md"
    out, art = tool._run(op="modify", path="business/wiki/x.md", content="new", summary="改一句")
    assert x.read_text(encoding="utf-8") == "old"  # 零写盘
    assert art["op"] == "modify" and art["base"] == "old"
    assert art["mtime"] == int(x.stat().st_mtime_ns // 1_000_000)
    assert art["abs_display"] == str(x.resolve()) and art["content"] == "new"
    assert "confirm" in out.lower()  # 模型侧英文确认句


def test_prepare_create_draft(tmp_path):
    tool = _tool(tmp_path)
    (tmp_path / "_inbox").mkdir()  # create 要求父目录存在（fixture 只建了 business/wiki）
    out, art = tool._run(op="create", path="_inbox/n.md", content="# N", summary="新建")
    assert art["base"] is None and art["mtime"] == 0 and art["op"] == "create"
    assert not (tmp_path / "_inbox" / "n.md").exists()  # 零写盘
    assert "NOT yet written" in out


def test_prepare_validation_errors(tmp_path):
    tool = _tool(tmp_path)
    with pytest.raises(ToolException):
        tool._run(op="modify", path="../escape.md", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="modify", path="business/wiki/x.txt", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="create", path="business/wiki/x.md", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="modify", path="ghost.md", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="create", path="ghostdir/x.md", content="c", summary="s")


def test_registry_registers_prepare_kb_write(tmp_path):
    reg = build_default_registry(cwd=".", kb=_StubRootKb(tmp_path), agent_id="kb_assistant")
    assert reg.get("prepare_kb_write") is not None
    assert reg.get("prepare_kb_write").kb_root == tmp_path
```

- [ ] **Step 2: 跑测试确认失败** — ImportError。
- [ ] **Step 3: 实现**（`kb_tools.py` 顶部补 `from pathlib import Path`、`from typing import Literal`、`from langchain_core.tools import ToolException`；文件追加）

```python
class PrepareKbWriteInput(BaseModel):
    op: Literal["create", "modify"] = Field(
        description="create: new file; modify: overwrite an existing file after user confirmation.")
    path: str = Field(
        description="Markdown path relative to the knowledge-base root, e.g. '_inbox/note.md'. Only .md is accepted.")
    content: str = Field(
        description="Full file content after writing, frontmatter included.")
    summary: str = Field(
        description="One-line description of what this draft does, shown on the user's confirmation card.")


class PrepareKbWriteTool(AiTooler):
    name: str = "prepare_kb_write"
    description: str = (
        "Prepare a knowledge-base write as a user-confirmable draft. This tool never touches disk: "
        "it only validates the target and returns a draft the user must confirm in the UI before "
        "anything is saved. Use it for every knowledge-base write and never claim the write has "
        "happened — say the user needs to confirm the draft card instead."
    )
    args_schema: type[BaseModel] = PrepareKbWriteInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"
    kb_root: Path = Path(".")

    def _run(self, op: str, path: str, content: str, summary: str) -> tuple[str, dict[str, Any]]:
        rel = (path or "").strip().replace("\\", "/").lstrip("/")
        if not rel or "\0" in rel:
            raise ToolException("Invalid path: provide a non-empty path relative to the knowledge base root.")
        if not rel.lower().endswith(".md"):
            raise ToolException("Only Markdown (.md) files can be written to the knowledge base.")
        root = Path(self.kb_root)
        if not root.is_dir():
            raise ToolException("Knowledge base root does not exist on disk yet.")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise ToolException("Invalid path: target escapes the knowledge base root.")
        rel = target.relative_to(root).as_posix()
        if op == "modify":
            if not target.is_file():
                raise ToolException(f"Cannot modify: {rel} does not exist; use op 'create' instead.")
            base = target.read_text(encoding="utf-8", errors="replace")
            mtime = int(target.stat().st_mtime_ns // 1_000_000)
        else:
            if target.exists():
                raise ToolException(f"Cannot create: {rel} already exists; use op 'modify' or another name.")
            if not target.parent.is_dir():
                raise ToolException(f"Cannot create: parent directory of {rel} does not exist.")
            base, mtime = None, 0
        draft = {"op": op, "path": rel, "abs_display": str(target), "summary": summary,
                 "content": content, "base": base, "mtime": mtime}
        verb = "new file" if op == "create" else "modification"
        return (f"Draft ready ({verb}, NOT yet written): {rel}. "
                "Ask the user to confirm the draft card; nothing is saved until they confirm."), draft
```

`__init__.py`：`from aitester.adapters.tools.kb_tools import KbSaveTool, KbSearchTool, PrepareKbWriteTool`；`build_default_registry` 的 kb 门内追加 `registry.register(PrepareKbWriteTool(kb_root=Path(kb.kb_root_dir)))`（顶部 `from pathlib import Path`）；`__all__` 加 `"PrepareKbWriteTool"`。既有 test_kb_tools 里给 `build_default_registry` 喂的旧桩若无 `kb_root_dir` 会 AttributeError——**这正是期望**：同步更新既有桩类加 `kb_root_dir` 属性（值传 tmp 根），不改产品代码容错。

- [ ] **Step 4: 跑绿** — `uv run pytest tests/test_kb_tools.py -v` + 全量。
- [ ] **Step 5: Commit** — `feat(kb): prepare_kb_write 草案工具——零写盘、校验后走 artifact 通道出草案`

---

### Task 5: 草案透传链路（run_graph → ChatService → SendResponse.drafts → chat_send）

**Files:**
- Modify: `backend/src/aitester/orchestration/agent_graph.py:68-86`（run_graph 收 drafts）
- Modify: `backend/src/aitester/services/chat.py:34-65`（_complete 透传）
- Modify: `backend/src/aitester/interaction/schemas.py:22-25`（SendResponse 扩字段 + KbDraft）
- Modify: `backend/src/aitester/interaction/router.py:64-68`（chat_send 带 drafts）
- Test: `backend/tests/test_agent_graph.py`、`backend/tests/test_chat_service.py`、`backend/tests/test_api.py` 各追加

**Interfaces:**
- Consumes: Task 4 artifact 形状。
- Produces: `run_graph(...) -> {"reply": str, "tool_traces": list[dict], "drafts": list[dict]}`；`ChatService.send/​_complete` 返回值含 `drafts`；`SendResponse.drafts: list[KbDraft]`（默认 `[]`，旧消费方向后兼容）；`KbDraft` 字段与 artifact 键一一对应。

- [ ] **Step 1: 写失败测试**

`test_agent_graph.py` 追加（该文件已有 `ScriptedProvider`/`_tools`，工具表需带 prepare——直接实例化工具入表）：

```python
def test_run_graph_collects_kb_drafts(tmp_path):
    from aitester.adapters.tools.kb_tools import PrepareKbWriteTool
    (tmp_path / "_inbox").mkdir()
    tools = [PrepareKbWriteTool(kb_root=tmp_path)]
    script = [
        AIMessage(content="", tool_calls=[{"name": "prepare_kb_write", "args": {
            "op": "create", "path": "_inbox/n.md", "content": "# N", "summary": "新建"},
            "id": "c1", "type": "tool_call"}]),
        AIMessage(content="草案已生成，请点确认"),
    ]
    result = run_agent(ScriptedProvider(script), tools, [HumanMessage(content="记一笔")])
    assert result["drafts"][0]["path"] == "_inbox/n.md"
    assert result["drafts"][0]["op"] == "create"
    assert not (tmp_path / "_inbox" / "n.md").exists()
    assert [t["tool"] for t in result["tool_traces"]] == ["prepare_kb_write"]
```

（`AIMessage/HumanMessage` 该文件顶部已有 import；`run_agent` 与 `run_graph` 同实现，直接复用。）

`test_chat_service.py` 追加（顶部若缺则补 `from aitester.adapters.llm import MockProvider`、`from langchain_core.messages import AIMessage, ToolMessage`）：

```python
def test_send_passes_drafts_through():
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage, ToolMessage

    draft = {"op": "create", "path": "_inbox/n.md", "abs_display": "P", "summary": "s",
             "content": "c", "base": None, "mtime": 0}

    class _FakeGraph:
        def invoke(self, state):
            return {"messages": [
                AIMessage(content="", tool_calls=[{"name": "prepare_kb_write", "args": {}, "id": "c1", "type": "tool_call"}]),
                ToolMessage(content="Draft ready", tool_call_id="c1", name="prepare_kb_write", artifact=draft),
                AIMessage(content="草案已生成"),
            ]}

    from aitester.services.agent_runtime import AgentInstance
    runtime = SimpleNamespace(build=lambda aid, sid, provider_override=None: AgentInstance(
        agent_id=aid, system_prompt="p", provider=MockProvider(), tools=[],
        build_graph=lambda provider, tools: _FakeGraph()))
    service = ChatService(agent_runtime=runtime)
    out = service.send("kb-console", "记一笔", "case_design")
    assert out["drafts"] == [draft]
    assert out["reply"] == "草案已生成"
```

`test_api.py`（或 `test_kb_api.py` 同款 `_client`）追加：

```python
def test_chat_send_returns_drafts(tmp_path):
    app = create_app(
        model_config_path=tmp_path / "m.json", capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None), kb_manager=_RecordingKbManager())
    app.state.chat_service = SimpleNamespace(send=lambda sid, msg, aid: {
        "reply": "r", "trace": ["services"], "model": "m",
        "drafts": [{"op": "create", "path": "a.md", "abs_display": "P",
                     "summary": "s", "content": "c", "base": None, "mtime": 0}]})
    with TestClient(app) as c:
        j = c.post("/api/chat/send", json={"message": "写点什么"}).json()
    assert j["drafts"][0]["path"] == "a.md"


def test_chat_send_drafts_defaults_empty(tmp_path):
    app = create_app(
        model_config_path=tmp_path / "m.json", capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None), kb_manager=_RecordingKbManager())
    app.state.chat_service = SimpleNamespace(send=lambda sid, msg, aid: {
        "reply": "r", "trace": ["services"], "model": "m"})  # 旧形态返回：无 drafts 键
    with TestClient(app) as c:
        j = c.post("/api/chat/send", json={"message": "echo 我"}).json()
    assert j["drafts"] == []  # 向后兼容：SendResponse 默认空列表
```

- [ ] **Step 2: 跑测试确认失败** — KeyError 'drafts'。
- [ ] **Step 3: 实现**

`agent_graph.py::run_graph`（68-86）返回前加收集：

```python
    reply = ""
    tool_traces: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    for msg in result["messages"]:
        if isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
            reply = str(msg.content)
        if isinstance(msg, ToolMessage):
            tool_traces.append({"tool": msg.name or "", "result": str(msg.content)})
            # prepare_kb_write 的草案走 artifact 通道（模型不可见），只发给 UI 确认
            if msg.name == "prepare_kb_write" and getattr(msg, "artifact", None):
                drafts.append(msg.artifact)
    return {"reply": reply, "tool_traces": tool_traces, "drafts": drafts}
```

`chat.py::_complete`：函数开头 `drafts: list = []`；`result = run_graph(...)` 后 `drafts = result.get("drafts", [])`；返回 `{"reply": reply, "trace": trace, "model": provider.model_ref, "drafts": drafts}`。`echo()` 自选键返回，不受影响。

`schemas.py`：

```python
class KbDraft(BaseModel):
    op: str
    path: str
    abs_display: str
    summary: str = ""
    content: str
    base: str | None = None
    mtime: int = 0


class SendResponse(BaseModel):
    reply: str
    trace: list[str]
    model: str
    drafts: list[KbDraft] = []
```

`router.py::chat_send` 返回加 `drafts=result.get("drafts", [])`。

- [ ] **Step 4: 跑绿** — `uv run pytest tests/test_agent_graph.py tests/test_chat_service.py tests/test_api.py tests/test_kb_api.py -v` + 全量。
- [ ] **Step 5: Commit** — `feat(kb): 草案透传链路——run_graph 收 artifact、SendResponse 扩 drafts（默认空、向后兼容）`

---

### Task 6: kb_assistant 内置智能体（强制绑定，能力配置不可见）

**Files:**
- Create: `backend/src/aitester/agents/prompts/kb_assistant.md`
- Modify: `backend/src/aitester/agents/catalog.py`（追加 `KB_ASSISTANT_SPEC`/`PLATFORM_AGENT_CATALOG`，`find_agent` 扩查）
- Modify: `backend/src/aitester/agents/__init__.py`（导出）
- Modify: `backend/src/aitester/services/agent_runtime.py`（平台智能体特例）
- Test: `backend/tests/test_agents.py`、`backend/tests/test_agent_runtime.py` 追加

**Interfaces:**
- Consumes: Task 4 注册（`prepare_kb_write` 已在注册表）、Task 1 `kb.workspace_dir/kb_root_dir`、Task 5 drafts 链路。
- Produces: `find_agent("kb_assistant") -> AgentSpec`（`default_tool_ids=("read","grep_search","glob_search","knowledge_search","prepare_kb_write")`）；`POST /api/chat/send {"agent_id":"kb_assistant","session_id":"kb-console"}` 可用；`AgentRuntime` 对 kb_assistant 的实例池键 `("default","kb_assistant")`、会话记忆键 `kb_assistant:kb-console`（前端固定值，Task 10）。

- [ ] **Step 1: 写提示词文件** `backend/src/aitester/agents/prompts/kb_assistant.md`（全文照抄，UI 面向用户为中文）

```markdown
你是 AiTester「知识库助手」，在知识库页面右栏工作，负责共享知识库的检索与受控更新。

# 工作原则
1. 检索优先：用户说「检索/查找/有没有某知识」时，先调 `knowledge_search`，命中后原样给出路径与得分摘要；文件名、目录、清单类诉求用 `glob_search` / `grep_search` / `read` 完成。
2. 写盘只走草案：任何写入诉求（新建笔记、改 description、目录索引、P0 清单、重复用例盘点）都必须调 `prepare_kb_write` 生成草案——该工具不写盘，落盘由用户在草案卡片上点「✓ 确认写入磁盘」完成。回复里要提示「请在右侧确认草案」，严禁声称已经写入。
3. 修改要基于最新内容：改现有文件前先 `read` 目标文件，产出全文时保留原内容只做指定增量；新节点带 frontmatter（name/description/bucket/status/confidence/updated_by_agent: aitester_kb_assistant/updated_at）。
4. 盘点类任务先扫全再出单：用 glob/grep 完成全量扫描后汇总为一份草案，不要边扫边出多份。
5. 新建默认落 `_inbox/`，除非用户指定桶；意图不明时先问一个澄清问题。
6. 回答简洁中文，面向测试同学；不要把工具的内部英文提示原样丢给用户。
```

- [ ] **Step 2: 写失败测试**

`test_agents.py` 追加：

```python
def test_kb_assistant_is_platform_agent():
    from aitester.agents import AGENT_CATALOG, PLATFORM_AGENT_CATALOG, find_agent
    spec = find_agent("kb_assistant")
    assert spec is not None
    assert spec.name == "知识库助手"
    assert spec.default_tool_ids == ("read", "grep_search", "glob_search", "knowledge_search", "prepare_kb_write")
    assert all(s.id != "kb_assistant" for s in AGENT_CATALOG)      # 不进能力配置目录
    assert [s.id for s in PLATFORM_AGENT_CATALOG] == ["kb_assistant"]


def test_capabilities_view_unchanged(tmp_path):
    # GET /api/capabilities 的 agents 仍只有 case_design（_client 形态同 test_kb_api.py:28-35）
    from types import SimpleNamespace  # noqa: F401
    from fastapi.testclient import TestClient
    from aitester.config import Settings
    from aitester.main import create_app
    from tests.test_kb_api import _RecordingKbManager

    app = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        settings=Settings(_env_file=None),
        kb_manager=_RecordingKbManager(),
    )
    with TestClient(app) as c:
        j = c.get("/api/capabilities").json()
    assert [a["id"] for a in j["agents"]] == ["case_design"]
    assert "prepare_kb_write" not in {t["id"] for t in j["tools"]}  # 设置页工具表保持不可见
```

`test_agent_runtime.py` 追加（该文件已有的构造 helper 复用；无则用下方独立构造）：

```python
def test_kb_assistant_forced_binding_ignores_capability(tmp_path):
    from aitester.agents import find_agent
    from aitester.adapters.tools import FileObservationStore
    from aitester.services.agent_runtime import AgentRuntime
    from aitester.services.capability_config import CapabilityConfigService
    from aitester.services.model_config import ModelConfigService
    from aitester.storage import FileJsonConfigRepository
    from aitester.adapters.llm import MockProvider

    s = Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"))
    model = ModelConfigService(FileJsonConfigRepository(tmp_path / "m.json"), s)
    cap = CapabilityConfigService(FileJsonConfigRepository(tmp_path / "c.json"), model)

    class _StubKb:
        is_enabled = True
        kb_root_dir = tmp_path / "bases" / "zhb_kb"
        def workspace_dir(self, project_id, agent_id):
            return tmp_path / "workspaces" / project_id / agent_id
        def run_job_sync(self, *a, **kw):  # pragma: no cover
            raise AssertionError

    runtime = AgentRuntime(cap, model, FileObservationStore(), kb=_StubKb())
    inst = runtime.build("kb_assistant", "kb-console", provider_override=MockProvider())
    assert [t.name for t in inst.tools] == [
        "read", "grep_search", "glob_search", "knowledge_search", "prepare_kb_write"]
    read_tool = inst.tools[0]
    assert str(read_tool.cwd) == str(tmp_path / "workspaces" / "default" / "kb_assistant")
    # 不受能力勾选管辖：case_design 工具集清空也不影响 kb_assistant
    cap.set_agent_tools("case_design", [])
    inst2 = runtime.build("kb_assistant", "kb-console", provider_override=MockProvider())
    assert len(inst2.tools) == 5


def test_kb_assistant_unregistered_kb_degrades(tmp_path):
    # 构造与上一用例相同（Settings/Capability/Model 服务三段式），仅 kb=None ——
    # 复用上一用例把公共构造提成 fixture 亦可，断言不变：
    runtime = AgentRuntime(cap, model, FileObservationStore(), kb=None)
    inst = runtime.build("kb_assistant", "s", provider_override=MockProvider())
    assert [t.name for t in inst.tools] == ["read", "grep_search", "glob_search"]
```

- [ ] **Step 3: 跑测试确认失败** — ImportError/ConfigNotFoundError。
- [ ] **Step 4: 实现**

`catalog.py`（`find_agent` 之前追加，`find_agent` 循环改 `for spec in (*AGENT_CATALOG, *PLATFORM_AGENT_CATALOG)`）：

```python
KB_ASSISTANT_SPEC = AgentSpec(
    id="kb_assistant",
    icon="📚",
    name="知识库助手",
    desc="知识库页内置助手：检索共享知识库、生成写入草案，用户确认后才落盘。",
    prompt=_load_prompt("kb_assistant"),
    default_tool_ids=(
        "read",
        "grep_search",
        "glob_search",
        "knowledge_search",
        "prepare_kb_write",
    ),
)

# 平台功能智能体：刻意不进 AGENT_CATALOG/DEFAULT_AGENT_STATE——能力配置、设置页、
# 聊天页下拉因此不可见；工具面由 AgentRuntime 强制绑定（spec 裁定②，勿改成走勾选）。
PLATFORM_AGENT_CATALOG: tuple[AgentSpec, ...] = (KB_ASSISTANT_SPEC,)
```

`agents/__init__.py` 导出 `KB_ASSISTANT_SPEC`、`PLATFORM_AGENT_CATALOG`。

`agent_runtime.py`：`from aitester.agents import PLATFORM_AGENT_CATALOG, find_agent`；`build()` 在 `spec is None` 检查之后插入：

```python
        if spec.id in {s.id for s in PLATFORM_AGENT_CATALOG}:
            return self._build_platform_agent(spec, session_id, provider_override)
```

新增方法（注释指回 spec 裁定②）：

```python
    def _build_platform_agent(
        self, spec, session_id: str, provider_override: LlmProvider | None
    ) -> AgentInstance:
        """平台功能智能体：强制绑定 spec.default_tool_ids，不读能力配置勾选状态。"""
        provider: LlmProvider = (
            provider_override
            if provider_override is not None
            else self._model_config.build_provider(self._model_config.default_uid)
        )
        cwd = "."
        if self._kb is not None:
            workspace = self._kb.workspace_dir("default", spec.id)
            workspace.mkdir(parents=True, exist_ok=True)
            cwd = str(workspace)
        registry = build_default_registry(
            cwd=cwd,
            session_id=f"{spec.id}:{session_id}",
            observed=self._observations,
            kb=self._kb,
            agent_id=spec.id,
        )
        return AgentInstance(
            agent_id=spec.id,
            system_prompt=spec.prompt,
            provider=provider,
            tools=registry.get_many(list(spec.default_tool_ids)),
            build_graph=get_graph_builder(spec.graph_builder),
        )
```

（`get_many` 天然按注册表存在性取交集：kb 关闭/未注入时 `knowledge_search`/`prepare_kb_write` 不在注册表，只剩三件套——spec「清单 ∩ 实际可注册集合」。`workspace/<project>/<agent>` 的 knowledge junction 由 reme 实例首启挂载，既有机制。）

- [ ] **Step 5: 跑绿** — `uv run pytest tests/test_agents.py tests/test_agent_runtime.py -v` + 全量。
- [ ] **Step 6: Commit** — `feat(kb): kb_assistant 平台智能体——强制绑定五工具、不进能力配置、workspace 目录为 cwd`

---

### Task 7: 前端 API 层（browse 六函数 + chatSend + ApiError + vite 代理口）

**Files:**
- Modify: `frontend/src/api/client.ts`（ApiError、类型、browse/chat 函数；`getKbStatus/getKbBases/kbSearch/kbSave` 保留——项目管理与后续清理仍引用）
- Modify: `frontend/vite.config.ts`（proxy target 支持 `process.env.VITE_PROXY_TARGET` 覆盖，缺省仍 `http://127.0.0.1:8000`——走查端口不碰用户 8000）

**Interfaces:**
- Consumes: Task 2/3 端点、Task 5 `SendResponse.drafts`。
- Produces: `ApiError{status,data}`；`kbTree/kbReadFile/kbSearchFiles/kbScanFiles/kbPutFile/kbPostFile`；`chatSend(sessionId, message, agentId): Promise<SendResponse>`；类型 `KbBrowseItem/KbTreeResponse/KbFileResponse/KbSearchHit/KbSearchResponse/KbScanDoc/KbScanResponse/KbWriteResponse/KbDraft/SendResponse`（Task 8-10 消费）。

- [ ] **Step 1: 实现**（`client.ts` 追加/替换；`apiFetch` 的 catch 分支里 `throw` 改为携带状态与响应体）

```ts
export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}
```

`apiFetch` 内 `throw new Error(detail)` → `throw new ApiError(detail, resp.status, body)`（body 解析失败时传 `null`）。其余既有函数不改行为（ApiError 是 Error 子类，旧 catch 路径兼容）。

类型与函数：

```ts
export interface KbBrowseItem { name: string; rel: string; dir: boolean; size: number; mtime: number }
export interface KbTreeResponse { root: string; rel: string; items: KbBrowseItem[] }
export interface KbFileResponse { rel: string; name: string; ext: string; content: string; size: number; mtime: number; editable: boolean }
export interface KbSearchHit { name: string; rel: string; dir: boolean; size: number; mtime: number }
export interface KbSearchResponse { root: string; total: number; truncated: boolean; hits: KbSearchHit[] }
export interface KbScanDoc { rel: string; name: string; size: number; mtime: number; fm: Record<string, string> | null }
export interface KbScanResponse { root: string; scanned: number; truncated: boolean; docs: KbScanDoc[] }
export interface KbWriteResponse { rel: string; size: number; mtime: number }

const browseApi = (sub: string, params: Record<string, string | number>) =>
  `/api/kb/browse/${sub}?${new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])).toString()}`;

export function kbTree(path: string): Promise<KbTreeResponse> {
  return apiFetch<KbTreeResponse>(browseApi("tree", { path }));
}
export function kbReadFile(path: string): Promise<KbFileResponse> {
  return apiFetch<KbFileResponse>(browseApi("file", { path }));
}
export function kbSearchFiles(q: string, limit = 120): Promise<KbSearchResponse> {
  return apiFetch<KbSearchResponse>(browseApi("search", { q, limit }));
}
export function kbScanFiles(path: string, limit = 800, md = true): Promise<KbScanResponse> {
  return apiFetch<KbScanResponse>(browseApi("scan", { path, limit, md: md ? "1" : "0" }));
}
export function kbPutFile(path: string, content: string, mtime: number): Promise<KbWriteResponse> {
  return apiFetch<KbWriteResponse>(browseApi("file", { path, mtime }), {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ content }) });
}
export function kbPostFile(path: string, content: string): Promise<KbWriteResponse> {
  return apiFetch<KbWriteResponse>(browseApi("file", { path }), {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ content }) });
}

export interface KbDraft {
  op: "create" | "modify";
  path: string;
  abs_display: string;
  summary: string;
  content: string;
  base: string | null;
  mtime: number;
}

export interface SendResponse {
  reply: string;
  trace: string[];
  model: string;
  drafts: KbDraft[];
}

export function chatSend(sessionId: string, message: string, agentId: string): Promise<SendResponse> {
  return apiFetch<SendResponse>("/api/chat/send", {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ session_id: sessionId, message, agent_id: agentId }) });
}
```

`vite.config.ts`：`target: process.env.VITE_PROXY_TARGET || "http://127.0.0.1:8000"`。

- [ ] **Step 2: 门禁** — `npm run build` 0 错误。
- [ ] **Step 3: Commit** — `feat(kb): 前端 API 层——browse 六函数、chatSend、ApiError 带状态码、vite 代理可覆盖端口`

---

### Task 8: 前端工具函数 + 三栏骨架 + 目录树栏 + CSS 移植

**Files:**
- Create: `frontend/src/pages/kb/utils.ts`
- Create: `frontend/src/pages/kb/KbTreePane.tsx`
- Modify: `frontend/src/pages/KbPage.tsx`（本任务重写为三栏骨架：布局/收起/拖拽/toast + 树栏；中栏、右栏占位空壳，Task 9/10 填充）
- Modify: `frontend/src/App.css`（现三区表单 kb 样式段整段替换为原型 `.kb-*` 族 + toast/modal/resizer 相关样式）

**Interfaces:**
- Consumes: Task 7 `kbTree/kbSearchFiles`、`ApiError`。
- Produces: `utils.ts` 导出 `escapeHtml/fmtSize/fmtTime/kbBytes/kbSlug/kbToday/mdRender/splitFm/kbDiffHtml/KB_BUCKETS/kbIsMd/kbDirOf/kbJoin/kbDisp` 与常量 `KB_FM_KEYS`；`KbTreePane` props（见实现）；KbPage 暴露给后续任务的类型 `KbDocState { rel; name; ext; content; disk; mtime; mode: "view" | "edit" }` 与 `toast(msg)`。

- [ ] **Step 1: `utils.ts`**——小函数直接照抄原型（`:2346-2355` 一行式族、`:1448` escapeHtml、`:2357` splitFm、`kbDiff` 改产 React 友好的 HTML 串版 `kbDiffHtml(oldT, newT): string`，逻辑逐行照原型 `kbDiff`）；`mdRender` 与 `wsIcon` 从原型 `index.html:1619` 起逐行转写为 TS 纯函数（`mdRender(src: string): string`、`wsIcon(name: string): string`），**不引任何 npm 包**；`splitFm(text): { fm: Record<string, string> | null; body: string }` 照原型 `:2357-2370`。另加：

```ts
export const KB_FM_KEYS = ["name", "description", "bucket", "status", "confidence",
  "priority", "requirement_id", "updated_by_agent", "updated_at", "signals"] as const;
export const KB_BUCKETS = ["_inbox", "business/dbInfo", "business/wiki", "test/test_cases",
  "test/defects", "test/test_design", "tools"];
```

- [ ] **Step 2: `KbTreePane.tsx`**

```ts
interface KbTreePaneProps {
  root: string;
  kids: Record<string, KbBrowseItem[]>;        // 目录 rel（''=根）→ 已加载子项
  expanded: Record<string, boolean>;
  activeRel: string;
  total: number | null;                          // 计数 num
  searchHits: KbSearchHit[] | null;              // 非 null = 搜索结果态
  onToggleDir: (rel: string) => void;
  onOpenFile: (rel: string) => void;
}
```

渲染规则照原型 `:2388-2424`（kbRenderTree）：搜索态列 hits（含目录项点击无效仅提示）；树态深度优先按 `expanded` 展开、行 = `arr(▸/▾) + 📁/wsIcon 文件名 + 体积`；行点击目录 `onToggleDir`、文件 `onOpenFile`；底部 `root` 路径脚注。**树数据加载逻辑留在 KbPage**（`kids` 缺失时 `kbTree(rel)` 拉取并缓存，懒加载即首次展开才拉；根在 mount 时加载一次并 `setTotal(items.length)`；搜索 300ms 防抖 + 序号防竞态，照原型 `kbSearchSeq` 写法）。

- [ ] **Step 3: KbPage 三栏骨架**

结构照原型 `:696-760` markup 移植：`div.kb-layout(side-hidden? chat-hidden?)` 内 `aside.kb-side`（头部「📚 目录 + num + « 收起 + 「📁 目录」恢复钮」）/ `div.resizer#kbResizer` / `section.kb-main`（Task 9 占位空 div）/ `div.resizer#kbChatResizer` / `aside.kb-chat`（Task 10 占位）。收起/恢复行为与类名逐条对齐既有裁定（« 收起、带文字按钮恢复）。拖拽：`useEffect` 里给两个 resizer 绑 mousedown→document mousemove→mouseup，写 CSS 变量 `--kbw/--kbc`（clamp 180–560 / 260–640），照原型 KB JS 段的 resizer 代码（grep `kbResizer` 于 `:2700-2741`）转 hook。toast 组件照原型 `:1512` + markup 转成 `<div className={"toast" + (msg ? " show" : "")}>`。

- [ ] **Step 4: CSS 移植**：`prototype/index.html:472-547` 的 `.kb-*` 全族（含 `.kb-layout/.kb-side/.kb-main/.kb-chat/.kb-tree/.kb-msg/.kb-draft/.kb-bucket/.resizer` 相关）+ 原型中 `.toast`、`#kbNewMask` 弹窗样式（grep `.toast`、`.kb-new` 定位行段）搬入 `App.css`，删除现三区表单专用样式段（保留其他页共用变量）。
- [ ] **Step 5: 门禁** — `npm run build` 0 错误。
- [ ] **Step 6: Commit** — `feat(kb): 前端三栏骨架——原型 .kb-* 样式整族移植、目录树栏懒加载+文件名搜索、双 resizer 与收起恢复`

---

### Task 9: 前端编辑栏（预览/编辑/保存/放弃/重载/新建笔记）

**Files:**
- Create: `frontend/src/pages/kb/KbEditorPane.tsx`
- Modify: `frontend/src/pages/KbPage.tsx`（接线：打开文件、kbWrite 冲突处理、新建笔记弹窗状态）

**Interfaces:**
- Consumes: Task 7 `kbReadFile/kbPutFile/kbPostFile/ApiError`；Task 8 `KbDocState`、utils。
- Produces: `KbEditorPane` props；KbPage 内 `kbWrite(rel, content, mode, mtime?): Promise<KbWriteResponse | null>`（Task 10 草案卡确认复用同一函数——409 自动重载语义统一）。

- [ ] **Step 1: `KbEditorPane.tsx`**——照原型 `:697-735` 中部 markup + `:2427-2500`（kbOpen/kbPaint/kbStat/kbSetMode）逻辑转写：

```ts
interface KbEditorPaneProps {
  doc: KbDocState | null;
  dirty: boolean;
  onMode: (m: "view" | "edit") => void;   // edit→view 时 KbPage 先回收 textarea 值
  onEditContent: (v: string) => void;
  onSave: () => void;
  onDiscard: () => void;
  onReload: () => void;
  onNewNote: () => void;
}
```

行为逐条对齐：面包屑（文件名 + 父路径，原型 kbCrumbs）；元信息 chips（`KB_FM_KEYS` 过滤非空 + 非 md 追加「只读 · 仅 Markdown 可编辑」+ 体积/磁盘时间尾 chip）；tabs 首列文本 `ro ? "原文" : "预览"`、非 md `hidden` 编辑 tab；view+md → `dangerouslySetInnerHTML: mdRender(body)`；view+非 md → `<pre>` 原文；edit → textarea 绑定 `onEditContent`；保存/放弃按钮仅 edit 态显示；状态栏 `rel · N 行 · 体积 · 磁盘时间` + 未保存标记。确认文案照原型：保存 `确认写入磁盘？\n\n{绝对路径}\n\n这是知识库真实文件，保存会直接覆盖。`；放弃 `放弃未保存的修改，恢复成磁盘内容？`；重载有脏 `有未保存修改，重新载入会丢弃它们，继续？`（均为 `window.confirm`）。

- [ ] **Step 2: KbPage 接线**

`kbOpen(rel, force)`：`kbReadFile` → 填 `doc`（`disk` 与 `content` 同初值，mode view）；失败 ApiError 走 toast。`kbWrite`：

```ts
async function kbWrite(rel: string, content: string, mode: "PUT" | "POST", mtime?: number) {
  try {
    const j = mode === "PUT"
      ? await kbPutFile(rel, content, mtime !== undefined ? mtime : doc?.mtime ?? 0)
      : await kbPostFile(rel, content);
    if (rel === doc?.rel) setDoc(d => d && { ...d, content: j ? content : d.content, disk: content, mtime: j.mtime });
    // 目录刷新：kids 删除 rel 所在目录缓存再重拉（原型 kbWrite 尾部同款）
    toast(`已写入 ${j.rel} · 索引自动收敛后可被检索（约数十秒）`);
    return j;
  } catch (err) {
    if (err instanceof ApiError && err.status === 409 && mode === "PUT") {
      toast("文件在别处被改过，已重新载入磁盘最新内容（未保存的修改已丢弃）");
      void kbOpen(rel, true);
      return null;
    }
    toast(err instanceof Error ? err.message : String(err));
    return null;
  }
}
```

（409 自动重载只保编辑路径语义；草案卡确认失败同样复用此函数，Task 10 卡片据返回值置态。）

- [ ] **Step 3: 新建笔记弹窗**——markup/逻辑照原型 `openKbNew` + `btnKbNewCreate`（`:2500-2545` 段）：目录默认当前打开文件的父目录或 `_inbox`；七个桶快捷 chips（`KB_BUCKETS`）；标题→`kbSlug` 自动文件名（补 `.md`）；模板 frontmatter 全文照原型字符串（`updated_by_agent: aitester_kb_assistant`，confidence 0.5）；成功后关窗、`kbOpen(rel)` 并进编辑态。
- [ ] **Step 4: 门禁** — `npm run build` 0 错误。
- [ ] **Step 5: Commit** — `feat(kb): 前端编辑栏——预览/编辑双 tab、mtime 冲突自动重载、新建笔记弹窗对齐原型`

---

### Task 10: 前端助手栏（对话 + 草案卡 + 确认写入）

**Files:**
- Create: `frontend/src/pages/kb/KbAssistantPane.tsx`、`frontend/src/pages/kb/KbDraftCard.tsx`
- Modify: `frontend/src/pages/KbPage.tsx`（助手状态与接线）

**Interfaces:**
- Consumes: Task 7 `chatSend`（固定 `agent_id="kb_assistant"`、`session_id="kb-console"`）、`KbDraft`；Task 9 `kbWrite`。
- Produces: 完整 /kb 三栏页（spec 用户裁定 1-4 全部落地）。

- [ ] **Step 1: `KbDraftCard.tsx`**——照原型 `kbDraftCard`（`:2560-2595`）转写：

```ts
interface KbDraftCardProps {
  draft: KbDraft;
  state: "pending" | "writing" | "done" | "canceled" | "failed";
  writtenAt?: number;
  onConfirm: () => void;
  onCancel: () => void;
}
```

渲染：头部徽标 `新建/修改` + 「待写入草案」+ 右态标（pending 「未写入」/done 「已写入 · 时间」/canceled 「已取消」/failed 「写入失败」）；`abs_display` 路径行；`summary` 行（create 缺省「新建文件，不改动任何原始笔记」）；diff 区——create 显 `content` 前 14 行 `+ `（`.add`），modify 用 `kbDiffHtml(base, content)`；pending 态两按钮「✓ 确认写入磁盘」「取消草案」，done/canceled 隐藏。确认前 `window.confirm`：`确认写入知识库磁盘？\n{新建|覆盖}：{abs_display}`。
- [ ] **Step 2: `KbAssistantPane.tsx`**——照原型 `:737-758` + `kbAsk/kbSay`（`:2548-2560`、`:2700-2723`）转写：消息流（`me` 纯文本 / `ai` `mdRender` HTML + 内嵌草案卡）、输入框 + 发送钮、Ctrl/Cmd+Enter、`busy` 锁（发送中禁按钮、占位气泡「思考中…」）、五条快速指令 chips 文案照抄（`按当前目录生成索引 / 查重复用例 / 给当前笔记补 description / 导出 P0 用例清单 / 搜索 舍入`）。
- [ ] **Step 3: KbPage 接线**

```ts
async function ask(text: string) {
  if (busy) return;
  setBusy(true); pushMsg("me", text); pushMsg("ai", "思考中…", true);
  try {
    const resp = await chatSend("kb-console", text, "kb_assistant");
    replaceLastAi(resp.reply, resp.drafts);   // drafts 逐条挂卡片，state=pending
  } catch (err) {
    replaceLastAi(err instanceof Error ? err.message : String(err), [], true);
  } finally { setBusy(false); }
}

async function confirmDraft(d: KbDraft, idx: number) {
  if (!window.confirm(`确认写入知识库磁盘？\n${d.op === "create" ? "新建" : "覆盖"}：${d.abs_display}`)) return;
  setDraftState(idx, "writing");
  const j = await kbWrite(d.path, d.content, d.op === "create" ? "POST" : "PUT", d.mtime);
  if (!j) { setDraftState(idx, "failed"); return; }  // 409 已在 kbWrite 内 toast+重载
  setDraftState(idx, "done", j.mtime);
}
```

注意 `kbWrite` 的 409 分支已带自己的 toast；卡片置 failed 即可，不重复弹窗。**真实 LLM 成本**：助手对话走真实模型链路，本任务自测只 `npm run build`，不起对话；真机走查前按成本门禁另行征得用户同意。
- [ ] **Step 4: 门禁** — `npm run build` 0 错误。
- [ ] **Step 5: Commit** — `feat(kb): 助手栏——kb_assistant 对话、草案卡片确认写入、快速指令与旧三区表单移除`

---

### Task 11: 文档回写与收尾

**Files:**
- Modify: `README.md`（知识库段落：三栏页 + browse 六端点 + kb_assistant 强制绑定 + 草案确认写盘；旧三区描述删除）
- Modify: `docs/superpowers/specs/2026-10-02-kb-assistant-page-design.md:3`（状态行「待实施」→「已实施（commit 见账本）」）
- Modify: `docs/superpowers/` 下既有设计文档若含「知识库页=三区表单」描述则同步改写（grep `检索区/写入区` 定位）

**Interfaces:**
- Consumes: Task 1-10 全部。
- Produces: 文档与实现一致。

- [ ] **Step 1: 更新 README**：能力清单补一行 `/api/kb/browse/*（文件浏览与受控写盘，serve.js 语义）`；知识库段落重写为三栏叙述，点明「写盘必经草案卡」与「索引自动收敛（约数十秒）」（勿写「立即重建」——终审裁定）。
- [ ] **Step 2: spec 状态回写 + 全量回归** — `uv run pytest -q`、`npm run build`。
- [ ] **Step 3: Commit** — `docs(kb): 三栏改版文档回写——README 能力段与 spec 状态`

---

## 验收（实施完成后另行执行，不在子代理任务内）

1. 全量 `uv run pytest -q` + `npm run build` 绿。
2. 真机走查（**需用户同意**，涉及真实 LLM 与真实 zhb_kb 写盘）：起 8001/5174 或用户同意复用 8000；三栏交互、树懒加载、编辑 409、助手「检索xxx」出命中、「我要写入xxx到知识库」出草案卡、确认后落盘、watch 收敛后可被检索；走查测试节点用后即清。
3. 收尾遗留一并处置：`zhb_kb/business/wiki/debug.md` 测试节点清理（用户复验后）。
