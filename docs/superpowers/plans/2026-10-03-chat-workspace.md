# 聊天页第 3 片（右栏工作区）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给聊天页装上右栏工作区：浏览当前项目目录、预览/编辑文本文件（mtime 乐观锁写回）、发送成功后自动刷新，让智能体写进项目里的产出物可见、可改。

**Architecture:** 后端先把 `kb_browse.py` 的路径锁/文本白名单/列目录抽成 `browse_common.py`（KB 行为一位不变，`test_kb_browse.py` 一字不改当回归锁），再新增 `project_browse.py` 三端点（tree / file / PUT），根 = 项目 `dir` 的 `expanduser()+resolve()` 结果——与 `chat.send` 的 `cwd` 同一消费入口（第 2 片 `~` 教训的对称性硬约束）。前端新增 `WorkspacePane`（整栏 = 拖拽条 + aside fragment，自持树/文档/拖宽状态，`key={projectId}` 换项目即重挂），ChatPage 负责折叠恢复、发送后刷新序号与切项目脏拦截。

**Tech Stack:** Python 3.11 + FastAPI + Pydantic v2（`uv run pytest`）；Vite + React 18 + TypeScript（`npm run build`，项目无前端测试框架）。

**Spec:** `docs/superpowers/specs/2026-10-03-chat-workspace-design.md`（裁定五条已定稿、偏离登记 8 条；本计划与 spec 一起进 Task 1 的落档提交）

## Global Constraints

每一任务的要求都隐式包含本节。

- **不 push**；在 `master` 上逐任务提交（第 1/2 片 SDD 口径）。
- **不许动正在跑的 8000 / 5173 服务**；且**不许跑 `scripts/dev.ps1`**（它会清 8000 端口上「本项目的残留服务」，正好会杀掉用户那个）。走查自起端口：后 8002 / 前 5176（runbook 见走查清单）。
- **真实 LLM 调用须用户当面授权**，且由用户本人发消息走查；agent 只证明免费部分（单测 + build）。
- 后端门禁：`cd backend && uv run pytest -q`，**基线 437 passed**（Task 2 之后 +11 用例 → 期望 448），每任务收尾必须全绿。
- 前端门禁：`cd frontend && npm run build`，0 error（无前端测试框架，这是既有裁定）。
- **Task 1 是纯重构**：`backend/tests/test_kb_browse.py` 一字不改、全绿——它是「KB 行为不变」的回归锁。
- **本片不得夹带**：新建/删除文件端点（无 UI 消费方就不做，裁定 2）、search/scan 迁移、边界执法（第 5 片）、流式与停止按钮（第 4 片）、多标签编辑器/语法高亮/二进制预览。
- UI 文案一律中文；页面 **0 处 `zhb`、0 个死按钮**；**不新增色值**——工作区全部消费第 1 片已落的 `.workspace/.ws-*/.e-*/.resizer` 样式，缺样式的补丁必须复用既有变量。
- **不用 `window.prompt`**（Electron 里静默抛异常）；脏确认用 `window.confirm`。
- Electron 走查一律 `localhost`，不用 `127.0.0.1`（vite 可能只监听 `[::1]`）。
- **实施型子代理一律前台派发并立即核验回执**（仓库出现过后台子代理编造 commit 的事故）。
- 执行账本落在 `.superpowers/sdd/2026-10-03-chat-workspace/`（progress.md + 每任务 brief/report），沿用第 2 片 SDD 口径；未获用户确认前不清账本。
- 行号会漂：计划里的 `:123` 只作定位提示，**认符号不认行号**。

## File Structure

后端（新建 2 文件、改 2 文件、1 个测试文件一字不动）：

| 文件 | 责任 | 动作 |
|---|---|---|
| `backend/src/aitester/interaction/browse_common.py` | KB/工作区共用原语：`resolve_within`（403 文案参数化）、`is_text_file`、`rel_of`、`stat_item`、`listing`、`TEXT_EXT`、`MAX_TEXT`、`BrowseWriteBody` | Create |
| `backend/src/aitester/interaction/kb_browse.py` | 改为消费共用件（删被迁代码），端点/文案/状态码零变化 | Modify |
| `backend/src/aitester/interaction/project_browse.py` | 项目工作区三端点（tree / file / PUT），根 = 项目 `dir` | Create |
| `backend/src/aitester/main.py` | `include_router(project_browse_router)` | Modify |
| `backend/tests/test_project_browse.py` | 新增 11 用例 | Create |
| `backend/tests/test_kb_browse.py` | 回归锁，一字不改 | 不动 |

前端（新建 2 文件、改 3 文件）：

| 文件 | 责任 | 动作 |
|---|---|---|
| `frontend/src/api/client.ts` | `Ws*` 类型 + `wsTree/wsReadFile/wsPutFile` | Modify |
| `frontend/src/components/dragBar.ts` | `bindDragBar` 从 KbPage 抽出（第二个消费方出现才抽） | Create |
| `frontend/src/pages/KbPage.tsx` | 改 import 共用件、删本地实现（行为不变） | Modify |
| `frontend/src/pages/chat/WorkspacePane.tsx` | 右栏工作区整栏（树 / 预览编辑 / 脏守卫 / 刷新 / 双向拖拽） | Create |
| `frontend/src/pages/ChatPage.tsx` | 接线：折叠恢复、切项目脏拦截、发送后刷树、挂载 | Modify |

两处刻意的中间态：Task 3 结束时 `WorkspacePane.tsx` 尚不存在（build 仍应 0 error）；Task 4 结束时 Pane 尚无消费方（未被 import 不进包，build 仍 0 error）——两处都不是事故。

---

### Task 1: 抽 `browse_common`（KB 行为不变的纯重构）+ 落档 spec/计划

**Files:**
- Create: `backend/src/aitester/interaction/browse_common.py`
- Modify: `backend/src/aitester/interaction/kb_browse.py`
- Test: `backend/tests/test_kb_browse.py`（一字不改）
- 落档: `docs/superpowers/specs/2026-10-03-chat-workspace-design.md`、`docs/superpowers/plans/2026-10-03-chat-workspace.md`（均未跟踪）

**Interfaces:**
- Consumes: `services/kb/paths.py` 的 `is_hidden` / `hidden_segment` / `mtime_ms`（已存在，一位不动）
- Produces（Task 2 与后续所有消费方依赖的精确签名）：
  - `resolve_within(root: Path, rel: str, *, outside_detail: str) -> Path`
  - `rel_of(root: Path, abs: Path) -> str`
  - `is_text_file(abs: Path) -> bool`
  - `stat_item(p: Path) -> dict[str, Any]`（`{"size", "mtime"}`）
  - `listing(root: Path, target: Path) -> list[dict[str, Any]]`（隐藏剔除 + dirs-first）
  - `TEXT_EXT: set[str]`、`MAX_TEXT: int`（2MB）
  - `class BrowseWriteBody(BaseModel)`：`content: str`

- [ ] **Step 1: 先落档 spec 与计划**

```bash
git add docs/superpowers/specs/2026-10-03-chat-workspace-design.md docs/superpowers/plans/2026-10-03-chat-workspace.md
git commit -m "docs(workspace): 第 3 片设计定稿与实施计划落档"
```

（`docs/superpowers/specs/2026-10-03-kb-index-sync-design.md` 是另一条挂起的专项，**不要**一起提交。）

- [ ] **Step 2: 记基线**

Run: `cd backend && uv run pytest -q`
Expected: **437 passed, 0 failed**。这个数就是本片一切「有没有弄坏 KB」的对照线。

- [ ] **Step 3: 写共用件**

新建 `backend/src/aitester/interaction/browse_common.py`：

```python
"""KB 浏览与项目工作区共用的文件层原语：路径锁、文本白名单、列一层、条目统计、写体。

`resolve_within` 的三段判据（NUL/越界/隐藏段）取自 kb_browse.py 原 `_resolve` 平移，
403 文案参数化（KB 说「知识库范围」、工作区说「项目目录范围」，行为完全一致）；
隐藏段判据继续消费 services/kb/paths.py——读侧与写侧工具共用同一谓词。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from aitester.services.kb.paths import hidden_segment, is_hidden, mtime_ms

TEXT_EXT = {".md", ".markdown", ".txt", ".json", ".jsonl", ".py", ".js", ".ts", ".yaml", ".yml",
            ".sql", ".sh", ".bat", ".ini", ".cfg", ".csv", ".html", ".css", ".xml", ".toml",
            ".gitignore", ".log"}
MAX_TEXT = 2 * 1024 * 1024


def resolve_within(root: Path, rel: str, *, outside_detail: str) -> Path:
    """路径锁：拒 NUL、越界（resolve 后必须锁在根内）、隐藏段命中一律 403。"""
    if rel and "\0" in rel:
        raise HTTPException(status_code=403, detail=outside_detail)
    clean = rel.replace("\\", "/").lstrip("/")
    root_r = root.resolve()
    cand = (root_r / clean).resolve() if clean else root_r
    if cand != root_r and root_r not in cand.parents:
        raise HTTPException(status_code=403, detail=outside_detail)
    if hidden_segment(cand.relative_to(root_r).parts):
        raise HTTPException(status_code=403, detail=outside_detail)
    return cand


def rel_of(root: Path, abs: Path) -> str:
    return abs.relative_to(root).as_posix()


def is_text_file(abs: Path) -> bool:
    ext = abs.suffix.lower()
    return ext in TEXT_EXT or ext == ""


def stat_item(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"size": st.st_size, "mtime": mtime_ms(st)}


def listing(root: Path, target: Path) -> list[dict[str, Any]]:
    """列一层：隐藏剔除、逐条 stat（失败按 0 收敛）、dirs-first（lower() 近似 zh 序）。"""
    try:
        entries = list(target.iterdir())
    except OSError:
        # 瞬时列举失败按空目录收敛（与 stat_item 守卫同口径韧性）
        entries = []
    items = []
    for e in entries:
        if is_hidden(e.name):
            continue
        info: dict[str, Any] = {"name": e.name, "rel": rel_of(root, e), "dir": e.is_dir()}
        try:
            info.update(stat_item(e))
        except OSError:
            info.update({"size": 0, "mtime": 0})
        items.append(info)
    # dirs-first；zh 序用 lower() 近似（Global Constraints 偏差②）
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return items


class BrowseWriteBody(BaseModel):
    content: str = Field(description="文件全文")
```

- [ ] **Step 4: `kb_browse.py` 改为消费共用件**

用下面的完整内容**整体替换** `backend/src/aitester/interaction/kb_browse.py`（端点、文案、状态码一位不变；被迁走的 `_resolve/_rel_of/_is_text/_stat_d/_hidden/KbBrowseWriteBody/TEXT_EXT/MAX_TEXT` 全部删除，不在 KB 侧留兼容别名）：

```python
"""知识库文件浏览接口：逐条移植 prototype/serve.js:93-198 的六 handler 语义。

错误体统一 FastAPI 的 detail 键（serve.js 用 error，这是唯一键名偏差）；
状态码、文案、限额与 serve.js 一致。安全：路径锁死 KB 实体根内、隐藏目录/点文件
不可见、仅白名单文本类型、2MB 上限——路径锁/白名单/列目录函数体已抽到 browse_common
（项目工作区共用，KB 侧行为一位不变）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from aitester.interaction.browse_common import (
    MAX_TEXT,
    BrowseWriteBody,
    is_text_file,
    listing,
    rel_of,
    resolve_within,
    stat_item,
)
from aitester.services.kb.paths import is_hidden, mtime_ms, resolve_kb_root

router = APIRouter(prefix="/api/kb/browse")

OUTSIDE = "路径超出知识库范围"
SCAN_MD_MAX = 512 * 1024
WALK_DEPTH = 12

_FM_RE = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---")
_KV_RE = re.compile(r"^([A-Za-z0-9_\-.]+):\s*(.*)$")


def _root(request: Request) -> Path:
    settings = request.app.state.settings
    if not settings.kb_enabled:
        raise HTTPException(status_code=503, detail="知识库未启用或未启动")
    root = resolve_kb_root(settings)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="知识库实体目录不存在")
    return root


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
        try:
            entries = sorted(cur.iterdir(), key=lambda e: e.name.lower())
        except OSError:
            # 瞬时列举失败按空目录收敛（与 stat_item 守卫同口径韧性）
            continue
        for e in entries:
            if is_hidden(e.name):
                continue
            out.append(e)
            if e.is_dir() and d + 1 < WALK_DEPTH:
                stack.append((e, d + 1))
    return out


@router.get("/tree")
def browse_tree(request: Request, path: str = "") -> dict[str, Any]:
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    return {"root": str(root), "rel": rel_of(root, target), "items": listing(root, target)}


@router.get("/file")
def browse_file(request: Request, path: str = "") -> Any:
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录，不是文件")
    if not is_text_file(target):
        # spec §A 错误表：415/413 附 editable:false（前端据此不进取编辑态）
        return JSONResponse(status_code=415, content={"detail": "暂不支持预览该文件类型", "editable": False})
    st = target.stat()
    if st.st_size > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "文件超过 2MB，只读不加载", "editable": False})
    return {
        "rel": rel_of(root, target), "name": target.name, "ext": target.suffix.lower(),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "size": st.st_size, "mtime": mtime_ms(st), "editable": True,
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
            info = {"name": abs.name, "rel": rel_of(root, abs), "dir": abs.is_dir()}
            info.update(stat_item(abs))
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
    target = resolve_within(root, path, outside_detail=OUTSIDE)
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
        docs.append({"rel": rel_of(root, abs), "name": abs.name, "size": st.st_size,
                     "mtime": mtime_ms(st), "fm": _parse_fm(head)})
        if len(docs) >= limit:
            break
    return {"root": str(root), "scanned": len(docs), "truncated": len(docs) >= limit, "docs": docs}


@router.put("/file")
def browse_put(request: Request, body: BrowseWriteBody,
               path: str = Query(""), mtime: int = Query(0)) -> Any:
    # body 必传（终审项 7）：无请求体由 FastAPI 直接 422，而非 None 解引用 500
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not is_text_file(target):
        # spec §A 错误表：415/413 附 editable:false（与 409 同款 JSONResponse 形态）
        return JSONResponse(status_code=415, content={"detail": "只允许写入文本文件", "editable": False})
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在，请用新建接口")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录")
    cur = mtime_ms(target.stat())
    if mtime and cur != mtime:
        # 对齐 serve.js：乐观锁冲突不覆盖，回磁盘当前 mtime 供前端重载
        return JSONResponse(status_code=409,
                            content={"detail": "文件在页面打开后被外部修改，未覆盖", "mtime": cur})
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "内容过大", "editable": False})
    target.write_bytes(data)
    return {"rel": rel_of(root, target), "size": len(data),
            "mtime": mtime_ms(target.stat())}


@router.post("/file")
def browse_post(request: Request, body: BrowseWriteBody,
                path: str = Query("")) -> Any:
    # body 必传（终审项 7）：无请求体由 FastAPI 直接 422，而非 None 解引用 500
    root = _root(request)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not is_text_file(target):
        return JSONResponse(status_code=415, content={"detail": "只允许写入文本文件", "editable": False})
    if target.exists():
        raise HTTPException(status_code=409, detail="同名文件已存在，请换个名字")
    if not target.parent.is_dir():
        raise HTTPException(status_code=400, detail="父目录不存在")
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "内容过大", "editable": False})
    target.write_bytes(data)
    return JSONResponse(status_code=201, content={
        "rel": rel_of(root, target), "size": len(data),
        "mtime": mtime_ms(target.stat()),
    })
```

- [ ] **Step 5: 回归锁 + 全量**

```bash
cd backend && uv run pytest tests/test_kb_browse.py -q     # 期望 13 passed（一字未改）
cd backend && uv run pytest -q                             # 期望 437 passed（数量不变）
```

若 `test_kb_browse.py` 有任何红：**不是改测试**，是共用件的行为与原文有出入，回 Step 3/4 找差异。

- [ ] **Step 6: Commit**

```bash
git add backend/src/aitester/interaction/browse_common.py backend/src/aitester/interaction/kb_browse.py
git commit -m "refactor(browse): 抽 browse_common——KB 与工作区共用的路径锁/文本白名单/条目统计"
```

---

### Task 2: 项目工作区三端点（tree / file / PUT）+ 11 条端点测试

**Files:**
- Create: `backend/src/aitester/interaction/project_browse.py`
- Modify: `backend/src/aitester/main.py`（import + `include_router`，`:20` 与 `:76-80` 附近）
- Test: `backend/tests/test_project_browse.py`

**Interfaces:**
- Consumes: Task 1 的 `resolve_within/is_text_file/rel_of/stat_item/listing/MAX_TEXT/BrowseWriteBody`；`ProjectService.get(project_id) -> dict`（`ConfigNotFoundError`）；`services/kb/paths.mtime_ms`
- Produces（Task 3 前端函数的对端）：
  - `GET /api/projects/{pid}/browse/tree?path=` → `{root, rel, items: [{name, rel, dir, size, mtime}]}`
  - `GET /api/projects/{pid}/browse/file?path=` → `{rel, name, ext, content, size, mtime, editable}`
  - `PUT /api/projects/{pid}/browse/file?path=&mtime=`（body `{content}`）→ `{rel, size, mtime}`
  - 常量 `OUTSIDE = "路径超出项目目录范围"`、`DIR_GONE = "项目目录不存在或已被移动，请到项目页确认路径"`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_project_browse.py`：

```python
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
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_project_browse.py -q`
Expected: **FAIL**——路由还没注册，`GET .../browse/tree` 得 404 `{"detail":"Not Found"}`（首个用例在 `j["items"]` 处 KeyError），`test_post_not_offered_405` 同样红。

- [ ] **Step 3: 写端点模块 + 接线**

新建 `backend/src/aitester/interaction/project_browse.py`：

```python
"""项目工作区文件接口：浏览项目目录、读文本、mtime 乐观锁写回。

根 = 项目 `dir` 的 expanduser()+resolve() 结果——与 `chat.send` 的 `cwd` 同一消费入口
（第 2 片的 `~` 教训：校验放行的每一种形态，消费侧必须同样处理）。
刻意不做 POST（无「新建文件」UI 消费方）与 search/scan（工作区不是知识库）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from aitester.interaction.browse_common import (
    MAX_TEXT,
    BrowseWriteBody,
    is_text_file,
    listing,
    rel_of,
    resolve_within,
    stat_item,
)
from aitester.services.kb.paths import mtime_ms
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import ProjectService

router = APIRouter(prefix="/api/projects")

OUTSIDE = "路径超出项目目录范围"
DIR_GONE = "项目目录不存在或已被移动，请到项目页确认路径"


def _svc(request: Request) -> ProjectService:
    return request.app.state.project_config  # type: ignore[return-value]


def _root(request: Request, project_id: str) -> Path:
    try:
        project = _svc(request).get(project_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    try:
        root = Path(project["dir"]).expanduser().resolve()
    except (OSError, ValueError):
        # 畸形 dir（NUL/超长）：一律答「目录不可用」，绝不把用户填的路径外泄成 500
        raise HTTPException(status_code=404, detail=DIR_GONE) from None
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=DIR_GONE)
    return root


@router.get("/{project_id}/browse/tree")
def project_tree(request: Request, project_id: str, path: str = "") -> dict[str, Any]:
    root = _root(request, project_id)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不是目录")
    return {"root": str(root), "rel": rel_of(root, target), "items": listing(root, target)}


@router.get("/{project_id}/browse/file")
def project_file(request: Request, project_id: str, path: str = "") -> Any:
    root = _root(request, project_id)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录，不是文件")
    if not is_text_file(target):
        return JSONResponse(status_code=415, content={"detail": "暂不支持预览该文件类型", "editable": False})
    st = target.stat()
    if st.st_size > MAX_TEXT:
        # 工作区没有只读视图，文案不能照抄 KB 的「只读不加载」
        return JSONResponse(status_code=413, content={"detail": "文件超过 2MB，无法打开", "editable": False})
    return {
        "rel": rel_of(root, target), "name": target.name, "ext": target.suffix.lower(),
        "content": target.read_text(encoding="utf-8", errors="replace"),
        "size": st.st_size, "mtime": mtime_ms(st), "editable": True,
    }


@router.put("/{project_id}/browse/file")
def project_put(request: Request, project_id: str, body: BrowseWriteBody,
                path: str = Query(""), mtime: int = Query(0)) -> Any:
    root = _root(request, project_id)
    target = resolve_within(root, path, outside_detail=OUTSIDE)
    if not is_text_file(target):
        return JSONResponse(status_code=415, content={"detail": "只允许写入文本文件", "editable": False})
    if not target.exists():
        # 工作区没有「新建接口」可指，文案指「被移动或删除」
        raise HTTPException(status_code=404, detail="保存失败：文件已不存在（可能被移动或删除）")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="是目录")
    cur = mtime_ms(target.stat())
    if mtime and cur != mtime:
        return JSONResponse(status_code=409,
                            content={"detail": "文件在页面打开后被外部修改，未覆盖", "mtime": cur})
    data = body.content.encode("utf-8")
    if len(data) > MAX_TEXT:
        return JSONResponse(status_code=413, content={"detail": "内容过大", "editable": False})
    target.write_bytes(data)
    return {"rel": rel_of(root, target), "size": len(data), "mtime": mtime_ms(target.stat())}
```

`backend/src/aitester/main.py` 两处接线（认符号不认行号）：

1. import 区（`from aitester.interaction.pick_dir import router as pick_dir_router` 之后）加一行：

```python
from aitester.interaction.project_browse import router as project_browse_router
```

2. `application.include_router(projects_router)` 之后加一行：

```python
    application.include_router(project_browse_router)
```

（`/api/projects` 前缀下只有子路径不同：`PUT /{pid}` 与 `PUT /{pid}/browse/file`、`GET ""` 与 `GET /{pid}/browse/tree` 路径形状不同，无路由歧义。）

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_project_browse.py -q`
Expected: **11 passed**。若 `test_post_not_offered_405` 意外 404 而非 405：说明路径未被任何路由模式匹配（PUT/GET 两条没注册成），回 Step 3 检查 `router` 与 include。

- [ ] **Step 5: 全量后端回归 + Commit**

```bash
cd backend && uv run pytest -q          # 期望 448 passed（437 + 11）
cd .. && git add backend/src/aitester/interaction/project_browse.py backend/src/aitester/main.py backend/tests/test_project_browse.py
git commit -m "feat(workspace): 项目工作区浏览三端点——列目录/读文本/乐观锁写回"
```

---

### Task 3: 前端 `ws` API 三函数 + `bindDragBar` 提为共用件

**Files:**
- Modify: `frontend/src/api/client.ts`（`kbPostFile` 之后、`KbDraft` 之前，约 `:256`）
- Create: `frontend/src/components/dragBar.ts`
- Modify: `frontend/src/pages/KbPage.tsx`（删本地 `bindDragBar` `:28-47`、import 区 `:1-11`）

**Interfaces:**
- Consumes: Task 2 的三端点（路径与查询参数逐字对齐）；`URLSearchParams`/`JSON_HEADERS`/`apiFetch` 既有模式
- Produces（Task 4 组件依赖的精确签名）：
  - `wsTree(pid: string, path: string): Promise<WsTreeResponse>`，`WsTreeResponse = { rel: string; items: WsItem[] }`
  - `wsReadFile(pid: string, path: string): Promise<WsFileResponse>`
  - `wsPutFile(pid: string, path: string, content: string, mtime: number): Promise<WsWriteResponse>`
  - `bindDragBar(el: HTMLElement, onMove: (ev: MouseEvent) => void): () => void`

- [ ] **Step 1: 抽 `bindDragBar`**

新建 `frontend/src/components/dragBar.ts`（从 `KbPage.tsx:28-47` 原样搬出，只加导出与头注释）：

```ts
/** 原型 :1652-1660 dragBar 的 hook 化：mousedown → document mousemove → mouseup，卸载即清理。
    KbPage 与聊天页工作区共用（第二个消费方出现才抽，行为一字不变）。 */
export function bindDragBar(el: HTMLElement, onMove: (ev: MouseEvent) => void): () => void {
  let detachMove: (() => void) | null = null;
  const down = (e: MouseEvent) => {
    e.preventDefault();
    el.classList.add("dragging");
    const move = (ev: MouseEvent) => onMove(ev);
    const up = () => {
      el.classList.remove("dragging");
      detachMove?.();
      detachMove = null;
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    detachMove = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
  };
  el.addEventListener("mousedown", down);
  return () => { el.removeEventListener("mousedown", down); detachMove?.(); document.body.style.userSelect = ""; };
}
```

`KbPage.tsx`：
1. 删掉本地 `bindDragBar`（`/** 原型 :1652-1660 dragBar 的 hook 化…… */` 整段函数）。
2. import 区加 `import { bindDragBar } from "../components/dragBar";`（放在 `import KbTreePane from "./kb/KbTreePane";` 之前，与其它组件 import 同块）。

其余一字不动；`npm run build` 过即证行为不变。

- [ ] **Step 2: `client.ts` 加 ws 函数族**

在 `kbPostFile` 函数之后插入：

```ts
export interface WsItem { name: string; rel: string; dir: boolean; size: number; mtime: number }
export interface WsTreeResponse { rel: string; items: WsItem[] }
export interface WsFileResponse { rel: string; name: string; ext: string; content: string; size: number; mtime: number; editable: boolean }
export interface WsWriteResponse { rel: string; size: number; mtime: number }

/** 项目工作区接口：形状与 KB browse 同款不同源——不复用 Kb* 类型，将来各自漂移不互累。 */
const wsApi = (pid: string, sub: string, params: Record<string, string | number>) =>
  `/api/projects/${pid}/browse/${sub}?${new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])).toString()}`;

export function wsTree(pid: string, path: string): Promise<WsTreeResponse> {
  return apiFetch<WsTreeResponse>(wsApi(pid, "tree", { path }));
}
export function wsReadFile(pid: string, path: string): Promise<WsFileResponse> {
  return apiFetch<WsFileResponse>(wsApi(pid, "file", { path }));
}
export function wsPutFile(pid: string, path: string, content: string, mtime: number): Promise<WsWriteResponse> {
  return apiFetch<WsWriteResponse>(wsApi(pid, "file", { path, mtime }), {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ content }) });
}
```

- [ ] **Step 3: 类型门禁 + Commit**

```bash
cd frontend && npm run build          # 期望 0 error
cd .. && git add frontend/src/api/client.ts frontend/src/components/dragBar.ts frontend/src/pages/KbPage.tsx
git commit -m "feat(workspace): 前端 ws API 三函数 + bindDragBar 提为共用件"
```

---

### Task 4: `WorkspacePane` 组件（树 / 预览编辑 / 脏守卫 / 刷新 / 双向拖拽）

**Files:**
- Create: `frontend/src/pages/chat/WorkspacePane.tsx`

**Interfaces:**
- Consumes: Task 3 的 `wsTree/wsReadFile/wsPutFile` 与 `WsFileResponse/WsItem` 类型、`bindDragBar`；`pages/kb/utils` 的 `wsIcon/fmtSize/fmtTime/kbBytes/mdRender`（`MessageList.tsx:4` 已有先例从该文件 import）；`App.css` 既有 `.workspace/.ws-*/.e-*/.resizer/.empty-tip/.icon-btn/.mini-btn` 样式（第 1 片已全量落好，**不新增一行 CSS**）
- Produces（Task 5 接线依赖的精确 props）：

```ts
export interface WorkspacePaneProps {
  project: Project;                            // 当前项目（ChatPage 只在有项目时挂载）
  collapsed: boolean;                          // 收起态由父持有（恢复钮在 chat-header）
  refreshSeq: number;                          // 发送成功 +1 → 静默重拉；手动 ↻ 走同一路径并 toast
  onCollapse: () => void;                      // ws-head » 收起
  onToast: (msg: string) => void;
  onDirtyChange: (dirty: boolean) => void;     // 父在切项目前查脏；须稳定身份（useCallback）
}
```

- [ ] **Step 1: 写组件**

新建 `frontend/src/pages/chat/WorkspacePane.tsx`：

```tsx
import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ApiError, wsPutFile, wsReadFile, wsTree,
  type Project, type WsFileResponse, type WsItem,
} from "../../api/client";
import { bindDragBar } from "../../components/dragBar";
import { fmtSize, fmtTime, kbBytes, mdRender, wsIcon } from "../kb/utils";

/** 中栏文档态（形态照 KbPage.KbDocState；工作区无只读态，mode 只有预览/编辑）。 */
interface WsDoc {
  rel: string;
  name: string;
  ext: string;
  content: string;
  disk: string;
  mtime: number;
  mode: "preview" | "edit";
}

/** 拖拽夹持：目录树 [180,560]（KB 已裁定的放宽口径）；聊天↔工作区 [260, 内宽-620]（保住聊天区 620）。 */
const TW_MIN = 180, TW_MAX = 560, WS_MIN = 260, CHAT_KEEP = 620;

export interface WorkspacePaneProps {
  project: Project;
  collapsed: boolean;
  refreshSeq: number;
  onCollapse: () => void;
  onToast: (msg: string) => void;
  onDirtyChange: (dirty: boolean) => void;
}

/** 原型 aside#workspace :631-671 + 工作区 JS :1480-1677 的 React 转写。
    整栏 = resizer + aside fragment；树/打开文件/拖宽都是视图态，key={projectId} 换项目即重挂归零。 */
export default function WorkspacePane({
  project, collapsed, refreshSeq, onCollapse, onToast, onDirtyChange,
}: WorkspacePaneProps) {
  const wsRef = useRef<HTMLElement>(null);
  const wsBodyRef = useRef<HTMLDivElement>(null);
  const wsSideRef = useRef<HTMLDivElement>(null);
  const chatResizerRef = useRef<HTMLDivElement>(null);
  const treeResizerRef = useRef<HTMLDivElement>(null);

  const [treeHidden, setTreeHidden] = useState(false);
  const [kids, setKids] = useState<Record<string, WsItem[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ "": true });
  const [treeErr, setTreeErr] = useState<string | null>(null);
  const kidsRef = useRef(kids);
  const expandedRef = useRef(expanded);
  kidsRef.current = kids;
  expandedRef.current = expanded;

  const [doc, setDoc] = useState<WsDoc | null>(null);
  const docRef = useRef(doc);
  docRef.current = doc;
  const rootSeq = useRef(0);   // 整树重拉的「最新一次」序号：迟到的成败都不得盖过更新的一轮
  const openSeq = useRef(0);   // 打开文件的「最新一次」序号

  const dirty = doc !== null && doc.content !== doc.disk;

  /** 整树重拉：根 + 全部已展开目录（手动 ↻ 与发送后静默刷新共用一条路径）。 */
  const reloadTree = useCallback(async (): Promise<boolean> => {
    const seq = ++rootSeq.current;
    let rootItems: WsItem[];
    try {
      rootItems = (await wsTree(project.id, "")).items;
    } catch (err) {
      if (seq === rootSeq.current) setTreeErr(err instanceof Error ? err.message : String(err));
      return false;
    }
    if (seq !== rootSeq.current) return false;
    const wanted = Object.keys(expandedRef.current).filter((d) => d !== "");
    const nextKids: Record<string, WsItem[]> = { "": rootItems };
    const nextExpanded: Record<string, boolean> = { "": true };
    for (const rel of wanted) {
      try {
        const sub = await wsTree(project.id, rel);
        if (seq !== rootSeq.current) return false;
        nextKids[rel] = sub.items;
        nextExpanded[rel] = true;
      } catch {
        // 目录在刷新窗口内被删/移走：折叠丢弃（刷新是收敛，不弹错）
      }
    }
    if (seq !== rootSeq.current) return false;
    // 拉取期间新展开的目录并入结果：刷新不把用户刚展开的树收回去
    for (const rel of Object.keys(expandedRef.current)) {
      if (nextExpanded[rel]) continue;
      nextExpanded[rel] = true;
      const cached = kidsRef.current[rel];
      if (cached) nextKids[rel] = cached;
    }
    kidsRef.current = nextKids;
    expandedRef.current = nextExpanded;
    setKids(nextKids);
    setExpanded(nextExpanded);
    setTreeErr(null);
    return true;
  }, [project.id]);

  // 挂载首拉（key={projectId} 重挂：换项目全量归零）
  useEffect(() => { void reloadTree(); }, [reloadTree]);

  // 父 bump refreshSeq（发送成功）→ 静默重拉；挂载首帧的 seq 不重复拉
  const seenSeq = useRef(refreshSeq);
  useEffect(() => {
    if (refreshSeq === seenSeq.current) return;
    seenSeq.current = refreshSeq;
    void reloadTree();
  }, [refreshSeq, reloadTree]);

  // 双向拖拽（原型 dragBar :1652-1669；夹持按 spec 裁定）
  useEffect(() => {
    const el = chatResizerRef.current;
    if (!el) return;
    return bindDragBar(el, (ev) => {
      const max = Math.max(WS_MIN, window.innerWidth - CHAT_KEEP);
      const w = Math.min(Math.max(window.innerWidth - ev.clientX, WS_MIN), max);
      wsRef.current?.style.setProperty("--w", `${w}px`);
    });
  }, []);
  useEffect(() => {
    const el = treeResizerRef.current;
    if (!el) return;
    return bindDragBar(el, (ev) => {
      const left = wsSideRef.current?.getBoundingClientRect().left ?? 0;
      const w = Math.min(Math.max(ev.clientX - left, TW_MIN), TW_MAX);
      wsBodyRef.current?.style.setProperty("--tw", `${w}px`);
    });
  }, []);

  /** 原型 kbLoadDir :2381-2385 同款：kids 缺失才拉（懒加载=首次展开触发）。 */
  const toggleDir = useCallback((rel: string) => {
    if (expandedRef.current[rel]) {
      const next = { ...expandedRef.current };
      delete next[rel];
      expandedRef.current = next;
      setExpanded(next);
      return;
    }
    expandedRef.current = { ...expandedRef.current, [rel]: true };
    setExpanded(expandedRef.current);
    if (kidsRef.current[rel]) return;
    wsTree(project.id, rel)
      .then((j) => { kidsRef.current = { ...kidsRef.current, [rel]: j.items }; setKids(kidsRef.current); })
      .catch((err) => onToast(err instanceof Error ? err.message : String(err)));
  }, [project.id, onToast]);

  /** 打开文件（force=true 为 409 后的强载，跳过脏确认）。 */
  const loadDoc = useCallback(async (rel: string, force: boolean): Promise<boolean> => {
    const cur = docRef.current;
    if (!force && cur && cur.content !== cur.disk) {
      if (!window.confirm("当前文件有未保存的修改，放弃并切换？")) return false;
    }
    const seq = ++openSeq.current;
    let j: WsFileResponse;
    try {
      j = await wsReadFile(project.id, rel);
    } catch (err) {
      if (seq === openSeq.current) onToast(err instanceof Error ? err.message : String(err));
      return false;
    }
    if (seq !== openSeq.current) return false;
    // 原型 wsOpenFile :1546 —— 清拖拽写的 inline --w，让 .wide 的 CSS 宽度生效
    wsRef.current?.style.removeProperty("--w");
    const isMd = j.ext === ".md" || j.ext === ".markdown";
    const next: WsDoc = {
      rel: j.rel, name: j.name, ext: j.ext,
      content: j.content, disk: j.content, mtime: j.mtime,
      mode: isMd ? "preview" : "edit",     // 原型 :1552-1554：md 默认预览，非 md 直接编辑
    };
    docRef.current = next;
    setDoc(next);
    return true;
  }, [project.id, onToast]);

  const openDoc = useCallback((rel: string) => { void loadDoc(rel, false); }, [loadDoc]);

  /** 原型 wsCloseFile :1566-1572 + 脏守卫：放弃修改需确认。 */
  const closeDoc = useCallback(() => {
    const cur = docRef.current;
    if (!cur) return;
    if (cur.content !== cur.disk && !window.confirm("当前文件有未保存的修改，放弃并关闭？")) return;
    docRef.current = null;
    setDoc(null);
    wsRef.current?.style.removeProperty("--w");
  }, []);

  /** 原型 wsSave :1574-1577 的落盘版：PUT + mtime 乐观锁；409/404 分支见 spec 错误表。 */
  const saveDoc = useCallback(async () => {
    const cur = docRef.current;
    if (!cur) return;
    if (cur.content === cur.disk) { onToast("没有需要保存的修改"); return; }
    try {
      const j = await wsPutFile(project.id, cur.rel, cur.content, cur.mtime);
      const now = docRef.current;
      if (now && now.rel === cur.rel) {
        // 保存期间可能又敲了字：disk 回填保存时的快照，content 保持当前输入
        const next = { ...now, disk: cur.content, mtime: j.mtime };
        docRef.current = next;
        setDoc(next);
      }
      onToast(`已保存 ${j.rel}`);
      void reloadTree();                      // 体积/mtime 变了，静默收敛树
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // 语义与 KB 页统一（KbPage.tsx kbWrite 同款）：冲突不覆盖不合并，强载磁盘最新
        onToast("文件在别处被改过，已重新载入磁盘最新内容（未保存的修改已丢弃）");
        void loadDoc(cur.rel, true);
        return;
      }
      onToast(err instanceof ApiError ? err.message : String(err));
      if (err instanceof ApiError && err.status === 404) void reloadTree();  // 外部被删：树收敛，内容留着
    }
  }, [loadDoc, onToast, project.id, reloadTree]);

  const setMode = useCallback((m: "preview" | "edit") => {
    const cur = docRef.current;
    if (!cur) return;
    const next = { ...cur, mode: m };
    docRef.current = next;
    setDoc(next);
  }, []);

  const onEditContent = useCallback((v: string) => {
    const cur = docRef.current;
    if (!cur) return;
    const next = { ...cur, content: v };
    docRef.current = next;
    setDoc(next);
  }, []);

  // 脏状态上报父组件（ref 消费）：卸载时回落，别让父带着过期的脏拦人
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const rows = (rel: string, depth: number): ReactNode[] =>
    (kids[rel] || []).map((it) => (
      <Fragment key={it.rel}>
        <div
          className={`ws-node${!it.dir && doc?.rel === it.rel ? " active" : ""}`}
          style={{ paddingLeft: `${8 + depth * 14}px` }}
          onClick={() => (it.dir ? toggleDir(it.rel) : openDoc(it.rel))}
        >
          <span className="arr">{it.dir ? (expanded[it.rel] ? "▾" : "▸") : ""}</span>
          <span>{it.dir ? (expanded[it.rel] ? "📂" : "📁") : wsIcon(it.name)}</span>
          <span className="lbl">{it.name}</span>
        </div>
        {it.dir && expanded[it.rel] && rows(it.rel, depth + 1)}
      </Fragment>
    ));

  const isMd = doc !== null && (doc.ext === ".md" || doc.ext === ".markdown");

  return (
    <>
      {/* 聊天区 / 工作区 拖拽分隔条（原型 :628；收起时整条隐藏） */}
      <div
        ref={chatResizerRef}
        className="resizer"
        title="拖拽调整聊天区 / 工作区宽度"
        style={collapsed ? { display: "none" } : undefined}
      ></div>
      <aside
        ref={wsRef}
        className={`workspace${collapsed ? " collapsed" : ""}${doc ? " wide" : ""}`}
      >
        <div className="ws-head">
          <span>🗀 工作区</span>
          {treeHidden && (
            <button className="icon-btn" title="显示目录栏" onClick={() => setTreeHidden(false)}>☰</button>
          )}
          <div className="spacer" />
          <button className="icon-btn" title="收起工作区" onClick={onCollapse}>»</button>
        </div>
        <div className="ws-toolbar">
          {/* 裁定 2：「＋ 新建文件」不渲染；只留带文字的手动刷新 */}
          <button
            className="ws-btn"
            title="刷新目录"
            onClick={() => void reloadTree().then((ok) => { if (ok) onToast("已刷新目录"); })}
          >↻ 刷新</button>
        </div>
        <div ref={wsBodyRef} className={`ws-body${treeHidden ? " tree-hidden" : ""}`}>
          <div ref={wsSideRef} className="ws-side">
            <div className="ws-dir-head">
              <span>📁 目录</span>
              <button className="icon-btn" title="隐藏目录栏" onClick={() => setTreeHidden(true)}>«</button>
            </div>
            <div className="ws-tree">
              {treeErr !== null ? (
                <div className="empty-tip">
                  {treeErr}
                  <div style={{ marginTop: 8 }}>
                    <button className="mini-btn" onClick={() => void reloadTree()}>↻ 重试</button>
                  </div>
                </div>
              ) : (
                <>
                  {rows("", 0)}
                  {kids[""] && kids[""].length === 0 && (
                    <div className="empty-tip">目录是空的 · 智能体的产出会出现在这里</div>
                  )}
                </>
              )}
            </div>
          </div>
          <div ref={treeResizerRef} className="ws-resizer" title="拖拽调整目录栏宽度"></div>
          {doc && (
            <div className={`ws-editor show${isMd && doc.mode === "preview" ? " preview" : ""}`}>
              <div className="e-head">
                <span className={`dirty${dirty ? " on" : ""}`}></span>
                <span>{doc.name}</span>
                {isMd && (
                  <span className="e-tabs show">
                    <button className={`e-tab${doc.mode === "preview" ? " on" : ""}`} onClick={() => setMode("preview")}>预览</button>
                    <button className={`e-tab${doc.mode === "edit" ? " on" : ""}`} onClick={() => setMode("edit")}>编辑</button>
                  </span>
                )}
                <div className="spacer" />
                <button className="icon-btn" title="关闭文件" onClick={closeDoc}>✕</button>
              </div>
              {isMd && <div className="md-preview" dangerouslySetInnerHTML={{ __html: mdRender(doc.content) }} />}
              <textarea spellCheck={false} value={doc.content} onChange={(e) => onEditContent(e.target.value)} />
              <div className="e-foot">
                <button className="ws-btn main" title="保存到磁盘" onClick={() => void saveDoc()}>💾 保存</button>
                <span style={{ fontSize: 11, color: "var(--text-2)" }}>
                  {fmtSize(kbBytes(doc.content))} · {fmtTime(doc.mtime)}
                </span>
              </div>
            </div>
          )}
        </div>
        {/* 裁定 3：状态栏 = 📁 项目名 · 绝对目录（原型的智能体标签去掉） */}
        <div className="ws-status">📁 {project.name} · {project.dir}</div>
      </aside>
    </>
  );
}
```

- [ ] **Step 2: 类型门禁 + Commit**

```bash
cd frontend && npm run build          # 期望 0 error（Pane 尚无消费方，只过类型检查）
cd .. && git add frontend/src/pages/chat/WorkspacePane.tsx
git commit -m "feat(workspace): WorkspacePane——树/预览编辑/脏守卫/刷新"
```

---

### Task 5: 聊天页接线（折叠恢复、切项目脏拦截、发送后刷树）

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`（import 区、状态区 `:46` 附近、`send` `:197-227`、`onProjectChange` `:264-270`、`agent` 变量 `:272`、chat-header `:381-397`、布局 `:360-427`）

**Interfaces:**
- Consumes: Task 4 的 `WorkspacePane` 与 props（`project/collapsed/refreshSeq/onCollapse/onToast/onDirtyChange`）
- Produces: 无（本片终点）

- [ ] **Step 1: import 与状态**

`import SessionPane from "./chat/SessionPane";` 之后加：

```tsx
import WorkspacePane from "./chat/WorkspacePane";
```

`const [collapsed, setCollapsed] = useState(false);` 之后加：

```tsx
  const [wsCollapsed, setWsCollapsed] = useState(false);   // 工作区收起（视图态，不落盘）
  const [wsSeq, setWsSeq] = useState(0);                   // 发送成功后 +1：工作区静默重拉
  const wsDirtyRef = useRef(false);                        // 工作区脏文档：切项目前问一句
```

`toast` 的 `useCallback` 之后加（**必须稳定身份**：Pane 的卸载 cleanup 会调它，身份一漂就误报干净）：

```tsx
  const onWsDirty = useCallback((d: boolean) => { wsDirtyRef.current = d; }, []);
```

- [ ] **Step 2: 切项目脏拦截**

把 `onProjectChange` 改为：

```tsx
  // 切项目与切智能体同构：guard → 清列表 → 回欢迎态；列表由 reloadSessions 的 effect 按新项目重拉
  const onProjectChange = useCallback((id: string) => {
    if (!guard()) return;
    // 工作区有脏文件时先问：确认才切（取消即早退，受控 select 会停在原项目）
    if (wsDirtyRef.current && !window.confirm("工作区有未保存的文件修改，切换项目将丢弃它们。继续？")) return;
    window.localStorage.setItem(PROJECT_STORAGE_KEY, id);
    setProjectId(id);
    setSessions([]);
    void openSession(null);
  }, [guard, openSession]);
```

- [ ] **Step 3: 发送成功刷树**

`send` 的成功块里，`if (resp.session_id !== activeId) setActiveId(resp.session_id);` 之后加：

```tsx
      setWsSeq((n) => n + 1);            // 模型可能刚写了产出物：工作区树静默重拉
```

- [ ] **Step 4: 当前项目变量 + 两处 projectName 收敛**

`const agent = agentOptions.find((a) => a.id === agentId);` 之后加：

```tsx
  const currentProject = projects.find((p) => p.id === projectId);
```

把 `MessageList` 与 `Composer` 的 `projectName={projects.find((p) => p.id === projectId)?.name ?? ""}` 两处都改成：

```tsx
          projectName={currentProject?.name ?? ""}
```

- [ ] **Step 5: chat-header 恢复钮**

`<div className="spacer" />` 与「新建会话」按钮之间插入：

```tsx
          {wsCollapsed && currentProject && (
            <button className="mini-btn" title="显示工作区" onClick={() => setWsCollapsed(false)}>📁 工作区</button>
          )}
```

- [ ] **Step 6: 布局挂载**

`</main>` 之后、`{toastEl}` 之前插入：

```tsx
      {currentProject && (
        <WorkspacePane
          key={currentProject.id}
          project={currentProject}
          collapsed={wsCollapsed}
          refreshSeq={wsSeq}
          onCollapse={() => setWsCollapsed(true)}
          onToast={toast}
          onDirtyChange={onWsDirty}
        />
      )}
```

- [ ] **Step 7: 类型门禁 + Commit**

```bash
cd frontend && npm run build          # 期望 0 error
cd .. && git add frontend/src/pages/ChatPage.tsx
git commit -m "feat(workspace): 聊天页接线——折叠恢复、切项目脏拦截、发送后刷树"
```

---

## 真机走查清单（Task 5 之后，由用户本人在自起端口上完成；真实 LLM 段须当面授权）

启动前提：用户自己的 8000/5173 如果在跑且代码已是最新（本片改动需后端 reload / 重启），直接在它们上面走；**不要动这两个进程，不要跑 `scripts/dev.ps1`**。若两个端口都停着，自起：

```bash
# 后端
cd backend && uv run uvicorn aitester.main:app --port 8002
# 前端（另开一窗；VITE_PROXY_TARGET 指向自起后端）
cd frontend && VITE_PROXY_TARGET=http://localhost:8002 npm run dev -- --port 5176
# 浏览器一律开 http://localhost:5176/chat（localhost，不用 127.0.0.1）
```

- 进 `/chat`：右栏「🗀 工作区」出现；状态栏 = `📁 项目名 · 目录`（自填值原样）
- 树：根一层列出；隐藏项（`.git` 等）不出现；点目录展开、再点折叠；文件带 📝/📜/📊/📄 与体积
- 打开 `.md`：默认预览、整栏变宽；切「编辑」改字 → 脏点亮 → 保存 → toast「已保存 …」、脏点灭；关闭再打开内容在
- 打开 `.py`/`.ts`：直接进编辑态；保存成功
- 改字不保存：切文件 / 关文件（✕） / 切项目 → 各自 confirm；**取消则原地不动**（切项目取消后 select 仍停在原项目）
- 手动「↻ 刷新」→「已刷新目录」；发一条消息让模型写文件（真实 LLM，当面授权后本人操作）→ 模型回复后树里能看到刚写的文件（发送成功后自动刷新）
- 收起工作区（»）→ 右栏滑出、分隔条消失；chat-header 出现「📁 工作区」，点击恢复
- 目录栏 `«` 隐藏 → 工作区头部出现 `☰`；点 `☰` 恢复
- 拖两处分隔条到两端：目录树夹在 180–560；工作区最小 260、聊天区始终保留 ~620
- 打开文件自动变宽；关闭文件回到默认宽度
- 冲突：在编辑器里改字后，先在磁盘上改同一文件，再点保存 → 409 → toast「文件在别处被改过，已重新载入磁盘最新内容（未保存的修改已丢弃）」且编辑器显示磁盘新内容
- 外部删除该文件后点保存 → toast「保存失败：文件已不存在（可能被移动或删除）」，内容仍在编辑器里
- 把项目目录改名/删掉后进聊天页：树区显示「项目目录不存在或已被移动，请到项目页确认路径」+「↻ 重试」
- 页面 0 处 `zhb`、0 个死按钮（工具栏没有「＋ 新建文件」）
- `/kb` 页一切照旧（共用件重构的回归位：目录树、预览/编辑、保存、409 自动重载）

走查产生的探针会话**用后即删**；模型写进项目目录的文件由用户决定去留。

## 计划自检结论（写完回看 spec）

- **覆盖**：裁定 1（全文本可编辑、md 默认预览、mtime 乐观锁）→ Task 2 端点 + Task 4 `loadDoc`/`saveDoc`；裁定 2（不做新建文件）→ Task 2 `test_post_not_offered_405` + Task 4 工具栏；裁定 3（状态栏）→ Task 4 `ws-status`；裁定 4（隐藏同知识库判据）→ Task 1 `listing`/`resolve_within` 消费 `paths.is_hidden/hidden_segment` + Task 2 树用例；裁定 5（发送后自动刷新）→ Task 4 `refreshSeq` effect + Task 5 Step 3。契约表 → Task 2/3；错误表逐行 → Task 2 用例 + Task 4 的 409/404/415 toast 分支；验收清单逐条 → 走查清单复刻。偏离登记：8 条全部有落点（#1→Task 2/4；#2→Task 4；#3→Task 5 Step 5；#4→Task 4；#5→Task 4 e-foot；#6→Task 4 树区两态；#7→全片无 file-card 代码；#8→Task 2 两条文案）。
- **消费对称性（第 2 片教训的专项）**：Task 2 `test_tilde_dir_consumed_at_same_place_as_send` 用 monkeypatch 家目录锁死「浏览根 = 创建校验时 expanduser 的同一目录」；`_root` 对 NUL/超长（`(OSError, ValueError)`）答 404 而非 500，有独立用例。
- **无占位**：每一处代码都是可粘贴全文/精确锚点；无 TBD、无「类似第 N 步」。
- **类型一致**：`resolve_within(root, rel, *, outside_detail)`、`is_text_file`、`stat_item`、`rel_of`、`listing`、`BrowseWriteBody`、`MAX_TEXT` 在 Task 1 定义、Task 2 消费；前端 `wsTree(pid, path)`/`wsReadFile(pid, path)`/`wsPutFile(pid, path, content, mtime)`、`WsItem/WsTreeResponse/WsFileResponse/WsWriteResponse`、`WorkspacePaneProps` 六个 props、`bindDragBar(el, onMove)` 在 Task 3/4 定义、Task 5 消费，命名与形参顺序一致。
- **回归锁**：Task 1 Step 5 先跑 `test_kb_browse.py` 再全量；refactor 任何红都按「行为有出入」处理，不允许改测试放行。
- **顺序风险**：Task 1 必须先于 Task 2（共用件是它的输入）；Task 4 的 `onDirtyChange` 身份稳定要求写进 Task 5 Step 1 的注释（不稳定身份会因卸载 cleanup 误报干净）；Task 4/5 之间 build 皆应绿，若红必是本任务的类型错误，不许用 `any` 放行。
