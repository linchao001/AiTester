# AiTester 工程骨架实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 AiTester 前后端分离单仓骨架：后端七层最小占位并打通 health/echo 窄链路，前端 Vite+React+TS 三路由占位并联调后端。

**Architecture:** `backend/`（uv + FastAPI，包 `aitester` 内七层子包，依赖方向 interaction → services → orchestration → context/memory → adapters/storage）+ `frontend/`（Vite proxy `/api` → 127.0.0.1:8000）。echo 响应携带逐层 trace 作为链路打通证据。

**Tech Stack:** Python 3.11+ / uv / FastAPI / LangGraph / pydantic-settings / pytest+httpx；Node 24 / npm / Vite / React 18 / TypeScript（strict）/ react-router-dom。

**Spec:** `docs/superpowers/specs/2026-09-30-aitester-scaffold-design.md`

## Global Constraints

- 本仓库**不执行任何 git 操作**（无 init/commit）；各任务以测试通过收尾，无 Commit 步骤。
- `prototype/` 目录只读保留，不修改、不移动、不进构建。
- 后端仅监听 `127.0.0.1:8000`；前端 dev server 端口 5173。
- 用户通过浏览器访问 `http://localhost:5173` 预览前端（不用应用内浏览器）。
- TS `strict: true`；Python 全部使用类型注解。
- UI 占位文案使用中文；层内注释仅在职责不直观处写一行。
- 每层以「Protocol + 最小实现 + `__init__.py` 导出」占位，不实现真实业务。

---

### Task 1: backend 项目初始化（uv 工程 + config + 七层目录）

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/.gitignore`
- Create: `backend/src/aitester/__init__.py`
- Create: `backend/src/aitester/config.py`
- Create: `backend/src/aitester/{interaction,services,orchestration,context,memory,adapters,storage}/__init__.py`（七个包各一行 docstring）
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces: `aitester.config.Settings`（字段 `host: str = "127.0.0.1"`、`port: int = 8000`、`llm_provider: str = "mock"`）、`aitester.config.get_settings() -> Settings`；可运行的 `uv run pytest`。

- [ ] **Step 0: 环境检查**

Run: `uv --version`
若命令不存在：`pip install uv`（或提示用户安装后继续）。

- [ ] **Step 1: 写 pyproject 与包骨架**

`backend/pyproject.toml`:

```toml
[project]
name = "aitester-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic-settings>=2.3",
  "langchain-core>=0.3",
  "langgraph>=0.2",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aitester"]
```

`backend/src/aitester/__init__.py`: `"""AiTester 测试智能体后端。"""`
七个层目录各建 `__init__.py`，内容形如 `"""交互层：前端 API 端点。"""`（按层职责写一句中文 docstring，无其他代码）。

`backend/.env.example`:

```
HOST=127.0.0.1
PORT=8000
LLM_PROVIDER=mock
DEEPSEEK_API_KEY=
DASHSCOPE_API_KEY=
```

`backend/.gitignore`:

```
.env
.venv/
__pycache__/
*.pyc
```

- [ ] **Step 2: 写失败测试**

`backend/tests/test_config.py`:

```python
from aitester.config import Settings, get_settings


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.llm_provider == "mock"


def test_get_settings_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && uv sync && uv run pytest tests/test_config.py -v`
Expected: FAIL（`ModuleNotFoundError: aitester.config` 或收集错误）

- [ ] **Step 4: 实现 config.py**

`backend/src/aitester/config.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    llm_provider: str = "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: 2 PASS

---

### Task 2: 存储层 + 记忆层最小实现

**Files:**
- Create: `backend/src/aitester/storage/base.py`
- Create: `backend/src/aitester/storage/in_memory.py`
- Modify: `backend/src/aitester/storage/__init__.py`（导出）
- Create: `backend/src/aitester/memory/base.py`
- Create: `backend/src/aitester/memory/in_memory.py`
- Modify: `backend/src/aitester/memory/__init__.py`（导出）
- Test: `backend/tests/test_storage_memory.py`

**Interfaces:**
- Consumes: Task 1 的包结构
- Produces:
  - `storage.Repository` Protocol：`put(key: str, value: dict[str, Any]) -> None`、`get(key: str) -> dict[str, Any] | None`
  - `storage.InMemoryRepository()` 实现上述协议
  - `memory.MemoryStore` Protocol：`save(session_id: str, role: str, content: str) -> None`、`recall(session_id: str) -> list[dict[str, str]]`
  - `memory.InMemoryMemoryStore()` 实现上述协议

- [ ] **Step 1: 写失败测试**

`backend/tests/test_storage_memory.py`:

```python
from aitester.memory import InMemoryMemoryStore
from aitester.storage import InMemoryRepository


def test_repository_put_get() -> None:
    repo = InMemoryRepository()
    repo.put("session:s1", {"session_id": "s1", "last_reply": "hi"})
    assert repo.get("session:s1") == {"session_id": "s1", "last_reply": "hi"}
    assert repo.get("session:missing") is None


def test_memory_store_save_recall() -> None:
    store = InMemoryMemoryStore()
    assert store.recall("s1") == []
    store.save("s1", "user", "你好")
    store.save("s1", "assistant", "[mock] 你好")
    assert store.recall("s1") == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "[mock] 你好"},
    ]
    assert store.recall("s2") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_storage_memory.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现两层**

`backend/src/aitester/storage/base.py`:

```python
from typing import Any, Protocol


class Repository(Protocol):
    """实体持久化抽象，后续专项开发替换为 SQLite/DB 实现。"""

    def put(self, key: str, value: dict[str, Any]) -> None: ...

    def get(self, key: str) -> dict[str, Any] | None: ...
```

`backend/src/aitester/storage/in_memory.py`:

```python
from typing import Any


class InMemoryRepository:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)
```

`backend/src/aitester/storage/__init__.py`:

```python
"""存储层：实体持久化抽象。"""
from aitester.storage.base import Repository
from aitester.storage.in_memory import InMemoryRepository

__all__ = ["Repository", "InMemoryRepository"]
```

`backend/src/aitester/memory/base.py`:

```python
from typing import Protocol


class MemoryStore(Protocol):
    """会话记忆抽象，最小实现为进程内存储（重启即失）。"""

    def save(self, session_id: str, role: str, content: str) -> None: ...

    def recall(self, session_id: str) -> list[dict[str, str]]: ...
```

`backend/src/aitester/memory/in_memory.py`:

```python
class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[dict[str, str]]] = {}

    def save(self, session_id: str, role: str, content: str) -> None:
        self._messages.setdefault(session_id, []).append({"role": role, "content": content})

    def recall(self, session_id: str) -> list[dict[str, str]]:
        return list(self._messages.get(session_id, []))
```

`backend/src/aitester/memory/__init__.py`:

```python
"""记忆层：会话消息的保存与召回。"""
from aitester.memory.base import MemoryStore
from aitester.memory.in_memory import InMemoryMemoryStore

__all__ = ["MemoryStore", "InMemoryMemoryStore"]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_storage_memory.py -v`
Expected: 2 PASS

---

### Task 3: 接入层（LlmProvider 协议 + MockProvider + kb/tools 预留包）

**Files:**
- Create: `backend/src/aitester/adapters/llm/__init__.py`
- Create: `backend/src/aitester/adapters/llm/base.py`
- Create: `backend/src/aitester/adapters/llm/mock.py`
- Create: `backend/src/aitester/adapters/kb/__init__.py`（仅 docstring）
- Create: `backend/src/aitester/adapters/tools/__init__.py`（仅 docstring）
- Modify: `backend/src/aitester/adapters/__init__.py`
- Test: `backend/tests/test_adapters.py`

**Interfaces:**
- Consumes: Task 1 包结构
- Produces:
  - `adapters.llm.LlmProvider` Protocol：属性 `name: str`；方法 `complete(messages: list[dict[str, str]]) -> str`
  - `adapters.llm.MockProvider()`：`name == "mock"`，`complete` 返回 `"[mock] " + 最后一条 user 消息内容`（无 user 消息返回 `"[mock]"`）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_adapters.py`:

```python
from aitester.adapters.llm import LlmProvider, MockProvider


def test_mock_provider echoes_last_user_message() -> None:
    provider: LlmProvider = MockProvider()
    messages = [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "生成用例"},
        {"role": "assistant", "content": "上一轮回复"},
        {"role": "user", "content": "再来一条"},
    ]
    assert provider.name == "mock"
    assert provider.complete(messages) == "[mock] 再来一条"


def test_mock_provider_without_user_message() -> None:
    assert MockProvider().complete([{"role": "system", "content": "hi"}]) == "[mock]"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_adapters.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现接入层**

`backend/src/aitester/adapters/llm/base.py`:

```python
from typing import Protocol


class LlmProvider(Protocol):
    """模型提供商适配器协议（对齐 provider 化模型配置方向）。"""

    name: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...
```

`backend/src/aitester/adapters/llm/mock.py`:

```python
class MockProvider:
    name = "mock"

    def complete(self, messages: list[dict[str, str]]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "user":
                return f"[mock] {msg['content']}"
        return "[mock]"
```

`backend/src/aitester/adapters/llm/__init__.py`:

```python
"""接入层-模型提供商适配。"""
from aitester.adapters.llm.base import LlmProvider
from aitester.adapters.llm.mock import MockProvider

__all__ = ["LlmProvider", "MockProvider"]
```

`backend/src/aitester/adapters/kb/__init__.py`: `"""接入层-知识库文件适配（预留，后续专项开发）。"""`
`backend/src/aitester/adapters/tools/__init__.py`: `"""接入层-外部工具适配（预留，后续专项开发）。"""`
`backend/src/aitester/adapters/__init__.py`: `"""接入层：对接外部世界（模型提供商/工具/知识库）。"""`

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_adapters.py -v`
Expected: 2 PASS

---

### Task 4: 上下文管理层最小实现

**Files:**
- Create: `backend/src/aitester/context/base.py`
- Create: `backend/src/aitester/context/passthrough.py`
- Modify: `backend/src/aitester/context/__init__.py`
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: Task 1 包结构
- Produces: `context.ContextBuilder` Protocol 与 `context.PassthroughContextBuilder()`，方法 `build(system_prompt: str, history: list[dict[str, str]], user_message: str) -> list[dict[str, str]]`，输出 `[system, *history, user]`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_context.py`:

```python
from aitester.context import ContextBuilder, PassthroughContextBuilder


def test_passthrough_builds_system_history_user() -> None:
    builder: ContextBuilder = PassthroughContextBuilder()
    messages = builder.build(
        "你是测试智能体",
        [{"role": "user", "content": "第一条"}],
        "第二条",
    )
    assert messages == [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "第一条"},
        {"role": "user", "content": "第二条"},
    ]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_context.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现**

`backend/src/aitester/context/base.py`:

```python
from typing import Protocol


class ContextBuilder(Protocol):
    """把会话原料装配为编排层可用的 prompt 消息列表。"""

    def build(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> list[dict[str, str]]: ...
```

`backend/src/aitester/context/passthrough.py`:

```python
class PassthroughContextBuilder:
    def build(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> list[dict[str, str]]:
        return (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": user_message}]
        )
```

`backend/src/aitester/context/__init__.py`:

```python
"""上下文管理层：prompt 装配。"""
from aitester.context.base import ContextBuilder
from aitester.context.passthrough import PassthroughContextBuilder

__all__ = ["ContextBuilder", "PassthroughContextBuilder"]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_context.py -v`
Expected: 1 PASS

---

### Task 5: 编排 loop 层（LangGraph 单节点图）

**Files:**
- Create: `backend/src/aitester/orchestration/graph.py`
- Modify: `backend/src/aitester/orchestration/__init__.py`
- Test: `backend/tests/test_orchestration.py`

**Interfaces:**
- Consumes: Task 3 的 `adapters.llm.LlmProvider`（`MockProvider` 作测试替身）
- Produces: `orchestration.EchoState`（TypedDict：`messages`、`reply`）、`orchestration.build_echo_graph(provider: LlmProvider)`（返回 `StateGraph` 编译后的图）、`orchestration.run_echo(provider: LlmProvider, messages: list[dict[str, str]]) -> str`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_orchestration.py`:

```python
from aitester.adapters.llm import MockProvider
from aitester.orchestration import run_echo


def test_run_echo_returns_provider_reply() -> None:
    messages = [
        {"role": "system", "content": "你是测试智能体"},
        {"role": "user", "content": "hello"},
    ]
    assert run_echo(MockProvider(), messages) == "[mock] hello"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_orchestration.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 LangGraph 单节点图**

`backend/src/aitester/orchestration/graph.py`:

```python
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from aitester.adapters.llm import LlmProvider


class EchoState(TypedDict):
    messages: list[dict[str, str]]
    reply: str


def build_echo_graph(provider: LlmProvider):
    """当前仅一个 mock_llm_node；后续多节点 loop 在此扩展。"""

    def mock_llm_node(state: EchoState) -> dict[str, str]:
        return {"reply": provider.complete(state["messages"])}

    graph = StateGraph(EchoState)
    graph.add_node("mock_llm_node", mock_llm_node)
    graph.add_edge(START, "mock_llm_node")
    graph.add_edge("mock_llm_node", END)
    return graph.compile()


def run_echo(provider: LlmProvider, messages: list[dict[str, str]]) -> str:
    result = build_echo_graph(provider).invoke({"messages": messages, "reply": ""})
    return result["reply"]
```

`backend/src/aitester/orchestration/__init__.py`:

```python
"""编排 loop 层：LangGraph 状态图。"""
from aitester.orchestration.graph import EchoState, build_echo_graph, run_echo

__all__ = ["EchoState", "build_echo_graph", "run_echo"]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_orchestration.py -v`
Expected: 1 PASS（首次运行会实际构建 langgraph 依赖，若 `uv sync` 未含 langgraph 则先 `uv add langgraph`——pyproject 已声明，正常不需要）

---

### Task 6: 服务层 ChatService（窄链路 + trace）

**Files:**
- Create: `backend/src/aitester/services/chat.py`
- Modify: `backend/src/aitester/services/__init__.py`
- Test: `backend/tests/test_chat_service.py`

**Interfaces:**
- Consumes: Task 2 `InMemoryMemoryStore`/`InMemoryRepository`、Task 3 `MockProvider`、Task 4 `PassthroughContextBuilder`、Task 5 `run_echo`
- Produces: `services.ChatService()`（无参构造即完成四层装配）、`ChatService.echo(session_id: str, message: str) -> dict`，返回 `{"reply": str, "trace": list[str]}`，trace 固定为 `["services","context","orchestration","adapters","memory","storage"]`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_chat_service.py`:

```python
from aitester.services import ChatService


def test_echo_full_chain_trace_and_reply() -> None:
    svc = ChatService()
    result = svc.echo("s1", "生成登录用例")
    assert result["reply"] == "[mock] 生成登录用例"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_echo_remembers_previous_turn() -> None:
    svc = ChatService()
    svc.echo("s1", "第一句")
    svc.echo("s1", "第二句")
    history = svc.memory.recall("s1")
    assert [m["content"] for m in history] == ["第一句", "[mock] 第一句", "第二句", "[mock] 第二句"]
    assert svc.repo.get("session:s1") == {"session_id": "s1", "last_reply": "[mock] 第二句"}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_chat_service.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 ChatService**

`backend/src/aitester/services/chat.py`:

```python
from typing import Any

from aitester.adapters.llm import LlmProvider, MockProvider
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_echo
from aitester.storage import InMemoryRepository, Repository

SYSTEM_PROMPT = "你是 AiTester 测试智能体（骨架占位）。"


class ChatService:
    """业务门面：装配四层并记录穿透 trace。"""

    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
    ) -> None:
        self.provider = provider or MockProvider()
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()

    def echo(self, session_id: str, message: str) -> dict[str, Any]:
        trace: list[str] = ["services"]

        history = self.memory.recall(session_id)
        messages = self.context.build(SYSTEM_PROMPT, history, message)
        trace.append("context")

        reply = run_echo(self.provider, messages)
        trace += ["orchestration", "adapters"]

        self.memory.save(session_id, "user", message)
        self.memory.save(session_id, "assistant", reply)
        trace.append("memory")

        self.repo.put(f"session:{session_id}", {"session_id": session_id, "last_reply": reply})
        trace.append("storage")

        return {"reply": reply, "trace": trace}
```

`backend/src/aitester/services/__init__.py`:

```python
"""服务层：业务门面。"""
from aitester.services.chat import ChatService

__all__ = ["ChatService"]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_chat_service.py -v`
Expected: 2 PASS

---

### Task 7: 交互层 + main.py 装配 + API 全链路测试

**Files:**
- Create: `backend/src/aitester/interaction/schemas.py`
- Create: `backend/src/aitester/interaction/router.py`
- Modify: `backend/src/aitester/interaction/__init__.py`
- Create: `backend/src/aitester/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 6 `ChatService.echo`
- Produces: `main.create_app() -> FastAPI`、模块级 `app`；端点 `GET /api/health` → `{"ok": true, "service": "aitester-backend"}`；`POST /api/chat/echo`（body `{session_id?: str, message: str}`）→ `{"reply": str, "trace": list[str]}`，trace 首位为 `"interaction"`（即七层全序）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_api.py`:

```python
from fastapi.testclient import TestClient

from aitester.main import app

client = TestClient(app)

ALL_LAYERS = [
    "interaction",
    "services",
    "context",
    "orchestration",
    "adapters",
    "memory",
    "storage",
]


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "aitester-backend"}


def test_chat_echo_traverses_all_seven_layers() -> None:
    resp = client.post("/api/chat/echo", json={"session_id": "s1", "message": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] hello"
    assert body["trace"] == ALL_LAYERS


def test_chat_echo_rejects_empty_message() -> None:
    resp = client.post("/api/chat/echo", json={"message": ""})
    assert resp.status_code == 422
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: FAIL（ImportError: aitester.main）

- [ ] **Step 3: 实现交互层与装配**

`backend/src/aitester/interaction/schemas.py`:

```python
from pydantic import BaseModel, Field


class EchoRequest(BaseModel):
    session_id: str = "default"
    message: str = Field(min_length=1)


class EchoResponse(BaseModel):
    reply: str
    trace: list[str]
```

`backend/src/aitester/interaction/router.py`:

```python
from fastapi import APIRouter

from aitester.interaction.schemas import EchoRequest, EchoResponse
from aitester.services import ChatService

router = APIRouter(prefix="/api")
_chat_service = ChatService()


@router.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "aitester-backend"}


@router.post("/chat/echo", response_model=EchoResponse)
def chat_echo(req: EchoRequest) -> EchoResponse:
    result = _chat_service.echo(req.session_id, req.message)
    return EchoResponse(reply=result["reply"], trace=["interaction"] + result["trace"])
```

`backend/src/aitester/interaction/__init__.py`:

```python
"""交互层：前端 API/SSE 端点。"""
from aitester.interaction.router import router

__all__ = ["router"]
```

`backend/src/aitester/main.py`:

```python
from fastapi import FastAPI

from aitester.interaction.router import router


def create_app() -> FastAPI:
    application = FastAPI(title="AiTester backend")
    application.include_router(router)
    return application


app = create_app()
```

- [ ] **Step 4: 运行确认通过（含全量回归）**

Run: `cd backend && uv run pytest -v`
Expected: 全部 PASS（test_config/test_storage_memory/test_adapters/test_context/test_orchestration/test_chat_service/test_api）

- [ ] **Step 5: 真实服务冒烟**

Run: `cd backend && uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000`（后台）
Run: `curl -s http://127.0.0.1:8000/api/health`
Expected: `{"ok":true,"service":"aitester-backend"}`；随后停掉进程。

---

### Task 8: frontend 骨架（Vite + React + TS，三路由 + health 联调）

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/vite-env.d.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.css`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/pages/ChatPage.tsx`
- Create: `frontend/src/pages/KbPage.tsx`
- Create: `frontend/src/pages/ProjectsPage.tsx`

**Interfaces:**
- Consumes: Task 7 `GET /api/health`（经 Vite proxy `/api` → `http://127.0.0.1:8000`）
- Produces: 可 `npm run build` 通过、`npm run dev` 在 5173 提供三路由（`/chat`、`/kb`、`/projects`）的前端应用

- [ ] **Step 1: 写工程文件**

`frontend/package.json`:

```json
{
  "name": "aitester-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.0"
  }
}
```

`frontend/vite.config.ts`:

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
```

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

`frontend/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>AiTester · 测试智能体</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/src/vite-env.d.ts`: `/// <reference types="vite/client" />`

- [ ] **Step 2: 写应用源码**

`frontend/src/api/client.ts`:

```typescript
export interface HealthResponse {
  ok: boolean;
  service: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const resp = await fetch("/api/health");
  if (!resp.ok) {
    throw new Error(`health 请求失败: HTTP ${resp.status}`);
  }
  return (await resp.json()) as HealthResponse;
}
```

`frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
```

`frontend/src/App.tsx`:

```tsx
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import ChatPage from "./pages/ChatPage";
import KbPage from "./pages/KbPage";
import ProjectsPage from "./pages/ProjectsPage";
import "./App.css";

const tabs = [
  { to: "/chat", label: "聊天" },
  { to: "/kb", label: "知识库" },
  { to: "/projects", label: "项目管理" },
];

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">AiTester · 测试智能体</span>
        <nav>
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} className={({ isActive }) => (isActive ? "tab active" : "tab")}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/kb" element={<KbPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </main>
    </div>
  );
}
```

`frontend/src/App.css`:

```css
.app { font-family: system-ui, sans-serif; color: #3b332a; background: #faf6ef; min-height: 100vh; }
.topbar { display: flex; align-items: center; gap: 24px; padding: 10px 20px; border-bottom: 1px solid #e8ddcc; background: #fffdf8; }
.brand { font-weight: 600; }
nav { display: flex; gap: 8px; }
.tab { padding: 6px 14px; border-radius: 8px; text-decoration: none; color: #6b5d4f; }
.tab.active { background: #f4e3cf; color: #b35a1b; }
main { padding: 24px; }
.placeholder { padding: 40px; border: 1px dashed #d8c9b4; border-radius: 12px; text-align: center; color: #8a7a66; }
.status-ok { color: #1b7a3c; }
.status-bad { color: #b3261e; }
```

`frontend/src/pages/ChatPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { getHealth, type HealthResponse } from "../api/client";

export default function ChatPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <section className="placeholder">
      <p>聊天页 · 骨架占位，后续按原型专项开发</p>
      {error !== null && <p className="status-bad">后端未联通：{error}（请先启动 backend，见 README）</p>}
      {health !== null && (
        <p className="status-ok">后端联通正常：{health.service}（{health.ok ? "ok" : "异常"}）</p>
      )}
    </section>
  );
}
```

`frontend/src/pages/KbPage.tsx`:

```tsx
export default function KbPage() {
  return <section className="placeholder">知识库页 · 骨架占位，后续按原型专项开发</section>;
}
```

`frontend/src/pages/ProjectsPage.tsx`:

```tsx
export default function ProjectsPage() {
  return <section className="placeholder">项目管理页 · 骨架占位，后续按原型专项开发</section>;
}
```

- [ ] **Step 3: 安装与构建验证**

Run: `cd frontend && npm install`
Run: `npm run build`
Expected: 构建成功，产出 `frontend/dist/`，无 TS 报错。

- [ ] **Step 4: dev 联通冒烟（配合 Task 7 后端）**

后台启动后端 `cd backend && uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000`，再启动 `cd frontend && npm run dev`。
Run: `curl -s http://localhost:5173/api/health`
Expected: 返回 `{"ok":true,...}`（证明 proxy 生效）。并告知用户打开 http://localhost:5173 查看三 tab 与联通状态文案。

---

### Task 9: README + 最终验收

**Files:**
- Create: `README.md`（仓库根）

**Interfaces:**
- Consumes: Task 1–8 全部
- Produces: 启动文档与 spec §7 验收清单证据

- [ ] **Step 1: 写 README.md**

```markdown
# AiTester · 测试智能体

前后端分离单仓工程。后端 Python + FastAPI + LangChain/LangGraph（七层分层骨架），
前端 TypeScript + React + Vite。`prototype/` 为已验证的 MVP 原型（静态页 + serve.js），
仅作参考，不参与构建。

## 后端（backend/）

cd backend
uv sync
uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000 --reload

- `GET /api/health`：联通检查
- `POST /api/chat/echo`：窄链路演示，响应 trace 穿透七层
  （interaction → services → context → orchestration → adapters → memory → storage）
- 测试：`uv run pytest`
- 配置：复制 `.env.example` 为 `.env`（当前仅 mock provider，无需 Key）

## 前端（frontend/）

cd frontend
npm install
npm run dev          # http://localhost:5173，/api 已代理到 127.0.0.1:8000

## 原型（prototype/）

cd prototype && node serve.js   # http://localhost:8899（原型页面，独立于正式工程）
```

注意：README 代码块以上为内容本体，写文件时去掉外层围栏、保留命令的普通段落或围栏均可，但需保证 markdown 合法。

- [ ] **Step 2: 全量验收**

- `cd backend && uv run pytest` → 全绿
- 起 uvicorn，`curl http://127.0.0.1:8000/api/health` → ok
- `curl -X POST http://127.0.0.1:8000/api/chat/echo -H "Content-Type: application/json" -d "{\"message\":\"hello\"}"` → reply 为 `[mock] hello`，trace 含七层
- `cd frontend && npm run build` → 通过；`npm run dev` 后 http://localhost:5173/chat 显示「后端联通正常」（交由用户浏览器确认，preview via localhost）

---

## Self-Review 结论

- Spec 覆盖：§3 布局（Task 1/8）、§4 七层与 trace（Task 2–7）、§5 前端（Task 8）、§6 配置与启动（Task 1/9）、§7 验收（Task 7/8/9）、原型保留与无 git（Global Constraints）。无缺口。
- 类型一致性：`MockProvider.complete`（Task 3 定义 = Task 5/6 消费）、`ChatService.echo` 返回结构（Task 6 = Task 7）、trace 七层全序（Task 6 尾部 + Task 7 头部拼接）已核对一致。
- 无占位词。
