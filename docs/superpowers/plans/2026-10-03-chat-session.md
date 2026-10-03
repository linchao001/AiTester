# 聊天页 · 第 1 片：会话闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `/chat` 成为真页——消息落盘、重启不丢、会话可列表/可续聊/可删除，并把原型的左侧会话历史、消息流与 composer 接上真实的 7 层链路。

**Architecture:** 后端新增 `SessionStore`（`index.json` 元信息 + `<id>.jsonl` 消息追加）与实现既有 `MemoryStore` 协议的 `FileMemoryStore`，由 `ChatService.send` 按会话 id 形态在「文件记忆 / 进程内记忆」之间选择；`/api/chat/sessions*` 三端点只管已落盘会话，`run_graph` 的过程痕迹（`ok/round/detail`）随 assistant 消息一起入档供 UI 呈现。前端 `pages/chat/` 拆 4 个组件 + 1 个纯函数模块，样式全部复用 `App.css` 里已存在但零消费方的聊天类。

**Tech Stack:** Python 3.12 / FastAPI / LangGraph（已有）/ pytest + TestClient；React 18 + TypeScript + Vite（无前端测试框架，门禁是 `npm run build`）。

**Spec:** `docs/superpowers/specs/2026-10-03-chat-session-design.md`（用户已批准；执行时两份一起读，spec 是裁定权威）

## Global Constraints

- 后端测试一律 `tmp_path` + `Settings(_env_file=None)` + 假 provider（`MockProvider`/`_SpyProvider`），**零真实网络、零真实 LLM 调用**；`backend/data/*.json` 与 `backend/data/sessions/` 对测试是只读的，任何 create_app 测试必须显式传 `sessions_dir`。
- 不许动正在跑的 **8000 / 5173** 服务；真机走查自起端口，走查用的探针会话**用后即删**。
- 未获用户明确同意：不 `git push`、不发起真实模型调用、不往真实配置写数据。提交（commit）本计划已获授权，逐任务提交，**只在 master 本地提交**。
- 文案口径：**模型侧/代码标识符英文，UI 文案与 API `detail` 中文**；注释中文且只在「为什么」不显然处写一行。
- UI **不得出现** `zhb` 或 reme 实体根路径；`backend/data/sessions` 是本应用数据目录、允许外显。
- 折叠面板沿用同一套模式；控件必须带文字或明确 title；不做纯图标死按钮。本期不渲染「当前项目」卡片与 `⋮ 更多`、`📁 工作区` 按钮。
- 用户动作必须有可见结果：点发送要么出回复，要么出中文错误 toast；不允许输入框留着原文加一句提示就结束。
- 真机走查要在 localhost 上给用户看（内置浏览器他看不到）。
- 会话 id 形态判据只有一处：`services/session_store.py::is_session_id`；任何地方要判 `sess_*` 都调它，不许再写一份正则。

---

## 文件结构

| 文件 | 动作 | 责任 |
|---|---|---|
| `backend/src/aitester/agents/catalog.py` | Modify | 新增 `is_platform_agent`：平台智能体唯一判据 |
| `backend/src/aitester/agents/__init__.py` | Modify | 导出 `is_platform_agent` |
| `backend/src/aitester/services/agent_runtime.py` | Modify:60 | 内联集合推导换成 `is_platform_agent` |
| `backend/src/aitester/services/session_store.py` | Create | 会话真相：`index.json` + `<id>.jsonl` 的唯一读写口 |
| `backend/src/aitester/memory/file_memory.py` | Create | 把 `MemoryStore` 协议接到 `SessionStore` 上 |
| `backend/src/aitester/memory/base.py` | Modify | `save` 增可选 `steps` 形参（协议扩一处） |
| `backend/src/aitester/memory/in_memory.py` | Modify | 接住 `steps` 并显式忽略 |
| `backend/src/aitester/memory/__init__.py` | Modify | 导出 `FileMemoryStore` |
| `backend/src/aitester/services/chat.py` | Modify | 会话解析、记忆选型、`HISTORY_MAX`、`steps` 出口 |
| `backend/src/aitester/main.py` | Modify | `create_app(sessions_dir=...)` 注入缝 + 装配 + 挂新路由 |
| `backend/src/aitester/orchestration/agent_graph.py` | Modify:71-88 | 过程痕迹扩 `ok/round/detail` |
| `backend/src/aitester/interaction/schemas.py` | Modify | `SendRequest.session_id` 默认 `""`、`StepInfo`、会话三响应 |
| `backend/src/aitester/interaction/sessions.py` | Create | 会话三端点 |
| `backend/src/aitester/interaction/router.py` | Modify:55-79 | `send` 透传 `session_id/title/steps` |
| `backend/tests/test_agents.py` | Append | `is_platform_agent` |
| `backend/tests/test_chat_service.py` | Append | 会话装配 + `HISTORY_MAX` |
| `backend/tests/test_orchestration.py` | Append | steps |
| `backend/tests/test_session_store.py` | Create | SessionStore 单测 |
| `backend/tests/test_file_memory.py` | Create | FileMemoryStore 单测 |
| `backend/tests/test_api_chat_sessions.py` | Create | 三端点契约 |
| `backend/tests/test_api.py` | Modify:29-39 | `_isolated_client` 补 `sessions_dir` |
| `backend/tests/test_kb_api.py` | Modify:28-35,87-125 | 同上 + `SimpleNamespace` 替身补键 |
| `frontend/src/api/client.ts` | Modify:268-279 尾追 | 会话类型与 4 个函数、`SendResponse` 新键 |
| `frontend/src/pages/chat/utils.ts` | Create | `estTokens/contextUsage/groupSessions/fmtTime` |
| `frontend/src/pages/chat/SessionPane.tsx` | Create | 侧栏：智能体卡片 + 新建 + 搜索 + 分组列表 |
| `frontend/src/pages/chat/MessageList.tsx` | Create | 消息流 + 欢迎态 + 过程块 + meta |
| `frontend/src/pages/chat/Composer.tsx` | Create | 输入区 + ctx meter + 只读 chip |
| `frontend/src/pages/ChatPage.tsx` | Rewrite | 装配与状态机 |
| `frontend/src/App.css` | Modify:128 后插一行 | 气泡内 `.md-preview` 显示规则 |
| `docs/superpowers/specs/2026-10-03-chat-session-design.md` | Modify:3 | 状态改「已实现」 |
| `README.md` | Modify | 聊天页能力段 |

---

### Task 1: 平台智能体判据收口 `is_platform_agent`

**Files:**
- Modify: `backend/src/aitester/agents/catalog.py`（`:64` 之后追加）
- Modify: `backend/src/aitester/agents/__init__.py`
- Modify: `backend/src/aitester/services/agent_runtime.py:60-61`
- Test: `backend/tests/test_agents.py`（文件末尾追加）

**Interfaces:**
- Consumes: 既有 `PLATFORM_AGENT_CATALOG`（`catalog.py:64`）
- Produces: `is_platform_agent(agent_id: str) -> bool`，从 `aitester.agents` 与 `aitester.agents.catalog` 均可导入

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_agents.py`：

```python
def test_is_platform_agent_only_covers_platform_catalog() -> None:
    from aitester.agents import is_platform_agent

    assert is_platform_agent("kb_assistant") is True   # 平台内置：不进能力配置/下拉
    assert is_platform_agent("case_design") is False   # 项目智能体：走落盘与会话列表
    assert is_platform_agent("ghost") is False         # 未知 id 不是「平台」，未知由 find_agent 负责拒
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_agents.py -k is_platform_agent -v`
Expected: FAIL — `ImportError: cannot import name 'is_platform_agent'`

- [ ] **Step 3: 实现**

`catalog.py` 在 `PLATFORM_AGENT_CATALOG` 定义之后、`find_agent` 之前插入：

```python
def is_platform_agent(agent_id: str) -> bool:
    """平台功能智能体唯一判据：会话闭环据此决定「落不落盘」（spec 裁定）。"""
    return any(spec.id == agent_id for spec in PLATFORM_AGENT_CATALOG)
```

`agents/__init__.py` 的 import 块与 `__all__` 各加一行 `"is_platform_agent"`（保持现有字母序：`__all__` 里放在 `"find_agent"` 之后）。

`services/agent_runtime.py:60-61` 换成：

```python
        if is_platform_agent(spec.id):
            return self._build_platform_agent(spec, session_id, provider_override)
```

并在该文件既有的 `from aitester.agents...` 导入里补 `is_platform_agent`；若 `PLATFORM_AGENT_CATALOG` 在该文件因此不再被引用，删掉它的导入（勿留死导入）。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_agents.py tests/test_agent_runtime.py -v`
Expected: PASS（`test_agent_runtime.py` 全绿＝短路行为未变）

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/agents/catalog.py backend/src/aitester/agents/__init__.py backend/src/aitester/services/agent_runtime.py backend/tests/test_agents.py
git commit -m "refactor(agents): 平台智能体判据收口为 is_platform_agent"
```

---

### Task 2: `SessionStore`（index.json + jsonl）

**Files:**
- Create: `backend/src/aitester/services/session_store.py`
- Test: `backend/tests/test_session_store.py`

**Interfaces:**
- Consumes: `FileJsonConfigRepository`（`storage/json_config_repo.py:23`，`load()->dict|None`、`save(dict)`，同目录 tmp + `os.replace`）
- Produces:
  - `SESSION_ID_RE`、`is_session_id(value: str) -> bool`
  - `Session`：`id, agent_id, title, created_at, updated_at, message_count`（dataclass，可 `to_dict()`）
  - `ChatMessage`：`role, content, ts, steps: list[dict] | None`（dataclass，`to_dict()` / `from_dict()`）
  - `SessionStoreError(detail)`，属性 `.detail` 中文
  - `SessionStore(root: Path)`：`new_id()`、`create(session_id, agent_id, first_message)`、`list(agent_id)`、`get(session_id)`、`messages(session_id)`、`append(session_id, role, content, steps=None)`、`delete(session_id)`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_session_store.py`：

```python
import json

import pytest

from aitester.services.session_store import (
    ChatMessage,
    Session,
    SessionStore,
    SessionStoreError,
    is_session_id,
)


def _store(tmp_path):
    return SessionStore(tmp_path / "sessions")


def test_new_id_matches_form_and_is_unique(tmp_path) -> None:
    store = _store(tmp_path)
    ids = {store.new_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(is_session_id(i) for i in ids)


def test_is_session_id_rejects_other_forms() -> None:
    assert is_session_id("sess_0a1b2c3d") is True
    for bad in ("sess_0A1B2C3D", "sess_0a1b2c", "kb-console", "default", "", "proj_7e29a494"):
        assert is_session_id(bad) is False, bad


def test_create_derives_title_from_first_message(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    sess = store.create(sid, "case_design", "  第一行很长的问题\n第二行不要进标题  ")
    assert sess.id == sid and sess.agent_id == "case_design"
    assert sess.title == "第一行很长的问题 第二行不"  # 换行转空格后取前 16 字
    assert sess.message_count == 0


def test_title_falls_back_when_first_message_blank(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.create(store.new_id(), "case_design", "  \n  ").title == "新会话"


def test_create_is_idempotent_for_existing_id(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    first = store.create(sid, "case_design", "原始问题")
    again = store.create(sid, "case_design", "另一个问题")
    assert again.title == first.title  # 已存在只返回既有，绝不覆盖标题
    assert [s.id for s in store.list("case_design")] == [sid]


def test_append_bumps_count_updates_and_appends_jsonl(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    store.append(sid, "user", "问题")
    store.append(sid, "assistant", "答", steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}])
    msgs = store.messages(sid)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].steps == [{"tool": "read", "ok": True, "round": 1, "detail": "{}"}]
    assert msgs[0].steps is None
    got = store.get(sid)
    assert got.message_count == 2 and got.updated_at >= msgs[-1].ts
    lines = (tmp_path / "sessions" / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["content"] == "答"


def test_append_unknown_session_raises_actionable(tmp_path) -> None:
    store = _store(tmp_path)
    with pytest.raises(SessionStoreError) as exc_info:
        store.append("sess_deadbeef", "user", "hi")
    assert exc_info.value.detail == "会话不存在或已被删除"


def test_list_sorts_by_updated_at_desc_and_scopes_by_agent(tmp_path) -> None:
    store = _store(tmp_path)
    a = store.create(store.new_id(), "case_design", "A")
    b = store.create(store.new_id(), "case_design", "B")
    store.append(b.id, "user", "B 又说话了")
    assert [s.id for s in store.list("case_design")] == [b.id, a.id]
    assert store.list("ghost") == []


def test_delete_removes_index_entry_and_file(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    store.append(sid, "user", "问题")
    path = tmp_path / "sessions" / f"{sid}.jsonl"
    assert path.exists()
    assert store.delete(sid) is True
    assert store.get(sid) is None and store.messages(sid) == []
    assert not path.exists()
    assert store.delete(sid) is False  # 二次删除明确返回 False，路由据此 404


def test_index_file_shape_is_versioned_and_ignores_orphans(tmp_path) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["version"] == 1
    assert index["sessions"][0]["id"] == sid
    assert set(index["sessions"][0]) == {
        "id", "agent_id", "title", "created_at", "updated_at", "message_count"
    }


def test_store_survives_reinstantiation(tmp_path) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", "问题")
    store.append(sid, "user", "问题")
    store.append(sid, "assistant", "答")
    reopened = _store(tmp_path)
    got = reopened.get(sid)
    assert got is not None and got.title == "问题" and got.message_count == 2
    assert [m.content for m in reopened.messages(sid)] == ["问题", "答"]
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_session_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aitester.services.session_store'`

- [ ] **Step 3: 实现**

`backend/src/aitester/services/session_store.py`：

```python
"""会话真相：index.json 存元信息，<session_id>.jsonl 逐行追加消息。

双文件是有意的：每次发消息重写全量历史是写放大，append 是 O(1)；列表只需 index。
index 走 FileJsonConfigRepository（同目录 tmp + os.replace），与项目/模型配置同一原子写口。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aitester.storage import FileJsonConfigRepository

SESSION_ID_RE = re.compile(r"^sess_[0-9a-f]{8}$")
TITLE_MAX = 16
DETAIL_MAX = 80
DEFAULT_TITLE = "新会话"
MISSING_SESSION_DETAIL = "会话不存在或已被删除"


def is_session_id(value: str) -> bool:
    """sess_ 形态是唯一真相口：非此形态的传入 id 一律按临时键处理（spec 兼容裁定）。"""
    return bool(SESSION_ID_RE.match(value or ""))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _title_from(first_message: str) -> str:
    text = " ".join((first_message or "").split())
    return text[:TITLE_MAX] or DEFAULT_TITLE


class SessionStoreError(RuntimeError):
    """会话读写失败，交互层映射 404，detail 面向用户且可照做。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class Session:
    id: str
    agent_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatMessage:
    role: str
    content: str
    ts: int
    steps: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "ChatMessage":
        steps = raw.get("steps")
        return ChatMessage(
            role=str(raw.get("role") or ""),
            content=str(raw.get("content") or ""),
            ts=int(raw.get("ts") or 0),
            steps=steps if isinstance(steps, list) else None,
        )


@dataclass
class _Index:
    version: int = 1
    sessions: list[dict[str, Any]] = field(default_factory=list)


class SessionStore:
    """会话目录的唯一读写者；index 重写由一把锁串行化（路由 sync def 跑在线程池）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._repo = FileJsonConfigRepository(self._root / "index.json")
        self._lock = threading.Lock()
        with self._lock:
            self._index = self._load_index()

    def _load_index(self) -> _Index:
        raw = self._repo.load()
        if not isinstance(raw, dict):
            return _Index()
        items = raw.get("sessions")
        items = items if isinstance(items, list) else []
        sessions: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or "")
            if not is_session_id(sid) or any(s["id"] == sid for s in sessions):
                continue
            agent_id = str(item.get("agent_id") or "")
            if not agent_id:
                continue
            created = int(item.get("created_at") or 0)
            sessions.append({
                "id": sid,
                "agent_id": agent_id,
                "title": str(item.get("title") or DEFAULT_TITLE)[:TITLE_MAX * 4] or DEFAULT_TITLE,
                "created_at": created,
                "updated_at": int(item.get("updated_at") or created) or created,
                "message_count": max(0, int(item.get("message_count") or 0)),
            })
        return _Index(sessions=sessions)

    def _save_index(self) -> None:
        # 调用方必须持锁；jsonl 是消息真相，index 损坏可由 messages() 兜底重建计数
        self._repo.save({"version": 1, "sessions": self._index.sessions})

    def _path(self, session_id: str) -> Path:
        if not is_session_id(session_id):
            raise SessionStoreError(MISSING_SESSION_DETAIL)
        return self._root / f"{session_id}.jsonl"

    def new_id(self) -> str:
        while True:
            sid = f"sess_{uuid.uuid4().hex[:8]}"
            if sid not in self._index_by_id():
                return sid

    def _index_by_id(self) -> dict[str, dict[str, Any]]:
        return {s["id"]: s for s in self._index.sessions}

    def create(self, session_id: str, agent_id: str, first_message: str) -> Session:
        if not is_session_id(session_id):
            raise SessionStoreError(MISSING_SESSION_DETAIL)
        with self._lock:
            existing = self._index_by_id().get(session_id)
            if existing is not None:  # 幂等：重入不覆盖标题与 created_at
                return Session(**existing)
            now = _now_ms()
            record = {
                "id": session_id,
                "agent_id": agent_id,
                "title": _title_from(first_message),
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
            }
            self._index.sessions.append(record)
            self._save_index()
            self._path(session_id).touch()
            return Session(**record)

    def list(self, agent_id: str) -> list[Session]:
        rows = [s for s in self._index.sessions if s["agent_id"] == agent_id]
        rows.sort(key=lambda s: (s["updated_at"], s["created_at"]), reverse=True)
        return [Session(**s) for s in rows]

    def get(self, session_id: str) -> Session | None:
        record = self._index_by_id().get(session_id)
        return Session(**record) if record else None

    def messages(self, session_id: str) -> list[ChatMessage]:
        path = self._path(session_id)
        if not path.exists():
            return []
        out: list[ChatMessage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue  # 半行（进程被杀）跳过，坏一行不该让整段历史读不出
            if isinstance(raw, dict):
                out.append(ChatMessage.from_dict(raw))
        return out

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        path = self._path(session_id)
        ts = _now_ms()
        line = ChatMessage(role=role, content=content, ts=ts, steps=steps or None).to_dict()
        # jsonl 先写、index 后记：中途崩溃只丢一次计数更新，消息本身不丢
        with self._lock:
            record = self._index_by_id().get(session_id)
            if record is None:
                raise SessionStoreError(MISSING_SESSION_DETAIL)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            record["updated_at"] = max(ts, record["created_at"])
            record["message_count"] = len(self.messages(session_id))
            self._save_index()

    def delete(self, session_id: str) -> bool:
        with self._lock:
            record = self._index_by_id().get(session_id)
            if record is None:
                return False
            self._index.sessions.remove(record)
            self._save_index()
            self._path(session_id).unlink(missing_ok=True)
            return True
```

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_session_store.py -v`
Expected: PASS（13 个用例全绿）

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/session_store.py backend/tests/test_session_store.py
git commit -m "feat(sessions): SessionStore 落盘 index.json 与逐行 jsonl"
```

---

### Task 3: `FileMemoryStore` 接上 `MemoryStore` 协议

**Files:**
- Modify: `backend/src/aitester/memory/base.py:7`
- Modify: `backend/src/aitester/memory/in_memory.py:6-7`
- Create: `backend/src/aitester/memory/file_memory.py`
- Modify: `backend/src/aitester/memory/__init__.py`
- Test: `backend/tests/test_file_memory.py`

**Interfaces:**
- Consumes: `SessionStore.new_id/create/append/messages`（Task 2）、`is_session_id`
- Produces: `FileMemoryStore(store: SessionStore)`，`save(session_id: str, role: str, content: str, steps: list[dict] | None = None) -> None`、`recall(session_id: str) -> list[dict[str, str]]`；`MemoryStore.save` 协议自本任务起带可选 `steps`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_file_memory.py`：

```python
from aitester.memory import FileMemoryStore, InMemoryMemoryStore
from aitester.services.session_store import SessionStore


def _new_sid(tmp_path) -> str:
    return SessionStore(tmp_path / "sessions").new_id()


def test_first_user_save_creates_session_with_title(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "生成登录用例")
    got = store.get(sid)
    assert got is not None and got.title == "生成登录用例" and got.agent_id == "case_design"
    assert [m.content for m in store.messages(sid)] == ["生成登录用例"]


def test_second_save_appends_and_steps_persist(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "问")
    mem.save(f"case_design:{sid}", "assistant", "答", steps=[{"tool": "read", "ok": True}])
    msgs = store.messages(sid)
    assert [(m.role, m.content) for m in msgs] == [("user", "问"), ("assistant", "答")]
    assert msgs[1].steps == [{"tool": "read", "ok": True}]


def test_recall_returns_role_content_in_order(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store)
    sid = _new_sid(tmp_path)
    for role, content in [("user", "一"), ("assistant", "二"), ("user", "三")]:
        mem.save(f"case_design:{sid}", role, content)
    assert mem.recall(f"case_design:{sid}") == [
        {"role": "user", "content": "一"},
        {"role": "assistant", "content": "二"},
        {"role": "user", "content": "三"},
    ]


def test_unregistered_or_temporary_key_recalls_empty(tmp_path) -> None:
    mem = FileMemoryStore(SessionStore(tmp_path / "sessions"))
    assert mem.recall("case_design:sess_00000000") == []
    assert mem.recall("kb_assistant:kb-console") == []


def test_key_split_uses_first_colon_only(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "含:冒号的内容")
    assert store.messages(sid)[0].content == "含:冒号的内容"


def test_in_memory_store_accepts_and_ignores_steps() -> None:
    mem = InMemoryMemoryStore()
    mem.save("s1", "assistant", "答", steps=[{"tool": "read"}])
    assert mem.recall("s1") == [{"role": "assistant", "content": "答"}]
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_file_memory.py -v`
Expected: FAIL — `ImportError: cannot import name 'FileMemoryStore'`

- [ ] **Step 3: 实现**

`memory/base.py:7` 改为（协议扩一处，注释说明为何扩）：

```python
    # steps：工具过程痕迹，只有文件实现需要持久化；实现者不接受该形参即断链
    def save(self, session_id: str, role: str, content: str,
             steps: list[dict] | None = None) -> None: ...
```

`memory/in_memory.py:6-7` 改为：

```python
    def save(self, session_id: str, role: str, content: str,
             steps: list[dict] | None = None) -> None:
        # 骨架实现不建模过程块：steps 是真实链路产物，进程内记忆只保 role/content
        self._messages.setdefault(session_id, []).append({"role": role, "content": content})
```

`memory/file_memory.py`：

```python
"""文件记忆：把 MemoryStore 的 (键, role, content) 落到 SessionStore。

键形态是刻意的耦合：ChatService 传 f"{agent_id}:{session_id}"，此处按首个冒号切分。
agent_id 与 sess_* 字符集均不含冒号，故切分无歧义（spec 裁定，测试锁住）。
"""

from __future__ import annotations

from aitester.services.session_store import SessionStore, is_session_id


class FileMemoryStore:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    @staticmethod
    def _split(key: str) -> tuple[str, str]:
        agent_id, _, session_id = key.partition(":")
        return agent_id, session_id

    def save(
        self,
        session_id: str,
        role: str,
        content: str,
        steps: list[dict] | None = None,
    ) -> None:
        agent_id, sid = self._split(session_id)
        if self._store.get(sid) is None:
            # 首条消息建会话（延迟落盘裁定）：失败发送不留 0 消息幽灵会话
            self._store.create(sid, agent_id, content if role == "user" else "")
        self._store.append(sid, role, content, steps)

    def recall(self, session_id: str) -> list[dict[str, str]]:
        _, sid = self._split(session_id)
        if not is_session_id(sid):
            return []
        return [
            {"role": m.role, "content": m.content}
            for m in self._store.messages(sid)
        ]
```

`memory/__init__.py`：

```python
"""记忆层：会话消息的保存与召回。"""
from aitester.memory.base import MemoryStore
from aitester.memory.file_memory import FileMemoryStore
from aitester.memory.in_memory import InMemoryMemoryStore

__all__ = ["FileMemoryStore", "InMemoryMemoryStore", "MemoryStore"]
```

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_file_memory.py tests/test_storage_memory.py tests/test_session_store.py -v`
Expected: PASS（`test_storage_memory.py` 是既有 InMemory/Repository 回归，确认协议扩参不破旧断言）

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/memory backend/tests/test_file_memory.py
git commit -m "feat(memory): FileMemoryStore 把记忆协议落到 SessionStore"
```

---

### Task 4: ChatService 装配（会话选型 / 延迟建会话 / HISTORY_MAX）

**Files:**
- Modify: `backend/src/aitester/services/chat.py`（`:1-100` 全文，重点 `:22-40`、`:41-76`、`:78-95`）
- Modify: `backend/src/aitester/main.py:21-68`
- Test: `backend/tests/test_chat_service.py`（末尾追加）

**Interfaces:**
- Consumes: `SessionStore`/`FileMemoryStore`/`is_session_id`（Task 2、3）、`is_platform_agent`（Task 1）
- Produces: `ChatService(..., sessions: SessionStore | None = None)`；`_complete(..., memory: MemoryStore | None = None)`；`send()` 返回 dict 增 `session_id`、`title`、`steps`；常量 `HISTORY_MAX = 40`；`create_app(sessions_dir: Path | None = None)`

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_chat_service.py`（该文件已有 `MockProvider`/`ProviderConfigError`/`ChatService`/`AIMessage` 导入，下面这段自带其余导入）：

```python
from types import SimpleNamespace

from aitester.memory import FileMemoryStore, InMemoryMemoryStore
from aitester.services.agent_runtime import AgentInstance
from aitester.services.session_store import SessionStore, SessionStoreError


def _sentinel_runtime():
    """provider 解析与记忆选型互不相关：假 runtime 只把注入的 provider 原样回出来。"""
    return SimpleNamespace(build=lambda agent_id, session_id, provider_override=None:
                           AgentInstance(agent_id=agent_id, system_prompt="p",
                                         provider=provider_override or MockProvider(),
                                         tools=[], build_graph=None))


class _RecordingService(ChatService):
    """抓 _complete 收到的 memory 实例：装配裁定（文件/进程内）只能在此处验。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seen_memory: list[object] = []

    def _complete(self, key, message, provider, system_prompt, build=None,
                  tools=None, memory=None):
        self.seen_memory.append(memory if memory is not None else self.memory)
        return super()._complete(key, message, provider, system_prompt,
                                 build=build, tools=tools, memory=memory)


def test_send_with_empty_session_id_generates_sess_id(tmp_path) -> None:
    svc = _RecordingService(provider=MockProvider(), sessions=SessionStore(tmp_path / "sessions"))
    svc.agent_runtime = _sentinel_runtime()
    result = svc.send("", "生成登录用例", "case_design")
    assert result["session_id"].startswith("sess_")
    assert result["title"] == "生成登录用例"
    assert isinstance(svc.seen_memory[0], FileMemoryStore)  # 新会话走文件记忆


def test_failed_send_leaves_no_session_on_disk(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    svc = ChatService(provider=MockProvider(), sessions=store)

    def _boom(agent_id, session_id, provider_override=None):
        raise ProviderConfigError("未配置模型")

    svc.agent_runtime = SimpleNamespace(build=_boom)
    with pytest.raises(ProviderConfigError):
        svc.send("", "hi", "case_design")
    assert store.list("case_design") == []  # 400 一次不得留 0 消息幽灵会话


def test_send_with_temporary_key_stays_in_memory(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    svc = _RecordingService(provider=MockProvider(), sessions=store)
    svc.agent_runtime = _sentinel_runtime()
    result = svc.send("kb-console", "hi", "case_design")  # 非 sess_ 形态：临时键
    assert result["session_id"] == "kb-console"
    assert result["title"] == ""                          # 临时键不在索引里
    assert isinstance(svc.seen_memory[0], InMemoryMemoryStore)
    assert store.list("case_design") == []


def test_send_with_unknown_sess_id_raises(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), sessions=SessionStore(tmp_path / "sessions"))
    svc.agent_runtime = _sentinel_runtime()
    with pytest.raises(SessionStoreError) as exc_info:
        svc.send("sess_deadbeef", "hi", "case_design")
    assert exc_info.value.detail == "会话不存在或已被删除"


def test_platform_agent_never_uses_file_memory(tmp_path) -> None:
    svc = _RecordingService(provider=MockProvider(), sessions=SessionStore(tmp_path / "sessions"))
    svc.agent_runtime = _sentinel_runtime()
    svc.send("kb-console", "hi", "kb_assistant")
    assert isinstance(svc.seen_memory[0], InMemoryMemoryStore)


def test_history_is_trimmed_to_last_history_max(tmp_path) -> None:
    seen: list[list] = []

    class _SpyProvider:
        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages):
            return "[spy]"

        def bind_tools(self, tools):
            return self

        def invoke_messages(self, messages):
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    store.create(sid, "case_design", "标题")
    for i in range(45):
        store.append(sid, "user", f"问{i}")
        store.append(sid, "assistant", f"答{i}")
    svc = ChatService(provider=_SpyProvider(), sessions=store)
    svc.agent_runtime = _sentinel_runtime()
    svc.send(sid, "新问题", "case_design")
    contents = [m.content for m in seen[0]]
    # 90 条历史只取最近 40 条进 prompt：[system] + 40 + [本轮 user]
    assert len(contents) == 42
    assert contents[0] == "p"          # AgentInstance.system_prompt
    assert contents[1] == "问25"        # 90 条里的第 51 条，更早的 50 条进不了 prompt
    assert contents[-2] == "答44"
    assert contents[-1] == "新问题"
    assert len(store.messages(sid)) == 92  # 磁盘保留全量（本轮 user+assistant 已追加）
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_chat_service.py -v`
Expected: FAIL — `TypeError: ChatService.__init__() got an unexpected keyword argument 'sessions'`

- [ ] **Step 3: 实现**

`services/chat.py` 顶部常量与导入补：

```python
from aitester.agents import is_platform_agent
from aitester.memory import FileMemoryStore, InMemoryMemoryStore, MemoryStore
from aitester.services.session_store import SessionStore, SessionStoreError, is_session_id

# 只截 prompt，磁盘保留全量：不截则真实模型下长会话每轮 token 线性上涨（spec 裁定 6）
HISTORY_MAX = 40
```

`__init__` 增形参与字段（放在 `repo` 之后）：

```python
        sessions: SessionStore | None = None,
    ) -> None:
        ...
        self.sessions = sessions
```

`_complete` 签名加 `memory: MemoryStore | None = None`，函数体内把 `self.memory` 的两处换成局部 `memory`：

```python
        memory = memory or self.memory
        history = memory.recall(key)[-HISTORY_MAX:]
        ...
        memory.save(key, "user", message)
        memory.save(key, "assistant", reply, steps=steps)
```

并在 `else` 分支里从 `tool_traces` 造 steps（`result` 字段最大 50KB，绝不外传给 UI）：

```python
            steps = [
                {"tool": tt["tool"], "ok": tt["ok"], "round": tt["round"], "detail": tt["detail"]}
                for tt in result["tool_traces"]
            ]
```

（`build is None` 的 echo 路径置 `steps: list[dict] = []`；返回 dict 增 `"steps": steps`。）

`send()` 整段换成：

```python
    def send(self, session_id: str, message: str, agent_id: str) -> dict[str, Any]:
        """真实链路：装配一次性实例（提示词/模型/工具/拓扑）后按图执行。

        会话 id 三态：空 → 服务端生成并延迟落盘；sess_* 已存在 → 续写；其余形态
        （kb-console 等临时键）→ 不建会话、走进程内记忆，行为与既有专项一致。
        """
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        sid = (session_id or "").strip()
        if not sid:
            if self.sessions is None or is_platform_agent(agent_id):
                raise ProviderConfigError("请指定会话 id 或通过 create_app 装配会话存储")
            sid = self.sessions.new_id()
        elif self.sessions is not None and is_session_id(sid) and self.sessions.get(sid) is None:
            raise SessionStoreError("会话不存在或已被删除")

        instance = self.agent_runtime.build(agent_id, sid, provider_override=self.provider)

        use_file = (
            self.sessions is not None
            and is_session_id(sid)
            and not is_platform_agent(agent_id)
        )
        memory = FileMemoryStore(self.sessions) if use_file else None

        result = self._complete(
            f"{instance.agent_id}:{sid}",
            message,
            instance.provider,
            instance.system_prompt,
            build=instance.build_graph,
            tools=instance.tools,
            memory=memory,
        )
        stored = self.sessions.get(sid) if self.sessions is not None else None
        result["session_id"] = sid
        result["title"] = stored.title if stored is not None else ""
        return result
```

`main.py`：`create_app` 增形参 `sessions_dir: Path | None = None`，装配处建 store 并注入：

```python
    sessions = SessionStore(sessions_dir or DATA_DIR / "sessions")
    ...
    application.state.sessions = sessions
    application.state.chat_service = ChatService(
        agent_runtime=application.state.agent_runtime, sessions=sessions
    )
```

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_chat_service.py tests/test_agent_runtime.py -v`
Expected: PASS（既有 9 个用例 + 新增 7 个全绿；既有用例都不传 `sessions`，故仍走进程内记忆）

- [ ] **Step 5: 全量后端回归**

Run: `cd backend && uv run pytest -q`
Expected: 若 `tests/test_api.py`、`tests/test_kb_api.py` 中出现写真实 `backend/data/sessions/` 的用例而失败，本步先记录、留给 Task 6 的注入缝修复（Task 6 Step 5 必须全绿）。真实 `backend/data` 目录不得因本次运行多出 `sessions/`：

```bash
ls backend/data   # 期望仍是 capability_config.json model_config.json projects.json workspaces
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/aitester/services/chat.py backend/src/aitester/main.py backend/tests/test_chat_service.py
git commit -m "feat(chat): 会话 id 三态选型与 HISTORY_MAX 裁剪"
```

---

### Task 5: `run_graph` 过程痕迹（ok / round / detail）

**Files:**
- Modify: `backend/src/aitester/orchestration/agent_graph.py:68-90`
- Test: `backend/tests/test_orchestration.py`（末尾追加）

**Interfaces:**
- Consumes: `ToolMessage.status`（langgraph `ToolNode` 在错误分支显式置 `"error"`）、`AIMessage.tool_calls`
- Produces: `run_graph(...) -> {"reply", "tool_traces", "drafts"}`，其中 `tool_traces` 每项为 `{tool: str, result: str, ok: bool, round: int, detail: str}`（`result` 保留，草案与既有 trace 逻辑仍依赖它）

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_orchestration.py`（沿用该文件既有的假 provider 造法；若该文件没有可复用的假 provider 类，就按下例自带一个）：

```python
from langchain_core.messages import AIMessage, ToolMessage

from aitester.orchestration import run_graph
from aitester.orchestration.agent_graph import build_agent_graph


class _ToolCallingProvider:
    """固定脚本：第 1 轮调两个工具（一个成功一个失败），第 2 轮出最终回复。"""

    name = "spy"
    model_ref = "spy/model"

    def __init__(self) -> None:
        self._turn = 0

    def complete(self, messages):
        return "[spy]"

    def bind_tools(self, tools):
        return self

    def invoke_messages(self, messages):
        self._turn += 1
        if self._turn == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "read", "args": {"path": "a.md"}, "id": "c1", "type": "tool_call"},
                {"name": "grep_search", "args": {"pattern": "x"}, "id": "c2", "type": "tool_call"},
            ])
        return AIMessage(content="完成")


def _graph_with(messages):
    class _Fixed:
        def invoke(self, state):
            return {"messages": messages}

    return lambda provider, tools: _Fixed()


def test_steps_carry_ok_round_and_detail() -> None:
    messages = [
        AIMessage(content="", tool_calls=[
            {"name": "read", "args": {"path": "a.md"}, "id": "c1", "type": "tool_call"},
            {"name": "grep_search", "args": {"pattern": "x" * 200}, "id": "c2", "type": "tool_call"},
        ]),
        ToolMessage(content="内容", tool_call_id="c1", name="read", status="success"),
        ToolMessage(content="工具执行失败", tool_call_id="c2", name="grep_search", status="error"),
        AIMessage(content="完成"),
    ]
    result = run_graph(_graph_with(messages), _ToolCallingProvider(), [], [])
    assert [t["ok"] for t in result["tool_traces"]] == [True, False]
    assert [t["round"] for t in result["tool_traces"]] == [1, 1]
    assert result["tool_traces"][0]["detail"] == '{"path": "a.md"}'
    assert len(result["tool_traces"][1]["detail"]) == 80  # 超长参数截断
    assert [t["tool"] for t in result["tool_traces"]] == ["read", "grep_search"]
    assert result["reply"] == "完成"


def test_round_increments_per_agent_turn() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="c1", name="read"),
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "c2", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="c2", name="read"),
        AIMessage(content="结束"),
    ]
    result = run_graph(_graph_with(messages), _ToolCallingProvider(), [], [])
    assert [t["round"] for t in result["tool_traces"]] == [1, 2]


def test_detail_is_empty_json_when_args_missing() -> None:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="ok", tool_call_id="c1", name="read"),
        AIMessage(content="结束"),
    ]
    result = run_graph(_graph_with(messages), _ToolCallingProvider(), [], [])
    assert result["tool_traces"][0]["detail"] == "{}"


def test_real_graph_still_loops_and_returns_reply() -> None:
    provider = _ToolCallingProvider()
    result = run_graph(build_agent_graph, provider, [], [])
    assert result["tool_traces"] == []  # 无工具 → 单节点直答图，过程块为空
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_orchestration.py -k steps -v`
Expected: FAIL — `KeyError: 'ok'`

- [ ] **Step 3: 实现**

`agent_graph.py::run_graph` 的遍历段换成（`result["messages"]` 顺序即图内产出顺序）：

```python
    reply = ""
    tool_traces: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    calls_by_id: dict[str, dict[str, Any]] = {}
    round_no = 0
    for msg in result["messages"]:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                round_no += 1  # 一轮 agent↔tools = 一条带 tool_calls 的 AIMessage
                for call in msg.tool_calls:
                    calls_by_id[str(call.get("id"))] = call
            elif msg.content:
                reply = str(msg.content)
            continue
        if isinstance(msg, ToolMessage):
            call = calls_by_id.get(str(msg.tool_call_id), {})
            try:
                detail = json.dumps(call.get("args") or {}, ensure_ascii=False)[:80]
            except (TypeError, ValueError):
                detail = str(call.get("args"))[:80]  # 非常规 args（非 JSON 可序列化）不退化成报错，UI 只截一行
            tool_traces.append({
                "tool": msg.name or "",
                "result": str(msg.content),
                "ok": str(getattr(msg, "status", "success")) != "error",
                "round": round_no,
                "detail": detail,
            })
            if msg.name == "prepare_kb_write" and getattr(msg, "artifact", None):
                drafts.append(msg.artifact)
```

并在该文件顶部补 `import json`。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_orchestration.py tests/test_agent_graph.py tests/test_kb_tools.py -v`
Expected: PASS（`drafts`/`reply` 既有语义不变）

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/orchestration/agent_graph.py backend/tests/test_orchestration.py
git commit -m "feat(orchestration): 工具痕迹补 ok/round/detail 供 UI 过程块"
```

---

### Task 6: 会话三端点 + send 契约（含测试注入缝收口）

**Files:**
- Modify: `backend/src/aitester/interaction/schemas.py:16-19,32-36` + 尾追三模型
- Create: `backend/src/aitester/interaction/sessions.py`
- Modify: `backend/src/aitester/interaction/router.py:55-79`
- Modify: `backend/src/aitester/main.py`（挂 `sessions_router`）
- Modify: `backend/tests/test_api.py:29-39`、`backend/tests/test_kb_api.py:28-35,91-94,104-105,118-120`
- Test: `backend/tests/test_api_chat_sessions.py`

**Interfaces:**
- Consumes: `request.app.state.sessions`（Task 4）、`SessionStore`、`SessionStoreError`
- Produces:
  - `StepInfo{tool:str, ok:bool, round:int, detail:str}`
  - `SendRequest.session_id` 默认 `""`；`SendResponse` 增 `session_id: str = ""`、`title: str = ""`、`steps: list[StepInfo] = []`
  - `SessionInfo`/`SessionsResponse{sessions:[SessionInfo]}`/`SessionMessagesResponse{session_id, messages:[ChatMessageInfo]}`
  - 端点：`GET /api/chat/sessions?agent_id=`、`GET /api/chat/sessions/{id}/messages`、`DELETE /api/chat/sessions/{id}`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_api_chat_sessions.py`：

```python
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService
from aitester.services.session_store import SessionStore


# 与 test_api_projects.py:12 同款假 manager：会话端点不碰 reme，免起真实实例
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
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )


def _client(tmp_path: Path) -> TestClient:
    return TestClient(_app(tmp_path))


def _seed(tmp_path: Path, agent_id: str = "case_design") -> tuple[TestClient, str]:
    application = _app(tmp_path)
    store: SessionStore = application.state.sessions
    sid = store.new_id()
    store.create(sid, agent_id, "订单退款用例设计")
    store.append(sid, "user", "订单退款用例设计")
    store.append(sid, "assistant", "好的",
                 steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}])
    return TestClient(application), sid


def _wired_client(tmp_path: Path) -> TestClient:
    """用真实 ChatService（注入 MockProvider，零网络）验 send 的会话契约。"""
    application = _app(tmp_path)
    application.state.chat_service = ChatService(
        provider=MockProvider(),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
    )
    return TestClient(application)


def test_list_sessions_returns_rows_for_agent(tmp_path: Path) -> None:
    client, sid = _seed(tmp_path)
    body = client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json()
    assert len(body["sessions"]) == 1
    row = body["sessions"][0]
    assert row["id"] == sid and row["title"] == "订单退款用例设计"
    assert row["message_count"] == 2
    assert isinstance(row["created_at"], int) and isinstance(row["updated_at"], int)


def test_list_sessions_is_empty_for_unknown_or_platform_agent(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    for agent in ("ghost", "kb_assistant"):
        r = client.get("/api/chat/sessions", params={"agent_id": agent})
        assert r.status_code == 200
        assert r.json() == {"sessions": []}  # 不泄露、不报错（spec 契约）


def test_list_sessions_requires_agent_id(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    assert client.get("/api/chat/sessions").status_code == 422


def test_messages_endpoint_returns_steps(tmp_path: Path) -> None:
    client, sid = _seed(tmp_path)
    body = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert body["session_id"] == sid
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["steps"] is None
    assert body["messages"][1]["steps"] == [
        {"tool": "read", "ok": True, "round": 1, "detail": "{}"}]


def test_messages_unknown_or_temporary_id_returns_404(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    for bad in ("sess_deadbeef", "kb-console"):
        r = client.get(f"/api/chat/sessions/{bad}/messages")
        assert r.status_code == 404
        assert r.json()["detail"] == "会话不存在或已被删除"


def test_delete_returns_204_and_removes_everything(tmp_path: Path) -> None:
    client, sid = _seed(tmp_path)
    assert client.delete(f"/api/chat/sessions/{sid}").status_code == 204
    assert client.delete(f"/api/chat/sessions/{sid}").json()["detail"] == "会话不存在或已被删除"
    assert client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json() == {"sessions": []}
    assert not (tmp_path / "sessions" / f"{sid}.jsonl").exists()


def test_send_defaults_to_empty_session_id(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    r = client.post("/api/chat/send", json={"message": "生成用例", "agent_id": "case_design"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"].startswith("sess_")   # 请求体不带 session_id 也能建会话
    assert body["title"] == "生成用例"
    assert body["steps"] == []                       # MockProvider 不调工具
    listed = client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json()["sessions"]
    assert [s["id"] for s in listed] == [body["session_id"]]


def test_send_with_temporary_key_does_not_appear_in_list(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    r = client.post("/api/chat/send",
                    json={"session_id": "kb-console", "message": "hi", "agent_id": "case_design"})
    assert r.status_code == 200
    assert r.json()["session_id"] == "kb-console"   # 临时键原样回显
    assert client.get("/api/chat/sessions", params={"agent_id": "case_design"}).json() == {"sessions": []}


def test_send_unknown_session_id_returns_404(tmp_path: Path) -> None:
    client = _wired_client(tmp_path)
    r = client.post("/api/chat/send",
                    json={"session_id": "sess_deadbeef", "message": "hi", "agent_id": "case_design"})
    assert r.status_code == 404
    assert r.json()["detail"] == "会话不存在或已被删除"
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_api_chat_sessions.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'sessions_dir'`

- [ ] **Step 3: 实现 schemas**

`interaction/schemas.py`：`SendRequest.session_id` 默认改 `""`（`:17`）；`SendResponse`（`:32-36`）后追加：

```python
class StepInfo(BaseModel):
    tool: str
    ok: bool
    round: int
    detail: str


class SessionInfo(BaseModel):
    id: str
    agent_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int


class SessionsResponse(BaseModel):
    sessions: list[SessionInfo]


class ChatMessageInfo(BaseModel):
    role: str
    content: str
    ts: int
    steps: list[StepInfo] | None = None


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[ChatMessageInfo]
```

并把 `SendResponse` 补三行（默认值不可省：既有测试用 `SimpleNamespace` 替身返回缺键 dict，见 Step 6）：

```python
    session_id: str = ""
    title: str = ""
    steps: list[StepInfo] = []
```

`EchoRequest.session_id` 保持 `"default"` 不动（`/chat/echo` 是 7 层回归探针，与会话落盘无关）。

- [ ] **Step 4: 实现 sessions 路由**

`interaction/sessions.py`：

```python
"""会话管理三端点：只服务已落盘的 sess_* 会话。

临时键（kb-console 等）在此一律 404——它们的真相不在 sessions 目录（spec 兼容裁定）。
detail 中文且可照做；不泄露 sessions 目录以外的任何绝对路径。
"""

from fastapi import APIRouter, HTTPException, Query, Request

from aitester.interaction.schemas import (
    ChatMessageInfo,
    SessionInfo,
    SessionMessagesResponse,
    SessionsResponse,
    StepInfo,
)
from aitester.services.session_store import SessionStore


def _store(request: Request) -> SessionStore:
    return request.app.state.sessions  # type: ignore[return-value]


def _missing(session_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail="会话不存在或已被删除")


router = APIRouter(prefix="/api/chat/sessions")


@router.get("", response_model=SessionsResponse)
def sessions_list(
    request: Request, agent_id: str = Query(min_length=1)
) -> SessionsResponse:
    # 未知或平台智能体 → 空列表 200：列表是「此处没有会话」，不是错误
    return SessionsResponse(
        sessions=[SessionInfo(**vars(s)) for s in _store(request).list(agent_id)]
    )


@router.get("/{session_id}/messages", response_model=SessionMessagesResponse)
def sessions_messages(request: Request, session_id: str) -> SessionMessagesResponse:
    store = _store(request)
    if store.get(session_id) is None:
        raise _missing(session_id)
    messages = [
        ChatMessageInfo(
            role=m.role, content=m.content, ts=m.ts,
            steps=[StepInfo(**s) for s in m.steps] if m.steps else None,
        )
        for m in store.messages(session_id)
    ]
    return SessionMessagesResponse(session_id=session_id, messages=messages)


@router.delete("/{session_id}", status_code=204)
def sessions_delete(request: Request, session_id: str) -> None:
    if not _store(request).delete(session_id):
        raise _missing(session_id)
```

- [ ] **Step 5: 接线 router / main**

`interaction/router.py` 的 `chat_send` 返回值补三键（用 `.get` 容错，与既有 `result.get("drafts", [])` 同口径）：

```python
    steps: list[StepInfo] = []
    for s in result.get("steps", []):
        try:
            steps.append(StepInfo(**s))
        except ValidationError:
            continue  # 与草案同口径：畸形过程痕迹逐条丢弃，不整响应 500
    return SendResponse(
        reply=result["reply"],
        trace=["interaction"] + result["trace"],
        model=result["model"],
        drafts=drafts,
        session_id=str(result.get("session_id", "")),
        title=str(result.get("title", "")),
        steps=steps,
    )
```

并在 `router.py` 的 schemas 导入里补 `StepInfo`；把 `send` 的 `except` 链补一条（放在 `ConfigNotFoundError` 之后）：

```python
    except SessionStoreError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
```

（导入 `from aitester.services.session_store import SessionStoreError`。）

`main.py` 导入并挂载：

```python
from aitester.interaction.sessions import router as sessions_router
...
    application.include_router(sessions_router)
```

（顺序放在 `projects_router` 之后；`sessions_router` 前缀 `/api/chat/sessions` 与 `router` 的 `/api/chat/send` 无冲突。）

- [ ] **Step 6: 修既有测试的注入缝**

`tests/test_api.py::_isolated_client` 的 `create_app(...)` 调用补 `sessions_dir=tmp_path / "sessions", projects_path=tmp_path / "p.json"`；`tests/test_kb_api.py::_client` 同样补 `sessions_dir=tmp_path / "sessions"`。该文件三处 `SimpleNamespace(send=lambda ...)` 的返回 dict 各补 `"session_id": "", "title": "", "steps": []`——**不补**：`SendResponse` 默认值已能兜住，此处显式补齐只为让替身形状与新契约一致（保留一条不补的用例以验默认兜底：`test_chat_send_drafts_defaults_empty` 保持只回旧形态键）。

- [ ] **Step 7: 全量后端回归**

Run: `cd backend && uv run pytest -q`
Expected: 全绿，且 `git status --porcelain backend/data` 为空（真实数据目录零写入）。

- [ ] **Step 8: Commit**

```bash
git add backend/src/aitester/interaction backend/src/aitester/main.py backend/tests
git commit -m "feat(api): /api/chat/sessions 三端点与 send 会话契约"
```

---

### Task 7: 前端 API 层

**Files:**
- Modify: `frontend/src/api/client.ts:268-279` 及其后追加

**Interfaces:**
- Consumes: Task 6 的响应形状
- Produces: `ChatStep`、`ChatSession`、`ChatMessage`、`SendResponse`（增 `session_id/title/steps`）、`getSessions(agentId)`、`getSessionMessages(sessionId)`、`deleteChatSession(sessionId)`；`chatSend(sessionId, message, agentId)` 签名不变（`sessionId` 传 `""` 即新建）

- [ ] **Step 1: 改类型**

把 `client.ts:268-273` 的 `SendResponse` 换成：

```ts
export interface ChatStep {
  tool: string;
  ok: boolean;
  round: number;
  detail: string;
}

export interface SendResponse {
  reply: string;
  trace: string[];
  model: string;
  drafts: KbDraft[];
  session_id: string;
  title: string;
  steps: ChatStep[];
}
```

（`chatSend` 本体 `:275-279` 不动。后端 `StepInfo` 是逐条容错后下发的合法对象，故 `steps` 非可选。）

- [ ] **Step 2: 追加密约函数**

在 `chatSend` 之后追加：

```ts
export interface ChatSession {
  id: string;
  agent_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  ts: number;
  steps: ChatStep[] | null;
}

const sessionsApi = (sub = "") => `/api/chat/sessions${sub}`;

export function getSessions(agentId: string): Promise<{ sessions: ChatSession[] }> {
  return apiFetch<{ sessions: ChatSession[] }>(
    `${sessionsApi()}?${new URLSearchParams({ agent_id: agentId }).toString()}`);
}

export function getSessionMessages(sessionId: string): Promise<{ session_id: string; messages: ChatMessage[] }> {
  return apiFetch<{ session_id: string; messages: ChatMessage[] }>(sessionsApi(`/${sessionId}/messages`));
}

/** 204 由 apiFetch 短路成 null（与 deleteProject 同款），失败时抛 ApiError。 */
export function deleteChatSession(sessionId: string): Promise<null> {
  return apiFetch<null>(sessionsApi(`/${sessionId}`), { method: "DELETE" });
}
```

- [ ] **Step 3: 类型门禁**

Run: `cd frontend && npm run build`
Expected: PASS（0 error；`KbPage.tsx:325` 的 `chatSend("kb-console", ...)` 调用点不报错——后端对临时键回 `session_id` 原值）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(frontend): 会话 API 与 send 契约类型"
```

---

### Task 8: `pages/chat/utils.ts`（纯函数）

**Files:**
- Create: `frontend/src/pages/chat/utils.ts`
- Test: 无前端测试框架；用 `npm run build` 做类型门禁，行为由 Step 2 的临时断言脚本自查后删除

**Interfaces:**
- Consumes: `ChatSession`（Task 7）
- Produces:
  - `estTokens(s: string): number`
  - `contextUsage(args: { systemPrompt: string; history: { content: string }[]; input: string; cap: number }): { used: number; cap: number; pct: number }`
  - `groupSessions(sessions: ChatSession[], now?: number): { label: string; items: ChatSession[] }[]`
  - `fmtTime(ts: number, now?: number): string`
  - `HISTORY_MAX` 不在前端（后端裁定），此处只算估算

- [ ] **Step 1: 实现**

`frontend/src/pages/chat/utils.ts`：

```ts
import type { ChatSession } from "../../api/client";

const DAY_MS = 86_400_000;

/** 原型 :1393-1398 —— CJK ≈ 1 token/字，其余 4 字符 ≈ 1 token。 */
export function estTokens(s: string): number {
  const text = String(s || "");
  const cjk = (text.match(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]/g) || []).length;
  return Math.round(cjk + (text.length - cjk) / 4);
}

/** 原型 :1399-1409 —— 系统提示词 + 历史 + 当前输入的占用估算。 */
export function contextUsage(args: {
  systemPrompt: string;
  history: { content: string }[];
  input: string;
  cap: number;
}): { used: number; cap: number; pct: number } {
  const used = args.history.reduce(
    (acc, m) => acc + estTokens(m.content), estTokens(args.systemPrompt)) + estTokens(args.input || "");
  const cap = args.cap > 0 ? args.cap : 0;
  return { used, cap, pct: cap ? Math.min(100, Math.round((used / cap) * 100)) : 0 };
}

/** 本地日历日差：同一天为 0 天，跨天按日历天算（不用 UTC 除法，避免 UTC+8 下午误归「更早」）。 */
function daysSince(ts: number, now: number): number {
  const startOfDay = (t: number) => new Date(t).setHours(0, 0, 0, 0);
  return Math.round((startOfDay(now) - startOfDay(ts)) / DAY_MS);
}

/** 原型 :1257 的三组（存死字符串）换成按 updated_at 现算；空组不出标题。 */
export function groupSessions(
  sessions: ChatSession[],
  now = Date.now(),
): { label: string; items: ChatSession[] }[] {
  const today: ChatSession[] = [];
  const week: ChatSession[] = [];
  const older: ChatSession[] = [];
  for (const s of sessions) {
    const d = daysSince(s.updated_at, now);
    if (d <= 0) today.push(s);
    else if (d < 7) week.push(s);
    else older.push(s);
  }
  return [{ label: "今天", items: today }, { label: "7 天内", items: week }, { label: "更早", items: older }]
    .filter((g) => g.items.length > 0);
}

/** 会话行与 meta 共用：今天只显 HH:MM，更早补 M月D日（原型 now() 恒带日期且补零，此处按日历日收敛成偏离）。 */
export function fmtTime(ts: number, now = Date.now()): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return daysSince(ts, now) <= 0 ? hm : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}
```

- [ ] **Step 2: 门禁**

Run: `cd frontend && npm run build`
Expected: PASS（`build` 脚本是 `tsc && vite build`，类型错即红）

**本任务没有单元测试，这是明写的取舍**：项目前端无测试框架（spec「测试策略」段已裁定门禁是 build + 真机走查）。但这三个纯函数里有**日历日边界**这种类型检查抓不到的逻辑（UTC+8 下午的会话必须归「今天」），所以用一个**跑完即删**的临时断言脚本自查一次——`frontend/package.json` 是 `"type": "module"`，Node 24 的类型剥离能直接 import 这个 `.ts`（`utils.ts` 只用 `import type`，无运行时依赖）。

新建 `frontend/tmp_task8_check.mts`（**不进 git**，Step 4 前删掉）：

```ts
import assert from "node:assert/strict";
import { contextUsage, estTokens, fmtTime, groupSessions } from "./src/pages/chat/utils.ts";
import type { ChatSession } from "./src/api/client.ts";

const mk = (id: string, updated_at: number): ChatSession =>
  ({ id, agent_id: "case_design", title: id, created_at: updated_at, updated_at, message_count: 1 });

// 固定到 UTC+8 的 2026-10-03 15:00 本地时刻，避免脚本随运行时刻漂移
const now = new Date(2026, 9, 3, 15, 0, 0).getTime();
const DAY = 86_400_000;

assert.equal(estTokens("生成登录用例"), 6);            // 全 CJK：1 token/字
assert.equal(estTokens("abcdefgh"), 2);                // 非 CJK：4 字符/词元

// 分组边界：今天下午、整 6 天前（仍在 7 天内）、整 8 天前（更早）
const groups = groupSessions([mk("今天", now - 5 * 3600_000), mk("六天前", now - 6 * DAY), mk("八天前", now - 8 * DAY)], now);
assert.deepEqual(groups.map((g) => g.label), ["今天", "7 天内", "更早"]);
assert.deepEqual(groupSessions([], now), []);          // 空组不出标题

// fmtTime：今天只显 HH:MM，跨天补 M月D日
assert.equal(fmtTime(now - 60_000, now), "14:59");
assert.equal(fmtTime(now - DAY, now), "10月2日 15:00");
// 与 groupSessions 的 `d <= 0` 同口径：客户端时钟落后于服务端、ts 落到「明天」也只显 HH:MM，不得显示 10月4日
assert.equal(fmtTime(new Date(2026, 9, 4, 0, 0, 30).getTime(), now), "00:00");
assert.deepEqual(groupSessions([mk("未来", new Date(2026, 9, 4, 0, 0, 30).getTime())], now).map((g) => g.label), ["今天"]);

// 上下文占用：cap 为 0 不除零；pct 封顶 100（注意 "p" 按 1/4 词元四舍五入为 0）
assert.deepEqual(contextUsage({ systemPrompt: "p", history: [{ content: "一" }], input: "", cap: 0 }), { used: 1, cap: 0, pct: 0 });
assert.equal(contextUsage({ systemPrompt: "p", history: [{ content: "生成登录用例" }], input: "再来一条", cap: 10 }).pct, 100);

console.log("task8 assertions OK");
```

Run: `cd frontend && node --experimental-strip-types tmp_task8_check.mts`
Expected: 打印 `task8 assertions OK`（若抛 AssertionError，就地修 `utils.ts` 再跑；分组不对只改 `daysSince`，不许在选择器里凑）。

- [ ] **Step 4: 删临时脚本并提交**

```bash
rm frontend/tmp_task8_check.mts
git add frontend/src/pages/chat/utils.ts
git commit -m "feat(chat-ui): 会话分组、时间格式化与上下文估算纯函数"
```

---

### Task 9: `SessionPane`（侧栏）

**Files:**
- Create: `frontend/src/pages/chat/SessionPane.tsx`
- Test: `cd frontend && npm run build` 门禁 + Task 11 真机走查

**Interfaces:**
- Consumes: `ChatSession`、`AgentInfo`（`client.ts:60-69`）、`groupSessions`（Task 8）
- Produces: `<SessionPane agents agentId sessions activeId query busy onAgentChange onQueryChange onNew onSelect onDelete onCollapse />`

- [ ] **Step 1: 实现**

`frontend/src/pages/chat/SessionPane.tsx`：

```tsx
import type { AgentInfo, ChatSession } from "../../api/client";
import { fmtTime, groupSessions } from "./utils";

interface Props {
  agents: AgentInfo[];
  agentId: string;
  sessions: ChatSession[];
  activeId: string | null;
  query: string;
  busy: boolean;
  collapsed: boolean;
  onAgentChange: (id: string) => void;
  onQueryChange: (q: string) => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCollapse: () => void;
}

/** 原型 :568-591 侧栏；「当前项目」card 整块不渲染（第 2 片才接项目维度）。 */
export default function SessionPane(p: Props) {
  const agent = p.agents.find((a) => a.id === p.agentId);
  const keyword = p.query.trim().toLowerCase();
  const shown = keyword ? p.sessions.filter((s) => s.title.toLowerCase().includes(keyword)) : p.sessions;
  const groups = groupSessions(shown);

  return (
    <aside className={`sidebar${p.collapsed ? " collapsed" : ""}`}>
      <div className="ctx-card">
        <div className="ctx-label">当前智能体 ({p.agents.length})</div>
        {/* busy 期间禁止切智能体：切智能体即切会话域，半途切换会让 activeId 与列表错位 */}
        {/* 用原生 select（可键盘可达、无死控件），内联样式压掉 .ctx-value 的 flex/pointer 卡片态：
            .ctx-value 原为「div + pop 弹层」设计，直接套在 select 上会露出透明背景与光标错位 */}
        <select
          className="ctx-value"
          style={{ width: "100%", border: "none", background: "transparent", font: "inherit", fontWeight: 700 }}
          value={p.agentId}
          disabled={p.busy}
          onChange={(e) => p.onAgentChange(e.target.value)}
          title="切换智能体会刷新会话列表"
        >
          {p.agents.map((a) => (
            <option key={a.id} value={a.id}>{a.icon} {a.name}</option>
          ))}
        </select>
      </div>
      <div className="side-head">
        <span>💬 会话历史</span>
        <button className="icon-btn" title="隐藏会话列表" onClick={p.onCollapse}>«</button>
      </div>
      <button className="btn-new" disabled={p.busy} onClick={p.onNew}>＋ 新建会话</button>
      <div className="search">
        <span className="mag">🔍</span>
        <input value={p.query} placeholder="搜索会话…" onChange={(e) => p.onQueryChange(e.target.value)} />
      </div>
      <div className="session-list">
        {!groups.length && (
          <div className="empty-tip">
            「{agent ? agent.name.replace("智能体", "") : p.agentId}」下暂无会话<br />点击「＋ 新建会话」开始
          </div>
        )}
        {groups.map((g) => (
          <div key={g.label}>
            <div className="group-title">
              <span>⌄ {g.label}</span><span className="count">{g.items.length}</span>
            </div>
            {g.items.map((s) => (
              <div
                key={s.id}
                className={`session${s.id === p.activeId ? " active" : ""}`}
                onClick={() => p.onSelect(s.id)}
              >
                <div className="t">
                  <span className="dot" /><span className="tt">{s.title}</span>
                  <button
                    className="s-del"
                    title="删除会话"
                    disabled={p.busy}
                    onClick={(e) => { e.stopPropagation(); p.onDelete(s.id); }}
                  >✕</button>
                </div>
                <div className="m"><span>{fmtTime(s.updated_at)}</span></div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
```

会话行 `.m` 保留原型的时间列（原型 `:1279` 同一行还有个假的 `▶ Web` 来源标签，本期不照搬——原型 mock 数据不迁入真机，与 spec 偏离表口径一致）。

- [ ] **Step 2: 类型门禁**

Run: `cd frontend && npm run build`
Expected: PASS（`tsc` 无错；本页尚无引用方，`ChatPage` 到 Task 11 才装配，此处只验本组件自洽）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/chat/SessionPane.tsx
git commit -m "feat(chat-ui): 会话侧栏（智能体门控、搜索、分组列表）"
```

---

### Task 10: `MessageList` + `Composer` + 两段 CSS

**Files:**
- Create: `frontend/src/pages/chat/MessageList.tsx`
- Create: `frontend/src/pages/chat/Composer.tsx`
- Modify: `frontend/src/App.css`（两处插入：1a 在 `.msg.agent .body b{color:#111}` 之后、1b 在 `details.thinking[open] summary{margin-bottom:6px}` 之后；一律按内容定位，不认行号）
- Test: `cd frontend && npm run build` 门禁 + Task 11 真机走查

**Interfaces:**
- Consumes: `ChatMessage`/`ChatStep`（Task 7）、`mdRender`（`frontend/src/pages/kb/utils.ts`）、`contextUsage`/`fmtTime`（Task 8）
- Produces: `<MessageList messages agentName busy onCopy onChip />`、`<Composer input busy modelLabel cap systemPrompt messages onInput onSubmit onToast />`

- [ ] **Step 1: 补 CSS（两处，缺一处就不对）**

**1a. 消息正文显示规则**（缺这条回复整段不可见）。`App.css` 在 `.msg.agent .body b{color:#111}`（`:133`）之后插入：

```css
  /* .md-preview 默认为 display:none（工作区编辑器专用排版），气泡内复用必须显打开、抹掉其内边距并还回聊天字号；
     另需归零 .md-preview ul/ol 的 padding-left:20px —— 那是工作区缩进，而聊天列表靠 li::before 圆点定行首（全局 *{padding:0} 本无缩进） */
  .msg.agent .body.md-preview{display:block;padding:0;font-size:14px}
  .msg.agent .body.md-preview ul,.msg.agent .body.md-preview ol{padding-left:0}
```

四项都必要，不是随手加：`display:block` 对抗 `.md-preview{display:none}`、`padding:0` 对抗其工作区专属 `padding:16px 22px`、`font-size:14px` 对抗其 `font-size:13px`（聊天正文按原型是 `body{font-size:14px}`，`prototype/index.html:27`；`.msg.agent .body` 自身不带 font-size），第四条对抗 `.md-preview ul,.md-preview ol{padding-left:20px}`——聊天的列表是 `list-style:none` + `li::before` 圆点自己定行首（全局 `*{padding:0}` 下本无缩进），留着那 20px 会比原型多一截缩进。**这条规则不是自创形态**：仓内已有同一件事的先例——`.kb-msg .md-preview{display:block;padding:0;font-size:12.5px;line-height:1.7}`（KB 助手气泡复用工作区排版时把字号还给它自己的 12.5px），这里按同样套路还给聊天的 14px。已核实的沿用取舍（走查时按此口径看，不算新偏离）：`flex:1;overflow-y:auto` 在无高度约束的气泡里不生效；`color:#33302a`（比 `--text` 略淡）与 `.md-preview h1..h4/code/pre/table` 的排版**会**在气泡内生效，与 `.kb-msg` 先例同一取舍。

**1b. 过程块逐行排版**（原型没有这个状态，必须新写规则并在提交说明里讲清）。`App.css` 在 `details.thinking[open] summary{margin-bottom:6px}`（`:143`）之后插入：

```css
  /* 原型的 thinking 只有整段文本（渲染在 prototype:1300，样式在 :143-148），没有「逐条工具调用」这一状态；
     本期按裁定显示真实工具序列，故在既有 details.thinking 框内补逐行排版，次要文本色沿用框自身的 var(--text-2) */
  .t-step{display:flex;align-items:baseline;gap:6px;padding:2px 0}
  .t-step .n{flex:none;font-variant-numeric:tabular-nums}
  .t-step .args{flex:1;min-width:0;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
```

取证结论（写计划时逐条 grep 过）：`.t-step` / `.n` / `.args` 在 `prototype/index.html` 与 `frontend/src/App.css` **都不存在**——不补这三条，Step 2 的过程块会渲染成一串挤在一起的裸文本。容器不新增：`details.thinking` 的虚线框、底色、12.5px 与 `color:var(--text-2)` 全部沿用原型，序号与参数串直接继承该淡色，**不引入新的色值**。

- [ ] **Step 2: MessageList**

`frontend/src/pages/chat/MessageList.tsx`：

```tsx
import { useEffect, useRef } from "react";
import type { ChatMessage, ChatStep } from "../../api/client";
import { mdRender } from "../kb/utils";

interface Props {
  messages: ChatMessage[];
  agentName: string;
  busy: boolean;
  onCopy: (text: string) => void;
  onChip: (text: string) => void;   // 原型 :1322-1324：chip 只填输入框，绝不自动发送
}

function Steps({ steps }: { steps: ChatStep[] }) {
  if (!steps.length) return null;  // 无工具调用时整个过程块不渲染（spec 裁定 4）
  return (
    <details className="thinking">
      <summary>🔧 执行过程</summary>
      {steps.map((s, i) => (
        <div className="t-step" key={`${s.round}-${s.tool}-${i}`}>
          <span className="n">{i + 1}.</span>
          <span>{s.tool} · {s.ok ? "成功" : "失败"}</span>
          <span className="args">{s.detail}</span>
        </div>
      ))}
    </details>
  );
}

export default function MessageList(p: Props) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [p.messages, p.busy]);

  if (!p.messages.length && !p.busy) {
    return (
      <div className="messages" ref={box}>
        <div className="welcome">
          <div className="w-logo">Ai</div>
          <h2>你好，我是 {p.agentName.replace("智能体", "")}</h2>
          <p>发送消息即在此智能体开始新会话 · 会话保存在本机</p>          <div className="chips">
            {WELCOME_CHIPS.map((c) => (
              <button className="chip" key={c.label} onClick={() => p.onChip(c.prompt)}>{c.label}</button>
            ))}
          </div>
        </div>
      </div>
    );
  }
  ...
}
```

并在文件顶部常量区补 chip 文案（原型 `:1316-1319` 是「短标签 + data-q 长指令」两截文本，这里同形保留：`label` 显示在 chip 上、`prompt` 填进输入框。四条按已批准的裁定 5 对齐 `case_design` 职责——📋/🐞 两条**逐字照搬原型**，🧩/🔌 两条为替换项，标签风格照搬「图标 + 名词短语」）：

```ts
const WELCOME_CHIPS = [
  { label: "📋 根据需求生成测试用例", prompt: "根据这份需求文档生成测试用例，按团队模板输出" },
  { label: "🧩 等价类与边界值补覆盖", prompt: "用等价类划分和边界值分析补齐这个功能的用例覆盖" },
  { label: "🔌 接口用例设计", prompt: "为这个接口设计测试用例，列出入参组合与断言点" },
  { label: "🐞 回归失败归因分析", prompt: "分析这次 CI 回归失败，判断是脚本问题还是真实缺陷" },
];
```

`...` 之后的消息流主体（放在上面 if 之后替换 `...`）：

```tsx
  return (
    <div className="messages" ref={box}>
      {p.messages.map((m, i) =>
        m.role === "user" ? (
          <div className="msg user" key={i}>
            <div>
              <div className="bubble">{m.content}</div>
              <div className="meta" style={{ justifyContent: "flex-end" }}>
                {fmtTime(m.ts)}<span className="copy" onClick={() => p.onCopy(m.content)}>⧉</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="msg agent" key={i}>
            <div className="who"><span className="avatar">Ai</span>AiTester</div>
            <Steps steps={m.steps ?? []} />
            <div className="body md-preview" dangerouslySetInnerHTML={{ __html: mdRender(m.content) }} />
            <div className="meta">
              🗀 {fmtTime(m.ts)}<span className="copy" onClick={() => p.onCopy(m.content)}>⧉</span>
            </div>
          </div>
        ))}
      {p.busy && (
        <div className="msg agent">
          <div className="who"><span className="avatar">Ai</span>AiTester</div>
          <span className="typing"><i /><i /><i /></span>
        </div>
      )}
    </div>
  );
```

（`fmtTime` 与 `mdRender` 的导入分别是 `./utils` 与 `../kb/utils`——后者是 KB 页已有的轻量 Markdown 渲染器，复用而非再造。）

- [ ] **Step 3: Composer**

`frontend/src/pages/chat/Composer.tsx`：

```tsx
import { useEffect } from "react";
import { contextUsage } from "./utils";

interface Props {
  input: string;
  busy: boolean;
  modelLabel: string;      // 只用于上下文 tooltip（原型 :1341 口径）；无可用模型传「未配置模型」
  cap: number;             // 上下文上限（ModelInfo.context），0 表示不可估算
  systemPrompt: string;
  messages: { content: string }[];
  inputRef: { current: HTMLTextAreaElement | null };  // 供 chip 点击后聚焦 + 输入框自增高（ChatPage 持有）
  onInput: (v: string) => void;
  onSubmit: () => void;
  onToast: (msg: string) => void;
}

/** 原型 :611-624 逐字对齐：bar 内只有 上下文 meter + 蓝 perm chip + spacer + 发送。
 *  原型橙 chip（:617）是「📁 项目 · Agent 工作目录」，属项目维度 → 第 2 片才接，本期不渲染；
 *  模型 chip 在 chat-header（:598），不在 composer 内，勿在此重复。 */
export default function Composer(p: Props) {
  const usage = contextUsage({ systemPrompt: p.systemPrompt, history: p.messages, input: p.input, cap: p.cap });
  const cls = `ctx-meter${usage.pct >= 90 ? " hot" : usage.pct >= 70 ? " warn" : ""}`;
  const tip = usage.cap
    ? `上下文占用约 ${fmtK(usage.used)} / ${fmtK(usage.cap)} tokens（按「${p.modelLabel}」的最大上下文估算，含系统提示词 + 历史消息 + 当前输入）`
      + (usage.pct >= 90 ? "：已接近上限，建议新建会话" : "")
    : "未配置可用模型，无法估算上下文占用";
  const canSend = p.input.trim().length > 0 && !p.busy;
  // 高度写在 style 上，发送后 input 清空需显回落，否则框体停在 160px（原型 :1437 同款收口）
  useEffect(() => {
    const el = p.inputRef.current;
    if (el && !p.input) el.style.height = "auto";
  }, [p.input, p.inputRef]);

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          rows={2}
          ref={p.inputRef}
          value={p.input}
          disabled={p.busy}
          placeholder="例如：根据这份需求生成测试用例"
          title="Enter 发送 · Shift+Enter 换行"
          onChange={(e) => {
            p.onInput(e.target.value);
            // 原型 :1423 的自适应高度：随输入长高，封顶 160px
            const el = e.currentTarget;
            el.style.height = "auto";
            el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (canSend) p.onSubmit(); } }}
        />
        <div className="bar">
          <span className={cls} title={tip}>
            <i className="cm-bar"><b style={{ width: `${usage.pct}%` }} /></i>
            <span className="cm-pct">{usage.cap ? `${usage.pct}%` : "—"}</span>
          </span>
          {/* 原型 :618 是可展开弹层（自由/严格，严格置灰）。本期只有「自由权限」一档生效，
              做成可点的按钮并给出原型同一条 toast 文案，避免 .c-chip 的 cursor:pointer 变成死控件 */}
          <button
            className="c-chip blue perm"
            disabled={p.busy}
            title="权限模式 · 严格权限暂未开放"
            onClick={() => p.onToast("「严格权限」暂未开放，敬请期待")}
          >🛡 自由权限 ▾</button>
          <div className="spacer" />
          <button
            className={`btn-send${canSend ? " on" : ""}`}
            title={p.busy ? "正在执行…" : "发送"}
            disabled={!canSend}
            onClick={p.onSubmit}
          >↑</button>
        </div>
      </div>
      <div className="foot-tip">为测试人员而生 · 用例生成 / 脚本编写 / 失败分析</div>
    </div>
  );
}
```

（`fmtTime` 在 Composer 未用，别写进导入列表——只 MessageList 用。）

- [ ] **Step 4: 类型门禁**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/chat/MessageList.tsx frontend/src/pages/chat/Composer.tsx frontend/src/App.css
git commit -m "feat(chat-ui): 消息流（真实 mdRender 排版）与输入区（对齐原型 composer）"
```

---

### Task 11: ChatPage 装配与状态机 + 真机走查

**Files:**
- Rewrite: `frontend/src/pages/ChatPage.tsx`
- Modify: `frontend/src/App.tsx`（评审后：`/chat` 路由多传 `onRetryHealth`，见 Step 1b 第 1 条）
- Modify: `frontend/src/pages/chat/Composer.tsx`（评审后：自增高收进组件自己的 effect，见 Step 1b 第 5 条）
- Delete: `frontend/src/components/PlaceholderPage.tsx`（ChatPage 改写后零引用）
- Test: `npm run build` + 起本地 dev 由用户走查

**Interfaces:**
- Consumes: Task 7 API 函数、Task 9/10 组件、`AgentInfo.effective_uid`（`client.ts:66-67`）与 `ModelsResponse`
- Produces: 完整 `/chat` 页；`App.tsx` 的 `<ChatPage health healthError onOpenSettings onRetryHealth />`（4 props，第 4 个是评审后补的，原写「三 props 保持不变」已被 Step 1b 推翻）

- [ ] **Step 1: 实现装配**

`frontend/src/pages/ChatPage.tsx`：

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  chatSend,
  deleteChatSession,
  getCapabilities,
  getModels,
  getSessionMessages,
  getSessions,
  type AgentInfo,
  type ChatMessage,
  type ChatSession,
  type HealthResponse,
} from "../api/client";
import Composer from "./chat/Composer";
import MessageList from "./chat/MessageList";
import SessionPane from "./chat/SessionPane";

interface Props {
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
}

export default function ChatPage({ health, healthError, onOpenSettings }: Props) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentId, setAgentId] = useState("");
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);            // 与 KbPage 同款同步重入锁（不依赖重渲染时序）
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [modelLabel, setModelLabel] = useState("未配置模型");
  const [cap, setCap] = useState(0);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [error, setError] = useState("");
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();

  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2200);
  }, []);

  // 进页面：能力（智能体清单 + 生效模型）与模型上限一次拉齐，任一失败都要给重试出路
  const reloadMeta = useCallback(async () => {
    try {
      const [caps, models] = await Promise.all([getCapabilities(), getModels()]);
      setAgents(caps.agents);
      if (!agentId && caps.agents.length) {
        setAgentId(caps.agents[0].id);
      }
      const agent = caps.agents.find((a) => a.id === agentId) || caps.agents[0];
      setSystemPrompt(agent?.prompt || "");
      const uid = agent?.effective_uid || models.default_uid;
      // uid 形态是 "provider/model"（模型专项既有约定），所以要跨 provider 找
      const hit = models.providers
        .flatMap((pv) => pv.models.map((m) => ({ label: m.name || m.id, ctx: m.context, key: `${pv.id}/${m.id}` })))
        .find((x) => x.key === uid);
      setModelLabel(hit ? hit.label : "未配置模型");
      setCap(hit ? hit.ctx : 0);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [agentId]);

  const reloadSessions = useCallback(async () => {
    if (!agentId) return;
    try {
      const j = await getSessions(agentId);
      setSessions(j.sessions);
      return j.sessions;
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
      return [];
    }
  }, [agentId, toast]);

  useEffect(() => { void reloadMeta(); }, [reloadMeta]);
  useEffect(() => { void reloadSessions(); }, [reloadSessions]);
  // 卸载清定时器（KbPage/ProjectsPage 同款收尾约定）
  useEffect(() => () => { window.clearTimeout(toastTimer.current); }, []);

  const openSession = useCallback(async (id: string | null) => {
    setActiveId(id);
    if (!id) { setMessages([]); return; }
    try {
      const j = await getSessionMessages(id);
      setMessages(j.messages);
    } catch (err) {
      setMessages([]);
      toast(err instanceof ApiError ? err.message : String(err));
    }
  }, [toast]);

  const guard = useCallback((): boolean => {
    // 无 health 即 /api/health 还没成功过（App 启动时拉），此时发送必失败，先给可见提示
    if (!health) { toast("后端未就绪，请稍候或重试"); return false; }
    if (busyRef.current) { toast("上一条消息还在执行，请稍候"); return false; }
    return true;
  }, [health, toast]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || !guard() || !agentId) return;
    busyRef.current = true;
    setBusy(true);
    // 乐观气泡按对象身份撤，不按文案匹配：同一句话在历史里出现过时，按内容 filter 会把旧的那条一起删掉
    const optimistic: ChatMessage = { role: "user", content: text, ts: Date.now(), steps: null };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");
    try {
      const resp = await chatSend(activeId ?? "", text, agentId);
      setMessages((prev) => [...prev, {
        role: "assistant", content: resp.reply, ts: Date.now(), steps: resp.steps,
      }]);
      if (resp.session_id !== activeId) setActiveId(resp.session_id);
      const rows = await reloadSessions();
      // 新建会话后标题由服务端定，用返回的 title 就地补齐，避免等整表刷新才可见
      const mine = rows.find((r) => r.id === resp.session_id);
      if (mine && resp.title && mine.title !== resp.title) {
        setSessions((prev) => prev.map((r) => (r.id === mine.id ? { ...r, title: resp.title } : r)));
      }
    } catch (err) {
      // 失败必须可见：撤掉乐观 user 气泡并回填原文，不让用户对着「发出去了却没回」的空框
      setMessages((prev) => prev.filter((m) => m !== optimistic));
      setInput(text);
      toast(err instanceof ApiError ? err.message : String(err));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [activeId, agentId, guard, input, reloadSessions]);

  const newSession = useCallback(() => {
    if (!guard()) return;
    setQuery("");
    void openSession(null);
  }, [openSession]);

  const remove = useCallback(async (id: string) => {
    if (!guard()) return;
    const row = sessions.find((s) => s.id === id);
    if (!window.confirm(`删除会话「${row?.title ?? id}」？删除后不可恢复。`)) return;
    try {
      await deleteChatSession(id);
      toast(`已删除会话「${row?.title ?? id}」`);
      const rows = await reloadSessions();
      if (activeId === id) void openSession(rows.length ? rows[0].id : null);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
    }
  }, [activeId, guard, reloadSessions, sessions, openSession, toast]);

  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制到剪贴板");
    } catch {
      toast("复制失败：浏览器未授权访问剪贴板");
    }
  }, [toast]);

  const agent = agents.find((a) => a.id === agentId);

  if (error) {
    return (
      <div className="page">
        <p className="p-empty">加载失败：{error} <button className="mini-btn" onClick={() => void reloadMeta()}>重试</button></p>
      </div>
    );
  }
  if (healthError) {
    return (
      <div className="page">
        <p className="p-empty">后端未就绪：{healthError} <button className="mini-btn" onClick={() => void reloadMeta()}>重试</button></p>
      </div>
    );
  }

  return (
    <div className="layout">
      <SessionPane
        agents={agents}
        agentId={agentId}
        sessions={sessions}
        activeId={activeId}
        query={query}
        busy={busy}
        collapsed={collapsed}
        onAgentChange={(id) => { if (!guard()) return; setAgentId(id); void openSession(null); }}
        onQueryChange={setQuery}
        onNew={newSession}
        onSelect={(id) => { if (!guard()) return; void openSession(id); }}
        onDelete={(id) => void remove(id)}
        onCollapse={() => setCollapsed(true)}
      />
      <main className="chat">
        <div className="chat-header">
          <button
            className="icon-btn"
            title={collapsed ? "显示会话列表" : "隐藏会话列表"}
            style={collapsed ? { color: "var(--primary-deep)" } : undefined}
            onClick={() => setCollapsed((v) => !v)}
          >☰</button>
          <span className="chat-title">{activeId ? (sessions.find((s) => s.id === activeId)?.title ?? "新会话") : "新会话"}</span>
          <button className="model-chip" title="会话级模型切换本期未开放，设置中改默认模型" onClick={onOpenSettings}>
            <span className="m-dot" />{modelLabel} ▾
          </button>
          <div className="spacer" />
          <button className="icon-btn" title="新建会话" disabled={busy} onClick={newSession}>
            💬<sup style={{ color: "var(--primary)", fontWeight: 800 }}>＋</sup>
          </button>
        </div>
        <MessageList
          messages={messages}
          agentName={agent?.name ?? "用例设计智能体"}
          busy={busy}
          onCopy={(t) => void copy(t)}
          onChip={(t) => { setInput(t); inputRef.current?.focus(); }}
        />
        <Composer
          input={input}
          busy={busy}
          modelLabel={modelLabel}
          cap={cap}
          systemPrompt={systemPrompt}
          messages={messages}
          inputRef={inputRef}
          onInput={setInput}
          onSubmit={() => void send()}
          onToast={toast}
        />
      </main>
      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  );
}
```

- [ ] **Step 1b: 评审后落地（控制端下手，代码以仓内为准）**

Task 11 评审两轮共提出 4 项 Important + 1 项控制端自引入的 Critical + 若干 Minor，落地如下（都改本步的草稿代码，不改已入库组件的对外契约，除第 1、5 条）：

1. **错误态出路**：`healthError` 只有 App 的 `refreshHealth` 成功才会清，本页拉不动它 → `ChatPage` 多一个必填 prop `onRetryHealth`（App 传 `refreshHealth`），两个错误态的「重试」统一走 `retryAll = onRetryHealth() + reloadMeta()`。**这条推翻了本任务 Interfaces 里「App.tsx 三 props 保持不变」的写法**（`App.tsx` 现传 4 个）。
2. **开会话的最新点击优先**：`openSeq` 序号 + `loadingRef`，慢响应不得覆盖后点的会话；`guard()` 增加「会话还在加载，请稍候」，防止在途加载期间发送把消息写进另一条会话。
3. **加载失败退回新会话**：`openSession` 的 catch 里 `setActiveId(null)`，不留「页头挂着那条会话、正文却是欢迎态、一发送就悄悄续写它」的状态。
4. **切智能体立刻清 `sessions`**：`onAgentChange` 加 `setSessions([])`。后端 `chat.py` 不校验会话归属，旧 agent 的行在整表刷新落地前仍可点，点进去就是往别人的会话里续写。
5. **自增高收进 `Composer`**：`useEffect` 改为每次 `input` 变化都量（`auto` → `min(scrollHeight,160)`），`onChange` 里那段量高删掉。这样 chip 填值、失败回填、发送清空三条路径共用一套，`ChatPage.onChip` 退回「`setInput` + `focus`」，不再需要 `react-dom` 的 `flushSync`。
6. **删除就地摘行**：`deleteChatSession` 成功后先 `setSessions(prev => prev.filter(...))` 再刷整表，整表刷新失败也不会留一条已删的行。
7. **`PlaceholderPage.tsx` 删除**：ChatPage 改写后它零引用，是死组件。
8. **第 2 轮修复（第 1 轮 scoped 复审抓出控制端自己写坏的 Critical，commit `1faf430`）**——第 2、3 条的实现形态以本条为准：
   - `loadingRef` 入口无条件认领：`openSession` 一进来就 `loadingRef.current = !!id`，`null` 分支的早退才会释放。原写法在「点会话 A → 点新建」这种 A→null 时序下让 A 的 `finally` 因 seq 已被认领而跳过，标记永久卡 `true` → `guard()` 恒假 → 发送/新建/切换/选中/删除全废，只能刷新页面。这是 Critical，是我第 1 轮修复引入的。
   - `listSeq` 守 `reloadSessions`：整表响应回来时只对「最新一次请求」生效，慢响应不得把旧智能体的行覆盖回侧栏（stale 分支仍 `return j.sessions` 供调用方，但不 `setSessions`）。
   - `mutRef` 锁 `remove` 的两段 `await`：删除尾部会自动开会话并抢 `openSeq`，此时点别的智能体/另一条会话正好落在交叉点上，`guard()` 里加「上一个操作还在执行，请稍候」。
   - `retryAll` 补 `void reloadSessions()`：错误态点「重试」只恢复了 `reloadMeta`，侧栏仍是空的，用户会以为重试没生效。
9. **第 2 轮 scoped 复审的 Minor（commit `27288a1`）**：错误页两个提前 `return` 会把 `reloadSessions` 的失败 toast 丢掉——后端宕机时点「重试」，若只有侧栏补拉失败，用户就只看到页面纹丝不动。`.toast` 本就是 `position:fixed`（`App.css:321-326`），所以把 toast 抽成 `toastEl` 挂进三个 return 分支即可，不影响任何排版。


未采纳（附理由）：`reloadMeta` 以 `agentId` 为 dep 导致挂载拉两遍——这次重拉正是「切智能体后刷新 `systemPrompt`/生效模型」的机制，拆开会引入新的时序 bug；非 `ApiError` 时 `String(err)` 把英文 `Failed to fetch` 送进 toast——`App.tsx:27` 早就这么写，KB/项目两页同口径，属跨页既存形态，本期不动（留给后续统一收口）。

- [ ] **Step 2: 构建门禁**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 3: 起本地预览给用户走查**

**禁用 `scripts/dev.ps1`**：它 `Clear-Port -Port 8000`（`dev.ps1:163`），会把用户正在跑的 8000 后端进程树杀掉。走查一律自起独立端口，且绝不碰 8000/5173：

```bash
# 终端 1：自起后端（读真实 backend/data 配置，会话写真实 backend/data/sessions，走查后清）
cd backend && uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8002
# 终端 2：前端代理指向 8002（vite.config.ts:11 已支持 VITE_PROXY_TARGET）
cd frontend && VITE_PROXY_TARGET=http://127.0.0.1:8002 npm run dev -- --port 5176 --strictPort
```

给用户 `http://localhost:5176`。

**真实 LLM 调用要当面同意**：8002 起的是真实配置，发一条消息就会打上游并按 token 计费。未获同意时只走查不发消息的部分（欢迎态、空列表、搜索、折叠、删除已有行、`未配置模型` 之外的错误态用 curl 造），并**在回执里明写「多轮真实回复与过程块未走查」**；获同意后按 spec「真机走查」条目执行，探针会话用后即删。

- [ ] **Step 3b: 无授权模式下的后端行为验证（零网络、零真实模型）**

用 TestClient 脚本（新脚本，不追加进历史脚本）跑一遍真实数据目录之外的完整链路，把「重启不丢」在进程级别验出来：

```python
# 临时脚本 scratch_chat_session_check.py —— 用完即删
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService
from aitester.services.session_store import SessionStore

ROOT = Path("./scratch-data/sessions").resolve()
CFG = Path("./scratch-data").resolve()   # 真实 backend/data 一律只读：配置也指到 scratch 副本


def _app():
    return create_app(
        model_config_path=CFG / "model_config.json",
        capability_config_path=CFG / "capability_config.json",
        projects_path=CFG / "projects.json",
        sessions_dir=ROOT,
        settings=Settings(_env_file=None),
        kb_manager=SimpleNamespace(
            start=lambda: None, close_all=lambda timeout=30.0: None,
            run_job_sync=lambda *a, **k: None),
    )


def _wired():
    app = _app()
    app.state.chat_service = ChatService(
        provider=MockProvider(), agent_runtime=app.state.agent_runtime,
        sessions=app.state.sessions)
    return TestClient(app)


if __name__ == "__main__":
    c = _wired()
    sid = c.post("/api/chat/send", json={"message": "生成退款用例", "agent_id": "case_design"}).json()["session_id"]
    c.post("/api/chat/send", json={"session_id": sid, "message": "再加异常路径", "agent_id": "case_design"})
    # 换 store 实例 = 模拟后端重启：列表与消息必须原样还在
    reopened = SessionStore(ROOT)
    assert [s.title for s in reopened.list("case_design")] == ["生成退款用例"]
    print("messages:", [(m.role, m.content) for m in reopened.messages(sid)])
    print("重启后会话仍在 OK；清理：删 scratch-sessions 目录")
```

Run: `cd backend && uv run python ../scratch_chat_session_check.py`
Expected: 打印两条 user + 两条 assistant 消息（`[mock] …`），断言不抛。跑完删除 `scratch-sessions/` 与该脚本。

- [ ] **Step 4: 走查清单（逐项过，任一不过即回 Step 1）**

- 发一条消息 → 侧栏出现会话行，标题是消息前 16 字，落在「今天」
- 点侧栏该行 → 历史消息全在（含过程块）
- 重启后端进程 → 会话与全部消息仍在，点开可见（本片核心承诺）
- 第二轮模型能引用第一轮内容
- 搜索过滤、新建会话（清空搜索 + 回欢迎态）、删除会话（原生确认 → 行消失 + jsonl 文件消失）
- 错误态出路：后端未就绪/加载失败页的「重试」按钮点下去真能恢复（`retryAll` 同时打回 App 的 `refreshHealth` 与本页 `reloadMeta`）
- 在途加载守门：连点两条会话、或在加载时按 Enter 发送，不串会话、不把消息写进另一条（`openSeq`/`loadingRef`）
- 切智能体：侧栏立刻清空再回填，不留上一个智能体的旧行；切失败 toast「会话还在加载，请稍候」
- 输入框回填：发送失败后长文本要按 Composer 自增高铺开，不停在 2 行内滚
- 过程块显示真实工具序列与成败；无工具时整块不出现
- 上下文百分比随对话增长变化（≥70 warn / ≥90 hot + tooltip 补「建议新建会话」）
- busy 期间 textarea/发送/新建/切换/删除全部禁用，重复发送不生效
- 侧栏 « 收起、chat-header ☰ 恢复，动画一致
- **与原型并排走查（`QODER.md` 要求）**：原型跑在 http://localhost:8899（`prototype/serve.js`，只读参考，不动它），本页起在 5176，同屏比三处——
  - 欢迎态：`你好，我是 用例设计` + 4 条 chip（📋/🐞 两条与原型逐字一致），点 chip 只把长指令填进输入框并聚焦，**不自动发送**
  - composer：`.bar` 内只有 上下文 meter → `🛡 自由权限 ▾` →  spacer → `↑`；**无橙 chip**；页底 `.foot-tip` 与原型 `:623` 逐字一致
  - chat-header：`☰` + 会话标题 + 模型 chip（点它进设置）+ `💬＋`；模型名与设置页默认模型一致
- 页面 0 处 `zhb`、0 个死按钮；回复正文 markdown 排版可见（验 `.md-preview` 那条 CSS）
- 失败路径：未配置模型时发送 → toast 出中文 detail、输入框回填原文、乐观气泡撤掉

- [ ] **Step 5: 清场**

删除走查期间产生的探针会话（页面删除即可，同时确认 `backend/data/sessions/<id>.jsonl` 已消失），停掉自起的 dev 服务；若曾起过 8002 之类自起后端，一并停净。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/ChatPage.tsx
git commit -m "feat(chat-ui): 聊天页装配——会话闭环打通"
```

---

### Task 12: 文档回写与后端全量回归

**Files:**
- Modify: `docs/superpowers/specs/2026-10-03-chat-session-design.md:3`
- Modify: `README.md`（能力段加聊天页一行）

**Interfaces:**
- Consumes: 本片实现结果
- Produces: 无（文档）

- [ ] **Step 1: spec 状态回写**

把 spec 第 3 行改为：

```markdown
日期：2026-10-03　状态：**第 1 片已实现**（会话落盘 + 三端点 + `/chat` 实页；项目维度/工作区/流式留后续片）
```

- [ ] **Step 2: README 能力段**

在 README 已实现能力清单里加一行（口径与既有各条一致，不承诺项目过滤）：

```markdown
- 聊天页 `/chat`：会话落盘（`backend/data/sessions`）、列表/搜索/分组/删除、多轮记忆（最近 40 条进 prompt）、真实工具调用过程展示
```

- [ ] **Step 3: 全量门禁**

```bash
cd backend && uv run pytest -q
cd ../frontend && npm run build
git status --porcelain backend/data   # 期望空
```
Expected: pytest 全绿（本片新增约 40 个用例）、build 0 error、真实数据目录零污染。

> 说明：`git status --porcelain backend/data` 被 `.gitignore:17` 整目录忽略架空、恒空、无证明力，不能用来验「零写入」；收尾应以「文件 size + mtime 双快照」为门禁（Task 12 已如此替代）。

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-03-chat-session-design.md
git commit -m "docs(chat): 聊天页第 1 片收尾——能力段与 spec 状态"
```

---

## 计划期新增偏离（`QODER.md`「禁止静默偏离」条款要求，1-5 条已随 spec 评审一并认可，2026-10-03 确认）

写计划时在 `prototype/index.html` 里逐个对照 composer / welcome / 侧栏三处，发现下面几处**必须**偏离（能力未到本期），已同步写进 spec 的「前端呈现」条。下表 1-5 是计划期（第 1 条是纠错，其余是本期范围决定），6-9 是执行期评审后补登记。

| # | 位置 | 原型 | 本期 | 理由 |
|---|---|---|---|---|
| 1 | composer `.bar` | 橙 chip 是 `📁 项目 · Agent 工作目录`（`:617`），模型 chip 在 chat-header（`:598`） | composer 内不放任何 chip 之外的东西，模型 chip 只保留在 chat-header | 早期草稿误把「🧠 模型」做成 composer 橙 chip，属自造，已撤回 |
| 2 | textarea placeholder | `↑↓ 浏览历史消息 · / 快捷指令 · 例如：根据这份需求生成测试用例`（`:614`） | 只留可落地的片段 `例如：根据这份需求生成测试用例`，键位提示移到 `title` | ↑↓ 历史与 `/` 指令本期都没有，写进 placeholder 是虚假承诺 |
| 3 | 权限 chip | 点开弹层列「自由权限 / 严格权限（置灰）」（`:618`、`:1451-1476`） | 保留 `🛡 自由权限 ▾` 外形，点击只给原型同一条 toast `「严格权限」暂未开放，敬请期待` | 本期只有自由权限一档；做成纯装饰 span 会变成 `cursor:pointer` 的死控件 |
| 4 | 欢迎态副行 | `📁 订单系统 · 发送消息即在此项目开始新会话`（`:1314`） | `发送消息即在此智能体开始新会话 · 会话保存在本机` | 项目维度是第 2 片；数据落点目录不在 UI 裸露（README 里写） |
| 5 | 欢迎态 4 条 chip | `📋 根据需求生成测试用例` / `🔌 自动化编码：接口脚本落地` / `🐞 回归失败归因分析` / `📊 生成测试报告`（`:1316-1319`） | 📋/🐞 两条标签与其长指令逐字照搬；🔌 换成 `🔌 接口用例设计`，🔌/🧩 新增 `🧩 等价类与边界值补覆盖` | 已批准的裁定 5：本期只有 `case_design`，「脚本落地」「生成测试报告」超出其职责 |

执行期新增（评审后登记，用户 2026-10-03 对 6-9 条答复「认可，按现状走」）：

| # | 位置 | 原型 | 本期 | 理由 |
|---|---|---|---|---|
| 6 | 过程块 | `🧠 Thinking` 整段文本（`:1300`，样式 `:143-148`） | `🔧 执行过程` + 逐行 `序号 / 工具名 · 成功\|失败 / 参数摘要`，为此在 `App.css` 新增 `.t-step/.n/.args` 三条规则 | 裁定 4 要求显示真实工具序列与成败，原型没有这一状态；容器仍是既有 `details.thinking`，色值沿用框自身的 `var(--text-2)`，不新增色值 |
| 7 | 回复正文排版 | 聊天正文有专用排版（`.msg.agent .body`） | 复用工作区 `.md-preview` 渲染，故气泡内 `color:#33302a`（比 `--text` 略淡）与 `h1..h4` 的边框、`code/pre` 底色会生效；`ul/ol` 缩进已归零对齐原型 | 不引第二套 markdown 渲染器（`mdRender` 已含转义与 href 白名单）；仓内先例 `.kb-msg .md-preview` 是同一取舍 |
| 8 | 键盘可达性 | 侧栏行、`⧉` 复制、过程块折叠都是 `div/span onClick`，删除按钮 hover 才出现 | 原样照搬，未加 `tabIndex` / `role` / 方向键导航 | 与原型同形；本期门禁是走查，可访问性留给后续专项（评审已同意不阻塞） |
| 9 | busy 视觉 | 原型无 busy 态 | busy 期间 `新建/切换/删除/模型 select` 已 `disabled`，但 `App.css` 没有这些控件的 `:disabled` 规则，外观不变灰 | 补禁用样式属样式专项，本期只保证「点了没反应且能看出在执行」（textarea 禁用 + 发送按钮 `正在执行…` + typing 动画） |
| 10 | chat-header 模型 chip | 裁定 3 的「点击进列表但不可选，底部『⚙ 设置 · 模型…』跳设置」 | `model-chip` 直接打开设置弹窗（`ChatPage.tsx` 的 `model-chip` 按钮 `onClick=onOpenSettings`；行号会漂，认符号不认行号），设置弹窗内本就展示只读模型清单 | 少一层中间弹层，且不新增可选能力，属措辞偏离而非裁定变更 |

`.foot-tip` 用原型 `:623` 原文 `为测试人员而生 · 用例生成 / 脚本编写 / 失败分析`，**无偏离**（早期草稿自造过一句「会话与消息保存在本机 · …」的说明文案，已撤回）。

---

## 终审留给第 2 片的议程

- 前端测试运行器（vitest）替代「写-跑-删」临时断言脚本——本期两次真缺陷（Task 8 的 `fmtTime` 日历日边界、Task 10 的 `fmtK` tooltip）的回归网都随脚本删掉了。
- 把 `session_store` 挪出 `services` 包，拆掉 `memory → services → memory` 的导入顺序防线（现靠 `chat.py` 的「延迟导入」注释与 `memory/__init__.py` 的导入顺序注释维持；行号会漂，认符号不认行号）。
- `project_id` 进会话键时，把 `send` 的归属校验从 `agent_id` 扩到项目维度。
- 孤儿 `.jsonl` 的回收，以及从正文 `.jsonl` 重建索引的路径（本期仅留档坏索引）。
- `backend/data` 单进程约束若要强制，可用锁文件在第二次启动时响亮拒绝。

## 自检结论（写完后回看 spec）

- **覆盖**：spec 的 6 条用户裁定 → Task 4（HISTORY_MAX、模型只读在 Task 10/11 呈现、真实工具序列 Task 5+10、首条消息才落盘 Task 2/3/4、欢迎文案与范围 Task 11 走查）；8 处偏离 → Task 9（`▶ Web` 假标签去除、项目 card 不渲染）、Task 10（`🔧 执行过程` 替 `🧠 Thinking`、无卡片、权限 chip 不 gate）、Task 11（busy gate、空会话不落盘）；API 契约表 4 行 → Task 6；组件拆分 → Task 8-11；测试策略 6 组 → Task 2/3/4/5/6 各步；验收清单 → Task 11 Step 4。
- **无占位**：全部步骤含可执行命令与实际代码，无 TBD/「类似 Task N」。
- **类型一致**：`is_session_id` / `SessionStore.new_id` / `create(session_id, agent_id, first_message)` / `append(..., steps)` / `FileMemoryStore.save(..., steps)` / `ChatStep`↔`StepInfo` / `SendResponse.steps` 前后任务命名一致。
- **本轮自查另修掉 4 处**（都是会让门禁或走查返工的）：
  1. Composer 里自造的 `🧠 模型` 橙 chip 删除——原型 composer（`:615-621`）只有 meter + 蓝 perm chip + 发送，模型 chip 在 chat-header（`:598`，Task 11 已放）；橙 chip 原型是「📁 项目 · 工作目录」，属第 2 片。
  2. `App.css` 插入点从 `:128` 改 `:133`（`:128` 是 `.msg.agent .body{line-height}`，`:133` 才是 `.body b{color:#111}` 即该组最后一条）。
  3. `.md-preview` 补的规则加 `font-size:14px`：只补 `display`+`padding` 会让聊天正文用 `.md-preview` 的 13px，比原型的 14px 小一号。
  4. `ChatPage` 的 `health` prop 在草稿里取了不用，`tsconfig` 开了 `noUnusedLocals/noUnusedParameters`（`frontend/tsconfig.json`）必红 → 改为在 `guard()` 里做「后端未就绪」gate；同处补齐 `send` 的 deps 漏 `guard`、`contextUsage` 的 `history` 形参与实现不一致、Task 9 漏「类型门禁」步骤。
