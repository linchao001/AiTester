# AiTester 知识库（ReMe 进程内嵌）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 backend（FastAPI）内以 qwenpaw 方式进程内嵌 ReMe（本地 whl），提供全局共享知识库 `zhb_kb` 的状态/检索/写入/inbox 后端能力、智能体工具接入与最小 /kb 前端页。

**Architecture:** `RemeKbManager` 应用级单例持一个专属事件循环线程，按 (project_id, agent_id) lazy 启动多个 `reme.Application` 实例（纯 dict 配置，不加载 reme yaml、绝不启 ReMe 的 HTTP 服务）；每个实例独占 workspace 目录，经 ReMe 自带 junction 挂载与跨进程写锁共享同一 KB 实体。端点与工具分别经 `run_job`（async）/`run_job_sync` 桥接。

**Tech Stack:** Python 3.11+ / FastAPI / uv / pytest（全同步）/ reme-ai 0.4.1.8（本地 whl）+ agentscope 2.0.6（裸包，reme 顶层 import 硬依赖，实施期实证补入）/ React+TS 前端

**Spec:** `docs/superpowers/specs/2026-10-01-knowledge-base-reme-design.md`（本计划实现该设计，四条裁定以其为准）

## Global Constraints

- ReMe 仅以 `Application(**config_dict)` → `await start()` → `await run_job(name, **kwargs)` 进程内使用；禁止调用 `reme start`/启用 service 后端监听（配置里 `"service": {"backend": "http", "web_enabled": False, ...}` 为 reme 单测证实的合法占位，永不绑端口）。
- 依赖只用 `lib/reme_ai-0.4.1.8-py3-none-any.whl` 的 base extras，**不装 `reme-ai[core]`**（faiss/neo4j/polars 等重依赖本期不需要）；但须另装裸包 **`agentscope==2.0.6`**——实施期实证：`reme/steps/base_step.py` 模块顶层 import `agentscope.model`，缺则 `import reme` 即 ModuleNotFoundError。BM25 分词器用默认 `regex` 后端（无需 jieba/rjieba）。
- 每个 (project_id, agent_id) 实例独占 workspace：`backend/data/workspaces/<project_id>/<agent_id>/`；严禁两个实例同开一个 workspace 目录。
- `knowledge_base_id` 是应用级配置（默认 `zhb_kb`），不进项目维度。
- backend 测试全同步、不用 pytest-asyncio（现状基线 239 例全绿）；新测试放 `backend/tests/` 平铺。
- UI 文案中文；控件必须带文字标签，禁止裸图标按钮。
- commit 信息沿用仓库风格：中文、`feat(kb)/fix(kb)/test(kb)` 前缀。
- 不产生真实 LLM/向量 API 调用：测试一律 tmp_path 假 KB + 空 Key（BM25 路径）。真实 Key 的向量检索属成本动作，须用户另行确认。

---

### Task 1: 引入 reme 本地 whl 依赖

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/tests/test_kb_deps.py`

**Interfaces:**
- Consumes: 无
- Produces: 环境内可 `import reme`，导出 `reme.Application`；`reme.__version__ == "0.4.1.8"`

- [ ] **Step 1: 写 smoke 测试（先失败）**

`backend/tests/test_kb_deps.py`：

```python
def test_reme_importable():
    import reme

    assert reme.__version__ == "0.4.1.8"
    assert hasattr(reme, "Application")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_kb_deps.py -q`
Expected: FAIL（ModuleNotFoundError: reme）

- [ ] **Step 3: 加依赖**

`backend/pyproject.toml`：`dependencies` 追加两行 `"reme-ai>=0.4.1.8",` 与 `"agentscope==2.0.6",`（后者为实施期实证补入的硬依赖，见 Global Constraints；带注释说明原因）；文件末尾若无 `[tool.uv.sources]` 段则新建并加：

```toml
[tool.uv.sources]
reme-ai = { path = "../lib/reme_ai-0.4.1.8-py3-none-any.whl" }
```

（若已有该段，只追加 `reme-ai` 条目。）

- [ ] **Step 4: 锁定安装并跑全量回归**

Run: `cd backend && uv lock && uv sync && uv run pytest -q`
Expected: `tests/test_kb_deps.py` PASS；全量 ≥240 通过、0 失败（基线 239 + 新增 1）。若 reme 依赖（pydantic≥2.12.5 等）引发既有用例失败，先修复兼容问题再提交，不得锁旧版绕过。

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/tests/test_kb_deps.py
git commit -m "feat(kb): 引入 reme-ai 本地 wheel 依赖（base extras，进程内嵌前置）"
```

---

### Task 2: ReMe 配置构建器（纯 dict，embedding 按 Key 启停）

**Files:**
- Create: `backend/src/aitester/services/kb/__init__.py`
- Create: `backend/src/aitester/services/kb/config.py`
- Test: `backend/tests/test_kb_config.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `KbConfig(workspace_dir: str, kb_id: str = "zhb_kb", kb_bases_dir: str = "", create_missing: bool = True, embedding_api_key: str = "", embedding_base_url: str = DEFAULT_EMBEDDING_BASE_URL, embedding_model: str = DEFAULT_EMBEDDING_MODEL, embedding_dimensions: int = 1024)`（frozen dataclass）
  - `build_reme_config(cfg: KbConfig) -> dict` —— 可直接展开进 `Application(**cfg)`；jobs 含 `status / list_knowledge_bases / knowledge_base_meta / knowledge_search / save_to_knowledge / reindex / list_knowledge_inbox / promote_knowledge_inbox / merge_knowledge_inbox / reject_knowledge_inbox`
  - 常量 `DEFAULT_EMBEDDING_BASE_URL`、`DEFAULT_EMBEDDING_MODEL`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_kb_config.py`：

```python
from aitester.services.kb.config import KbConfig, build_reme_config


def test_bm25_only_without_api_key():
    cfg = build_reme_config(KbConfig(workspace_dir="ws", kb_id="demo"))
    assert cfg["knowledge_base_id"] == "demo"
    assert cfg["knowledge_bases_dir"] == ""
    assert cfg["create_knowledge_base"] is True
    assert cfg["service"]["web_enabled"] is False
    assert "as_embedding" not in cfg["components"]
    assert "embedding_store" not in cfg["components"]
    assert cfg["components"]["file_store"]["default"]["embedding_store"] == ""
    assert set(cfg["jobs"]) >= {
        "status", "list_knowledge_bases", "knowledge_search",
        "save_to_knowledge", "reindex", "list_knowledge_inbox",
        "promote_knowledge_inbox", "merge_knowledge_inbox", "reject_knowledge_inbox",
    }


def test_embedding_injected_when_key_present():
    cfg = build_reme_config(KbConfig(
        workspace_dir="ws",
        embedding_api_key="sk-x",
        embedding_model="m-emb",
        embedding_dimensions=512,
    ))
    emb = cfg["components"]["as_embedding"]["default"]
    assert emb["backend"] == "openai"
    assert emb["model"] == "m-emb"
    assert emb["dimensions"] == 512
    assert emb["credential"] == {
        "api_key": "sk-x",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    assert cfg["components"]["embedding_store"]["default"]["as_embedding"] == "default"
    assert cfg["components"]["file_store"]["default"]["embedding_store"] == "default"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_kb_config.py -q`
Expected: FAIL（ModuleNotFoundError: aitester.services.kb.config）

- [ ] **Step 3: 实现**

`backend/src/aitester/services/kb/__init__.py`：

```python
"""知识库专项服务：ReMe 进程内嵌配置与实例管理。"""
```

`backend/src/aitester/services/kb/config.py`：

```python
"""构建 ReMe Application 的纯 dict 配置（不加载 reme yaml）。

jobs/steps/components 结构与键名逐项对齐 reme 0.4.1.8 的
reme/config/default.yaml 与 reme/schema/application_config.py；
最小 dict 形态经 reme 仓库单测 tests/unit/test_knowledge_base.py 实证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"


@dataclass(frozen=True)
class KbConfig:
    workspace_dir: str
    kb_id: str = "zhb_kb"
    kb_bases_dir: str = ""
    create_missing: bool = True
    embedding_api_key: str = ""
    embedding_base_url: str = DEFAULT_EMBEDDING_BASE_URL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dimensions: int = 1024


_KB_JOBS: dict[str, Any] = {
    "status": {"backend": "base", "steps": [{"backend": "status_step"}]},
    "list_knowledge_bases": {
        "backend": "base",
        "steps": [{"backend": "list_knowledge_bases_step"}],
    },
    "knowledge_base_meta": {
        "backend": "base",
        "steps": [{"backend": "knowledge_base_meta_step"}],
    },
    "knowledge_search": {
        "backend": "base",
        "steps": [{
            "backend": "knowledge_search_step",
            "vector_weight": 0.7,
            "candidate_multiplier": 5.0,
            "expand_links": True,
            "max_links_per_direction": 10,
        }],
    },
    "save_to_knowledge": {
        "backend": "base",
        "steps": [{"backend": "save_to_knowledge_step"}],
    },
    "reindex": {
        "backend": "base",
        "steps": [
            {"backend": "clear_store_step"},
            {
                "backend": "init_changes_step",
                "monitor_type": "file_store",
                "monitor_name": "default",
                "dispatch_steps": ["update_index_step"],
            },
        ],
    },
    "list_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "list_knowledge_inbox_step"}],
    },
    "promote_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "promote_knowledge_inbox_step"}],
    },
    "merge_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "merge_knowledge_inbox_step"}],
    },
    "reject_knowledge_inbox": {
        "backend": "base",
        "steps": [{"backend": "reject_knowledge_inbox_step"}],
    },
}


def build_reme_config(cfg: KbConfig) -> dict[str, Any]:
    components: dict[str, Any] = {
        "tokenizer": {"default": {"backend": "regex"}},
        "keyword_index": {"default": {"backend": "bm25", "tokenizer": "default"}},
        "file_graph": {"default": {"backend": "local"}},
        "file_store": {
            "default": {
                "backend": "local",
                "embedding_store": "",
                "keyword_index": "default",
                "file_graph": "default",
            },
        },
    }
    if cfg.embedding_api_key:
        components["as_embedding"] = {
            "default": {
                "backend": "openai",
                "model": cfg.embedding_model,
                "dimensions": cfg.embedding_dimensions,
                "credential": {
                    "api_key": cfg.embedding_api_key,
                    "base_url": cfg.embedding_base_url,
                },
                "parameters": {},
            },
        }
        components["embedding_store"] = {
            "default": {"backend": "local", "as_embedding": "default"},
        }
        components["file_store"]["default"]["embedding_store"] = "default"

    return {
        "enable_logo": False,
        "log_to_file": False,
        "log_to_console": False,
        "workspace_dir": cfg.workspace_dir,
        "knowledge_bases_dir": cfg.kb_bases_dir,
        "knowledge_base_id": cfg.kb_id,
        "knowledge_dir": "knowledge",
        "create_knowledge_base": cfg.create_missing,
        "knowledge_write_mode": "open",
        "service": {"backend": "http", "web_enabled": False, "port": 8199},
        "components": components,
        "jobs": {name: dict(spec) for name, spec in _KB_JOBS.items()},
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_kb_config.py -q`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/kb backend/tests/test_kb_config.py
git commit -m "feat(kb): ReMe 纯 dict 配置构建器——embedding 按 Key 注入、无 Key 纯 BM25 降级"
```

---

### Task 3: RemeKbManager（专属事件循环 + 实例池 + 同步/异步桥）

**Files:**
- Create: `backend/src/aitester/services/kb/manager.py`
- Test: `backend/tests/test_kb_manager.py`

**Interfaces:**
- Consumes: `build_reme_config(KbConfig) -> dict`（Task 2）；`Settings.kb_enabled / kb_id / kb_bases_dir / kb_create_missing / kb_embedding_api_key / kb_embedding_base_url / kb_embedding_model / kb_embedding_dimensions`（Task 4 才落 Settings；本 Task 内 manager 构造签名收 `settings: Settings`，测试用 `Settings(_env_file=None, ...)` 直传同名新字段——Task 3 与 Task 4 一起才绿时，可先在本 Task 测试里用 `SimpleNamespace` 传入这些字段，Task 4 换真 Settings；本计划采用 SimpleNamespace 方案，Task 3 不依赖 Task 4）。
- Produces:
  - `KbUnavailableError(RuntimeError)`
  - `RemeKbManager(settings, data_dir: pathlib.Path)`；方法 `start() -> None`、`close_all(timeout: float = 30) -> None`、`run_job_sync(name: str, *, project_id: str = "default", agent_id: str = "console", timeout: float = 60.0, **kwargs) -> reme Response`、`async run_job(name, *, project_id="default", agent_id="console", **kwargs) -> Response`、`is_started: bool`（属性）
  - Response 形状：`.success: bool`、`.answer`、`.metadata: dict`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_kb_manager.py`：

```python
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aitester.services.kb.manager import KbUnavailableError, RemeKbManager


def _settings(tmp_path, **kw):
    base = dict(
        kb_enabled=True,
        kb_id="demo",
        kb_bases_dir=str(tmp_path / "knowledge_bases"),
        kb_create_missing=False,
        kb_embedding_api_key="",
        kb_embedding_base_url="https://example.invalid/v1",
        kb_embedding_model="m",
        kb_embedding_dimensions=1024,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _seed_kb(tmp_path):
    kb_root = tmp_path / "knowledge_bases" / "demo"
    (kb_root / "business" / "wiki").mkdir(parents=True)
    (kb_root / "KB.md").write_text(
        "---\nid: demo\nname: Demo\ndomain: business\nversion: 1\n---\n",
        encoding="utf-8",
    )
    return kb_root


def test_disabled_manager_raises(tmp_path):
    mgr = RemeKbManager(settings=_settings(tmp_path, kb_enabled=False), data_dir=tmp_path)
    mgr.start()
    assert mgr.is_started is False
    with pytest.raises(KbUnavailableError):
        mgr.run_job_sync("status")


def test_save_reindex_search_roundtrip(tmp_path):
    kb_root = _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        saved = mgr.run_job_sync(
            "save_to_knowledge",
            title="测试节点",
            content="这是一个测试知识节点。",
            bucket="business/wiki",
        )
        assert saved.success
        assert (kb_root / "business" / "wiki" / "测试节点.md").is_file()

        bases = mgr.run_job_sync("list_knowledge_bases")
        assert bases.success
        assert "demo" in json.dumps(bases.metadata, ensure_ascii=False) + str(bases.answer)

        mgr.run_job_sync("reindex")
        deadline = time.time() + 20
        blob = ""
        while time.time() < deadline:
            found = mgr.run_job_sync("knowledge_search", query="测试知识节点", limit=5)
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "测试节点" in blob:
                break
            time.sleep(1)
        assert "测试节点" in blob
    finally:
        mgr.close_all()
    assert mgr.is_started is False


def test_two_agents_get_two_workspaces(tmp_path):
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        mgr.run_job_sync("status", project_id="p1", agent_id="a1")
        mgr.run_job_sync("status", project_id="p1", agent_id="a2")
        assert (tmp_path / "data" / "workspaces" / "p1" / "a1").is_dir()
        assert (tmp_path / "data" / "workspaces" / "p1" / "a2").is_dir()
    finally:
        mgr.close_all()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_kb_manager.py -q`
Expected: FAIL（ModuleNotFoundError: aitester.services.kb.manager）

- [ ] **Step 3: 实现**

`backend/src/aitester/services/kb/manager.py`：

```python
"""ReMe 进程内嵌管理器：专属事件循环线程 + (project, agent) 实例池。

裁定依据 spec 2026-10-01-knowledge-base-reme-design.md：
禁 HTTP 服务、每实例独占 workspace、KB 全局共享、embedding 按 Key。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from aitester.services.kb.config import KbConfig, build_reme_config

DEFAULT_PROJECT = "default"
DEFAULT_CONSOLE_AGENT = "console"


class KbUnavailableError(RuntimeError):
    """知识库未启用或 ReMe 实例不可用。"""


class RemeKbManager:
    def __init__(self, settings: Any, data_dir: Path) -> None:
        self._settings = settings
        self._data_dir = Path(data_dir)
        self._apps: dict[tuple[str, str], Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        if not getattr(self._settings, "kb_enabled", False) or self._started:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="reme-kb-loop", daemon=True
        )
        self._thread.start()
        self._started = True

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro: Any) -> Future:
        if not self._started or self._loop is None:
            coro.close()
            raise KbUnavailableError("知识库未启用或未启动")
        assert self._thread is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _kb_config(self, project_id: str, agent_id: str) -> KbConfig:
        s = self._settings
        return KbConfig(
            workspace_dir=str(self._data_dir / "workspaces" / project_id / agent_id),
            kb_id=s.kb_id,
            kb_bases_dir=s.kb_bases_dir,
            create_missing=s.kb_create_missing,
            embedding_api_key=s.kb_embedding_api_key or s.dashscope_api_key,
            embedding_base_url=s.kb_embedding_base_url,
            embedding_model=s.kb_embedding_model,
            embedding_dimensions=s.kb_embedding_dimensions,
        )

    async def _get_app(self, project_id: str, agent_id: str):
        key = (project_id, agent_id)
        app = self._apps.get(key)
        if app is not None:
            return app
        from reme import Application

        app = Application(**build_reme_config(self._kb_config(project_id, agent_id)))
        await app.start()
        self._apps[key] = app
        return app

    async def _run(self, project_id: str, agent_id: str, name: str, kwargs: dict):
        app = await self._get_app(project_id, agent_id)
        return await app.run_job(name, **kwargs)

    def run_job_sync(
        self,
        name: str,
        *,
        project_id: str = DEFAULT_PROJECT,
        agent_id: str = DEFAULT_CONSOLE_AGENT,
        timeout: float = 60.0,
        **kwargs: Any,
    ):
        return self._submit(self._run(project_id, agent_id, name, kwargs)).result(timeout)

    async def run_job(
        self,
        name: str,
        *,
        project_id: str = DEFAULT_PROJECT,
        agent_id: str = DEFAULT_CONSOLE_AGENT,
        **kwargs: Any,
    ):
        future = self._submit(self._run(project_id, agent_id, name, kwargs))
        return await asyncio.wrap_future(future)

    def close_all(self, timeout: float = 30.0) -> None:
        if not self._started:
            return

        async def _close() -> None:
            for app in list(self._apps.values()):
                await app.close()
            self._apps.clear()

        try:
            self._submit(_close()).result(timeout)
        finally:
            assert self._loop is not None and self._thread is not None
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout)
            self._started = False
            self._loop = None
            self._thread = None
```

注：`test_disabled_manager_raises` 里 `SimpleNamespace` 无 `dashscope_api_key` 也没关系——disabled 路径在 `_kb_config` 前就抛。`roundtrip` 用例 settings 未含 `dashscope_api_key`，`_kb_config` 用 `getattr` 兜底：把 `embedding_api_key=s.kb_embedding_api_key or s.dashscope_api_key` 改为 `embedding_api_key=s.kb_embedding_api_key or getattr(s, "dashscope_api_key", "")`。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_kb_manager.py -q`
Expected: 3 passed（roundtrip 用例真起 Application，若 Windows junction 创建失败会显式报错——排查目标盘权限后重试，不得跳过）

- [ ] **Step 5: 全量回归 + Commit**

```bash
cd backend && uv run pytest -q
git add backend/src/aitester/services/kb/manager.py backend/tests/test_kb_manager.py
git commit -m "feat(kb): RemeKbManager 专属事件循环 + (project,agent) 实例池，save→reindex→search 实测回路"
```

---

### Task 4: Settings 扩展与 FastAPI 生命周期接线

**Files:**
- Modify: `backend/src/aitester/config.py:7-19`（Settings 字段）
- Modify: `backend/src/aitester/main.py:16-43`（create_app）
- Test: `backend/tests/test_kb_lifespan.py`

**Interfaces:**
- Consumes: `RemeKbManager`（Task 3）
- Produces:
  - `Settings` 新字段：`kb_enabled: bool = True`、`kb_id: str = "zhb_kb"`、`kb_bases_dir: str = ""`、`kb_create_missing: bool = True`、`kb_embedding_api_key: str = ""`、`kb_embedding_base_url: str = DEFAULT_EMBEDDING_BASE_URL`、`kb_embedding_model: str = DEFAULT_EMBEDDING_MODEL`、`kb_embedding_dimensions: int = 1024`
  - `create_app(model_config_path=None, capability_config_path=None, settings=None, kb_manager=None)`；`app.state.kb_manager`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_kb_lifespan.py`：

```python
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app


class _FakeKbManager:
    def __init__(self):
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def close_all(self, timeout: float = 30.0):
        self.closed = True


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        kb_id="demo",
        kb_bases_dir=str(tmp_path / "knowledge_bases"),
        kb_embedding_api_key="",
    )


def test_kb_manager_starts_and_closes_with_app(tmp_path):
    fake = _FakeKbManager()
    app = create_app(
        model_config_path=tmp_path / "models.json",
        capability_config_path=tmp_path / "caps.json",
        settings=_settings(tmp_path),
        kb_manager=fake,
    )
    with TestClient(app):
        assert app.state.kb_manager is fake
        assert fake.started
    assert fake.closed


def test_default_settings_kb_fields():
    s = Settings(_env_file=None)
    assert s.kb_enabled is True
    assert s.kb_id == "zhb_kb"
    assert s.kb_embedding_dimensions == 1024
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_kb_lifespan.py -q`
Expected: FAIL（TypeError: __init__() got an unexpected keyword argument 'kb_enabled' 或 create_app 无 kb_manager 参数）

- [ ] **Step 3: 实现**

`config.py` Settings 追加 8 个字段（名称/默认值见 Interfaces，embedding 两个默认值从 `aitester.services.kb.config` import 常量）。

`main.py`：

```python
from contextlib import asynccontextmanager
from aitester.services.kb.manager import RemeKbManager
```

`create_app(...)` 签名加 `kb_manager=None`；在现有 `application.state` 装配处之前：

```python
kb = kb_manager if kb_manager is not None else RemeKbManager(settings=settings, data_dir=DATA_DIR)

@asynccontextmanager
async def lifespan(_: FastAPI):
    kb.start()
    try:
        yield
    finally:
        kb.close_all()
```

`FastAPI(title="AiTester backend")` 改为 `FastAPI(title="AiTester backend", lifespan=lifespan)`，并 `app.state.kb_manager = kb`。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd backend && uv run pytest -q`
Expected: 全绿（≥246；既有 create_app 调用点如受新参数影响，用默认 None 自动建 manager——注意测试环境会 lazy 起真实例，但 manager 只在首次 run_job 才建 Application，TestClient 场景 `kb_enabled` 默认 True 但无人调 run_job，`close_all` 空池无害，不会拖慢测试；如某既有用例显式关闭 kb，可传 `kb_manager=_FakeKbManager()`）

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/config.py backend/src/aitester/main.py backend/tests/test_kb_lifespan.py
git commit -m "feat(kb): Settings 知识库字段 + FastAPI lifespan 挂载 RemeKbManager 启停"
```

---

### Task 5: /api/kb/* 端点

**Files:**
- Modify: `backend/src/aitester/interaction/schemas.py`（追加模型）
- Modify: `backend/src/aitester/interaction/router.py:24`（追加路由）
- Test: `backend/tests/test_kb_api.py`

**Interfaces:**
- Consumes: `app.state.kb_manager.run_job(...)`（Task 3/4）；`KbUnavailableError`
- Produces（全部挂在现有 `APIRouter(prefix="/api")`）：
  - `GET /api/kb/status` → `KbResponse`（job `status`）
  - `GET /api/kb/bases` → `KbResponse`（job `list_knowledge_bases`）
  - `POST /api/kb/search`（body `{query, limit=5, bucket="all"}`）→ `KbResponse`（job `knowledge_search`）
  - `POST /api/kb/save`（body `{title, content, bucket="business/wiki"}`）→ `KbResponse`（job `save_to_knowledge`）
  - `GET /api/kb/inbox` → `KbResponse`（job `list_knowledge_inbox`）
  - `POST /api/kb/inbox/promote|merge|reject`（body `{stem}`，merge 另收 `{target_path="", mode="REFINE"}`）→ `KbResponse`
  - schemas：`KbSearchRequest`、`KbSaveRequest`、`KbInboxStemRequest`、`KbInboxMergeRequest`、`KbResponse(success: bool, answer: Any, metadata: dict)`
  - 错误映射：`KbUnavailableError → 503`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_kb_api.py`（复用 test_api.py:29 `_isolated_client` 的 tmp 隔离思路）：

```python
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app
from aitester.services.kb.manager import KbUnavailableError


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


def _client(tmp_path, kb):
    app = create_app(
        model_config_path=tmp_path / "models.json",
        capability_config_path=tmp_path / "caps.json",
        settings=Settings(_env_file=None, kb_embedding_api_key=""),
        kb_manager=kb,
    )
    return TestClient(app)


def test_kb_search_dispatches_job(tmp_path):
    kb = _RecordingKbManager()
    with _client(tmp_path, kb) as client:
        r = client.post("/api/kb/search", json={"query": "热词", "limit": 3, "bucket": "business"})
    assert r.status_code == 200
    assert r.json() == {"success": True, "answer": "ok", "metadata": {"echo": "knowledge_search"}}
    assert kb.calls == [("knowledge_search", {"query": "热词", "limit": 3, "bucket": "business"})]


def test_kb_save_and_inbox_routes(tmp_path):
    kb = _RecordingKbManager()
    with _client(tmp_path, kb) as client:
        assert client.post("/api/kb/save", json={"title": "t", "content": "c"}).status_code == 200
        assert client.get("/api/kb/inbox").status_code == 200
        assert client.post("/api/kb/inbox/promote", json={"stem": "s"}).status_code == 200
    assert kb.calls[0] == ("save_to_knowledge", {"title": "t", "content": "c", "bucket": "business/wiki"})
    assert kb.calls[1] == ("list_knowledge_inbox", {})
    assert kb.calls[2] == ("promote_knowledge_inbox", {"stem": "s"})


def test_kb_status_unavailable_maps_503(tmp_path):
    kb = _RecordingKbManager(exc=KbUnavailableError("知识库未启用或未启动"))
    with _client(tmp_path, kb) as client:
        r = client.get("/api/kb/status")
    assert r.status_code == 503


def test_kb_get_bases(tmp_path):
    kb = _RecordingKbManager()
    with _client(tmp_path, kb) as client:
        assert client.get("/api/kb/bases").status_code == 200
    assert kb.calls == [("list_knowledge_bases", {})]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_kb_api.py -q`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 实现**

`schemas.py` 追加：

```python
class KbSearchRequest(BaseModel):
    query: str
    limit: int = 5
    bucket: str = "all"


class KbSaveRequest(BaseModel):
    title: str
    content: str
    bucket: str = "business/wiki"


class KbInboxStemRequest(BaseModel):
    stem: str


class KbInboxMergeRequest(BaseModel):
    stem: str
    target_path: str = ""
    mode: str = "REFINE"


class KbResponse(BaseModel):
    success: bool
    answer: Any = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
```

（`Any`/`Field` 若 schemas.py 未 import 则补。）

`router.py` 追加（沿用文件里既有异常映射写法，`KbUnavailableError` 单独 except → `HTTPException(503, detail=str(exc))`）：

```python
def _kb(request: Request):
    return request.app.state.kb_manager


def _payload(resp) -> dict:
    return {"success": resp.success, "answer": resp.answer, "metadata": resp.metadata or {}}


@router.get("/kb/status", response_model=KbResponse)
async def kb_status(request: Request):
    return _payload(await _kb(request).run_job("status"))


@router.get("/kb/bases", response_model=KbResponse)
async def kb_bases(request: Request):
    return _payload(await _kb(request).run_job("list_knowledge_bases"))


@router.post("/kb/search", response_model=KbResponse)
async def kb_search(request: Request, body: KbSearchRequest):
    return _payload(await _kb(request).run_job(
        "knowledge_search", query=body.query, limit=body.limit, bucket=body.bucket,
    ))


@router.post("/kb/save", response_model=KbResponse)
async def kb_save(request: Request, body: KbSaveRequest):
    return _payload(await _kb(request).run_job(
        "save_to_knowledge", title=body.title, content=body.content, bucket=body.bucket,
    ))


@router.get("/kb/inbox", response_model=KbResponse)
async def kb_inbox(request: Request):
    return _payload(await _kb(request).run_job("list_knowledge_inbox"))


@router.post("/kb/inbox/promote", response_model=KbResponse)
async def kb_inbox_promote(request: Request, body: KbInboxStemRequest):
    return _payload(await _kb(request).run_job("promote_knowledge_inbox", stem=body.stem))


@router.post("/kb/inbox/merge", response_model=KbResponse)
async def kb_inbox_merge(request: Request, body: KbInboxMergeRequest):
    return _payload(await _kb(request).run_job(
        "merge_knowledge_inbox", stem=body.stem, target_path=body.target_path, mode=body.mode,
    ))


@router.post("/kb/inbox/reject", response_model=KbResponse)
async def kb_inbox_reject(request: Request, body: KbInboxStemRequest):
    return _payload(await _kb(request).run_job("reject_knowledge_inbox", stem=body.stem))
```

（GET 端点同样包 try/except KbUnavailableError→503；`Request` 未 import 则补。）

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd backend && uv run pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/interaction backend/tests/test_kb_api.py
git commit -m "feat(kb): /api/kb 状态/检索/写入/inbox 端点，KbUnavailableError 映射 503"
```

---

### Task 6: 知识库工具接入智能体

**Files:**
- Create: `backend/src/aitester/adapters/tools/kb_tools.py`
- Modify: `backend/src/aitester/adapters/tools/__init__.py`（build_default_registry 扩参并注册）
- Modify: `backend/src/aitester/services/capability_config.py:15`（TOOL_CATALOG 追加两条）
- Modify: `backend/src/aitester/services/agent_runtime.py:35-45,61-71`（构造参数 kb + build 透传）
- Modify: `backend/src/aitester/main.py`（AgentRuntime 装配处传入 kb manager）
- Test: `backend/tests/test_kb_tools.py`

**Interfaces:**
- Consumes: `RemeKbManager.run_job_sync`（Task 3）；`ToolRegistry`/`AiTooler`（现有）
- Produces:
  - `KbSearchTool(kb, agent_id)` name=`knowledge_search`，args `{query: str, limit: int=5, bucket: str="all"}`
  - `KbSaveTool(kb, agent_id)` name=`save_to_knowledge`，args `{title: str, content: str, bucket: str="business/wiki"}`
  - `build_default_registry(cwd=".", session_id="default", observed=None, kb=None, agent_id="console")`——`kb is None` 时不注册 KB 工具（行为与现状一致）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_kb_tools.py`：

```python
from types import SimpleNamespace

from aitester.adapters.tools import build_default_registry


class _FakeKb:
    def __init__(self):
        self.calls = []

    def run_job_sync(self, name, *, project_id="default", agent_id="console", timeout=60.0, **kwargs):
        self.calls.append((name, agent_id, kwargs))
        return SimpleNamespace(success=True, answer="命中：测试节点", metadata={})


def test_registry_without_kb_has_no_kb_tools():
    reg = build_default_registry(cwd=".")
    assert reg.get("knowledge_search") is None


def test_kb_tools_run_against_manager():
    kb = _FakeKb()
    reg = build_default_registry(cwd=".", kb=kb, agent_id="case_design")
    out = reg.get("knowledge_search").invoke({"query": "热词", "limit": 2})
    assert "测试节点" in out
    assert kb.calls == [("knowledge_search", "case_design", {"query": "热词", "limit": 2, "bucket": "all"})]

    out2 = reg.get("save_to_knowledge").invoke({"title": "t", "content": "c"})
    assert out2
    assert kb.calls[1] == ("save_to_knowledge", "case_design", {"title": "t", "content": "c", "bucket": "business/wiki"})
```

（registry.py 基于 dict 存储，`get` 未命中返回 None，`test_registry_without_kb_has_no_kb_tools` 的断言成立。）

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_kb_tools.py -q`
Expected: FAIL（build_default_registry 不接受 kb 参数）

- [ ] **Step 3: 实现**

`kb_tools.py`：

```python
"""知识库工具：经 RemeKbManager 调用共享 KB 的检索与写入 job。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from aitester.adapters.tools.base import AiTooler


class KbSearchInput(BaseModel):
    query: str = Field(description="检索关键词或问题")
    limit: int = Field(default=5, ge=1, le=20, description="最多返回条数")
    bucket: str = Field(default="all", description="范围：all / business / test / business/wiki 等")


class KbSaveInput(BaseModel):
    title: str = Field(description="知识节点标题")
    content: str = Field(description="知识节点正文（Markdown）")
    bucket: str = Field(default="business/wiki", description="发布桶，如 business/wiki、test/test_design")


class KbSearchTool(AiTooler):
    name: str = "knowledge_search"
    description: str = (
        "检索全局共享知识库（智会宝 zhb_kb 等），返回命中的知识节点与出处路径。"
        "编写用例、回答业务问题前先用它查证已有知识。"
    )
    args_schema: type[BaseModel] = KbSearchInput
    kb: Any = None
    agent_id: str = "console"

    def _run(self, query: str, limit: int = 5, bucket: str = "all", **_: Any) -> str:
        resp = self.kb.run_job_sync(
            "knowledge_search", agent_id=self.agent_id, query=query, limit=limit, bucket=bucket,
        )
        if not resp.success:
            return f"知识库检索失败：{resp.answer}"
        if isinstance(resp.answer, str) and resp.answer.strip():
            return resp.answer
        return json.dumps(resp.metadata, ensure_ascii=False, default=str)


class KbSaveTool(AiTooler):
    name: str = "save_to_knowledge"
    description: str = (
        "把一个确定成立的知识节点写入共享知识库发布桶。写入会影响所有项目与其他智能体，"
        "内容必须经用户确认或来自已验证事实。"
    )
    args_schema: type[BaseModel] = KbSaveInput
    kb: Any = None
    agent_id: str = "console"

    def _run(self, title: str, content: str, bucket: str = "business/wiki", **_: Any) -> str:
        resp = self.kb.run_job_sync(
            "save_to_knowledge", agent_id=self.agent_id, title=title, content=content, bucket=bucket,
        )
        head = "已写入知识库" if resp.success else "写入知识库失败"
        body = resp.answer if isinstance(resp.answer, str) else json.dumps(resp.answer, ensure_ascii=False, default=str)
        return f"{head}：{body}"
```

`adapters/tools/__init__.py`：`build_default_registry` 签名加 `kb=None, agent_id: str = "console"`；末尾条件注册：

```python
    if kb is not None:
        registry.register(KbSearchTool(kb=kb, agent_id=agent_id))
        registry.register(KbSaveTool(kb=kb, agent_id=agent_id))
```

`capability_config.py TOOL_CATALOG` 追加两条（风格对齐现有条目）：

```python
    {
        "id": "knowledge_search",
        "group": "知识库工具",
        "icon": "🔍",
        "label": "knowledge_search",
        "os": "全平台",
        "desc": "检索全局共享知识库（发布桶范围），返回命中节点与出处；回答业务问题前先查证。",
    },
    {
        "id": "save_to_knowledge",
        "group": "知识库工具",
        "icon": "📥",
        "label": "save_to_knowledge",
        "os": "全平台",
        "desc": "把已确认的知识节点写入共享知识库发布桶，全项目共享可见；写入前必须经用户确认。",
    },
```

`agent_runtime.py`：`__init__` 加 `kb=None` 参数存 `self._kb`；`build()` 里 `build_default_registry(...)` 追加 `kb=self._kb, agent_id=agent_id`。`main.py` AgentRuntime 构造处传 `kb=app.state.kb_manager`（即 `kb` 局部变量）。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `cd backend && uv run pytest -q`
Expected: 全绿。既有用例若因 `agent_state` 目录新增两条目而断言失败，按其语义更新期望值（工具目录是显式断言面的用例需同步 +2），不许反向迁就实现。

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/adapters/tools backend/src/aitester/services backend/src/aitester/main.py backend/tests/test_kb_tools.py
git commit -m "feat(kb): knowledge_search / save_to_knowledge 注册为智能体内置工具（按 agent_id 绑定实例池）"
```

---

### Task 7: /kb 前端页（状态 / 检索 / 写入）

**Files:**
- Modify: `frontend/src/api/client.ts`（追加 KB 接口函数）
- Modify: `frontend/src/pages/KbPage.tsx`（替换占位实现）

**Interfaces:**
- Consumes: Task 5 的 `/api/kb/*` 端点与 `KbResponse` JSON 形状 `{success, answer, metadata}`
- Produces: 可交互 /kb 页（应用级导航项不变：App.tsx 已有 `/kb` 路由）

- [ ] **Step 1: client.ts 追加**

```typescript
export interface KbResponse {
  success: boolean;
  answer: unknown;
  metadata: Record<string, unknown>;
}

export function getKbStatus(): Promise<KbResponse> {
  return apiFetch("/api/kb/status");
}

export function kbSearch(query: string, limit: number, bucket: string): Promise<KbResponse> {
  return apiFetch("/api/kb/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit, bucket }),
  });
}

export function kbSave(title: string, content: string, bucket: string): Promise<KbResponse> {
  return apiFetch("/api/kb/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, content, bucket }),
  });
}
```

（apiFetch 的 URL 前缀写法以 client.ts:76-93 现状为准对齐。）

- [ ] **Step 2: KbPage.tsx 重写为三区布局**

单文件组件，三个带文字标题的区块（沿用项目现有页面 CSS 类名风格，参考 ChatPage.tsx/settings 组件的排版基元；不引入新依赖）：

1. **状态卡**：挂载时 `getKbStatus()` + `getKbBases()`（如 client 未导出则用 `apiFetch("/api/kb/bases")` 同法补一个 `getKbBases()`）；显示 `success`、`metadata` 中 KB id 与实体路径（解析失败时降级显示原始 JSON）；`kb_enabled=False`/503 时显示「知识库未启用或不可用」提示条，不白屏。
2. **检索区**：label 文本输入（查询）、数字输入（条数，默认 5）、下拉（范围：all/business/test/business/wiki/test/test_design/test/defects）、按钮「检索」；结果区渲染 `answer` 文本与 `metadata.results` 列表（标题+路径），空结果显式提示「无命中，可尝试更换范围桶」。
3. **写入区**：标题输入、正文 textarea、桶下拉（business/wiki 默认）、按钮「写入知识库」；点击前 `window.confirm` 提示「写入后对所有项目与其他智能体可见，确认？」；成功后展示返回信息并提示可在检索区验证；失败展示后端 detail。

控件全部带可见文字标签；无裸图标按钮；文案中文。

- [ ] **Step 3: 构建校验**

Run: `cd frontend && npm run build`
Expected: tsc+vite 构建 0 错误

- [ ] **Step 4: 本地实测（用户可见验证）**

按项目现有启动方式起 backend（`cd backend && uv run uvicorn aitester.main:app`，端口以 Settings 为准）与前端 dev server，浏览器打开用户可访问的 localhost 地址：
- /kb 页状态卡显示真实 `zhb_kb` 的状态与实体路径；
- 用用户指定的已有知识词检索命中并显示出处路径；
- **写入实测属真实环境写盘**（本 API 面无删除 job，测试节点须手动清理）：仅在用户当面明确同意后点「写入知识库」，否则跳过此步、只验状态与检索；
- 若真实向量 Key 未配置，确认纯 BM25 下检索正常（不做任何真实 embedding API 调用）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/KbPage.tsx
git commit -m "feat(kb): 知识库页首版——全局状态、检索与写入三区，替换占位页"
```

---

### Task 8: 文档与收尾

**Files:**
- Modify: `README.md`（能力清单追加知识库段落）
- Modify: `docs/superpowers/specs/2026-10-01-knowledge-base-reme-design.md`（状态：已实施）

**Interfaces:**
- Consumes: Task 1-7 全部产出
- Produces: 文档一致

- [ ] **Step 1: README 追加知识库段落**

3-5 行：进程内嵌 ReMe、`zhb_kb` 全局共享（实体 `~/.reme/knowledge_bases/zhb_kb`）、embedding 按 Key 启用（`KB_EMBEDDING_API_KEY` 环境变量，未设则用 `DASHSCOPE_API_KEY`，均无则纯 BM25）、inbox 与 dream 后续专项。

- [ ] **Step 2: 设计文档状态改「已实施」，全量测试最终确认**

Run: `cd backend && uv run pytest -q`
Expected: 全绿，记录最终用例数（供 commit 信息引用）

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-01-knowledge-base-reme-design.md
git commit -m "docs(kb): 知识库专项文档收尾——README 能力段落与设计文档状态回写"
```

---

## 验收清单（对照 spec）

| spec 要求 | 落点 |
|---|---|
| 裁定 1：进程内嵌、禁 HTTP | Task 3 manager（仅 Application/run_job）、Task 2 service 占位 web_enabled=False |
| 裁定 2：embedding 按 Key | Task 2 builder + Task 4 Settings（kb_embedding_api_key 回落 dashscope_api_key） |
| 裁定 3：(project, agent) 实例池 | Task 3 pool + `test_two_agents_get_two_workspaces` |
| 裁定 4：KB 全局共享 | Task 2 kb_id 应用级、Task 7 应用级 /kb 页；写锁依赖 ReMe lock.py（无自造锁代码） |
| 五项能力 | 路径（Task 3 解析+Task 7 展示）、访问（Task 3/5）、索引（save/reindex）、检索（knowledge_search）、回流（inbox 端点；dream 留后续） |
| 成本门禁 | 全程测试零真实 API 调用；Task 7 Step 4 明确不做 embedding 实调，真实 Key 实测需用户确认 |
