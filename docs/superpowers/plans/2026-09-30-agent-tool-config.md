# 设置 · 智能体配置 / 工具（能力配置入口）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AiTester 用户在「⚙ 设置」弹窗里配置智能体默认模型、智能体携带工具与工具启用状态，配置落盘即时生效可回读；本期只做配置入口，不参与任何执行逻辑。

**Architecture:** 方案 A——storage 层把模型配置仓储泛化为通用 JSON 仓储（一次改名重构，行为不变），services 层新增 `CapabilityConfigService`（代码种子注册表 + 落盘状态 + 读侧校验），interaction 层新增 `/api/capabilities` 四端点（每个响应都返回完整 `{tools, agents}` 视图）；前端把设置弹窗拆成「外壳 + 三个 pane」，模型 pane 行为原样迁移，新增智能体 pane 与工具 pane。`CapabilityConfigService` 单向读 `ModelConfigService`（校验 uid 可用性、取全局默认），两服务之间**没有写级联**。

**Tech Stack:** Python 3.11 + FastAPI + pydantic v2 + uv/pytest；TypeScript + React 18 + Vite（strict, noUnusedLocals, noUnusedParameters）。

**Spec:** `docs/superpowers/specs/2026-09-30-agent-tool-config-design.md`

## Global Constraints

- 七层依赖方向：storage 不得 import services/adapters；services 可 import storage/services/config；interaction 只 import services；禁止反向。
- 本期智能体**只有一个**：`a1 用例设计智能体`。不得加入 测试执行/自动化编码（置灰也不行）。
- 系统提示词只读展示，随代码种子维护，**不可编辑、不落盘**。
- 工具启用禁用沿用原型级联：禁用即在**同一次落盘**里从所有智能体 `tool_ids` 摘除；重新启用**不自动补回**。
- 本配置不参与执行：`GET /api/models`、`GET /api/health`、`POST /api/chat/echo`、`POST /api/chat/send` 的行为与响应体保持不变（既有测试断言不许改语义）。
- 落盘文件 `backend/data/capability_config.json`（`backend/data/` 已在 `.gitignore`），内容不含任何密钥。
- 模型引用一律 uid 串 `providerId/modelId`；`default_uid=""` 语义为「跟随全局默认」。后端 `ModelInfo` 无 display name 字段，**前端一律展示 uid**（与现有模型弹窗一致）。
- 所有测试离线：文件一律 `tmp_path`，测试绝不写真实 `backend/data/`（模块级 `app = create_app()` 在导入时生成真实种子文件属预期，gitignore 已覆盖）。
- 后端命令均在 `backend/`：`uv run pytest -q`；前端命令在 `frontend/`：`npm run build`（tsc strict + vite）。
- UI 与错误文案中文；控件必须带文字标签（禁止纯图标按钮）；操作必须有可见结果（保存后即时刷新两个 pane）。
- 错误语义：`CapabilityConfigError`→400（detail 可直接照做、点名「设置 · 模型设置」或「工具」）、`ConfigNotFoundError`→404；本专项无上游调用，故无 502。
- 种子值的唯一权威定义在 Task 2 的 `TOOL_CATALOG` / `AGENT_CATALOG` / `DEFAULT_AGENT_STATE`，后续任务逐字引用，不得改写文案。
- 每任务结尾单独 git 提交，中文 conventional 风格；提交前 `git status` 确认无 `.env`、无 `backend/data/`。

当前基线：HEAD=`d2b25dd`，后端 45 项测试全绿，前端 build 绿。测试数预期 T1→45（纯迁移）、T2→56、T3→62、T4→69，T5–T9 不再增加后端测试；±2 以内以实际为准，但禁止失败。

---

### Task 1: storage 层泛化为通用 JSON 仓储

把与「模型」无关的文件仓储改名复用，避免出现第二份原子写实现。

**Files:**
- Rename: `backend/src/aitester/storage/model_config_repo.py` → `backend/src/aitester/storage/json_config_repo.py`
- Rename: `backend/tests/test_model_config_repo.py` → `backend/tests/test_json_config_repo.py`
- Modify: `backend/src/aitester/storage/__init__.py`
- Modify: `backend/src/aitester/services/model_config.py:7,68`
- Modify: `backend/src/aitester/main.py:9,19`
- Modify: `backend/tests/test_model_config.py:9,14,21,51,57`
- Modify: `backend/tests/test_chat_service.py:8,13`

**Interfaces:**
- Consumes: 无（仅标准库 + pathlib）。
- Produces（`aitester.storage` 包直接可导入；Task 2/3/4 依赖）：
  - `ConfigStorageError(RuntimeError)`，属性 `.detail: str`（名称不变）
  - `JsonConfigRepository` Protocol：`load() -> dict[str, Any] | None`、`save(config: dict[str, Any]) -> None`
  - `FileJsonConfigRepository(path: Path)`
  - 旧名 `ModelConfigRepository` / `FileModelConfigRepository` **全部删除，不留别名**。

- [ ] **Step 1: 先改名并把测试指向新符号，确认失败**

```bash
cd backend
git mv tests/test_model_config_repo.py tests/test_json_config_repo.py
git mv src/aitester/storage/model_config_repo.py src/aitester/storage/json_config_repo.py
```

把 `tests/test_json_config_repo.py` 内所有 `FileModelConfigRepository` 全文替换为 `FileJsonConfigRepository`（其余断言一字不动），然后：

```bash
uv run pytest -q
```

预期：`ImportError: cannot import name 'FileJsonConfigRepository'`（仓储类内部类名仍是旧的，`storage/__init__.py` 也仍导出旧名）。

- [ ] **Step 2: 改仓储实现与包导出**

`src/aitester/storage/json_config_repo.py` 全文（只有符号名与一句文案变化，逻辑与基线一致）：

```python
import json
import os
from pathlib import Path
from typing import Any, Protocol


class ConfigStorageError(RuntimeError):
    """配置 JSON 文件读写失败，detail 面向用户且含文件路径。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class JsonConfigRepository(Protocol):
    """运行期配置 JSON 持久化抽象。"""

    def load(self) -> dict[str, Any] | None: ...

    def save(self, config: dict[str, Any]) -> None: ...


class FileJsonConfigRepository:
    """单 JSON 文件实现：同目录临时文件 + os.replace 原子替换。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigStorageError(f"配置文件已损坏：{self._path}（{exc}）") from exc

    def save(self, config: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)
```

`src/aitester/storage/__init__.py` 全文：

```python
"""存储层：实体持久化抽象。"""
from aitester.storage.base import Repository
from aitester.storage.in_memory import InMemoryRepository
from aitester.storage.json_config_repo import (
    ConfigStorageError,
    FileJsonConfigRepository,
    JsonConfigRepository,
)

__all__ = [
    "Repository",
    "InMemoryRepository",
    "ConfigStorageError",
    "FileJsonConfigRepository",
    "JsonConfigRepository",
]
```

- [ ] **Step 3: 改全部引用点**

- `services/model_config.py`：第 7 行改为 `from aitester.storage import JsonConfigRepository`；构造签名改 `def __init__(self, repo: JsonConfigRepository, settings: Settings) -> None:`。
- `main.py`：第 9 行改 `from aitester.storage import FileJsonConfigRepository`；第 19 行改 `ModelConfigService(FileJsonConfigRepository(path), s)`。
- `tests/test_model_config.py`（第 9 行导入 + 第 14/21/51/57 行四处构造）与 `tests/test_chat_service.py`（第 8 行导入 + 第 13 行构造）：符号名换成 `FileJsonConfigRepository`，路径参数一字不改。
- `tests/test_json_config_repo.py` 若断言损坏文案含「模型配置文件已损坏」，同步改为「配置文件已损坏」（与 Step 2 同一份 detail）。

- [ ] **Step 4: 确认全绿且无残留**

```bash
uv run pytest -q
grep -rn "ModelConfigRepository" src tests | grep -v __pycache__
```

预期：45 passed；grep 无输出（旧名彻底消失；`ModelConfigService` 不算）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/aitester/storage backend/src/aitester/services backend/src/aitester/main.py backend/tests
git commit -m "refactor(storage): 模型配置仓储泛化为通用 JSON 仓储，供能力配置复用"
```

---

### Task 2: CapabilityConfigService——种子、视图与智能体默认模型

**Files:**
- Create: `backend/src/aitester/services/capability_config.py`
- Create: `backend/tests/test_capability_config.py`
- Modify: `backend/src/aitester/services/__init__.py`
- Modify: `backend/src/aitester/services/model_config.py`（新增 `is_usable_uid`）
- Modify: `backend/tests/test_model_config.py`（追加 1 个测试）

**Interfaces:**
- Consumes: `aitester.storage.JsonConfigRepository`；`ModelConfigService.is_usable_uid(uid: str) -> bool`（本任务新增，内部即 `return self._uid_usable(uid)`）、`ModelConfigService.default_uid`（已有 property）、`ModelConfigService.update_api_key/set_model_enabled`（仅测试用于制造不可用态）。
- Produces（`aitester.services.capability_config` 可导入；Task 3 往同类补方法，Task 4 消费）：
  - `TOOL_CATALOG: list[dict[str, Any]]`，键：`id, group, icon, label, os, desc`
  - `AGENT_CATALOG: list[dict[str, Any]]`，键：`id, icon, name, desc, prompt`
  - `CapabilityConfigError(RuntimeError)`，属性 `.detail: str`
  - `CapabilityConfigService(repo: JsonConfigRepository, model_config: ModelConfigService)`
    - `get_view() -> dict[str, Any]` → `{"tools": [ToolView…], "agents": [AgentView…]}`
    - `set_agent_default_model(agent_id: str, uid: str) -> None`
  - ToolView 键：`id, group, icon, label, os, desc, enabled, carried_by`（`carried_by: list[str]` 为智能体 id）
  - AgentView 键：`id, icon, name, desc, prompt, default_uid, effective_uid, tool_ids`

- [ ] **Step 1: 给 ModelConfigService 开只读校验口**

在 `services/model_config.py` 的 `_uid_usable` 方法之后插入：

```python
    def is_usable_uid(self, uid: str) -> bool:
        """供能力配置读取：该 uid 是否「模型已启用 + 提供商已配 Key」。"""
        return self._uid_usable(uid)
```

在 `tests/test_model_config.py` 末尾追加：

```python
def test_is_usable_uid_rules(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert svc.is_usable_uid("deepseek/deepseek-flash") is True
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    assert svc.is_usable_uid("deepseek/deepseek-flash") is False
    assert svc.is_usable_uid("dashscope/qwen3.7-max") is False  # 未配 Key
    assert svc.is_usable_uid("deepseek/nope") is False
    assert svc.is_usable_uid("garbage") is False
```

- [ ] **Step 2: 写失败测试**

创建 `backend/tests/test_capability_config.py`：

```python
from pathlib import Path

import pytest

from aitester.config import Settings
from aitester.services.capability_config import (
    AGENT_CATALOG,
    TOOL_CATALOG,
    CapabilityConfigError,
    CapabilityConfigService,
)
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _svc(
    tmp_path: Path, **settings_kwargs: object
) -> tuple[CapabilityConfigService, ModelConfigService]:
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return capability, model_config


def _stored(tmp_path: Path) -> dict[str, object]:
    loaded = FileJsonConfigRepository(tmp_path / "capability_config.json").load()
    assert loaded is not None
    return loaded


def test_first_start_seeds_and_persists(tmp_path: Path) -> None:
    _svc(tmp_path)
    stored = _stored(tmp_path)
    assert stored["version"] == 1
    assert stored["tool_state"] == {
        "read": True,
        "write": True,
        "edit": True,
        "pwsh": True,
        "bash": False,
        "web_search": True,
    }
    assert stored["agents"] == {
        "a1": {"default_uid": "", "tool_ids": ["read", "write", "edit", "web_search"]}
    }


def test_existing_file_is_not_reseeded(tmp_path: Path) -> None:
    saved = {
        "version": 1,
        "tool_state": {
            "read": False,
            "write": True,
            "edit": True,
            "pwsh": True,
            "bash": True,
            "web_search": False,
        },
        "agents": {
            "a1": {"default_uid": "deepseek/deepseek-flash", "tool_ids": ["write"]}
        },
    }
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(saved)
    capability, _ = _svc(tmp_path)
    assert _stored(tmp_path) == saved
    agent = capability.get_view()["agents"][0]
    assert agent["default_uid"] == "deepseek/deepseek-flash"
    assert agent["tool_ids"] == ["write"]


def test_catalog_seeds_exactly_one_agent_and_six_tools() -> None:
    assert [a["id"] for a in AGENT_CATALOG] == ["a1"]
    assert AGENT_CATALOG[0]["name"] == "用例设计智能体"
    assert [t["id"] for t in TOOL_CATALOG] == [
        "read",
        "write",
        "edit",
        "pwsh",
        "bash",
        "web_search",
    ]


def test_get_view_tools_shape_and_carried_by(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    view = capability.get_view()
    assert [t["id"] for t in view["tools"]] == [t["id"] for t in TOOL_CATALOG]
    read = view["tools"][0]
    assert read["group"] == "文件处理工具"
    assert read["enabled"] is True
    assert read["carried_by"] == ["a1"]
    bash = next(t for t in view["tools"] if t["id"] == "bash")
    assert bash["enabled"] is False
    assert bash["os"] == "macOS"
    assert bash["carried_by"] == []


def test_get_view_agent_carries_readonly_prompt(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    agent = capability.get_view()["agents"][0]
    assert agent["id"] == "a1"
    assert agent["icon"] == "📋"
    assert "## 职责" in agent["prompt"]
    assert agent["default_uid"] == ""
    assert agent["effective_uid"] == ""
    assert agent["tool_ids"] == ["read", "write", "edit", "web_search"]


def test_effective_uid_follows_global_default_when_unset(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert model_config.default_uid == "deepseek/deepseek-flash"
    assert capability.get_view()["agents"][0]["effective_uid"] == "deepseek/deepseek-flash"


def test_effective_uid_prefers_agent_default(tmp_path: Path) -> None:
    capability, model_config = _svc(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("a1", "dashscope/qwen3.7-max")
    agent = capability.get_view()["agents"][0]
    assert agent["default_uid"] == "dashscope/qwen3.7-max"
    assert agent["effective_uid"] == "dashscope/qwen3.7-max"


def test_effective_uid_falls_back_when_agent_default_becomes_unusable(tmp_path: Path) -> None:
    capability, model_config = _svc(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("a1", "dashscope/qwen3.7-max")
    model_config.set_model_enabled("dashscope", "qwen3.7-max", False)
    agent = capability.get_view()["agents"][0]
    # 配置不自愈：default_uid 原样保留，只在读取时回落全局默认
    assert agent["default_uid"] == "dashscope/qwen3.7-max"
    assert agent["effective_uid"] == "deepseek/deepseek-flash"


def test_effective_uid_empty_when_nothing_configured(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    assert capability.get_view()["agents"][0]["effective_uid"] == ""


def test_set_agent_default_model_empty_and_valid_persist(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    capability.set_agent_default_model("a1", "deepseek/deepseek-v4-pro")
    assert _stored(tmp_path)["agents"]["a1"]["default_uid"] == "deepseek/deepseek-v4-pro"
    capability.set_agent_default_model("a1", "")
    assert _stored(tmp_path)["agents"]["a1"]["default_uid"] == ""


def test_set_agent_default_model_rejects_unusable(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    model_config.set_model_enabled("deepseek", "deepseek-flash", False)
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_agent_default_model("a1", "deepseek/deepseek-flash")
    assert "设置 · 模型设置" in exc_info.value.detail
    with pytest.raises(CapabilityConfigError):
        capability.set_agent_default_model("a1", "dashscope/qwen3.7-max")  # 未配 Key
    with pytest.raises(CapabilityConfigError):
        capability.set_agent_default_model("a1", "deepseek/nope")  # 不存在
    assert _stored(tmp_path)["agents"]["a1"]["default_uid"] == ""


def test_set_agent_default_model_unknown_agent_raises_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_default_model("a9", "")
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run pytest tests/test_capability_config.py -q
```

预期：`ModuleNotFoundError: No module named 'aitester.services.capability_config'`。

- [ ] **Step 4: 写实现**

创建 `backend/src/aitester/services/capability_config.py`（Task 3 往同一个类追加两个方法）：

```python
"""运行期能力配置：智能体默认模型与工具启用状态，JSON 落盘为唯一真相。"""
import copy
from typing import Any

from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import JsonConfigRepository

TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "read",
        "group": "文件处理工具",
        "icon": "📖",
        "label": "read",
        "os": "全平台",
        "desc": "读取项目与知识库中的文本文件，返回带行号的原文；改动前用它确认现状。",
    },
    {
        "id": "write",
        "group": "文件处理工具",
        "icon": "✍️",
        "label": "write",
        "os": "全平台",
        "desc": "整文件写入：新建文件，或用新内容整体覆盖旧文件。写知识库真实文件前必须先出草案确认。",
    },
    {
        "id": "edit",
        "group": "文件处理工具",
        "icon": "✏️",
        "label": "edit",
        "os": "全平台",
        "desc": "按精确字符串定位替换，只做小范围改动；目标不唯一或未命中即失败，避免误伤无关代码。",
    },
    {
        "id": "pwsh",
        "group": "命令执行工具",
        "icon": "🖥",
        "label": "pwsh",
        "os": "Windows",
        "desc": "执行 PowerShell：跑 pytest / npm test、装依赖、调用 CLI，回收 stdout、stderr 与退出码。",
    },
    {
        "id": "bash",
        "group": "命令执行工具",
        "icon": "🐚",
        "label": "bash",
        "os": "macOS",
        "desc": "在 macOS / Linux 上执行 Bash，用途与 pwsh 相同。当前运行在 Windows，默认禁用。",
    },
    {
        "id": "web_search",
        "group": "网页搜索工具",
        "icon": "🔎",
        "label": "web_search",
        "os": "全平台",
        "desc": "按关键词搜索公网，返回标题、链接与摘要，用于查官方文档、错误码与版本变更说明。",
    },
]

AGENT_CATALOG: list[dict[str, Any]] = [
    {
        "id": "a1",
        "icon": "📋",
        "name": "用例设计智能体",
        "desc": "读需求与接口文档，产出可直接执行的测试用例并同步用例平台，覆盖等价类、边界值与异常路径。",
        "prompt": """你是「用例设计智能体」，服务对象是软件测试工程师。

## 职责
- 依据需求说明、接口文档与存量用例，设计功能 / 接口 / 回归测试用例
- 用等价类划分、边界值、状态迁移、异常注入保证覆盖，并标注 P0 / P1 / P2
- 产出统一用例表：编号、需求号、前置条件、步骤、预期结果、优先级

## 约束
- 项目文件只读，新增文件一律落在 cases/ 下，不改动生产配置
- 需求信息不足时先列出待澄清问题，不臆造验收标准
- 每条用例必须能被测试执行智能体直接跑：步骤可操作、预期结果可判定
- 全程使用中文与 Markdown 表格，不省略步骤""",
    },
]

DEFAULT_TOOL_STATE: dict[str, bool] = {t["id"]: t["id"] != "bash" for t in TOOL_CATALOG}
DEFAULT_AGENT_STATE: dict[str, dict[str, Any]] = {
    "a1": {"default_uid": "", "tool_ids": ["read", "write", "edit", "web_search"]}
}


class CapabilityConfigError(RuntimeError):
    """配置类校验失败，交互层映射 400，detail 面向用户且可照做。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "tool_state": dict(DEFAULT_TOOL_STATE),
        "agents": copy.deepcopy(DEFAULT_AGENT_STATE),
    }


class CapabilityConfigService:
    """能力配置真相：构造时 load（缺则种子并落盘），每次变更立即 save。"""

    def __init__(self, repo: JsonConfigRepository, model_config: ModelConfigService) -> None:
        self._repo = repo
        self._model_config = model_config
        config = repo.load()
        if config is None:
            config = _default_config()
            repo.save(config)
        self._config = config

    def _tool(self, tool_id: str) -> dict[str, Any]:
        for t in TOOL_CATALOG:
            if t["id"] == tool_id:
                return t
        raise ConfigNotFoundError(f"未知工具「{tool_id}」")

    def _agent_state(self, agent_id: str) -> dict[str, Any]:
        agents = self._config["agents"]
        if agent_id not in agents:
            raise ConfigNotFoundError(f"未知智能体「{agent_id}」")
        return agents[agent_id]

    def _enabled(self, tool_id: str) -> bool:
        return bool(self._config["tool_state"][tool_id])

    def get_view(self) -> dict[str, Any]:
        agents_state = self._config["agents"]
        tools = [
            {
                "id": t["id"],
                "group": t["group"],
                "icon": t["icon"],
                "label": t["label"],
                "os": t["os"],
                "desc": t["desc"],
                "enabled": self._enabled(t["id"]),
                "carried_by": [
                    aid
                    for aid, state in agents_state.items()
                    if t["id"] in state["tool_ids"]
                ],
            }
            for t in TOOL_CATALOG
        ]
        views: list[dict[str, Any]] = []
        for seed in AGENT_CATALOG:
            state = agents_state[seed["id"]]
            default_uid = state["default_uid"]
            if default_uid and self._model_config.is_usable_uid(default_uid):
                effective_uid = default_uid
            else:
                effective_uid = self._model_config.default_uid
            views.append(
                {
                    **seed,
                    "default_uid": default_uid,
                    "effective_uid": effective_uid,
                    "tool_ids": list(state["tool_ids"]),
                }
            )
        return {"tools": tools, "agents": views}

    def set_agent_default_model(self, agent_id: str, uid: str) -> None:
        state = self._agent_state(agent_id)
        value = uid.strip()
        if value and not self._model_config.is_usable_uid(value):
            raise CapabilityConfigError(
                f"模型「{value}」当前不可用（未启用或提供商未配置 API Key）："
                "请在 设置 · 模型设置 处理，或改选「跟随全局默认」"
            )
        state["default_uid"] = value
        self._repo.save(self._config)
```

`services/__init__.py` 全文：

```python
"""服务层：业务门面。"""
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.chat import ChatService
from aitester.services.model_config import ModelConfigService

__all__ = ["CapabilityConfigService", "ChatService", "ModelConfigService"]
```

- [ ] **Step 5: 运行确认通过**

```bash
uv run pytest -q
```

预期：56 passed。

- [ ] **Step 6: 提交**

```bash
git add backend/src/aitester/services backend/tests/test_capability_config.py backend/tests/test_model_config.py
git commit -m "feat(services): 能力配置服务种子与视图——单智能体注册表、默认模型校验、回落全局默认"
```

---

### Task 3: CapabilityConfigService——携带工具与工具启用级联

**Files:**
- Modify: `backend/src/aitester/services/capability_config.py`（在 `set_agent_default_model` 之后追加两个方法）
- Modify: `backend/tests/test_capability_config.py`（追加测试）

**Interfaces:**
- Consumes: Task 2 的 `_tool` / `_agent_state` / `_enabled` / `self._config` / `self._repo`、`ConfigNotFoundError`、`CapabilityConfigError`。
- Produces（Task 4 消费）：
  - `set_agent_tools(agent_id: str, tool_ids: list[str]) -> None`
  - `set_tool_enabled(tool_id: str, enabled: bool) -> None`（返回 `None`，视图由调用方 `get_view()` 取）

- [ ] **Step 1: 写失败测试**

在 `tests/test_capability_config.py` 末尾追加（沿用已有 `_svc` / `_stored`）：

```python
def test_set_agent_tools_keeps_order_and_dedups(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_agent_tools("a1", ["edit", "read", "read", "pwsh"])
    assert _stored(tmp_path)["agents"]["a1"]["tool_ids"] == ["edit", "read", "pwsh"]
    view = capability.get_view()
    assert view["agents"][0]["tool_ids"] == ["edit", "read", "pwsh"]
    assert next(t for t in view["tools"] if t["id"] == "pwsh")["carried_by"] == ["a1"]


def test_set_agent_tools_empty_list_is_allowed(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_agent_tools("a1", [])
    assert _stored(tmp_path)["agents"]["a1"]["tool_ids"] == []
    assert all(t["carried_by"] == [] for t in capability.get_view()["tools"])


def test_set_agent_tools_rejects_disabled_tool(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(CapabilityConfigError) as exc_info:
        capability.set_agent_tools("a1", ["read", "bash"])
    assert "bash" in exc_info.value.detail
    assert "工具" in exc_info.value.detail
    assert _stored(tmp_path)["agents"]["a1"]["tool_ids"] == [
        "read",
        "write",
        "edit",
        "web_search",
    ]


def test_set_agent_tools_unknown_ids_raise_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("a1", ["nope"])
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("a9", ["read"])


def test_disable_tool_strips_every_agent(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_tool_enabled("read", False)
    view = capability.get_view()
    read = next(t for t in view["tools"] if t["id"] == "read")
    assert read["enabled"] is False
    assert read["carried_by"] == []
    assert "read" not in view["agents"][0]["tool_ids"]
    stored = _stored(tmp_path)
    assert stored["tool_state"]["read"] is False
    assert stored["agents"]["a1"]["tool_ids"] == ["write", "edit", "web_search"]


def test_reenable_tool_does_not_restore_carriers(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    capability.set_tool_enabled("read", False)
    capability.set_tool_enabled("read", True)
    stored = _stored(tmp_path)
    assert stored["tool_state"]["read"] is True
    assert "read" not in stored["agents"]["a1"]["tool_ids"]
    read = next(t for t in capability.get_view()["tools"] if t["id"] == "read")
    assert read["enabled"] is True and read["carried_by"] == []


def test_set_tool_enabled_unknown_tool_raises_404_error(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        capability.set_tool_enabled("nope", False)
    with pytest.raises(ConfigNotFoundError):
        capability.set_agent_tools("a1", ["nope"])
    assert capability.set_tool_enabled("bash", True) is None
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_capability_config.py -q
```

预期：`AttributeError: 'CapabilityConfigService' object has no attribute 'set_agent_tools'`。

- [ ] **Step 3: 追加实现**

```python
    def set_agent_tools(self, agent_id: str, tool_ids: list[str]) -> None:
        state = self._agent_state(agent_id)
        unique: list[str] = []
        for tool_id in tool_ids:
            self._tool(tool_id)
            if tool_id not in unique:
                unique.append(tool_id)
        disabled = [t for t in unique if not self._enabled(t)]
        if disabled:
            raise CapabilityConfigError(
                f"工具「{'、'.join(disabled)}」已禁用：请先在 设置 · 工具 中启用后再携带"
            )
        state["tool_ids"] = unique
        self._repo.save(self._config)

    def set_tool_enabled(self, tool_id: str, enabled: bool) -> None:
        self._tool(tool_id)
        self._config["tool_state"][tool_id] = enabled
        if not enabled:
            for state in self._config["agents"].values():
                if tool_id in state["tool_ids"]:
                    state["tool_ids"] = [t for t in state["tool_ids"] if t != tool_id]
        self._repo.save(self._config)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest -q
```

预期：62 passed。

- [ ] **Step 5: 提交**

```bash
git add backend/src/aitester/services/capability_config.py backend/tests/test_capability_config.py
git commit -m "feat(services): 能力配置携带工具与工具启停——禁用级联摘除、重新启用不自动补回"
```

---

### Task 4: /api/capabilities 四端点与装配

**Files:**
- Modify: `backend/src/aitester/interaction/schemas.py`（追加 6 个模型）
- Modify: `backend/src/aitester/interaction/router.py`（追加 4 个端点 + import）
- Modify: `backend/src/aitester/main.py`（新增 `capability_config_path` 参数与 `app.state.capability_config`）
- Modify: `backend/tests/test_api.py`（`_isolated_client` 与三处 `create_app` 补能力路径）
- Create: `backend/tests/test_api_capabilities.py`

**Interfaces:**
- Consumes: Task 2/3 的 `CapabilityConfigService.get_view / set_agent_default_model / set_agent_tools / set_tool_enabled`；`CapabilityConfigError`、`ConfigNotFoundError`。
- Produces：
  - `GET /api/capabilities`、`PUT /api/capabilities/agents/{aid}/default-model`、`PUT /api/capabilities/agents/{aid}/tools`、`PUT /api/capabilities/tools/{tid}/enabled`——四者响应体都是 `CapabilityResponse`（`{tools: ToolInfo[], agents: AgentInfo[]}`）。
  - `create_app(model_config_path: Path | None = None, capability_config_path: Path | None = None, settings: Settings | None = None) -> FastAPI`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_api_capabilities.py`：

```python
from pathlib import Path

from fastapi.testclient import TestClient

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
        "pwsh",
        "bash",
        "web_search",
    ]
    assert [a["id"] for a in body["agents"]] == ["a1"]
    agent = body["agents"][0]
    assert agent["name"] == "用例设计智能体"
    assert agent["default_uid"] == "" and agent["effective_uid"] == ""
    assert agent["tool_ids"] == ["read", "write", "edit", "web_search"]


def test_agent_default_model_endpoints(tmp_path: Path) -> None:
    c = _client(tmp_path, deepseek_api_key="sk-x123456789")
    bad = c.put(
        "/api/capabilities/agents/a1/default-model", json={"uid": "dashscope/qwen3.7-max"}
    )
    assert bad.status_code == 400
    assert "设置 · 模型设置" in bad.json()["detail"]
    ok = c.put(
        "/api/capabilities/agents/a1/default-model",
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
    assert c.put("/api/capabilities/agents/a1/default-model", json={"uid": ""}).status_code == 200
    assert c.put("/api/capabilities/agents/a9/default-model", json={"uid": ""}).status_code == 404


def test_agent_tools_endpoint(tmp_path: Path) -> None:
    c = _client(tmp_path)
    bad = c.put("/api/capabilities/agents/a1/tools", json={"tool_ids": ["read", "bash"]})
    assert bad.status_code == 400 and "工具" in bad.json()["detail"]
    ok = c.put("/api/capabilities/agents/a1/tools", json={"tool_ids": ["read", "pwsh"]})
    assert ok.status_code == 200
    assert ok.json()["agents"][0]["tool_ids"] == ["read", "pwsh"]
    assert c.put("/api/capabilities/agents/a1/tools", json={"tool_ids": ["nope"]}).status_code == 404


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
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_api_capabilities.py -q
```

预期：`TypeError: create_app() got an unexpected keyword argument 'capability_config_path'`。

- [ ] **Step 3: 加 schemas**

在 `interaction/schemas.py` 末尾追加（`BaseModel` 已在文件顶部导入）：

```python
class ToolInfo(BaseModel):
    id: str
    group: str
    icon: str
    label: str
    os: str
    desc: str
    enabled: bool
    carried_by: list[str]


class AgentInfo(BaseModel):
    id: str
    icon: str
    name: str
    desc: str
    prompt: str
    default_uid: str
    effective_uid: str
    tool_ids: list[str]


class CapabilityResponse(BaseModel):
    tools: list[ToolInfo]
    agents: list[AgentInfo]


class AgentDefaultUpdate(BaseModel):
    uid: str


class AgentToolsUpdate(BaseModel):
    tool_ids: list[str] = []


class ToolEnabledUpdate(BaseModel):
    enabled: bool
```

- [ ] **Step 4: 加 router**

`interaction/router.py` 的 `from aitester.interaction.schemas import (...)` 块内补 `AgentDefaultUpdate`、`AgentToolsUpdate`、`CapabilityResponse`、`ToolEnabledUpdate` 四个名字；并把 services 导入两行改为：

```python
from aitester.services import ChatService
from aitester.services.capability_config import CapabilityConfigError, CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
```

在文件末尾追加：

```python
def _cap_view(request: Request) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    return CapabilityResponse(**capability.get_view())


@router.get("/capabilities", response_model=CapabilityResponse)
def capabilities(request: Request) -> CapabilityResponse:
    return _cap_view(request)


@router.put("/capabilities/agents/{aid}/default-model", response_model=CapabilityResponse)
def capabilities_agent_default_model(
    aid: str, req: AgentDefaultUpdate, request: Request
) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    try:
        capability.set_agent_default_model(aid, req.uid)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except CapabilityConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return _cap_view(request)


@router.put("/capabilities/agents/{aid}/tools", response_model=CapabilityResponse)
def capabilities_agent_tools(
    aid: str, req: AgentToolsUpdate, request: Request
) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    try:
        capability.set_agent_tools(aid, req.tool_ids)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except CapabilityConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return _cap_view(request)


@router.put("/capabilities/tools/{tid}/enabled", response_model=CapabilityResponse)
def capabilities_tool_enabled(
    tid: str, req: ToolEnabledUpdate, request: Request
) -> CapabilityResponse:
    capability: CapabilityConfigService = request.app.state.capability_config
    try:
        capability.set_tool_enabled(tid, req.enabled)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return _cap_view(request)
```

- [ ] **Step 5: 加装配**

`main.py` 全文（基线常量 `DEFAULT_MODEL_CONFIG_PATH` 由 `DATA_DIR` 取代，两份配置同目录；`FastAPI(title="AiTester backend")` 与模块级 `app = create_app()` 保持基线不变）：

```python
from pathlib import Path

from fastapi import FastAPI

from aitester.config import Settings, get_settings
from aitester.interaction.router import router
from aitester.services import CapabilityConfigService, ChatService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileJsonConfigRepository

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def create_app(
    model_config_path: Path | None = None,
    capability_config_path: Path | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    s = settings or get_settings()
    model_config = ModelConfigService(
        FileJsonConfigRepository(model_config_path or DATA_DIR / "model_config.json"), s
    )
    capability_config = CapabilityConfigService(
        FileJsonConfigRepository(
            capability_config_path or DATA_DIR / "capability_config.json"
        ),
        model_config,
    )
    application = FastAPI(title="AiTester backend")
    application.state.model_config = model_config
    application.state.capability_config = capability_config
    application.state.chat_service = ChatService(model_config=model_config)
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("aitester.main:app", host=s.host, port=s.port)
```

`tests/test_api.py` 改动（共 4 处，其余断言一字不动）：

```python
def _isolated_client(
    tmp_path: Path,
    name: str = "model_config.json",
    cap_name: str = "capability_config.json",
) -> TestClient:
    application = create_app(
        model_config_path=tmp_path / name,
        capability_config_path=tmp_path / cap_name,
        settings=Settings(_env_file=None),
    )
    return TestClient(application)
```

`test_send_uses_injected_provider_and_reports_model`（约 59 行）、`test_send_upstream_failure_returns_502`（约 87 行）各加 `capability_config_path=tmp_path / "c.cap.json"`；`test_chat_service_is_per_app_instance`（约 97–98 行）两行各加 `capability_config_path=tmp_path / "a.cap.json"` / `"b.cap.json"`。

- [ ] **Step 6: 运行确认全绿**

```bash
uv run pytest -q
git status --short
```

预期：69 passed；`git status --short` 不出现 `backend/data/` 内任何文件。

- [ ] **Step 7: 提交**

```bash
git add backend/src/aitester/interaction backend/src/aitester/main.py backend/tests
git commit -m "feat(interaction): /api/capabilities 四端点——能力视图、默认模型、携带工具与工具启停"
```

---

### Task 5: 前端 API 客户端——能力类型与四端点封装

**Files:**
- Modify: `frontend/src/api/client.ts`（在 `ModelsResponse` 之后加类型；文件末尾加四个函数）

**Interfaces:**
- Consumes: Task 4 的四个端点。
- Produces（Task 6–8 消费）：`ToolInfo`、`AgentInfo`、`CapabilityResponse`、`getCapabilities(): Promise<CapabilityResponse>`、`putAgentDefaultModel(agentId: string, uid: string)`、`putAgentTools(agentId: string, toolIds: string[])`、`putToolEnabled(toolId: string, enabled: boolean)`——三个写函数均返回 `Promise<CapabilityResponse>`。

- [ ] **Step 1: 加类型**

紧跟现有 `export interface ModelsResponse {…}` 之后插入：

```ts
export interface ToolInfo {
  id: string;
  group: string;
  icon: string;
  label: string;
  os: string;
  desc: string;
  enabled: boolean;
  carried_by: string[];
}

export interface AgentInfo {
  id: string;
  icon: string;
  name: string;
  desc: string;
  prompt: string;
  default_uid: string;
  effective_uid: string;
  tool_ids: string[];
}

export interface CapabilityResponse {
  tools: ToolInfo[];
  agents: AgentInfo[];
}
```

- [ ] **Step 2: 加四个封装**

在文件末尾（现有 `putDefault` 之后）追加，复用同文件已有的 `apiFetch` 与 `JSON_HEADERS`：

```ts
export function getCapabilities(): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>("/api/capabilities");
}

export function putAgentDefaultModel(
  agentId: string,
  uid: string,
): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>(`/api/capabilities/agents/${agentId}/default-model`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ uid }),
  });
}

export function putAgentTools(
  agentId: string,
  toolIds: string[],
): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>(`/api/capabilities/agents/${agentId}/tools`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ tool_ids: toolIds }),
  });
}

export function putToolEnabled(
  toolId: string,
  enabled: boolean,
): Promise<CapabilityResponse> {
  return apiFetch<CapabilityResponse>(`/api/capabilities/tools/${toolId}/enabled`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ enabled }),
  });
}
```

- [ ] **Step 3: 验证并提交**

```bash
cd frontend && npm run build
```

预期：tsc + vite 通过（本任务只新增导出，无消费方，`noUnusedLocals` 不适用于导出符号）。

```bash
git add frontend/src/api/client.ts
git commit -m "feat(frontend): client 能力配置类型与 /api/capabilities 四端点封装"
```

---

### Task 6: 设置弹窗拆为「外壳 + 三 tab」，模型 pane 原样迁移

三个 pane 同时挂载、用 `hidden` 切换，切 tab 不重置面板内部选择状态；本任务 agent/tool 两个 pane 先渲染空容器（外壳默认停在模型 tab，用户看不到任何空白页）。

**Files:**
- Create: `frontend/src/components/settings/SettingsModal.tsx`（新外壳）
- Create: `frontend/src/components/settings/ModelPane.tsx`
- Delete: `frontend/src/components/SettingsModal.tsx`
- Modify: `frontend/src/App.tsx:4`（import 路径）
- Modify: `frontend/src/App.css`（`.modal` 高度 + tab 条 + pane 共用样式）

**Interfaces:**
- Consumes: 既有 `getModels`、`putApiKey`、`putDefault`、`putModelEnabled`、`ModelsResponse`；Task 5 的 `getCapabilities`、`CapabilityResponse`。
- Produces（Task 7/8 消费）：
  - `ModelPane` props：`{ models: ModelsResponse; saving: boolean; onAction: (action: () => Promise<ModelsResponse>) => void }`
  - 外壳内部：`type SettingsTab = "model" | "agent" | "tool"`、`runCaps(action: () => Promise<CapabilityResponse>): Promise<void>`（与 `runModels` 同构，成功后 `setCaps(await action())` 并 `onChanged()`）。

- [ ] **Step 1: 建 ModelPane（把基线模型 UI 整段搬过去）**

打开基线 `frontend/src/components/SettingsModal.tsx`，把 **第 60–172 行**（即 `.modal-body` 内部、第 59 行与第 173 行两个 `modal-body` 标签之间的 `.provider-list` 与 `.provider-form` 两个兄弟节点；`modal-mask`/`modal`/`modal-head`/错误行/`加载中…` 分支一律不搬）**逐字复制**进新文件 `frontend/src/components/settings/ModelPane.tsx`，外面包一层 `<>…</>`；复制后文件里不得留下任何注释形式的占位节点。然后只做下面四处改动，其余（含每个 className、每个 placeholder 文案、每个 `disabled` 表达式）**一字不改**：

```tsx
import { useState } from "react";
import {
  putApiKey,
  putDefault,
  putModelEnabled,
  type ModelsResponse,
} from "../../api/client";

interface ModelPaneProps {
  models: ModelsResponse;
  saving: boolean;
  onAction: (action: () => Promise<ModelsResponse>) => void;
}

export default function ModelPane({ models, saving, onAction }: ModelPaneProps) {
  const [selectedId, setSelectedId] = useState(models.providers[0].id);
  const [keyInput, setKeyInput] = useState("");
  const selected =
    models.providers.find((p) => p.id === selectedId) ?? models.providers[0];

  return (
    <>
      {/* 此处放上面逐字复制来的 .provider-list 与 .provider-form 两个兄弟节点，内容一字不改 */}
    </>
  );
}
```

四处改动清单：
1. `models`、`saving`、`setError`、`setModels` 相关的本地状态删掉，改由 props 提供；`run(...)` 调用点整体换成 `onAction(...)`（签名一致，无需 async 包装）。
2. `selected` 由 `models?.providers.find(...) ?? null` 改为上面的写法（不再可能为 `null`，因此 JSX 里 `selected !== null &&` 外层条件删掉，其余表达式不变）。
3. 「保存 Key」按钮的 `onClick` 保持基线形态：`onAction(async () => { const resp = await putApiKey(selected.id, keyInput); setKeyInput(""); return resp; })`。
4. 保留基线全部规则：提供商项 `● Key 已配置 / ○ Key 未配置`；点项同时 `setSelectedId(p.id); setKeyInput("")`；Base URL `readOnly`；Key 输入 `type="password"` + 已配置时 placeholder `当前 ${selected.key_masked}（已配置，留空则不变）`；`disabled={keyInput === "" || saving}`；已配置时的「清除 Key」按钮（`onAction(() => putApiKey(selected.id, ""))`）；默认 LLM select 列出全部 uid、`!usable` 项 `disabled` 且追加 ` · 未配 Key` / ` · 已停用`；模型表四列（模型 ID｜最大上下文｜最大输出｜启用 checkbox）。

- [ ] **Step 2: 建外壳**

`frontend/src/components/settings/SettingsModal.tsx` 全文：

```tsx
import { useCallback, useEffect, useState } from "react";
import {
  getCapabilities,
  getModels,
  type CapabilityResponse,
  type ModelsResponse,
} from "../../api/client";
import ModelPane from "./ModelPane";

type SettingsTab = "model" | "agent" | "tool";

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "model", label: "🧠 模型设置" },
  { id: "agent", label: "🤖 智能体配置" },
  { id: "tool", label: "🛠 工具" },
];

interface SettingsModalProps {
  onClose: () => void;
  onChanged: () => void;
}

export default function SettingsModal({ onClose, onChanged }: SettingsModalProps) {
  const [tab, setTab] = useState<SettingsTab>("model");
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [caps, setCaps] = useState<CapabilityResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([getModels(), getCapabilities()])
      .then(([m, c]) => {
        setModels(m);
        setCaps(c);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const runModels = useCallback(
    async (action: () => Promise<ModelsResponse>): Promise<void> => {
      if (saving) return;
      setSaving(true);
      setError(null);
      try {
        setModels(await action());
        // 模型停用/清 Key 会让智能体默认模型回落，视图需同步
        setCaps(await getCapabilities());
        onChanged();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setSaving(false);
      }
    },
    [saving, onChanged],
  );

  const runCaps = useCallback(
    async (action: () => Promise<CapabilityResponse>): Promise<void> => {
      if (saving) return;
      setSaving(true);
      setError(null);
      try {
        setCaps(await action());
        onChanged();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setSaving(false);
      }
    },
    [saving, onChanged],
  );

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>⚙ 设置</strong>
          <button className="btn-secondary" onClick={onClose}>关闭</button>
        </div>
        {error !== null && <p className="modal-error">{error}</p>}
        <div className="s-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={t.id === tab ? "s-tab active" : "s-tab"}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {models === null || caps === null ? (
          <p>{error === null ? "加载中…" : "配置加载失败，请关闭后重试"}</p>
        ) : (
          <>
            <div className="modal-body" hidden={tab !== "model"}>
              <ModelPane models={models} saving={saving} onAction={runModels} />
            </div>
            <div className="modal-body" hidden={tab !== "agent"} />
            <div className="modal-body" hidden={tab !== "tool"} />
          </>
        )}
      </div>
    </div>
  );
}
```

（最后三个 `modal-body` 里，第二个在 Task 7 变成 `<AgentPane … />`、第三个在 Task 8 变成 `<ToolPane … />`。）

- [ ] **Step 3: 删除旧文件、改 import、加样式**

```bash
cd frontend && git rm src/components/SettingsModal.tsx
```

`App.tsx` 第 4 行改为：`import SettingsModal from "./components/settings/SettingsModal";`（其余不动）。

`App.css`：把 `.modal` 规则里的 `height: 560px` 改为 `height: 620px`（切 tab 不跳高），并追加：

```css
.s-tabs { display: flex; gap: 4px; border-bottom: 1px solid #e8ddcc; margin-bottom: 12px; }
.s-tab { padding: 8px 14px; border: none; border-radius: 8px 8px 0 0; background: none; color: #6b5d4f; font-size: 13px; cursor: pointer; }
.s-tab.active { background: #f4e3cf; color: #b35a1b; font-weight: 600; }
.pane-list { width: 210px; display: flex; flex-direction: column; gap: 6px; }
.pane-item { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 10px; border-radius: 8px; border: 1px solid transparent; background: none; cursor: pointer; text-align: left; color: #3b332a; }
.pane-item.active { background: #f4e3cf; border-color: #e0cfb4; color: #b35a1b; }
.pane-item-sub { font-size: 12px; color: #8a7a66; }
.pane-note { font-size: 11.5px; color: #8a7a66; margin-top: 8px; }
.check-opts { display: flex; flex-wrap: wrap; gap: 6px; }
.check-opt { display: flex; align-items: center; gap: 4px; padding: 5px 9px; border: 1px solid #e0cfb4; border-radius: 8px; background: #fff; font-size: 13px; cursor: pointer; }
.check-opt.on { background: #f4e3cf; border-color: #e0cfb4; color: #b35a1b; }
.prompt-pre { margin: 8px 0 0; padding: 10px; border: 1px solid #e8ddcc; border-radius: 8px; background: #fffdf6; font-family: ui-monospace, monospace; font-size: 12px; line-height: 1.55; white-space: pre-wrap; max-height: 200px; overflow-y: auto; }
.field-hint { font-size: 11.5px; color: #8a7a66; margin: 4px 0 0; }
.stale-warn { font-size: 12px; color: #9a5b12; margin: 4px 0 0; }
.tool-state { font-size: 12px; }
.tool-state.on { color: #1b7a3c; }
.tool-state.off { color: #8a7a66; }
.group-row td { background: #f7efe1; color: #6b5d4f; font-weight: 600; }
```

- [ ] **Step 4: 验证并提交**

```bash
cd frontend && npm run build
```

预期：build 通过。三节完整人工走查统一留到 Task 9。

```bash
git add frontend/src/App.tsx frontend/src/App.css frontend/src/components
git commit -m "refactor(frontend): 设置弹窗拆为外壳+模型 pane 并加入三节 tab 条"
```

---

### Task 7: 智能体配置 pane

**Files:**
- Create: `frontend/src/components/settings/AgentPane.tsx`
- Modify: `frontend/src/components/settings/SettingsModal.tsx`（agent 那个空 `modal-body` 内填入组件 + 顶部 import）

**Interfaces:**
- Consumes: Task 5 的 `AgentInfo`（未直接引用，类型由 `CapabilityResponse["agents"][number]` 推出）、`putAgentDefaultModel`、`putAgentTools`、`CapabilityResponse`、`ModelsResponse`；Task 6 外壳的 `saving` 与 `runCaps`。
- Produces：`AgentPane` props `{ models: ModelsResponse; caps: CapabilityResponse; saving: boolean; onAction: (action: () => Promise<CapabilityResponse>) => void }`。

- [ ] **Step 1: 写 AgentPane**

`frontend/src/components/settings/AgentPane.tsx` 全文：

```tsx
import { useState } from "react";
import {
  putAgentDefaultModel,
  putAgentTools,
  type CapabilityResponse,
  type ModelsResponse,
} from "../../api/client";

interface AgentPaneProps {
  models: ModelsResponse;
  caps: CapabilityResponse;
  saving: boolean;
  onAction: (action: () => Promise<CapabilityResponse>) => void;
}

export default function AgentPane({ models, caps, saving, onAction }: AgentPaneProps) {
  const [selectedId, setSelectedId] = useState(caps.agents[0].id);
  const [promptOpen, setPromptOpen] = useState(false);
  const agent = caps.agents.find((a) => a.id === selectedId) ?? caps.agents[0];
  const stale = agent.default_uid !== "" && agent.default_uid !== agent.effective_uid;
  const allModels = models.providers.flatMap((p) =>
    p.models.map((m) => ({
      uid: `${p.id}/${m.id}`,
      usable: m.enabled && p.has_key,
    })),
  );
  const globalLabel = models.default_uid === "" ? "未配置" : models.default_uid;

  function toggleTool(toolId: string, checked: boolean): void {
    const next = checked ? [...agent.tool_ids, toolId] : agent.tool_ids.filter((t) => t !== toolId);
    onAction(() => putAgentTools(agent.id, next));
  }

  return (
    <>
      <div className="pane-list">
        {caps.agents.map((a) => (
          <button
            key={a.id}
            className={a.id === selectedId ? "pane-item active" : "pane-item"}
            onClick={() => {
              setSelectedId(a.id);
              setPromptOpen(false);
            }}
          >
            <span>
              {a.icon} {a.name}
              {a.default_uid === "" ? " · 跟随默认" : ""}
            </span>
            <span className="pane-item-sub">
              {a.effective_uid === "" ? "未配置可用模型" : a.effective_uid} · 携带 {a.tool_ids.length} 个工具
            </span>
          </button>
        ))}
        <p className="pane-note">内置智能体由平台统一维护，本期只配置已注册的智能体。</p>
      </div>
      <div className="provider-form">
        <div className="field-row">
          <label>职责</label>
          <p className="field-hint" style={{ flex: 1 }}>{agent.desc}</p>
        </div>
        <div className="field-row">
          <label>默认模型</label>
          <select
            value={agent.default_uid}
            disabled={saving}
            onChange={(e) => onAction(() => putAgentDefaultModel(agent.id, e.target.value))}
          >
            <option value="">跟随全局默认（{globalLabel}）</option>
            {allModels.map((m) => (
              <option key={m.uid} value={m.uid} disabled={!m.usable}>
                {m.uid}
                {m.usable ? "" : "（不可用）"}
              </option>
            ))}
          </select>
        </div>
        <p className="field-hint">新建会话自动带入该默认模型；不指定则用全局默认 LLM。</p>
        {stale && (
          <p className="stale-warn">
            已选的 {agent.default_uid} 当前不可用，实际回落全局默认 {agent.effective_uid || "（无）"}；
            请到 设置 · 模型设置 启用该模型或配置 API Key。
          </p>
        )}
        <div className="field-row" style={{ alignItems: "flex-start" }}>
          <label>携带工具</label>
          <div className="check-opts">
            {caps.tools.map((t) => {
              const carried = agent.tool_ids.includes(t.id);
              return (
                <label key={t.id} className={carried && t.enabled ? "check-opt on" : "check-opt"} title={t.desc}>
                  <input
                    type="checkbox"
                    checked={carried && t.enabled}
                    disabled={saving || !t.enabled}
                    onChange={(e) => toggleTool(t.id, e.target.checked)}
                  />
                  {t.icon} {t.label}
                </label>
              );
            })}
          </div>
        </div>
        <p className="field-hint">
          只能勾选「🛠 工具」里已启用的工具；禁用某工具会自动从所有智能体摘掉，重新启用不自动补回。
        </p>
        <div className="field-row">
          <label>系统提示词</label>
          <button className="btn-secondary" onClick={() => setPromptOpen((v) => !v)}>
            {promptOpen ? "收起系统提示词" : "查看系统提示词"}
          </button>
          <span className="field-hint">
            {promptOpen ? "展开中" : "只读展示，由平台统一维护"}
          </span>
          <span className="field-hint">
            {agent.prompt.split(/\r?\n/).length} 行 · {agent.prompt.length} 字
          </span>
        </div>
        {promptOpen && <pre className="prompt-pre">{agent.prompt}</pre>}
      </div>
    </>
  );
}
```

- [ ] **Step 2: 接入外壳**

`SettingsModal.tsx` 顶部 import 增加 `import AgentPane from "./AgentPane";`；把 `hidden={tab !== "agent"}` 那个空 `div` 改为：

```tsx
            <div className="modal-body" hidden={tab !== "agent"}>
              <AgentPane models={models} caps={caps} saving={saving} onAction={runCaps} />
            </div>
```

- [ ] **Step 3: 验证并提交**

```bash
cd frontend && npm run build
git add frontend/src/components/settings
git commit -m "feat(frontend): 设置·智能体配置——默认模型、携带工具与只读系统提示词"
```

---

### Task 8: 工具 pane（含禁用级联确认）

**Files:**
- Create: `frontend/src/components/settings/ToolPane.tsx`
- Modify: `frontend/src/components/settings/SettingsModal.tsx`（tool 那个空 `modal-body` 填入组件 + 顶部 import）

**Interfaces:**
- Consumes: Task 5 的 `ToolInfo`、`putToolEnabled`、`CapabilityResponse`；Task 6 外壳 `saving` 与 `runCaps`；`caps.agents` 用于「被谁携带」与禁用前确认文案。
- Produces：`ToolPane` props `{ caps: CapabilityResponse; saving: boolean; onAction: (action: () => Promise<CapabilityResponse>) => void }`。

- [ ] **Step 1: 写 ToolPane**

`frontend/src/components/settings/ToolPane.tsx` 全文：

```tsx
import { Fragment, useState } from "react";
import { putToolEnabled, type CapabilityResponse, type ToolInfo } from "../../api/client";

interface ToolPaneProps {
  caps: CapabilityResponse;
  saving: boolean;
  onAction: (action: () => Promise<CapabilityResponse>) => void;
}

function ToolRow({
  tool,
  caps,
  saving,
  onToggle,
}: {
  tool: ToolInfo;
  caps: CapabilityResponse;
  saving: boolean;
  onToggle: (tool: ToolInfo) => void;
}) {
  const holders = caps.agents
    .filter((a) => a.tool_ids.includes(tool.id))
    .map((a) => a.name.replace("智能体", ""));
  return (
    <tr>
      <td>
        {tool.icon} {tool.label}
      </td>
      <td>
        <div>{tool.desc}</div>
        <div className="pane-item-sub">
          {holders.length > 0 ? `被 ${holders.join("、")} 携带` : "未被任何智能体携带"}
        </div>
      </td>
      <td>{tool.os}</td>
      <td>
        <span className={tool.enabled ? "tool-state on" : "tool-state off"}>
          {tool.enabled ? "● 已启用" : "○ 已禁用"}
        </span>
      </td>
      <td>
        <button className="btn-secondary" disabled={saving} onClick={() => onToggle(tool)}>
          {tool.enabled ? "禁用" : "启用"}
        </button>
      </td>
    </tr>
  );
}

export default function ToolPane({ caps, saving, onAction }: ToolPaneProps) {
  const [tip, setTip] = useState("");
  const enabledCount = caps.tools.filter((t) => t.enabled).length;

  const groups: { name: string; tools: ToolInfo[] }[] = [];
  caps.tools.forEach((t) => {
    const last = groups[groups.length - 1];
    if (last !== undefined && last.name === t.group) last.tools.push(t);
    else groups.push({ name: t.group, tools: [t] });
  });

  function toggle(tool: ToolInfo): void {
    if (!tool.enabled) {
      onAction(async () => {
        const resp = await putToolEnabled(tool.id, true);
        setTip(`已启用 ${tool.label}，可在「智能体配置」里勾选携带。`);
        return resp;
      });
      return;
    }
    const holders = caps.agents.filter((a) => a.tool_ids.includes(tool.id)).map((a) => a.name);
    const message = `禁用「${tool.label}」工具？\n\n将从 ${holders.length} 个智能体摘掉该工具：${holders.join("、") || "无"}`;
    if (!window.confirm(message)) return;
    onAction(async () => {
      const resp = await putToolEnabled(tool.id, false);
      setTip(`已禁用 ${tool.label}，相关智能体不再具备该能力。`);
      return resp;
    });
  }

  return (
    <div className="provider-form">
      <p className="field-hint">
        内置工具 <strong>{enabledCount}/{caps.tools.length}</strong>　禁用后，所有智能体都不会再调用该工具
      </p>
      <table className="model-table">
        <thead>
          <tr>
            <th>工具</th>
            <th>说明</th>
            <th>平台</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <Fragment key={g.name}>
              <tr className="group-row">
                <td colSpan={5}>🧩 {g.name}</td>
              </tr>
              {g.tools.map((t) => (
                <ToolRow key={t.id} tool={t} caps={caps} saving={saving} onToggle={toggle} />
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
      {tip !== "" && <p className="field-hint">{tip}</p>}
    </div>
  );
}
```

硬性要求：`colSpan={5}` 与表头五列严格一一对应；`window.confirm` 取消时**不发请求、不改 tip**（操作必有可见结果，取消就是无副作用）。

- [ ] **Step 2: 接入外壳**

`SettingsModal.tsx` 顶部 import 增加 `import ToolPane from "./ToolPane";`；把 `hidden={tab !== "tool"}` 那个空 `div` 改为：

```tsx
            <div className="modal-body" hidden={tab !== "tool"}>
              <ToolPane caps={caps} saving={saving} onAction={runCaps} />
            </div>
```

- [ ] **Step 3: 验证并提交**

```bash
cd frontend && npm run build
git add frontend/src/components/settings
git commit -m "feat(frontend): 设置·工具分区——分组清单、携带关系与禁用级联确认"
```

---

### Task 9: 文档与全量验收

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 4 的四个端点、Task 6–8 的三节设置弹窗。
- Produces: 文档 + 交付给用户的人工验收清单；无代码接口。

- [ ] **Step 1: 更新 README**

在「后端（backend/）」端点清单里 `GET /api/models` 那条之后插入：

```markdown
- `GET /api/capabilities` + 三个 `PUT`：智能体默认模型 / 携带工具 / 工具启用停用的运行期读写
  （对应前端「⚙ 设置 · 智能体配置 / 工具」；禁用工具会从所有智能体级联摘除，重新启用不自动补回；
  本期只做配置入口，暂不参与实际执行逻辑）
```

在「配置：复制 `.env.example` 为 `.env` …」段落末尾追加：

```markdown
  能力配置（智能体默认模型、携带工具、工具启停）存 `backend/data/capability_config.json`
  （同样 gitignore，不含密钥），首次启动自动生成种子
```

在「自动检查 8000/前端端口占用…」那段之后另起一行：

```markdown
「⚙ 设置」弹窗分三节：🧠 模型设置 / 🤖 智能体配置（本期仅「用例设计智能体」，系统提示词只读）/ 🛠 工具。
```

- [ ] **Step 2: 全量离线验证**

```bash
cd backend && uv run pytest -q
cd ../frontend && npm run build
git status --short
```

预期：后端 69 passed；前端 build 通过；`git status --short` 只剩本次源码/文档改动，**绝无** `.env`、`backend/data/*`、`node_modules`、`dist`。

- [ ] **Step 3: 人工验收（给用户 localhost 地址，逐条走查）**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1
```

打开 http://localhost:5173 → 顶栏「⚙ 设置」：
1. 三节 tab 可切换，标题「⚙ 设置」，弹窗高度不跳；
2. 模型设置一节既有行为不变（Key 保存/清除、模型启用勾选、默认 LLM 下拉）；
3. 智能体配置：左列只有 `📋 用例设计智能体`（带「跟随默认」与副行 uid + 携带数）；默认模型下拉里未启用/未配 Key 的项置灰并标「（不可用）」；改默认模型后关闭重开仍在；「查看系统提示词」展开只读 `<pre>`，右侧显示「n 行 · m 字」；
4. 勾选/摘掉携带工具 → 左列携带计数与工具 pane「被 … 携带」同时变化；
5. 工具 pane：三分组 6 行，`bash` 为 `○ 已禁用`、计数 `5/6`；点 pwsh「禁用」先弹确认（列出「用例设计智能体」），确认后智能体 pane 里 pwsh 变灰未勾选；点「启用」不自动补回并给出提示文案；
6. 在模型设置里停用「智能体配置」当前指定的默认模型 → 切回智能体 pane 出现黄色回落提示，且实际生效模型显示为全局默认；
7. 关闭后端再启动，`Get-Content backend\data\capability_config.json` 内容与界面一致（重启读回）。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 补能力配置端点与设置弹窗三节说明"
```
