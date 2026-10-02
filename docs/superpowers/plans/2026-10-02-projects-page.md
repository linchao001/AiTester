# 项目管理页（项目 CRUD + 只读知识库配置）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `/projects` 项目管理页——后端项目注册表（JSON 落盘 + CRUD 四端点）与前端表格/弹窗，每个项目带一条默认且不可改的「知识库」配置，值为别名 `kb`，映射 reme 的 `zhb_kb` 且映射结果永不出现在界面与响应里。

**Architecture:** 服务层 `ProjectService` 是校验与不可改裁定的唯一落点（`dir`/`kb` 冻结、同名唯一、最少保留 1 个项目），交互层 `interaction/projects.py` 只做薄映射（`ProjectConfigError → 400`、`ConfigNotFoundError → 404`）；知识库别名解析集中在新模块 `services/kb/aliases.py`，本期不改检索/写盘链路。前端 `ProjectsPage` 走既有 `.p-table`/`.mask`/`.field` CSS 与 `ApiError` 通道，校验顺序与文案逐条对齐原型。

**Tech Stack:** Python 3.12 + FastAPI + pydantic v2（uv 管理，`JsonConfigRepository` 原子写）；React 18 + TypeScript + Vite；无新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-02-projects-page-design.md`（用户裁定的约束来源，与本计划一并阅读）

## Global Constraints

- 范围只有项目 CRUD：**不动聊天链路**——`SendRequest` 不加 `project_id`、工具 `cwd` 仍恒 `"."`、会话键仍是 `agent_id:session_id`、`/api/kb/browse/*` 与 reme 实例池继续用 `settings.kb_id`。
- **脱敏红线**：`kb → zhb_kb` 的映射结果（真实 reme 知识库 id）与 KB 实体根路径**不得**出现在任何 API 响应体、前端文案或用户可见 detail 中；服务端代码与测试里可以出现真实 kb_id（例如别名层测试断言解析结果）。把用户自己提交的字符串原样回显在校验 detail 里不算泄漏（用户并未从中得知映射）。
- 文案口径：面向用户的 API detail 与 UI 中文；注释与 docstring 中文；本期不新增工具，故无模型侧英文文案。
- 知识库配置默认且不可改：默认值恒为别名 `"kb"`；`PUT` 携带不同的 `dir` 或 `kb` → 400（携带相同值放行，保持幂等）。
- `dir` 服务端**只校验绝对路径形态**（原型判据 `ABS_PATH`），不 `stat`、不创建、不列目录。
- 种子为空项目列表：不造假项目、不造假目录；`会话数` 本期恒 0；表格不出「当前/设为当前」。
- 运行纪律：不重启/杀掉/占用在跑的 8000 与 5173；`backend/data/*.json` 对本机是只读运行期状态，**测试一律注入 `tmp_path`**；master 直提、**不推远端**。
- 门禁命令：后端 `cd backend && uv run pytest -q`；前端 `cd frontend && npm run build`（0 错误）。
- SDD 纪律：实施型子代理**前台派发并在回执后立即 `git log` 核验**；报告文件早建、边做边追加。

## File Structure

| 文件 | 责任 |
| --- | --- |
| Create `backend/src/aitester/services/kb/aliases.py` | 知识库别名注册表与解析（`kb → settings.kb_id`），未知别名抛错 |
| Create `backend/src/aitester/services/project_config.py` | 项目注册表真相：形状归一、字段校验、不可改裁定、CRUD |
| Modify `backend/src/aitester/interaction/schemas.py` | `ProjectInfo` / `ProjectsResponse` / `ProjectCreateRequest` / `ProjectUpdateRequest` |
| Create `backend/src/aitester/interaction/projects.py` | `/api/projects` 四端点与错误码映射 |
| Modify `backend/src/aitester/main.py` | `create_app` 增 `projects_path` 注入缝、`state.project_config`、`include_router` |
| Create `backend/tests/test_kb_aliases.py`、`backend/tests/test_project_config.py`、`backend/tests/test_api_projects.py` | 三层各自行为测试 |
| Modify `frontend/src/api/client.ts` | `Project` 类型 + 四个 API 函数；`apiFetch` 支持 204 空体 |
| Rewrite `frontend/src/pages/ProjectsPage.tsx` | 表格页：计数、七列、行操作、空态、toast |
| Create `frontend/src/pages/projects/ProjectFormModal.tsx` | 新建/编辑弹窗：四可编辑字段 + 只读知识库行 + 原型校验顺序 + 浏览… |

---

### Task 1: 知识库别名层 `services/kb/aliases.py`

**Files:**
- Create: `backend/src/aitester/services/kb/aliases.py`
- Test: `backend/tests/test_kb_aliases.py`

**Interfaces:**
- Consumes: `aitester.services.kb.paths.resolve_kb_bases_dir(settings)`（已存在，签名 `-> Path`）、`settings.kb_id`（`Settings`，默认 `"zhb_kb"`）。
- Produces: `PROJECT_KB_DEFAULT: str == "kb"`、`registered_aliases() -> list[str]`、`is_registered(alias: str) -> bool`、`UnknownKbAlias(detail)`（带 `.detail`）、`resolve_kb_id(alias: str, settings: Any) -> str`、`resolve_kb_root_for(alias: str, settings: Any) -> Path`。Task 2 只用 `PROJECT_KB_DEFAULT` 与 `is_registered`；`resolve_kb_id`/`resolve_kb_root_for` 是后续多 KB 专项的接线点，本期由测试锁定行为。

- [ ] **Step 1: 写失败测试** — 新建 `backend/tests/test_kb_aliases.py`：

```python
from pathlib import Path

import pytest

from aitester.config import Settings
from aitester.services.kb.aliases import (
    PROJECT_KB_DEFAULT,
    UnknownKbAlias,
    is_registered,
    registered_aliases,
    resolve_kb_id,
    resolve_kb_root_for,
)


def test_default_alias_is_kb_and_only_registered_alias() -> None:
    assert PROJECT_KB_DEFAULT == "kb"
    assert registered_aliases() == ["kb"]
    assert is_registered("kb") is True
    assert is_registered("") is False
    assert is_registered("KB") is False  # 别名大小写敏感，配置不猜用户意图


def test_resolve_kb_id_maps_alias_to_settings_kb_id(tmp_path: Path) -> None:
    s = Settings(_env_file=None, kb_id="zhb_kb", kb_bases_dir=str(tmp_path / "bases"))
    assert resolve_kb_id("kb", s) == "zhb_kb"


def test_resolve_unknown_alias_raises_chinese_detail() -> None:
    s = Settings(_env_file=None)
    with pytest.raises(UnknownKbAlias) as exc:
        resolve_kb_id("nope", s)
    assert "未知知识库" in str(exc.value.detail)


def test_resolve_kb_root_for_uses_bases_dir(tmp_path: Path) -> None:
    s = Settings(_env_file=None, kb_id="zhb_kb", kb_bases_dir=str(tmp_path / "bases"))
    assert resolve_kb_root_for("kb", s) == (tmp_path / "bases" / "zhb_kb").resolve()
```

- [ ] **Step 2: 跑到失败** — `cd backend && uv run pytest tests/test_kb_aliases.py -q`；预期 `ModuleNotFoundError: No module named 'aitester.services.kb.aliases'`。
- [ ] **Step 3: 实现** — 新建 `backend/src/aitester/services/kb/aliases.py`：

```python
"""知识库别名注册表：项目配置只存别名，真实 reme 知识库 id 在此集中解析。

脱敏线：`resolve_kb_id` 的结果（如 zhb_kb）与实体根路径只供服务端使用，任何 API 响应
与界面文案一律不得回显。本期检索/写盘链路仍走 `settings.kb_id`（知识库全局单份裁定），
本模块承担项目 `kb` 字段的写时校验口径，并作为后续多 KB 专项的接线点。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aitester.services.kb.paths import resolve_kb_bases_dir

PROJECT_KB_DEFAULT = "kb"

# 别名 → 真实 kb_id 由 settings 供值（不写死部署常量，换 KB 只改配置不改代码）
_ALIASES = (PROJECT_KB_DEFAULT,)


class UnknownKbAlias(RuntimeError):
    """未知知识库别名，交互层映射 400，detail 面向用户且可照做。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def registered_aliases() -> list[str]:
    return list(_ALIASES)


def is_registered(alias: str) -> bool:
    return alias in _ALIASES


def resolve_kb_id(alias: str, settings: Any) -> str:
    if not is_registered(alias):
        raise UnknownKbAlias(
            f"未知知识库「{alias or '（空）'}」，可选值：{'、'.join(_ALIASES)}"
        )
    return settings.kb_id


def resolve_kb_root_for(alias: str, settings: Any) -> Path:
    return resolve_kb_bases_dir(settings) / resolve_kb_id(alias, settings)
```

- [ ] **Step 4: 跑到通过** — `cd backend && uv run pytest tests/test_kb_aliases.py -q`，预期 4 passed。
- [ ] **Step 5: 全量回归 + 提交**

```bash
cd backend && uv run pytest -q
git add backend/src/aitester/services/kb/aliases.py backend/tests/test_kb_aliases.py
git commit -m "feat(kb): 知识库别名层——项目侧只存 kb 别名，解析集中且不外泄真实 kb_id"
```

---

### Task 2: 项目注册表服务 `ProjectService`

**Files:**
- Create: `backend/src/aitester/services/project_config.py`
- Test: `backend/tests/test_project_config.py`

**Interfaces:**
- Consumes: Task 1 的 `PROJECT_KB_DEFAULT`、`is_registered`；`aitester.agents.AGENT_CATALOG`（只含可见智能体，`spec.id`）与 `aitester.agents.PLATFORM_AGENT_CATALOG`（平台内置，如 `kb_assistant`）；`aitester.services.model_config.ConfigNotFoundError`（已有，`.detail`）；`aitester.storage.JsonConfigRepository`（`load() -> dict | None` / `save(dict)`）。
- Produces: `ProjectConfigError(detail)`、`ABS_PATH`、`ID_RE`、`NAME_MAX=30`/`DESC_MAX=200`/`DIR_MAX=500`、`visible_agent_ids() -> list[str]`、`ProjectService(repo)` 及其方法 `list_projects() -> list[dict]`、`get(project_id) -> dict`、`create(*, name, desc, dir_, agents, kb=PROJECT_KB_DEFAULT) -> dict`、`update(project_id, *, name, desc, agents, dir_=None, kb=None) -> dict`、`delete(project_id) -> None`。项目 dict 键固定 `{id,name,desc,dir,agents,kb}`，`list_projects()` 每条追加 `session_count: 0`。Task 3 的端点只做映射与传参。

- [ ] **Step 1: 写失败测试** — 新建 `backend/tests/test_project_config.py`：

```python
import pytest

from aitester.services.kb.aliases import PROJECT_KB_DEFAULT
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import (
    DESC_MAX,
    DIR_MAX,
    NAME_MAX,
    ProjectConfigError,
    ProjectService,
    visible_agent_ids,
)
from aitester.storage import FileJsonConfigRepository


@pytest.fixture()
def svc(tmp_path):
    return ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))


def _mk(svc, name="订单系统", **kw):
    kw.setdefault("desc", "交易链路")
    kw.setdefault("dir_", "D:/work/projects/order")
    kw.setdefault("agents", [visible_agent_ids()[0]])
    return svc.create(name=name, **kw)


def test_seed_is_empty_and_persisted(tmp_path):
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    assert svc.list_projects() == []
    assert (tmp_path / "projects.json").exists()


def test_create_defaults_and_view_keys(svc):
    p = _mk(svc)
    assert set(p) == {"id", "name", "desc", "dir", "agents", "kb"}
    assert p["kb"] == PROJECT_KB_DEFAULT
    assert p["id"].startswith("proj_")
    assert svc.list_projects()[0]["session_count"] == 0


def test_create_rejects_blank_and_overlong_fields(svc):
    first = _mk(svc)
    with pytest.raises(ProjectConfigError, match="请填写项目名称"):
        _mk(svc, name="   ")
    with pytest.raises(ProjectConfigError, match="已存在同名项目"):
        _mk(svc, name=first["name"])
    with pytest.raises(ProjectConfigError, match=f"不能超过 {NAME_MAX} 字"):
        _mk(svc, name="项" * (NAME_MAX + 1))
    with pytest.raises(ProjectConfigError, match=f"不能超过 {DESC_MAX} 字"):
        _mk(svc, desc="描" * (DESC_MAX + 1))


def test_dir_must_be_absolute_any_shape(svc):
    for bad in ("order-system", "work/projects/order", "relative\\path"):
        with pytest.raises(ProjectConfigError, match="绝对路径"):
            _mk(svc, name=f"坏{bad}", dir_=bad)
    for good in ("D:/work/a", "/home/me/a", "\\\\srv\\share\\a", "~/a"):
        assert _mk(svc, name=f"好{good}", dir_=good)["dir"]


def test_dir_strips_trailing_separators_but_keeps_root(svc):
    assert _mk(svc, name="尾斜杠", dir_="D:/work/a///")["dir"] == "D:/work/a"
    assert _mk(svc, name="纯根", dir_="D:/")["dir"] == "D:/"


def test_dir_overlong_rejected(svc):
    with pytest.raises(ProjectConfigError, match=f"不能超过 {DIR_MAX} 字"):
        _mk(svc, name="超长", dir_="D:/" + "x" * DIR_MAX)


def test_agents_required_deduped_and_whitelisted(svc):
    with pytest.raises(ProjectConfigError, match="请至少选择一个智能体"):
        _mk(svc, name="无智能体", agents=[])
    with pytest.raises(ProjectConfigError, match="未知智能体"):
        _mk(svc, name="假智能体", agents=["nope"])
    # 平台内置智能体不出现在可见目录，也不允许被项目启用
    assert "kb_assistant" not in visible_agent_ids()
    with pytest.raises(ProjectConfigError, match="不可启用"):
        _mk(svc, name="平台智能体", agents=["kb_assistant"])
    head = visible_agent_ids()[0]
    p = _mk(svc, name="去重", agents=[head, head])
    assert p["agents"] == [head]


def test_kb_alias_must_be_registered(svc):
    with pytest.raises(ProjectConfigError, match="未知知识库"):
        _mk(svc, name="坏KB", kb="zhb_kb")
    assert _mk(svc, name="默认KB")["kb"] == PROJECT_KB_DEFAULT


def test_view_never_leaks_real_kb_identity(svc):
    _mk(svc)
    blob = str(svc.list_projects()).lower()
    assert "zhb" not in blob


def test_get_unknown_id_raises_404_style(svc):
    with pytest.raises(ConfigNotFoundError, match="未知项目"):
        svc.get("proj_00000000")


def test_update_name_desc_agents_only(svc):
    p = _mk(svc)
    got = svc.update(p["id"], name="支付中心", desc="", agents=visible_agent_ids())
    assert got["name"] == "支付中心" and got["desc"] == ""
    # 同名仍然唯一（排除自身后可用）
    _mk(svc, name="会员中心")
    with pytest.raises(ProjectConfigError, match="已存在同名项目"):
        svc.update(p["id"], name="会员中心", desc="", agents=visible_agent_ids())


def test_update_immutable_dir_and_kb_idempotent(svc):
    p = _mk(svc)
    assert svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"],
                      dir_=p["dir"], kb=p["kb"])["id"] == p["id"]
    with pytest.raises(ProjectConfigError, match="本地文件目录创建后不可修改"):
        svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"], dir_="E:/other")
    with pytest.raises(ProjectConfigError, match="知识库配置创建后不可修改"):
        svc.update(p["id"], name=p["name"], desc=p["desc"], agents=p["agents"], kb="other")


def test_update_does_not_revalidate_frozen_dir(tmp_path):
    # 读侧自愈可能留下非绝对路径的历史 dir：编辑不得因此被「目录必须是绝对路径」锁死
    repo = FileJsonConfigRepository(tmp_path / "projects.json")
    repo.save({"version": 1, "projects": [
        {"id": "proj_aaaaaaaa", "name": "旧项目", "desc": "", "dir": "relative/old",
         "agents": [visible_agent_ids()[0]], "kb": "kb"}]})
    svc = ProjectService(repo)
    got = svc.update("proj_aaaaaaaa", name="旧项目改名", desc="", agents=visible_agent_ids())
    assert got["name"] == "旧项目改名" and got["dir"] == "relative/old"


def test_delete_last_project_protected(svc):
    p = _mk(svc)
    with pytest.raises(ProjectConfigError, match="至少需要保留 1 个项目"):
        svc.delete(p["id"])
    q = _mk(svc, name="会员中心", dir_="D:/work/m")
    svc.delete(q["id"])
    assert [x["id"] for x in svc.list_projects()] == [p["id"]]
    with pytest.raises(ConfigNotFoundError, match="未知项目"):
        svc.delete(q["id"])


def test_normalization_self_heals_hand_edited_junk(tmp_path):
    repo = FileJsonConfigRepository(tmp_path / "projects.json")
    repo.save({"version": 1, "projects": [
        "not-a-dict",
        {"id": "evil/..", "name": "  坏 id  ", "dir": "D:/x", "agents": ["ghost"], "kb": "nope"},
        {"name": "", "dir": "", "agents": []},
    ]})
    svc = ProjectService(repo)
    rows = svc.list_projects()
    assert len(rows) == 2
    assert all(r["id"].startswith("proj_") for r in rows)
    assert all(r["kb"] == PROJECT_KB_DEFAULT for r in rows)
    assert all(set(r["agents"]) <= set(visible_agent_ids()) for r in rows)
    assert rows[1]["name"] == "未命名项目"
    # 自愈结果立即回写：磁盘不再留坏形状
    assert ProjectService(FileJsonConfigRepository(tmp_path / "projects.json")).list_projects() == rows


def test_persist_across_service_instances(svc, tmp_path):
    p = _mk(svc)
    again = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    assert [x["id"] for x in again.list_projects()] == [p["id"]]
    assert again.get(p["id"])["dir"] == p["dir"]
```

- [ ] **Step 2: 跑到失败** — `cd backend && uv run pytest tests/test_project_config.py -q`；预期 `ModuleNotFoundError: No module named 'aitester.services.project_config'`。
- [ ] **Step 3: 实现** — 新建 `backend/src/aitester/services/project_config.py`：

```python
"""项目注册表：JSON 落盘为唯一真相，校验与不可改裁定集中在服务层。

知识库字段只存别名（默认 `kb`），真实 reme 知识库 id 与实体根路径由 `services/kb/aliases`
解析且永不出现在返回值里（脱敏裁定）。本地文件目录只校验绝对路径形态，不触磁盘、
不建目录——浏览… 由前端拼接，路径真相归用户。
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any

from aitester.agents import AGENT_CATALOG, PLATFORM_AGENT_CATALOG
from aitester.services.kb.aliases import PROJECT_KB_DEFAULT, is_registered, registered_aliases
from aitester.services.model_config import ConfigNotFoundError
from aitester.storage import JsonConfigRepository

# 与 prototype/index.html:1809 同款判据：盘符 / 根 / UNC / ~ 前缀
ABS_PATH = re.compile(r"^([A-Za-z]:[\\/]|/|\\\\|~[\\/])")
ID_RE = re.compile(r"^proj_[0-9a-f]{8}$")
NAME_MAX = 30
DESC_MAX = 200
DIR_MAX = 500
PLATFORM_AGENT_IDS = frozenset(spec.id for spec in PLATFORM_AGENT_CATALOG)


class ProjectConfigError(RuntimeError):
    """项目配置校验失败，交互层映射 400，detail 面向用户且可照做。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def visible_agent_ids() -> list[str]:
    """项目可启用的智能体：只取可见目录，平台内置智能体（如 kb_assistant）不在其中。"""
    return [spec.id for spec in AGENT_CATALOG]


def _new_id(existing: list[dict[str, Any]]) -> str:
    ids = {str(p.get("id")) for p in existing}
    while True:
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        if pid not in ids:
            return pid


def _clean_dir(dir_: str) -> str:
    """剥尾部分隔符（原型同款），但纯根路径 `D:/` 不能剥成空串。"""
    value = (dir_ or "").strip()
    stripped = re.sub(r"[\\/]+$", "", value)
    return stripped or value


def _validate_dir(dir_: str) -> str:
    """只校验形态（绝对路径），不 stat、不建目录。仅创建时调用——dir 冻结后不再重复校验。"""
    clean = _clean_dir(dir_)
    if not clean:
        raise ProjectConfigError("请填写本地文件目录")
    if not ABS_PATH.match(clean):
        raise ProjectConfigError("目录必须是绝对路径，例如 D:/work/projects/order-system")
    if len(clean) > DIR_MAX:
        raise ProjectConfigError(f"本地文件目录不能超过 {DIR_MAX} 字")
    return clean


def _validate(*, name: str, desc: str, agents: list[str], kb: str) -> dict[str, Any]:
    clean_name = (name or "").strip()
    if not clean_name:
        raise ProjectConfigError("请填写项目名称")
    if len(clean_name) > NAME_MAX:
        raise ProjectConfigError(f"项目名称不能超过 {NAME_MAX} 字")
    clean_desc = (desc or "").strip()
    if len(clean_desc) > DESC_MAX:
        raise ProjectConfigError(f"项目描述不能超过 {DESC_MAX} 字")
    unique: list[str] = []
    for agent_id in agents or []:
        if agent_id not in unique:
            unique.append(agent_id)
    if not unique:
        raise ProjectConfigError("请至少选择一个智能体")
    allowed = visible_agent_ids()
    # 平台内置智能体先判：它们不在可见目录，但错误要说清「是内置的」而非笼统「未知」
    platform = [a for a in unique if a in PLATFORM_AGENT_IDS]
    if platform:
        raise ProjectConfigError(f"智能体「{'、'.join(platform)}」是平台内置智能体，不可启用")
    unknown = [a for a in unique if a not in allowed]
    if unknown:
        raise ProjectConfigError(f"未知智能体「{'、'.join(unknown)}」，请选择要启用的智能体")
    clean_kb = (kb or "").strip()
    if not is_registered(clean_kb):
        raise ProjectConfigError(
            f"未知知识库「{clean_kb or '（空）'}」，可选值：{'、'.join(registered_aliases())}"
        )
    return {"name": clean_name, "desc": clean_desc, "agents": unique, "kb": clean_kb}


def _default_config() -> dict[str, Any]:
    # 空种子：不造假项目、不造假目录（真实项目由用户在项目页创建）
    return {"version": 1, "projects": []}


def _normalized(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """按目录与判据重建形状：坏条目自愈而不报错，与能力配置读侧自愈口径一致。"""
    raw = config.get("projects")
    raw = raw if isinstance(raw, list) else []
    allowed = visible_agent_ids()
    projects: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kb = str(item.get("kb") or "")
        if not is_registered(kb):
            kb = PROJECT_KB_DEFAULT
        pid = str(item.get("id") or "")
        if not ID_RE.match(pid) or any(p["id"] == pid for p in projects):
            pid = _new_id(projects)
        agents = [a for a in (item.get("agents") or []) if isinstance(a, str) and a in allowed]
        if not agents and allowed:
            agents = [allowed[0]]
        name = str(item.get("name") or "").strip()[:NAME_MAX] or "未命名项目"
        projects.append({
            "id": pid,
            "name": name,
            "desc": str(item.get("desc") or "").strip()[:DESC_MAX],
            "dir": _clean_dir(str(item.get("dir") or ""))[:DIR_MAX],
            "agents": agents,
            "kb": kb,
        })
    normalized = {"version": 1, "projects": projects}
    return normalized, normalized != config


class ProjectService:
    """项目真相：构造时 load（缺则种子并落盘），每次变更立即 save。"""

    def __init__(self, repo: JsonConfigRepository) -> None:
        self._repo = repo
        config = repo.load()
        if config is None:
            config = _default_config()
            repo.save(config)
        else:
            config, drifted = _normalized(config)
            if drifted:
                repo.save(config)
        self._config = config

    def _find(self, project_id: str) -> dict[str, Any]:
        for p in self._config["projects"]:
            if p["id"] == project_id:
                return p
        raise ConfigNotFoundError(f"未知项目「{project_id}」")

    def _ensure_name_free(self, name: str, exclude_id: str | None) -> None:
        for p in self._config["projects"]:
            if p["name"] == name and p["id"] != exclude_id:
                raise ProjectConfigError(f"已存在同名项目「{name}」，请换一个名称")

    def list_projects(self) -> list[dict[str, Any]]:
        # 会话键尚无项目维度（本期范围裁定），真实会话数留给聊天专项，这里恒 0
        return [{**copy.deepcopy(p), "session_count": 0} for p in self._config["projects"]]

    def get(self, project_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._find(project_id))

    def create(
        self,
        *,
        name: str,
        desc: str,
        dir_: str,
        agents: list[str],
        kb: str = PROJECT_KB_DEFAULT,
    ) -> dict[str, Any]:
        # 顺序照原型 btnProjSave：名称/描述 → 目录 → 智能体 → 知识库别名
        clean_dir = _validate_dir(dir_)
        clean = _validate(name=name, desc=desc, agents=agents, kb=kb)
        self._ensure_name_free(clean["name"], None)
        record = {"id": _new_id(self._config["projects"]), **clean, "dir": clean_dir}
        self._config["projects"].append(record)
        self._repo.save(self._config)
        return copy.deepcopy(record)

    def update(
        self,
        project_id: str,
        *,
        name: str,
        desc: str,
        agents: list[str],
        dir_: str | None = None,
        kb: str | None = None,
    ) -> dict[str, Any]:
        stored = self._find(project_id)
        # 不可改字段：传相同值放行（前端整体提交会带上原值），传不同值拒绝
        if dir_ is not None and _clean_dir(dir_) != stored["dir"]:
            raise ProjectConfigError("本地文件目录创建后不可修改")
        if kb is not None and (kb or "").strip() != stored["kb"]:
            raise ProjectConfigError("知识库配置创建后不可修改")
        # dir/kb 已冻结，编辑时不再重复校验其形态（读侧自愈过的坏 dir 也不该把项目锁死）
        clean = _validate(name=name, desc=desc, agents=agents, kb=stored["kb"])
        self._ensure_name_free(clean["name"], project_id)
        stored.update(clean)
        self._repo.save(self._config)
        return copy.deepcopy(stored)

    def delete(self, project_id: str) -> None:
        stored = self._find(project_id)
        if len(self._config["projects"]) <= 1:
            raise ProjectConfigError("至少需要保留 1 个项目，无法删除")
        self._config["projects"].remove(stored)
        self._repo.save(self._config)
```

- [ ] **Step 4: 跑到通过** — `cd backend && uv run pytest tests/test_project_config.py -q`，预期 16 passed。若 `test_dir_must_be_absolute_any_shape` 里 `"relative\\path"` 被 `ABS_PATH` 误放行，说明 UNC 分支 `\\\\` 写成了单反斜杠判据——按实现里的 `r"^([A-Za-z]:[\\/]|/|\\\\|~[\\/])"` 修正（四个反斜杠匹配两个字符的 UNC 前缀）。
- [ ] **Step 5: 全量回归 + 提交**

```bash
cd backend && uv run pytest -q
git add backend/src/aitester/services/project_config.py backend/tests/test_project_config.py
git commit -m "feat(projects): 项目注册表服务——空种子、绝对路径与智能体白名单校验、dir/kb 冻结、最少保留 1 个"
```

---

### Task 3: `/api/projects` 端点与应用接线

**Files:**
- Modify: `backend/src/aitester/interaction/schemas.py`（追加 4 个模型，接在 `KbDraft`/`SendResponse` 之后）
- Create: `backend/src/aitester/interaction/projects.py`
- Modify: `backend/src/aitester/main.py:19-61`（`create_app` 签名、服务装配、`include_router`）
- Test: `backend/tests/test_api_projects.py`

**Interfaces:**
- Consumes: Task 2 的 `ProjectService` 全部方法、`ProjectConfigError`；既有 `ConfigNotFoundError`（404 语义）。
- Produces: HTTP 契约 `GET /api/projects` → `{projects: ProjectInfo[]}`；`POST /api/projects` → 201 + `ProjectInfo`；`PUT /api/projects/{id}` → `ProjectInfo`；`DELETE /api/projects/{id}` → 204 无体；`ProjectInfo = {id,name,desc,dir,agents,kb,session_count}`；请求体字段名 `dir`（服务层参数是 `dir_`，端点做转写）。`app.state.project_config` 与 `create_app(projects_path=...)` 注入缝。Task 4 前端按此契约实现。

- [ ] **Step 1: 写失败测试** — 新建 `backend/tests/test_api_projects.py`：

```python
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app


# 与 test_kb_browse.py 同款：项目端点不碰 reme，注入假 manager 免起真实实例
class _NoopKbManager:
    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})

    def run_job_sync(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        projects_path=tmp_path / "projects.json",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )
    return TestClient(app)


def _body(client, **over):
    payload = {
        "name": "订单系统",
        "desc": "交易链路",
        "dir": "D:/work/projects/order",
        "agents": ["case_design"],
    }
    payload.update(over)
    return payload


def test_list_is_empty_json_array(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}


def test_create_returns_201_with_kb_alias_and_zero_sessions(client):
    resp = client.post("/api/projects", json=_body(client))
    assert resp.status_code == 201
    p = resp.json()
    assert p["kb"] == "kb" and p["session_count"] == 0
    assert set(p) == {"id", "name", "desc", "dir", "agents", "kb", "session_count"}
    assert client.get("/api/projects").json()["projects"][0]["id"] == p["id"]


def test_response_body_never_contains_real_kb_identity(client):
    client.post("/api/projects", json=_body(client))
    assert "zhb" not in client.get("/api/projects").text.lower()


def test_create_persists_to_injected_path_not_data_dir(client, tmp_path):
    client.post("/api/projects", json=_body(client))
    assert (tmp_path / "projects.json").exists()
    assert "订单系统" in (tmp_path / "projects.json").read_text(encoding="utf-8")


def test_validation_maps_to_400_with_chinese_detail(client):
    assert client.post("/api/projects", json=_body(client, name="  ")).status_code == 400
    bad = client.post("/api/projects", json=_body(client, dir="work/order"))
    assert bad.status_code == 400 and "绝对路径" in bad.json()["detail"]
    agents = client.post("/api/projects", json=_body(client, agents=["kb_assistant"]))
    assert agents.status_code == 400 and "不可启用" in agents.json()["detail"]
    kb = client.post("/api/projects", json=_body(client, kb="zhb_kb"))
    assert kb.status_code == 400 and "未知知识库" in kb.json()["detail"]


def test_duplicate_name_rejected(client):
    assert client.post("/api/projects", json=_body(client)).status_code == 201
    dup = client.post("/api/projects", json=_body(client, dir="D:/work/other"))
    assert dup.status_code == 400 and "同名项目" in dup.json()["detail"]


def test_update_immutable_fields_and_unknown_id(client):
    pid = client.post("/api/projects", json=_body(client)).json()["id"]
    ok = client.put(f"/api/projects/{pid}", json=_body(client, name="支付中心"))
    assert ok.status_code == 200 and ok.json()["name"] == "支付中心"
    # 不带 dir/kb 也合法（不传即不改）
    assert client.put(f"/api/projects/{pid}", json={
        "name": "支付中心", "desc": "", "agents": ["case_design"]}).status_code == 200
    clash = client.put(f"/api/projects/{pid}", json=_body(client, dir="E:/x"))
    assert clash.status_code == 400 and "本地文件目录" in clash.json()["detail"]
    kb = client.put(f"/api/projects/{pid}", json=_body(client, kb="other"))
    assert kb.status_code == 400 and "知识库配置" in kb.json()["detail"]
    ghost = client.put("/api/projects/proj_00000000", json=_body(client, name="幽灵"))
    assert ghost.status_code == 404 and "未知项目" in ghost.json()["detail"]


def test_delete_204_then_last_project_protected(client):
    a = client.post("/api/projects", json=_body(client)).json()["id"]
    b = client.post("/api/projects", json=_body(client, name="会员中心", dir="D:/work/m")).json()["id"]
    assert client.delete(f"/api/projects/{b}").status_code == 204
    assert client.delete(f"/api/projects/{b}").status_code == 404
    last = client.delete(f"/api/projects/{a}")
    assert last.status_code == 400 and "至少需要保留 1 个项目" in last.json()["detail"]


def test_unknown_project_id_404(client):
    assert client.delete("/api/projects/proj_deadbeef").status_code == 404
    assert client.put("/api/projects/proj_deadbeef", json=_body(client)).status_code == 404


def test_malformed_project_items_self_heal_on_read(tmp_path):
    (tmp_path / "projects.json").write_text(
        '{"version":1,"projects":[{"id":"evil","name":"坏","dir":"D:/x","agents":["ghost"],"kb":"nope"}]}',
        encoding="utf-8")
    app = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.json",
        projects_path=tmp_path / "projects.json",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )
    row = TestClient(app).get("/api/projects").json()["projects"][0]
    assert row["id"].startswith("proj_") and row["kb"] == "kb"
    assert row["agents"] == ["case_design"]
```

- [ ] **Step 2: 跑到失败** — `cd backend && uv run pytest tests/test_api_projects.py -q`；预期 `TypeError: create_app() got an unexpected keyword argument 'projects_path'`。
- [ ] **Step 3: schemas** — 在 `backend/src/aitester/interaction/schemas.py` 末尾追加：

```python
class ProjectInfo(BaseModel):
    """项目条目：kb 只回别名，真实知识库 id 与实体路径不外泄。"""

    id: str
    name: str
    desc: str
    dir: str
    agents: list[str]
    kb: str
    session_count: int = 0


class ProjectsResponse(BaseModel):
    projects: list[ProjectInfo]


class ProjectCreateRequest(BaseModel):
    name: str
    desc: str = ""
    dir: str
    agents: list[str]
    kb: str | None = None


class ProjectUpdateRequest(BaseModel):
    name: str
    desc: str = ""
    agents: list[str]
    # 不可改字段：不传即不改，传了必须与原值相同（判等在服务层）
    dir: str | None = None
    kb: str | None = None
```

- [ ] **Step 4: 端点模块** — 新建 `backend/src/aitester/interaction/projects.py`：

```python
"""项目接口：CRUD 四端点。校验失败 400、未知 id 404，detail 中文且可照做。

响应只回知识库别名（`kb`），不回 reme 知识库 id 或实体根路径（脱敏裁定）。
"""

from fastapi import APIRouter, HTTPException, Request

from aitester.interaction.schemas import (
    ProjectCreateRequest,
    ProjectInfo,
    ProjectsResponse,
    ProjectUpdateRequest,
)
from aitester.services.kb.aliases import PROJECT_KB_DEFAULT
from aitester.services.model_config import ConfigNotFoundError
from aitester.services.project_config import ProjectConfigError, ProjectService

router = APIRouter(prefix="/api/projects")


def _svc(request: Request) -> ProjectService:
    return request.app.state.project_config  # type: ignore[return-value]


@router.get("", response_model=ProjectsResponse)
def projects(request: Request) -> ProjectsResponse:
    return ProjectsResponse(
        projects=[ProjectInfo(**p) for p in _svc(request).list_projects()]
    )


@router.post("", response_model=ProjectInfo, status_code=201)
def projects_create(req: ProjectCreateRequest, request: Request) -> ProjectInfo:
    try:
        created = _svc(request).create(
            name=req.name,
            desc=req.desc,
            dir_=req.dir,
            agents=req.agents,
            kb=req.kb if req.kb is not None else PROJECT_KB_DEFAULT,
        )
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return ProjectInfo(**created)


@router.put("/{project_id}", response_model=ProjectInfo)
def projects_update(project_id: str, req: ProjectUpdateRequest, request: Request) -> ProjectInfo:
    try:
        updated = _svc(request).update(
            project_id, name=req.name, desc=req.desc, agents=req.agents,
            dir_=req.dir, kb=req.kb,
        )
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return ProjectInfo(**updated)


@router.delete("/{project_id}", status_code=204)
def projects_delete(project_id: str, request: Request) -> None:
    try:
        _svc(request).delete(project_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
```

`status_code=204` 已在路由声明上，返回 `None` 即为无体 204（FastAPI 会剥掉响应体），不要再手动注入 `Response` 改状态码；因此本文件不 import `Response`。

- [ ] **Step 5: main 接线** — 改 `backend/src/aitester/main.py`：

```python
from aitester.interaction.projects import router as projects_router
from aitester.services.project_config import ProjectService
```

`create_app` 签名加 `projects_path: Path | None = None`（放在 `capability_config_path` 之后），体内在 `capability_config` 之后加：

```python
    project_config = ProjectService(
        FileJsonConfigRepository(projects_path or DATA_DIR / "projects.json")
    )
```

`application.state` 段加 `application.state.project_config = project_config`（紧邻 `capability_config` 那行），末尾 `include_router` 段在 `kb_browse_router` 之后加 `application.include_router(projects_router)`。

- [ ] **Step 6: 跑到通过** — `cd backend && uv run pytest tests/test_api_projects.py -q`，预期 10 passed。
- [ ] **Step 7: 全量回归 + 提交**

```bash
cd backend && uv run pytest -q
git add backend/src/aitester/interaction/schemas.py backend/src/aitester/interaction/projects.py backend/src/aitester/main.py backend/tests/test_api_projects.py
git commit -m "feat(projects): /api/projects 四端点与应用接线——400/404 语义、204 无体、响应脱敏"
```

---

### Task 4: 前端 API 层与项目表格页

**Files:**
- Modify: `frontend/src/api/client.ts`（`apiFetch` 支持 204 + `Project` 类型与四个函数，接在 `chatSend` 之后）
- Rewrite: `frontend/src/pages/ProjectsPage.tsx`
- Modify: `frontend/src/App.css`（项目页需要的新类：`.p-kb`、`.p-empty`；沿用已有 `.p-table/.row-acts/.mini-btn/.btn-primary/.toast/.num/.spacer/.page-head`）

**Interfaces:**
- Consumes: Task 3 的 HTTP 契约；`getCapabilities()`（已存在，返回 `agents: AgentInfo[]`，只含可见智能体）用于把 `agents` id 渲染成中文名与提供弹窗选项；既有 `ApiError`（`.status`、`.message` 即 detail）。
- Produces: `Project` 接口、`getProjects/postProject/putProject/deleteProject`、`ProjectFormValues`（`{name,desc,dir,agents}`）、`ProjectsPage` 状态 `{projects, agentNames, error, toastMsg}`。本任务的新建/编辑按钮与行「编辑」按钮先 `disabled`（无 `modal` 状态），Task 5 引入 `modal` 状态后解禁——这是刻意的中间态。

- [ ] **Step 1: `apiFetch` 支持 204** — `frontend/src/api/client.ts` 中把 `return (await resp.json()) as T;` 改为：

```ts
  // 204 无响应体（DELETE /api/projects/{id}）：先短路，否则 resp.json() 抛「Unexpected end of JSON input」
  if (resp.status === 204) return null as T;
  return (await resp.json()) as T;
```

- [ ] **Step 2: 项目类型与函数** — 同文件末尾追加：

```ts
export interface Project {
  id: string;
  name: string;
  desc: string;
  dir: string;
  agents: string[];
  kb: string;
  session_count: number;
}

export interface ProjectsResponse {
  projects: Project[];
}

export interface ProjectFormValues {
  name: string;
  desc: string;
  dir: string;
  agents: string[];
}

export function getProjects(): Promise<ProjectsResponse> {
  return apiFetch<ProjectsResponse>("/api/projects");
}

export function postProject(values: ProjectFormValues): Promise<Project> {
  return apiFetch<Project>("/api/projects", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify(values) });
}

export function putProject(id: string, values: ProjectFormValues): Promise<Project> {
  return apiFetch<Project>(`/api/projects/${id}`, {
    method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(values) });
}

export function deleteProject(id: string): Promise<null> {
  return apiFetch<null>(`/api/projects/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 3: 表格页** — 重写 `frontend/src/pages/ProjectsPage.tsx`（弹窗容器本任务先以 `{null}` 占位，Task 5 替换）：

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteProject,
  getCapabilities,
  getProjects,
  type Project,
} from "../api/client";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();

  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2200);
  }, []);

  const reload = useCallback(async () => {
    try {
      const [pj, caps] = await Promise.all([getProjects(), getCapabilities()]);
      setProjects(pj.projects);
      setAgentNames(Object.fromEntries(caps.agents.map((a) => [a.id, a.name])));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  async function onRemove(p: Project) {
    // 与 /kb 页同款原生确认（CDP 自动化会挂，人工点击无碍）
    if (!window.confirm(`删除项目「${p.name}」？删除后不可恢复。`)) return;
    try {
      await deleteProject(p.id);
      toast(`已删除项目「${p.name}」`);
      void reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>项目管理</h1>
        <span className="num">共 {projects.length} 个</span>
        <div className="spacer" />
        <button className="btn-primary" onClick={() => undefined} disabled>
          ＋ 新建项目
        </button>
      </div>
      {error && <p className="p-empty">加载失败：{error}</p>}
      {!error && projects.length === 0 && (
        <p className="p-empty">还没有项目，点右上「＋ 新建项目」创建第一个。</p>
      )}
      {projects.length > 0 && (
        <div className="p-table">
          <table>
            <thead>
              <tr>
                <th style={{ width: 150 }}>项目名称</th>
                <th>描述</th>
                <th style={{ width: 190 }}>本地文件目录</th>
                <th style={{ width: 200 }}>启用智能体</th>
                <th style={{ width: 90 }}>知识库</th>
                <th style={{ width: 60 }}>会话数</th>
                <th style={{ width: 130 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td><span className="p-name">{p.name}</span></td>
                  <td className="p-desc">{p.desc || <span className="p-none">暂无描述</span>}</td>
                  <td><span className="p-dir" title={p.dir}>{p.dir}</span></td>
                  <td>{p.agents.map((a) => (
                    <span key={a} className="a-badge">{agentNames[a] || a}</span>
                  ))}</td>
                  {/* 知识库列只显别名：真实知识库 id 与实体路径不外泄（脱敏裁定） */}
                  <td><span className="p-kb">{p.kb}</span></td>
                  <td>{p.session_count}</td>
                  <td><div className="row-acts">
                    <button className="mini-btn" onClick={() => undefined} disabled>编辑</button>
                    <button className="mini-btn danger" onClick={() => void onRemove(p)}>删除</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="p-tip">
        提示：项目与智能体是多对多关系——此处勾选的智能体即聊天页「当前智能体」的可选范围；
        本地文件目录与知识库配置在项目创建后不可修改。
      </p>
      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  );
}
```

Task 4 阶段「新建/编辑」按钮先 `disabled`（Task 5 打开弹窗即解禁）——这是刻意的中间态，不是遗漏。

- [ ] **Step 4: CSS 增量** — `frontend/src/App.css` 的项目表段落（`.p-dir` 之后）追加：

```css
  .p-kb{display:inline-block;background:#eef3fb;color:#2f5496;border-radius:6px;padding:1px 8px;font-size:11.5px;font-weight:700}
  .p-none{color:#c9bfb0}
  .p-empty{max-width:1080px;margin:0 auto 12px;font-size:12.5px;color:var(--text-2)}
  .p-tip{max-width:1080px;margin:14px auto 0;font-size:12px;color:var(--text-2)}
```

- [ ] **Step 5: 门禁** — `cd frontend && npm run build`，预期 0 错误。
- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/client.ts frontend/src/pages/ProjectsPage.tsx frontend/src/App.css
git commit -m "feat(projects): 前端项目表格页——七列含只读知识库别名、删除确认与 apiFetch 204 支持"
```

---

### Task 5: 前端新建/编辑弹窗（含目录浏览与原型校验顺序）

**Files:**
- Create: `frontend/src/pages/projects/ProjectFormModal.tsx`
- Modify: `frontend/src/pages/ProjectsPage.tsx`（接线：新建/编辑按钮、提交与 tip、成功后 reload）

**Interfaces:**
- Consumes: Task 4 的 `ProjectFormValues`、`postProject`、`putProject`、`Project`；`getCapabilities()` 的 `agents`（选项与默认勾选）。
- Produces: 完整可用的项目 CRUD 页。`ProjectFormModal` props：

```ts
interface ProjectFormModalProps {
  mode: "create" | "edit";
  project: Project | null;          // edit 必填
  agentOptions: { id: string; name: string }[];
  onClose: () => void;
  onSaved: (msg: string) => void;   // 成功回调（父层负责 reload + toast）
}
```

- [ ] **Step 1: 弹窗组件** — 新建 `frontend/src/pages/projects/ProjectFormModal.tsx`，字段顺序、hint 文案与校验顺序照原型 `openProjModal :1768-1783` + `btnProjSave :1847-1878` + `btnPickDir :1816-1843`：

```tsx
import { useEffect, useState } from "react";
import {
  ApiError,
  postProject,
  putProject,
  type Project,
  type ProjectFormValues,
} from "../../api/client";

const ABS_PATH = /^([A-Za-z]:[\\/]|\/|\\\\|~[\\/])/;
const PROJECT_ROOT_DEFAULT = "D:/work/projects";
const joinPath = (parent: string, name: string) => `${parent.replace(/[\\/]+$/, "")}/${name}`;

interface ProjectFormModalProps {
  mode: "create" | "edit";
  project: Project | null;
  agentOptions: { id: string; name: string }[];
  onClose: () => void;
  onSaved: (msg: string) => void;
}

export default function ProjectFormModal({
  mode, project, agentOptions, onClose, onSaved,
}: ProjectFormModalProps) {
  const editing = mode === "edit";
  const [name, setName] = useState(project?.name ?? "");
  const [desc, setDesc] = useState(project?.desc ?? "");
  const [dir, setDir] = useState(project?.dir ?? "");
  const [agents, setAgents] = useState<string[]>(
    project?.agents ?? (agentOptions[0] ? [agentOptions[0].id] : [])
  );
  const [tip, setTip] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function toggle(id: string) {
    setAgents((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  /* 浏览…：浏览器读不到所选文件夹的完整路径，父目录需确认后才能拼成绝对路径（原型同款） */
  async function pickDir() {
    const raw = dir.trim();
    let picked = "";
    if (typeof window.showDirectoryPicker === "function") {
      try {
        picked = (await window.showDirectoryPicker({ mode: "readwrite" })).name;
      } catch (err) {
        if ((err as { name?: string })?.name === "AbortError") return;
        // 该浏览器/上下文不可用：回落手输，不让按钮再撞同一次
        (window as { showDirectoryPicker?: unknown }).showDirectoryPicker = undefined;
      }
    }
    if (!picked) {
      const typed = window.prompt("输入本地目录的绝对路径", ABS_PATH.test(raw) ? raw : PROJECT_ROOT_DEFAULT);
      if (typed === null) return;
      setDir(typed.trim());
      setTip(typed.trim() && !ABS_PATH.test(typed.trim()) ? "该路径不是绝对路径，请补全盘符或根路径" : "");
      return;
    }
    if (ABS_PATH.test(raw)) {
      setDir(joinPath(raw, picked));
      setTip(`已补全子目录：${picked}`);
      return;
    }
    const parent = window.prompt(
      `已取到文件夹名「${picked}」，浏览器读不到它的完整路径，请确认绝对父目录`,
      PROJECT_ROOT_DEFAULT
    );
    if (parent === null) { setDir(picked); setTip("请在该文件夹名前补全绝对父路径"); return; }
    const base = ABS_PATH.test(parent.trim()) ? parent.trim() : PROJECT_ROOT_DEFAULT;
    setDir(joinPath(base, picked));
    setTip("已拼出绝对路径，前缀可直接编辑修改");
  }

  /* 校验顺序与文案逐条对齐原型 btnProjSave；服务端 400 的 detail 落在同一个 tip 位 */
  function validate(): string {
    const n = name.trim();
    if (!n) return "请填写项目名称";
    if (!dir.trim()) return "请填写本地文件目录";
    if (!ABS_PATH.test(dir.trim())) return "目录必须是绝对路径，例如 D:/work/projects/order-system";
    if (!agents.length) return "请至少选择一个智能体";
    return "";
  }

  async function save() {
    const first = validate();
    if (first) { setTip(first); return; }
    const values: ProjectFormValues = {
      name: name.trim(), desc: desc.trim(), dir: dir.trim(), agents,
    };
    setSaving(true);
    try {
      if (editing && project) {
        await putProject(project.id, values);
        onSaved(`已保存项目「${values.name}」`);
      } else {
        await postProject(values);
        onSaved(`已创建项目「${values.name}」，可在聊天页左上「当前项目」中选择`);
      }
    } catch (err) {
      // 同名、不可改字段、平台智能体等由服务端裁定，detail 直接呈现
      setTip(err instanceof ApiError ? err.message : String(err));
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
  }

  return (
    <div className="mask" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-modal="true">
        <div className="m-head"><span>{editing ? "编辑项目" : "新建项目"}</span>
          <div className="spacer" />
          <button className="icon-btn" title="关闭" onClick={onClose}>✕</button>
        </div>
        <div className="m-body">
          <div className="field">
            <label htmlFor="pfName">项目名称 <span className="req">*</span></label>
            <input id="pfName" type="text" maxLength={30} value={name}
              placeholder="例如：订单系统"
              onChange={(e) => { setName(e.target.value); setTip(""); }} />
          </div>
          <div className="field">
            <label htmlFor="pfDesc">项目描述</label>
            <textarea id="pfDesc" maxLength={200} value={desc}
              placeholder="一句话说明这个项目的测试范围"
              onChange={(e) => { setDesc(e.target.value); setTip(""); }} />
          </div>
          <div className="field">
            <label htmlFor="pfDir">本地文件目录（绝对路径） <span className="req">*</span></label>
            <div className="ro">
              <input id="pfDir" type="text" spellCheck={false} value={dir} disabled={editing}
                placeholder="例如 D:/work/projects/order-system 或 /home/me/order-system"
                onChange={(e) => { setDir(e.target.value); setTip(""); }} />
              {!editing && (
                <button className="mini-btn" onClick={() => void pickDir()}>📁 浏览…</button>
              )}
            </div>
            <div className="hint">
              {editing
                ? "本地文件目录创建后不可修改。"
                : "可直接粘贴绝对路径；或点「浏览…」选中文件夹，再确认它的绝对父目录，自动拼成完整路径（浏览器读不到所选文件夹的完整路径）。产出（用例 / 脚本 / 报告）都写入该目录，创建后不可修改。"}
            </div>
          </div>
          <div className="field">
            <label>启用智能体 <span className="req">*</span>（可多选）</label>
            <div className="agent-opts">
              {agentOptions.map((a) => (
                <label key={a.id} className={agents.includes(a.id) ? "on" : ""}>
                  <input type="checkbox" checked={agents.includes(a.id)}
                    onChange={() => { toggle(a.id); setTip(""); }} />
                  {a.name}
                </label>
              ))}
            </div>
            <div className="hint">至少选择一个智能体。</div>
          </div>
          {/* 知识库：默认且不可改，只显示别名（脱敏裁定） */}
          <div className="field">
            <label htmlFor="pfKb">知识库 <span className="req">*</span>（默认且不可修改）</label>
            <div className="ro">
              <input id="pfKb" type="text" value={project?.kb ?? "kb"} readOnly />
            </div>
            <div className="hint">知识库配置默认且不可修改，所有项目共用同一份知识库。</div>
          </div>
        </div>
        <div className="m-foot">
          <span className="m-tip">{tip}</span>
          <div className="spacer" />
          <button className="mini-btn" onClick={onClose} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={() => void save()} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

`window.showDirectoryPicker` 在 TS 的 `Window` 上可能未声明——若 `npm run build` 报类型错，在本文件顶部加最小声明而**不要**引入 `@types` 新依赖：

```ts
declare global {
  interface Window {
    showDirectoryPicker?: (opts?: { mode?: "read" | "readwrite" }) => Promise<{ name: string }>;
  }
}
```

**与原型的一处刻意偏差**：原型在客户端判同名（`WSS.some(...)`），本期项目真相在服务端，同名/白名单/不可改一律由 `POST/PUT` 的 400 detail 回落同一个 `.m-tip` 位；所以本地校验顺序是 名称 → 目录 → 智能体，服务端判据随后到达。别在客户端再造一份列表判重，两处判据会漂。`ProjectFormValues` 故意不含 `kb`——新建走服务端默认、编辑不传即不改，界面上它只是只读展示。

- [ ] **Step 2: 接线** — `frontend/src/pages/ProjectsPage.tsx`：加 `const [modal, setModal] = useState<{ mode: "create" | "edit"; project: Project | null } | null>(null);`；把 `agentNames` 扩成同时保存 `{id,name}` 列表（`const [agentOptions, setAgentOptions] = useState<{ id: string; name: string }[]>([])`，reload 里 `setAgentOptions(caps.agents.map((a) => ({ id: a.id, name: a.name })))`，`agentNames` 由它派生）；页头按钮 `disabled` 改 `onClick={() => setModal({ mode: "create", project: null })}`；行「编辑」按钮改 `onClick={() => setModal({ mode: "edit", project: p })}`；表格之后渲染：

```tsx
{modal && (
  <ProjectFormModal
    mode={modal.mode}
    project={modal.project}
    agentOptions={agentOptions}
    onClose={() => setModal(null)}
    onSaved={(msg) => { toast(msg); void reload(); }}
  />
)}
```

注意 `onSaved` 里**不**调 `setModal(null)`（关闭由弹窗自身负责），编辑态关闭后若仍在编辑当前行，`reload()` 会取回最新名称。

- [ ] **Step 3: 门禁** — `cd frontend && npm run build`，预期 0 错误。
- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/projects/ProjectFormModal.tsx frontend/src/pages/ProjectsPage.tsx
git commit -m "feat(projects): 项目新建/编辑弹窗——原型校验顺序与目录浏览、目录与知识库创建后冻结"
```

---

### Task 6: 文档回写

**Files:**
- Modify: `README.md`（能力清单补项目端点一行 + 新增「项目管理」段落；确认无「立即重建」类表述）
- Modify: `docs/superpowers/specs/2026-10-02-projects-page-design.md:4`（状态行「待实施」→「已实施（commit 见 git log）」）

**Interfaces:**
- Consumes: Task 1-5 全部。
- Produces: 文档与实现一致。

- [ ] **Step 1: README** — 能力清单加一行：

```markdown
- `GET/POST/PUT/DELETE /api/projects`（项目 CRUD，JSON 落盘；本地文件目录与知识库配置创建后不可修改）
```

并新增简短段落（3-5 行）说明：项目 = `{名称, 描述, 本地文件目录, 启用智能体, 知识库}`；知识库字段是**别名 `kb` 且不可改**，界面不回显底层知识库标识；本期只落 CRUD，聊天页按项目隔离与会话数统计属后续专项。

- [ ] **Step 2: spec 状态回写 + 全量回归** — spec 状态行改「已实施」；跑 `cd backend && uv run pytest -q` 与 `cd frontend && npm run build`。
- [ ] **Step 3: 提交** — `docs(projects): 项目管理页文档回写——README 能力段与 spec 状态`

---

## 验收（实施完成后另行执行，不在子代理任务内）

1. 全量 `uv run pytest -q` + `npm run build` 绿。
2. 真机走查（**需用户同意**，涉及真实 LLM 成本为零但会写真实 `backend/data/projects.json`）：起独立端口（如 8002/5175，**不碰在跑的 8000/5173**）；新建/编辑/删除全链、`dir` 与 `kb` 编辑态禁用、同名拒绝、非绝对路径拒绝、唯一项目不可删、刷新后持久、知识库列恒显 `kb` 且整页搜不到 `zhb` 字样。走查新建项目用后即清。
3. 若用户不想我动 `backend/data/projects.json`，则走查改用注入 `projects_path` 的临时实例或纯 API 等价路径（此前 KB 专项已按此口径获授权）。
