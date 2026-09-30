# 运行期模型配置（设置 · 模型设置）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AiTester 用户通过前端「⚙ 设置 · 模型设置」在运行期配置提供商 API Key、启用模型与默认 LLM，后端 JSON 落盘即时生效，`POST /api/chat/send` 按默认模型真实调用。

**Architecture:** 方案 A——storage 层新增文件仓储（原子写 JSON），services 层新增 `ModelConfigService`（种子迁移、掩码视图、校验、级联回落、构建 `OpenAICompatProvider`），interaction 层新增 `/api/models` 四端点并把 health 改报运行期 `default_uid`；前端把弹窗状态与 health 提升到 App，设置弹窗直接消费这些 API。接入层 `.env` factory 废弃删除。

**Tech Stack:** Python 3.11 + FastAPI + pydantic v2 + LangChain ChatOpenAI + uv/pytest；TypeScript + React 18 + Vite（strict）。

**Spec:** `docs/superpowers/specs/2026-09-30-model-config-design.md`

## Global Constraints

- 七层依赖方向：storage 不得 import services/adapters；services 可 import storage/adapters/config；interaction 只 import services；禁止反向。
- API Key 明文只存本机 `backend/data/model_config.json` 与 `backend/.env`；任何接口响应/日志/异常文本不含明文 Key。掩码规则（唯一权威定义）：空串→`""`；长度≤8→`"***"`；其余→`f"{key[:4]}…{key[-4:]}"`（spec 示例 `sk-a…9f` 以此规则为准）。
- `backend/data/` 与 `.env` 必须在 `.gitignore` 内，永不入库。
- 所有测试离线：ChatOpenAI 一律 monkeypatch `aitester.adapters.llm.openai_compat.ChatOpenAI`；文件一律 `tmp_path`。
- 后端命令均在 `backend/` 目录：`uv run pytest -q`；前端命令在 `frontend/`：`npm run build`（tsc strict + vite）。
- UI 与错误文案为中文；控件必须带标签（「⚙ 设置」必须带文字，禁止纯图标）；用户操作必须产生可见结果（保存后状态即时刷新）。
- 错误语义沿用接入层：配置类 `ProviderConfigError`→400（detail 可直接照做、点名「设置 · 模型设置」）、未知提供商/模型 `ConfigNotFoundError`→404、上游 `ProviderError`→502。
- `/api/chat/echo` 语义不变（恒 mock、七层 trace 回归）；`GET /api/health` 的 `llm_provider` = 运行期 `default_uid`，未配置时 `"mock"`。
- 每任务结尾单独 git 提交，提交信息中文 conventional 风格；提交前 `git status` 确认无 `.env`、无 `backend/data/`。

## 文件结构（总览）

- Create: `backend/src/aitester/storage/model_config_repo.py`（协议 + 文件仓储 + ConfigStorageError）
- Create: `backend/src/aitester/services/model_config.py`（CATALOG / mask_key / ModelConfigService / ConfigNotFoundError）
- Create: `backend/tests/test_model_config_repo.py`、`backend/tests/test_model_config.py`
- Create: `frontend/src/components/SettingsModal.tsx`
- Modify: `backend/src/aitester/storage/__init__.py`、`services/__init__.py`、`services/chat.py`、`config.py`、`main.py`、`interaction/router.py`、`interaction/schemas.py`
- Modify: `backend/src/aitester/adapters/llm/__init__.py`；Delete: `backend/src/aitester/adapters/llm/factory.py`
- Modify: `backend/tests/test_config.py`、`test_adapters.py`、`test_chat_service.py`、`test_api.py`
- Modify: `backend/.env.example`、仓库根 `.gitignore`、`README.md`
- Modify: `frontend/src/api/client.ts`、`App.tsx`、`pages/ChatPage.tsx`、`App.css`

当前基线：HEAD=`ac8f2a8`，后端 28 项测试全绿。测试数预期 T1→32、T2→38、T3→45、T4→40（factory/旧 send 测试删除所致）、T5→44；±1 以内以实际为准，但禁止失败。

---

### Task 1: storage 层——模型配置文件仓储

**Files:**
- Create: `backend/src/aitester/storage/model_config_repo.py`
- Modify: `backend/src/aitester/storage/__init__.py`
- Test: `backend/tests/test_model_config_repo.py`

**Interfaces:**
- Consumes: 无（仅标准库 + pathlib）。
- Produces（`aitester.storage` 包直接可导入，Task 2/4/5 依赖）：
  - `ConfigStorageError(RuntimeError)`，属性 `.detail: str`
  - `ModelConfigRepository` Protocol：`load() -> dict[str, Any] | None`、`save(config: dict[str, Any]) -> None`
  - `FileModelConfigRepository(path: Path)`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_model_config_repo.py`：

```python
from pathlib import Path

import pytest

from aitester.storage import ConfigStorageError, FileModelConfigRepository


def _repo(tmp_path: Path) -> FileModelConfigRepository:
    return FileModelConfigRepository(tmp_path / "model_config.json")


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert _repo(tmp_path).load() is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    cfg = {"version": 1, "default_uid": "deepseek/deepseek-flash", "providers": []}
    repo = _repo(tmp_path)
    repo.save(cfg)
    assert repo.load() == cfg


def test_save_leaves_no_tmp_residue(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.save({"version": 1, "default_uid": "", "providers": []})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["model_config.json"]


def test_load_corrupt_json_raises_with_path(tmp_path: Path) -> None:
    target = tmp_path / "model_config.json"
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigStorageError) as exc_info:
        _repo(tmp_path).load()
    assert str(target) in exc_info.value.detail
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_model_config_repo.py -q`
Expected: ERROR——`ImportError: cannot import name 'ConfigStorageError' from 'aitester.storage'`

- [ ] **Step 3: 最小实现**

创建 `backend/src/aitester/storage/model_config_repo.py`：

```python
import json
import os
from pathlib import Path
from typing import Any, Protocol


class ConfigStorageError(RuntimeError):
    """配置文件读写失败，detail 面向用户且含文件路径。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ModelConfigRepository(Protocol):
    """模型配置持久化抽象。"""

    def load(self) -> dict[str, Any] | None: ...

    def save(self, config: dict[str, Any]) -> None: ...


class FileModelConfigRepository:
    """单 JSON 文件实现：同目录临时文件 + os.replace 原子替换。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigStorageError(f"模型配置文件已损坏：{self._path}（{exc}）") from exc

    def save(self, config: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)
```

修改 `backend/src/aitester/storage/__init__.py` 为：

```python
"""存储层：实体持久化抽象。"""
from aitester.storage.base import Repository
from aitester.storage.in_memory import InMemoryRepository
from aitester.storage.model_config_repo import (
    ConfigStorageError,
    FileModelConfigRepository,
    ModelConfigRepository,
)

__all__ = [
    "Repository",
    "InMemoryRepository",
    "ConfigStorageError",
    "FileModelConfigRepository",
    "ModelConfigRepository",
]
```

- [ ] **Step 4: 运行确认通过（含全量）**

Run: `cd backend && uv run pytest -q`
Expected: 32 全绿（28 + 4）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/aitester/storage backend/tests/test_model_config_repo.py
git commit -m "feat(storage): 模型配置文件仓储（协议+原子写+损坏报错）"
```

---

### Task 2: services 层——ModelConfigService 读侧（种子/掩码/视图）

**Files:**
- Create: `backend/src/aitester/services/model_config.py`
- Test: `backend/tests/test_model_config.py`

**Interfaces:**
- Consumes: `aitester.storage.FileModelConfigRepository/ModelConfigRepository`（Task 1）；`aitester.config.Settings`（现有字段 `deepseek_api_key`/`dashscope_api_key`）。
- Produces（Task 3/4/5 依赖）：
  - `CATALOG: list[dict[str, Any]]`
  - `mask_key(key: str) -> str`
  - `ModelConfigService(repo: ModelConfigRepository, settings: Settings)`，成员 `default_uid: str`（property）、`get_view() -> dict[str, Any]`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_model_config.py`：

```python
from pathlib import Path

from aitester.config import Settings
from aitester.services.model_config import ModelConfigService, mask_key
from aitester.storage import FileModelConfigRepository


def _svc(tmp_path: Path, **settings_kwargs: object) -> ModelConfigService:
    return ModelConfigService(
        FileModelConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )


def test_first_start_seeds_from_settings_and_persists(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="  sk-seed-abcdef123456  ")
    stored = FileModelConfigRepository(tmp_path / "model_config.json").load()
    assert stored is not None
    assert stored["version"] == 1
    assert stored["providers"][0]["api_key"] == "sk-seed-abcdef123456"
    assert svc.default_uid == "deepseek/deepseek-flash"


def test_seed_prefers_dashscope_when_only_it_has_key(tmp_path: Path) -> None:
    svc = _svc(tmp_path, dashscope_api_key="sk-dash-000111")
    assert svc.default_uid == "dashscope/qwen3.7-max"


def test_seed_without_keys_leaves_default_empty(tmp_path: Path) -> None:
    assert _svc(tmp_path).default_uid == ""


def test_existing_file_is_not_reseeded(tmp_path: Path) -> None:
    saved = {
        "version": 1,
        "default_uid": "custom/x",
        "providers": [
            {
                "id": "custom",
                "name": "Custom",
                "base_url": "https://x",
                "api_key": "k",
                "models": [{"id": "x", "enabled": True, "max_output": 1, "context": 1}],
            }
        ],
    }
    repo = FileModelConfigRepository(tmp_path / "model_config.json")
    repo.save(saved)
    svc = ModelConfigService(
        repo, Settings(_env_file=None, deepseek_api_key="sk-should-not-appear")
    )
    assert svc.default_uid == "custom/x"
    assert FileModelConfigRepository(tmp_path / "model_config.json").load() == saved


def test_get_view_shape_and_masking(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-ABCDEFGHIJKLMNOP")
    view = svc.get_view()
    assert view["default_uid"] == "deepseek/deepseek-flash"
    provider = view["providers"][0]
    assert provider["id"] == "deepseek"
    assert provider["has_key"] is True
    assert provider["key_masked"] == "sk-A…MNOP"
    assert provider["models"][0]["id"] == "deepseek-flash"
    assert "sk-ABCDEFGHIJKLMNOP" not in str(view)
    assert len(view["providers"]) == 2


def test_mask_key_rules() -> None:
    assert mask_key("") == ""
    assert mask_key("12345678") == "***"
    assert mask_key("123456789") == "1234…6789"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_model_config.py -q`
Expected: ERROR——`ModuleNotFoundError: No module named 'aitester.services.model_config'`

- [ ] **Step 3: 最小实现**

创建 `backend/src/aitester/services/model_config.py`：

```python
"""运行期模型配置服务：JSON 落盘为唯一真相，.env 仅作首次种子。"""
import copy
from typing import Any

from aitester.config import Settings
from aitester.storage import ModelConfigRepository

CATALOG: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-flash", "enabled": True, "max_output": 393216, "context": 1048576},
            {"id": "deepseek-v4-pro", "enabled": True, "max_output": 393216, "context": 1048576},
        ],
    },
    {
        "id": "dashscope",
        "name": "通义千问 · DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen3.7-max", "enabled": True, "max_output": 8192, "context": 1000000},
            {"id": "qwen3.8-max", "enabled": True, "max_output": 8192, "context": 131072},
            {"id": "qwen3.7-plus", "enabled": True, "max_output": 8192, "context": 1000000},
            {"id": "qwen3.6-plus", "enabled": True, "max_output": 8192, "context": 1000000},
        ],
    },
]


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


def _default_config(settings: Settings) -> dict[str, Any]:
    providers = copy.deepcopy(CATALOG)
    keys = {
        "deepseek": settings.deepseek_api_key.strip(),
        "dashscope": settings.dashscope_api_key.strip(),
    }
    for provider in providers:
        provider["api_key"] = keys[provider["id"]]
    default_uid = ""
    for provider in providers:
        if provider["api_key"]:
            default_uid = f"{provider['id']}/{provider['models'][0]['id']}"
            break
    return {"version": 1, "default_uid": default_uid, "providers": providers}


class ModelConfigService:
    """配置真相：构造时 load（缺则种子并落盘），每次变更立即 save。"""

    def __init__(self, repo: ModelConfigRepository, settings: Settings) -> None:
        self._repo = repo
        config = repo.load()
        if config is None:
            config = _default_config(settings)
            repo.save(config)
        self._config = config

    @property
    def default_uid(self) -> str:
        return self._config["default_uid"]

    def get_view(self) -> dict[str, Any]:
        return {
            "default_uid": self._config["default_uid"],
            "providers": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "base_url": p["base_url"],
                    "has_key": bool(p["api_key"]),
                    "key_masked": mask_key(p["api_key"]),
                    "models": [dict(m) for m in p["models"]],
                }
                for p in self._config["providers"]
            ],
        }
```

- [ ] **Step 4: 运行确认通过（含全量）**

Run: `cd backend && uv run pytest -q`
Expected: 38 全绿。

- [ ] **Step 5: 提交**

```bash
git add backend/src/aitester/services/model_config.py backend/tests/test_model_config.py
git commit -m "feat(services): 模型配置服务读侧——.env 种子迁移与掩码视图"
```

---

### Task 3: services 层——ModelConfigService 写侧（校验/级联/构建 provider）

**Files:**
- Modify: `backend/src/aitester/services/model_config.py`
- Test: `backend/tests/test_model_config.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `ModelConfigService`/`_config`；`aitester.adapters.llm` 的 `ProviderConfigError`、`OpenAICompatProvider`、`LlmProvider`（均已存在）。
- Produces（Task 4/5 依赖）：
  - `ConfigNotFoundError(KeyError)`，属性 `.detail: str`（交互层映射 404）
  - `update_api_key(provider_id: str, api_key: str | None) -> None`（None=不变，字符串=strip 后覆盖，空串=清除）
  - `set_model_enabled(provider_id: str, model_id: str, enabled: bool) -> None`
  - `set_default(uid: str) -> None`
  - `build_default_provider() -> LlmProvider`

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_model_config.py` 导入区补充：

```python
import pytest

from aitester.adapters.llm import ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.services.model_config import ConfigNotFoundError
```

文件末尾追加：

```python
def test_update_api_key_none_keeps_string_sets(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.update_api_key("deepseek", None)
    assert svc.get_view()["providers"][0]["has_key"] is False
    svc.update_api_key("deepseek", "  sk-x123456789  ")
    assert svc.get_view()["providers"][0]["has_key"] is True


def test_update_unknown_provider_raises_404_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigNotFoundError):
        _svc(tmp_path).update_api_key("glm", "k")


def test_set_default_validations(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.set_default("deepseek/deepseek-flash")
    assert "设置 · 模型设置" in exc_info.value.detail
    svc.update_api_key("deepseek", "sk-x123456789")
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    with pytest.raises(ProviderConfigError) as exc_info2:
        svc.set_default("deepseek/deepseek-flash")
    assert "启用" in exc_info2.value.detail
    with pytest.raises(ConfigNotFoundError):
        svc.set_default("deepseek/nope")
    with pytest.raises(ProviderConfigError):
        svc.set_default("garbage")


def test_cascade_on_disable_default_persisted(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert svc.default_uid == "deepseek/deepseek-flash"
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    assert svc.default_uid == "deepseek/deepseek-v4-pro"
    svc.set_model_enabled("deepseek", "deepseek-v4-pro", False)
    assert svc.default_uid == ""
    assert _svc(tmp_path).default_uid == ""


def test_cascade_on_clear_key_falls_back_to_next_provider(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789")
    svc.update_api_key("deepseek", "")
    assert svc.default_uid == "dashscope/qwen3.7-max"


def test_build_default_provider_passes_expected_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    provider = svc.build_default_provider()
    assert provider.model_ref == "deepseek/deepseek-flash"
    assert recorded == {
        "model": "deepseek-flash",
        "api_key": "sk-x123456789",
        "base_url": "https://api.deepseek.com",
        "timeout": 60,
    }


def test_build_default_provider_without_default(tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigError) as exc_info:
        _svc(tmp_path).build_default_provider()
    assert "设置 · 模型设置" in exc_info.value.detail
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_model_config.py -q`
Expected: ERROR——`ImportError: cannot import name 'ConfigNotFoundError'`

- [ ] **Step 3: 实现写侧**

`backend/src/aitester/services/model_config.py` 头部导入替换为：

```python
"""运行期模型配置服务：JSON 落盘为唯一真相，.env 仅作首次种子。"""
import copy
from typing import Any

from aitester.adapters.llm import LlmProvider, OpenAICompatProvider, ProviderConfigError
from aitester.config import Settings
from aitester.storage import ModelConfigRepository
```

`mask_key` 之后新增异常类：

```python
class ConfigNotFoundError(KeyError):
    """未知提供商/模型，交互层映射 404。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
```

`ModelConfigService` 类体内 `get_view` 之后追加：

```python
    def _provider(self, provider_id: str) -> dict[str, Any]:
        for p in self._config["providers"]:
            if p["id"] == provider_id:
                return p
        raise ConfigNotFoundError(f"未知提供商「{provider_id}」")

    def _model(self, provider: dict[str, Any], model_id: str) -> dict[str, Any]:
        for m in provider["models"]:
            if m["id"] == model_id:
                return m
        raise ConfigNotFoundError(f"提供商「{provider['id']}」没有模型「{model_id}」")

    def _uid_usable(self, uid: str) -> bool:
        pid, sep, mid = uid.partition("/")
        if not sep or not mid:
            return False
        for p in self._config["providers"]:
            if p["id"] == pid:
                for m in p["models"]:
                    if m["id"] == mid:
                        return bool(m["enabled"] and p["api_key"])
        return False

    def _first_available_uid(self) -> str:
        for p in self._config["providers"]:
            if not p["api_key"]:
                continue
            for m in p["models"]:
                if m["enabled"]:
                    return f"{p['id']}/{m['id']}"
        return ""

    def _cascade_default(self) -> None:
        current = self._config["default_uid"]
        if current and not self._uid_usable(current):
            self._config["default_uid"] = self._first_available_uid()

    def update_api_key(self, provider_id: str, api_key: str | None) -> None:
        provider = self._provider(provider_id)
        if api_key is not None:
            provider["api_key"] = api_key.strip()
        self._cascade_default()
        self._repo.save(self._config)

    def set_model_enabled(self, provider_id: str, model_id: str, enabled: bool) -> None:
        provider = self._provider(provider_id)
        model = self._model(provider, model_id)
        model["enabled"] = enabled
        self._cascade_default()
        self._repo.save(self._config)

    def set_default(self, uid: str) -> None:
        pid, sep, mid = uid.partition("/")
        if not sep or not mid:
            raise ProviderConfigError(f"无效的模型标识「{uid}」，应为 提供商/模型 形式")
        provider = self._provider(pid)
        model = self._model(provider, mid)
        if not model["enabled"]:
            raise ProviderConfigError(f"模型「{uid}」已停用，请先在 设置 · 模型设置 中启用")
        if not provider["api_key"]:
            raise ProviderConfigError(
                f"提供商「{provider['name']}」未配置 API Key，请在 设置 · 模型设置 中填写"
            )
        self._config["default_uid"] = uid
        self._repo.save(self._config)

    def build_default_provider(self) -> LlmProvider:
        uid = self._config["default_uid"]
        if not uid:
            raise ProviderConfigError(
                "尚未配置默认模型：请在 设置 · 模型设置 中填写 API Key 并选择默认 LLM"
            )
        pid, _, mid = uid.partition("/")
        provider = self._provider(pid)
        model = self._model(provider, mid)
        if not provider["api_key"]:
            raise ProviderConfigError(
                f"默认模型「{uid}」的提供商未配置 API Key：请在 设置 · 模型设置 中填写"
            )
        if not model["enabled"]:
            raise ProviderConfigError(
                f"默认模型「{uid}」已停用：请在 设置 · 模型设置 中启用或改选默认模型"
            )
        return OpenAICompatProvider(
            name=pid,
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model=mid,
        )
```

- [ ] **Step 4: 运行确认通过（含全量）**

Run: `cd backend && uv run pytest -q`
Expected: 45 全绿。

- [ ] **Step 5: 提交**

```bash
git add backend/src/aitester/services/model_config.py backend/tests/test_model_config.py
git commit -m "feat(services): 模型配置写侧——校验、级联回落与按配置构建 provider"
```

---

### Task 4: 后端装配改造（config 瘦身 / factory 废弃 / send 与 health 改指运行期）

**Files:**
- Modify: `backend/src/aitester/config.py`
- Delete: `backend/src/aitester/adapters/llm/factory.py`
- Modify: `backend/src/aitester/adapters/llm/__init__.py`、`services/__init__.py`、`services/chat.py`、`main.py`、`interaction/router.py`（仅 health）
- Modify: `backend/tests/test_config.py`、`test_adapters.py`、`test_chat_service.py`、`test_api.py`
- Modify: `backend/.env.example`、仓库根 `.gitignore`

**Interfaces:**
- Consumes: Task 3 的 `ModelConfigService(repo, settings)`、`build_default_provider()`、`default_uid`。
- Produces（Task 5/6/7 依赖）：
  - `Settings`：仅 `host/port/deepseek_api_key/dashscope_api_key`
  - `ChatService(provider=None, memory=None, context=None, repo=None, model_config: ModelConfigService | None = None)`
  - `create_app(model_config_path: Path | None = None, settings: Settings | None = None) -> FastAPI`，装配 `app.state.model_config` 与 `app.state.chat_service`
  - health：`{ok, service, llm_provider: default_uid 或 "mock"}`

- [ ] **Step 1: test_config.py 整文件替换（失败测试先行）**

```python
from aitester.config import Settings, get_settings


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.deepseek_api_key == ""
    assert s.dashscope_api_key == ""
    assert not hasattr(s, "llm_provider")


def test_get_settings_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)
```

Run: `cd backend && uv run pytest tests/test_config.py -q`
Expected: FAIL——`assert not hasattr(s, "llm_provider")` 不成立

- [ ] **Step 2: config.py 瘦身**

`Settings` 类体替换为：

```python
    host: str = "127.0.0.1"
    port: int = 8000

    # 仅首次启动生成 backend/data/model_config.json 时作为种子读取
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""
```

Run: `cd backend && uv run pytest tests/test_config.py -q` → PASS

- [ ] **Step 3: test_adapters.py 整文件替换（删 factory 测试）**

```python
import pytest

from aitester.adapters.llm import (
    LlmProvider,
    MockProvider,
    OpenAICompatProvider,
    ProviderError,
)
from aitester.adapters.llm import openai_compat


def test_mock_provider_echoes_last_user_message() -> None:
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


def test_mock_provider_model_ref() -> None:
    assert MockProvider().model_ref == "mock/mock"


def test_openai_compat_complete_passes_messages_and_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            self.received: list[dict[str, str]] | None = None

        def invoke(self, messages: list[dict[str, str]]) -> object:
            self.received = messages
            return type("R", (), {"content": "真实回复"})()

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    provider = OpenAICompatProvider("deepseek", "sk-x", "https://api.deepseek.com", "deepseek-flash")
    messages = [{"role": "user", "content": "hi"}]
    assert provider.complete(messages) == "真实回复"
    assert provider._client.received == messages  # type: ignore[union-attr]


def test_openai_compat_wraps_upstream_error_and_redacts_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            pass

        def invoke(self, messages: list[dict[str, str]]) -> object:
            raise RuntimeError(f"invalid api_key sk-SECRET123 in request to {messages[0]['content']}")

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    provider = OpenAICompatProvider("dashscope", "sk-SECRET123", "https://example.com/v1", "qwen3.7-max")
    with pytest.raises(ProviderError) as exc_info:
        provider.complete([{"role": "user", "content": "hi"}])
    detail = exc_info.value.detail
    assert "dashscope/qwen3.7-max" in detail
    assert "sk-SECRET123" not in detail
```

- [ ] **Step 4: 删除 factory 并更新 llm 包导出**

```bash
rm backend/src/aitester/adapters/llm/factory.py
```

`backend/src/aitester/adapters/llm/__init__.py` 整文件替换为：

```python
"""接入层-模型提供商适配。"""
from aitester.adapters.llm.base import LlmProvider
from aitester.adapters.llm.errors import ProviderConfigError, ProviderError
from aitester.adapters.llm.mock import MockProvider
from aitester.adapters.llm.openai_compat import OpenAICompatProvider

__all__ = [
    "LlmProvider",
    "MockProvider",
    "OpenAICompatProvider",
    "ProviderConfigError",
    "ProviderError",
]
```

Run: `cd backend && uv run pytest tests/test_config.py tests/test_adapters.py -q` → PASS（7 绿）

- [ ] **Step 5: test_chat_service.py 整文件替换（send 改走配置服务，失败测试先行）**

```python
import pytest

from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.config import Settings
from aitester.services import ChatService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileModelConfigRepository


def _model_config(tmp_path, **settings_kwargs: object) -> ModelConfigService:
    return ModelConfigService(
        FileModelConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )


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


def test_send_uses_injected_provider_and_reports_model() -> None:
    svc = ChatService(provider=MockProvider())
    result = svc.send("s1", "生成用例")
    assert result["reply"] == "[mock] 生成用例"
    assert result["model"] == "mock/mock"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_send_resolves_default_from_model_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def invoke(self, messages: list[dict[str, str]]) -> object:
            return type("R", (), {"content": "真实回复"})()

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = ChatService(model_config=_model_config(tmp_path, deepseek_api_key="sk-x123456789"))
    result = svc.send("s1", "hi")
    assert result["reply"] == "真实回复"
    assert result["model"] == "deepseek/deepseek-flash"
    assert recorded["model"] == "deepseek-flash"


def test_send_without_usable_default_raises_actionable_config_error(tmp_path) -> None:
    svc = ChatService(model_config=_model_config(tmp_path))
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi")
    assert "设置 · 模型设置" in exc_info.value.detail
```

Run: `cd backend && uv run pytest tests/test_chat_service.py -q`
Expected: FAIL——`ChatService.__init__() got an unexpected keyword argument 'model_config'`

- [ ] **Step 6: 改造 ChatService 与 services 导出**

`backend/src/aitester/services/chat.py` 顶部导入替换为：

```python
from typing import Any

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_echo
from aitester.services.model_config import ModelConfigService
from aitester.storage import InMemoryRepository, Repository
```

`__init__` 与 `send` 替换为（`_complete`、`echo` 保持不动）：

```python
    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
        model_config: ModelConfigService | None = None,
    ) -> None:
        self.provider = provider
        self.model_config = model_config
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()

    def send(self, session_id: str, message: str) -> dict[str, Any]:
        """真实链路：注入 provider 优先，否则请求期按运行期配置的默认模型构建。"""
        provider = self.provider
        if provider is None:
            if self.model_config is None:
                raise ProviderConfigError("服务未装配模型配置，请通过 create_app 启动后端")
            provider = self.model_config.build_default_provider()
        return self._complete(session_id, message, provider)
```

`backend/src/aitester/services/__init__.py` 整文件替换为：

```python
"""服务层：业务门面。"""
from aitester.services.chat import ChatService
from aitester.services.model_config import ModelConfigService

__all__ = ["ChatService", "ModelConfigService"]
```

Run: `cd backend && uv run pytest tests/test_chat_service.py -q` → PASS（5 绿）

- [ ] **Step 7: test_api.py 整文件替换（隔离装配 + health 语义，失败测试先行）**

```python
from pathlib import Path

from fastapi.testclient import TestClient

from aitester.adapters.llm import MockProvider, ProviderError
from aitester.config import Settings
from aitester.main import app, create_app
from aitester.services.chat import ChatService

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


def _isolated_client(tmp_path: Path, name: str = "model_config.json") -> TestClient:
    application = create_app(
        model_config_path=tmp_path / name,
        settings=Settings(_env_file=None),
    )
    return TestClient(application)


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "aitester-backend"
    assert isinstance(body["llm_provider"], str) and body["llm_provider"]


def test_health_reports_mock_when_no_default_configured(tmp_path: Path) -> None:
    assert _isolated_client(tmp_path).get("/api/health").json()["llm_provider"] == "mock"


def test_chat_echo_traverses_all_seven_layers() -> None:
    resp = client.post("/api/chat/echo", json={"session_id": "s1", "message": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] hello"
    assert body["trace"] == ALL_LAYERS


def test_chat_echo_rejects_empty_message() -> None:
    resp = client.post("/api/chat/echo", json={"message": ""})
    assert resp.status_code == 422


def test_send_uses_injected_provider_and_reports_model(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json", settings=Settings(_env_file=None)
    )
    application.state.chat_service = ChatService(provider=MockProvider())
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s2", "message": "生成用例"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] 生成用例"
    assert body["trace"] == ALL_LAYERS
    assert body["model"] == "mock/mock"


def test_send_without_configured_default_returns_400(tmp_path: Path) -> None:
    resp = _isolated_client(tmp_path).post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 400
    assert "设置 · 模型设置" in resp.json()["detail"]


def test_send_upstream_failure_returns_502(tmp_path: Path) -> None:
    class FailingProvider:
        name = "fake"
        model_ref = "fake/model-x"

        def complete(self, messages: list[dict[str, str]]) -> str:
            raise ProviderError("调用 fake/model-x 失败: HTTP 401")

    application = create_app(
        model_config_path=tmp_path / "m.json", settings=Settings(_env_file=None)
    )
    application.state.chat_service = ChatService(provider=FailingProvider())
    resp = TestClient(application).post("/api/chat/send", json={"message": "hi"})
    assert resp.status_code == 502
    assert "fake/model-x" in resp.json()["detail"]


def test_chat_service_is_per_app_instance(tmp_path: Path) -> None:
    app_a = create_app(model_config_path=tmp_path / "a.json", settings=Settings(_env_file=None))
    app_b = create_app(model_config_path=tmp_path / "b.json", settings=Settings(_env_file=None))
    client_a, client_b = TestClient(app_a), TestClient(app_b)
    resp = client_a.post("/api/chat/echo", json={"session_id": "iso", "message": "hello"})
    assert resp.status_code == 200
    resp_b = client_b.post("/api/chat/echo", json={"session_id": "other", "message": "hi"})
    assert resp_b.status_code == 200
    assert app_a.state.chat_service.memory.recall("iso")
    assert app_b.state.chat_service.memory.recall("iso") == []
```

Run: `cd backend && uv run pytest tests/test_api.py -q`
Expected: FAIL——`create_app() got an unexpected keyword argument 'model_config_path'`

- [ ] **Step 8: main.py 装配 + router health**

`backend/src/aitester/main.py` 整文件替换为：

```python
from pathlib import Path

from fastapi import FastAPI

from aitester.config import Settings, get_settings
from aitester.interaction.router import router
from aitester.services import ChatService
from aitester.services.model_config import ModelConfigService
from aitester.storage import FileModelConfigRepository

DEFAULT_MODEL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "model_config.json"


def create_app(
    model_config_path: Path | None = None, settings: Settings | None = None
) -> FastAPI:
    s = settings or get_settings()
    path = model_config_path or DEFAULT_MODEL_CONFIG_PATH
    model_config = ModelConfigService(FileModelConfigRepository(path), s)
    application = FastAPI(title="AiTester backend")
    application.state.model_config = model_config
    application.state.chat_service = ChatService(model_config=model_config)
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("aitester.main:app", host=s.host, port=s.port)
```

`backend/src/aitester/interaction/router.py`：删除 `from aitester.services import chat as chat_module` 这一行；新增 `from aitester.services.model_config import ModelConfigService`；`health` 替换为：

```python
@router.get("/health")
def health(request: Request) -> dict[str, object]:
    model_config: ModelConfigService = request.app.state.model_config
    return {
        "ok": True,
        "service": "aitester-backend",
        "llm_provider": model_config.default_uid or "mock",
    }
```

- [ ] **Step 9: 全量确认通过**

Run: `cd backend && uv run pytest -q`
Expected: 40 全绿（factory 5 项与旧 send/health 配置测试删除、新测试补入后）。

- [ ] **Step 10: .env.example 与 .gitignore**

`backend/.env.example` 整文件替换为：

```
HOST=127.0.0.1
PORT=8000

# 以下两个 Key 仅首次启动生成 backend/data/model_config.json 时作为种子导入；
# 之后运行期配置一律在前端「设置 · 模型设置」中管理。
DEEPSEEK_API_KEY=
DASHSCOPE_API_KEY=
```

仓库根 `.gitignore` 的「本地配置（含密钥）」段（`.env` 行）之后追加：

```
# 运行期模型配置（含密钥，不入库）
backend/data/
```

- [ ] **Step 11: 提交**

```bash
git add backend/src backend/tests backend/.env.example .gitignore
git commit -m "feat: 运行期模型配置成为唯一真相——config 瘦身、factory 废弃、send/health 改指配置服务"
```

---

### Task 5: interaction 层——/api/models 四端点

**Files:**
- Modify: `backend/src/aitester/interaction/schemas.py`（追加）
- Modify: `backend/src/aitester/interaction/router.py`
- Test: `backend/tests/test_api.py`（追加）

**Interfaces:**
- Consumes: Task 3/4 的 `ModelConfigService` 写侧方法、`ConfigNotFoundError`、`app.state.model_config`。
- Produces（REST 契约，Task 6 前端逐字对齐）：
  - `GET /api/models` → 200 `ModelsResponse{default_uid: str, providers: [{id,name,base_url,has_key,key_masked,models:[{id,enabled,max_output,context}]}]}`
  - `PUT /api/models/providers/{pid}/key`，body `{api_key: string | null}` → 200 `ModelsResponse`
  - `PUT /api/models/providers/{pid}/models/{mid}/enabled`，body `{enabled: boolean}` → 200 `ModelsResponse`
  - `PUT /api/models/default`，body `{uid: string}` → 200 `ModelsResponse`；配置非法 400；未知 pid/mid 404

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_api.py` 顶部导入区加 `import json`（与 `from pathlib import Path` 并列）；文件末尾追加：

```python
def test_models_view_masks_key(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    resp = c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    assert resp.status_code == 200
    body = c.get("/api/models").json()
    assert "sk-SECRET123456" not in json.dumps(body, ensure_ascii=False)
    provider = body["providers"][0]
    assert provider["has_key"] is True
    assert provider["key_masked"] == "sk-S…3456"
    assert body["default_uid"] == ""


def test_set_default_flow_updates_health(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    resp = c.put("/api/models/default", json={"uid": "deepseek/deepseek-flash"})
    assert resp.status_code == 400
    assert "设置 · 模型设置" in resp.json()["detail"]
    c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    assert c.put("/api/models/default", json={"uid": "nope/x"}).status_code == 404
    assert c.put("/api/models/default", json={"uid": "garbage"}).status_code == 400
    ok = c.put("/api/models/default", json={"uid": "deepseek/deepseek-flash"})
    assert ok.status_code == 200
    assert ok.json()["default_uid"] == "deepseek/deepseek-flash"
    assert c.get("/api/health").json()["llm_provider"] == "deepseek/deepseek-flash"


def test_disable_default_model_cascades(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    c.put("/api/models/providers/deepseek/key", json={"api_key": "sk-SECRET123456"})
    c.put("/api/models/default", json={"uid": "deepseek/deepseek-flash"})
    resp = c.put(
        "/api/models/providers/deepseek/models/deepseek-flash/enabled",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["default_uid"] == "deepseek/deepseek-v4-pro"


def test_models_endpoints_404_for_unknown_provider(tmp_path: Path) -> None:
    c = _isolated_client(tmp_path)
    assert c.put("/api/models/providers/glm/key", json={"api_key": "k"}).status_code == 404
    assert (
        c.put("/api/models/providers/deepseek/models/nope/enabled", json={"enabled": True})
    ).status_code == 404
```

Run: `cd backend && uv run pytest tests/test_api.py -q`
Expected: FAIL——`GET /api/models` 返回 404（端点不存在）

- [ ] **Step 2: schemas 追加**

`backend/src/aitester/interaction/schemas.py` 末尾追加：

```python
class ModelInfo(BaseModel):
    id: str
    enabled: bool
    max_output: int
    context: int


class ProviderInfo(BaseModel):
    id: str
    name: str
    base_url: str
    has_key: bool
    key_masked: str
    models: list[ModelInfo]


class ModelsResponse(BaseModel):
    default_uid: str
    providers: list[ProviderInfo]


class KeyUpdate(BaseModel):
    api_key: str | None = None


class EnabledUpdate(BaseModel):
    enabled: bool


class DefaultUpdate(BaseModel):
    uid: str
```

- [ ] **Step 3: router 追加端点**

`backend/src/aitester/interaction/router.py`：
- 导入行 `from aitester.services.model_config import ModelConfigService` 改为 `from aitester.services.model_config import ConfigNotFoundError, ModelConfigService`；
- `from aitester.interaction.schemas import (...)` 追加 `DefaultUpdate, EnabledUpdate, KeyUpdate, ModelsResponse`（按字母序并入现有括号导入）；
- 文件末尾追加：

```python
def _view(request: Request) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    return ModelsResponse(**model_config.get_view())


@router.get("/models", response_model=ModelsResponse)
def models(request: Request) -> ModelsResponse:
    return _view(request)


@router.put("/models/providers/{pid}/key", response_model=ModelsResponse)
def models_update_key(pid: str, req: KeyUpdate, request: Request) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    try:
        model_config.update_api_key(pid, req.api_key)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return _view(request)


@router.put("/models/providers/{pid}/models/{mid}/enabled", response_model=ModelsResponse)
def models_update_enabled(
    pid: str, mid: str, req: EnabledUpdate, request: Request
) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    try:
        model_config.set_model_enabled(pid, mid, req.enabled)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return _view(request)


@router.put("/models/default", response_model=ModelsResponse)
def models_update_default(req: DefaultUpdate, request: Request) -> ModelsResponse:
    model_config: ModelConfigService = request.app.state.model_config
    try:
        model_config.set_default(req.uid)
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    return _view(request)
```

- [ ] **Step 4: 全量确认通过**

Run: `cd backend && uv run pytest -q`
Expected: 44 全绿。

- [ ] **Step 5: 提交**

```bash
git add backend/src/aitester/interaction backend/tests/test_api.py
git commit -m "feat(interaction): /api/models 四端点——掩码视图、Key 更新、启用开关与默认选择"
```

---

### Task 6: 前端——「⚙ 设置」入口与模型设置弹窗、聊天页联动

**Files:**
- Create: `frontend/src/components/SettingsModal.tsx`
- Modify: `frontend/src/api/client.ts`、`frontend/src/App.tsx`、`frontend/src/pages/ChatPage.tsx`、`frontend/src/App.css`

**Interfaces:**
- Consumes: Task 5 REST 契约（路径/字段逐字一致；`ModelsResponse` JSON 即 `get_view()`）。
- Produces:
  - `client.ts` 导出 `HealthResponse`（含 `llm_provider`）、`ModelInfo`、`ProviderInfo`、`ModelsResponse`、`getHealth()`、`getModels()`、`putApiKey(providerId: string, apiKey: string)`、`putModelEnabled(providerId: string, modelId: string, enabled: boolean)`、`putDefault(uid: string)`；失败统一 `throw new Error(detail)`
  - `SettingsModal` 组件 props `{ onClose: () => void; onChanged: () => void }`
  - `ChatPage` props `{ health: HealthResponse | null; healthError: string | null; onOpenSettings: () => void }`

说明：本仓前端无单测框架，本任务验收门 = `npm run build`（tsc strict）+ Task 7 浏览器人工验收。

- [ ] **Step 1: client.ts 整文件替换**

```ts
export interface HealthResponse {
  ok: boolean;
  service: string;
  llm_provider: string;
}

export interface ModelInfo {
  id: string;
  enabled: boolean;
  max_output: number;
  context: number;
}

export interface ProviderInfo {
  id: string;
  name: string;
  base_url: string;
  has_key: boolean;
  key_masked: string;
  models: ModelInfo[];
}

export interface ModelsResponse {
  default_uid: string;
  providers: ProviderInfo[];
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = `请求失败: HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // 响应体不是 JSON 时保留默认错误文案
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/api/health");
}

export function getModels(): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>("/api/models");
}

export function putApiKey(providerId: string, apiKey: string): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>(`/api/models/providers/${providerId}/key`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function putModelEnabled(
  providerId: string,
  modelId: string,
  enabled: boolean,
): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>(
    `/api/models/providers/${providerId}/models/${modelId}/enabled`,
    {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({ enabled }),
    },
  );
}

export function putDefault(uid: string): Promise<ModelsResponse> {
  return apiFetch<ModelsResponse>("/api/models/default", {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ uid }),
  });
}
```

- [ ] **Step 2: SettingsModal.tsx 新建（整文件）**

```tsx
import { useEffect, useState } from "react";
import {
  getModels,
  putApiKey,
  putDefault,
  putModelEnabled,
  type ModelsResponse,
} from "../api/client";

interface SettingsModalProps {
  onClose: () => void;
  onChanged: () => void;
}

export default function SettingsModal({ onClose, onChanged }: SettingsModalProps) {
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getModels()
      .then((resp) => {
        setModels(resp);
        setSelectedId(resp.providers[0].id);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function run(action: () => Promise<ModelsResponse>): Promise<void> {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const resp = await action();
      setModels(resp);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const selected = models?.providers.find((p) => p.id === selectedId) ?? null;

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>设置 · 模型设置</strong>
          <button className="btn-secondary" onClick={onClose}>关闭</button>
        </div>
        {error !== null && <p className="modal-error">{error}</p>}
        {models === null ? (
          <p>加载中…</p>
        ) : (
          <div className="modal-body">
            <div className="provider-list">
              {models.providers.map((p) => (
                <button
                  key={p.id}
                  className={p.id === selectedId ? "provider-item active" : "provider-item"}
                  onClick={() => {
                    setSelectedId(p.id);
                    setKeyInput("");
                  }}
                >
                  <span>{p.name}</span>
                  <span className="provider-state">
                    {p.has_key ? "● Key 已配置" : "○ Key 未配置"}
                  </span>
                </button>
              ))}
            </div>
            {selected !== null && (
              <div className="provider-form">
                <div className="field-row">
                  <label>Base URL</label>
                  <input className="text-input" value={selected.base_url} readOnly />
                </div>
                <div className="field-row">
                  <label>API Key</label>
                  <input
                    className="text-input"
                    type="password"
                    value={keyInput}
                    placeholder={
                      selected.has_key
                        ? `当前 ${selected.key_masked}（已配置，留空则不变）`
                        : "尚未配置，粘贴 API Key"
                    }
                    onChange={(e) => setKeyInput(e.target.value)}
                  />
                  <button
                    className="btn-primary"
                    disabled={keyInput === "" || saving}
                    onClick={() =>
                      run(async () => {
                        const resp = await putApiKey(selected.id, keyInput);
                        setKeyInput("");
                        return resp;
                      })
                    }
                  >
                    保存 Key
                  </button>
                  {selected.has_key && (
                    <button
                      className="btn-secondary"
                      disabled={saving}
                      onClick={() => run(() => putApiKey(selected.id, ""))}
                    >
                      清除 Key
                    </button>
                  )}
                </div>
                <div className="field-row">
                  <label>默认 LLM</label>
                  <select
                    value={models.default_uid}
                    disabled={saving}
                    onChange={(e) => run(() => putDefault(e.target.value))}
                  >
                    <option value="" disabled>未配置默认模型</option>
                    {models.providers.flatMap((p) =>
                      p.models.map((m) => {
                        const uid = `${p.id}/${m.id}`;
                        const usable = m.enabled && p.has_key;
                        const note = usable ? "" : m.enabled ? " · 未配 Key" : " · 已停用";
                        return (
                          <option key={uid} value={uid} disabled={!usable}>
                            {uid}
                            {note}
                          </option>
                        );
                      }),
                    )}
                  </select>
                </div>
                <table className="model-table">
                  <thead>
                    <tr>
                      <th>模型 ID</th>
                      <th>最大上下文</th>
                      <th>最大输出</th>
                      <th>启用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.models.map((m) => (
                      <tr key={m.id}>
                        <td>{m.id}</td>
                        <td>{m.context.toLocaleString()}</td>
                        <td>{m.max_output.toLocaleString()}</td>
                        <td>
                          <input
                            type="checkbox"
                            checked={m.enabled}
                            disabled={saving}
                            onChange={(e) =>
                              run(() => putModelEnabled(selected.id, m.id, e.target.checked))
                            }
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: App.tsx 整文件替换（设置状态与 health 提升到 App）**

```tsx
import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getHealth, type HealthResponse } from "./api/client";
import SettingsModal from "./components/SettingsModal";
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
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const refreshHealth = useCallback(() => {
    getHealth()
      .then((h) => {
        setHealth(h);
        setHealthError(null);
      })
      .catch((e: Error) => setHealthError(e.message));
  }, []);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

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
        <button className="btn-settings" onClick={() => setSettingsOpen(true)}>⚙ 设置</button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route
            path="/chat"
            element={
              <ChatPage
                health={health}
                healthError={healthError}
                onOpenSettings={() => setSettingsOpen(true)}
              />
            }
          />
          <Route path="/kb" element={<KbPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </main>
      {settingsOpen && (
        <SettingsModal onClose={() => setSettingsOpen(false)} onChanged={refreshHealth} />
      )}
    </div>
  );
}
```

- [ ] **Step 4: ChatPage.tsx 整文件替换（消费 App 下发状态）**

```tsx
import type { HealthResponse } from "../api/client";

interface ChatPageProps {
  health: HealthResponse | null;
  healthError: string | null;
  onOpenSettings: () => void;
}

export default function ChatPage({ health, healthError, onOpenSettings }: ChatPageProps) {
  return (
    <section className="placeholder">
      <p>聊天页 · 骨架占位，后续按原型专项开发</p>
      {healthError !== null && (
        <p className="status-bad">后端未联通：{healthError}（请先启动 backend，见 README）</p>
      )}
      {health !== null && (
        <p className="status-ok">
          后端联通正常：{health.service}（{health.ok ? "ok" : "异常"}）
        </p>
      )}
      {health !== null &&
        (health.llm_provider === "mock" ? (
          <p className="status-bad">
            未配置可用模型，
            <button className="link-btn" onClick={onOpenSettings}>打开 设置 · 模型设置</button>
          </p>
        ) : (
          <p className="status-ok">当前默认模型：{health.llm_provider}</p>
        ))}
    </section>
  );
}
```

- [ ] **Step 5: App.css 末尾追加（暖米色 + 橙主色体系）**

```css
.btn-settings { margin-left: auto; padding: 6px 14px; border-radius: 8px; border: 1px solid #e0cfb4; background: #fff; color: #6b5d4f; cursor: pointer; }
.modal-mask { position: fixed; inset: 0; background: rgba(59, 51, 42, 0.35); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { width: 780px; max-width: 94vw; height: 560px; background: #fffdf8; border: 1px solid #e8ddcc; border-radius: 14px; padding: 16px 20px; display: flex; flex-direction: column; overflow: hidden; }
.modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.modal-body { display: flex; gap: 16px; flex: 1; min-height: 0; }
.provider-list { width: 210px; display: flex; flex-direction: column; gap: 6px; }
.provider-item { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 10px; border-radius: 8px; border: 1px solid transparent; background: none; cursor: pointer; text-align: left; color: #3b332a; }
.provider-item.active { background: #f4e3cf; border-color: #e0cfb4; color: #b35a1b; }
.provider-state { font-size: 12px; color: #8a7a66; }
.provider-form { flex: 1; overflow-y: auto; }
.field-row { display: flex; gap: 8px; align-items: center; margin: 8px 0; }
.field-row label { width: 64px; color: #6b5d4f; font-size: 13px; }
.text-input { flex: 1; padding: 6px 8px; border: 1px solid #e0cfb4; border-radius: 8px; background: #fff; }
.model-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
.model-table th, .model-table td { border-bottom: 1px solid #eee2cf; padding: 6px 8px; font-size: 13px; text-align: left; }
.btn-primary { padding: 6px 12px; border-radius: 8px; border: none; background: #d97a2b; color: #fff; cursor: pointer; }
.btn-primary:disabled { opacity: 0.5; cursor: default; }
.btn-secondary { padding: 6px 12px; border-radius: 8px; border: 1px solid #e0cfb4; background: #fff; color: #6b5d4f; cursor: pointer; }
.link-btn { background: none; border: none; color: #b35a1b; cursor: pointer; padding: 0; text-decoration: underline; }
.modal-error { color: #b3261e; margin: 0 0 8px; }
```

- [ ] **Step 6: 构建验证**

Run: `cd frontend && npm run build`
Expected: tsc 无错误、vite 构建成功。

- [ ] **Step 7: 提交**

```bash
git add frontend/src
git commit -m "feat(frontend): 设置·模型弹窗——Key/启用/默认 LLM 运行期配置与聊天页联动"
```

---

### Task 7: README 更新 + 全量回归 + 进程级冒烟 + 交用户验收

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: 完整文档、验证记录、用户人工验收指引（含真实 Key 冒烟入口，补接入层遗留项）。

- [ ] **Step 1: README「后端（backend/）」章节的端点/测试/配置列表替换为**

```markdown
- `GET /api/health`：联通检查（`llm_provider` 为运行期默认模型 uid，未配置时 `mock`）
- `POST /api/chat/echo`：窄链路演示，响应 trace 穿透七层
  （interaction → services → context → orchestration → adapters → memory → storage），恒走 mock
- `POST /api/chat/send`：真实 LLM 链路，按「设置 · 模型设置」的默认模型调用
  （配置缺失 → 400 指引；上游失败 → 502）
- `GET /api/models` + 三个 `PUT`：模型配置运行期读写（Key 掩码返回，明文永不出口），
  对应前端顶栏「⚙ 设置」弹窗
- 测试：`uv run pytest`
- 配置：复制 `.env.example` 为 `.env`（仅 HOST/PORT + 两个可选种子 Key）；
  运行期模型配置存 `backend/data/model_config.json`（gitignore，含密钥），
  首次启动自动从 `.env` 种子导入，之后在前端「设置 · 模型设置」管理；
  **Key 不入库、不出现在任何响应/日志/异常明文**
```

- [ ] **Step 2: 全量测试与构建**

Run: `cd backend && uv run pytest -q` → 44 全绿；`cd frontend && npm run build` → 成功。

- [ ] **Step 3: 进程级冒烟（假 Key，绝不发起真实外呼——冒烟只做配置链路，不打 send）**

```bash
cd backend && uv run python -m aitester.main &
sleep 3
curl -s http://127.0.0.1:8000/api/health
curl -s -X PUT http://127.0.0.1:8000/api/models/providers/deepseek/key -H "Content-Type: application/json" -d "{\"api_key\":\"sk-smoke123456789\"}"
curl -s -X PUT http://127.0.0.1:8000/api/models/default -H "Content-Type: application/json" -d "{\"uid\":\"deepseek/deepseek-flash\"}"
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/models
curl -s -X PUT http://127.0.0.1:8000/api/models/providers/deepseek/key -H "Content-Type: application/json" -d "{\"api_key\":\"\"}"
curl -s http://127.0.0.1:8000/api/health
```

Expected: 第二次 health 报 `deepseek/deepseek-flash`；GET /api/models 不含 `sk-smoke123456789` 明文（掩码 `sk-s…6789`）；清除 Key 后级联回落、最后一个 health 回 `mock` 或回落值。结束后杀掉了起的进程（`taskkill /PID <pid> /T /F` 仅针对本次启动的 python/uvicorn）。
注意：若本机 `.env` 已配真实 Key，种子文件会带真实 Key——冒烟只 PUT 清除再恢复，或删 `backend/data/model_config.json` 让下次启动重新种子（本机数据，gitignore）。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 更新运行期模型配置说明与 /api/models 端点"
```

- [ ] **Step 5: 请用户人工验收**

告知用户：`powershell -File scripts/dev.ps1`（5173 被 QwenPaw 占用时加 `-FrontendPort 5175`），打开对应的 http://localhost 地址 → 顶栏「⚙ 设置」→ 填真实 Key → 选默认 LLM → 聊天页状态行确认实时更新 → 用户配好真实 Key 后补一次真实 `POST /api/chat/send` 冒烟（接入层遗留项）。
