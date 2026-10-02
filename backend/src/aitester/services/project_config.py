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
    """剥尾部分隔符（原型同款），但纯根路径不能剥坏：`/` 不剥成空串、`D:/` 不剥成 `D:`、`~/` 不剥成 `~`。"""
    value = (dir_ or "").strip()
    stripped = re.sub(r"[\\/]+$", "", value)
    if not stripped or re.fullmatch(r"[A-Za-z]:|~", stripped):
        # 剥完塌成空串、裸盘符或裸 `~` 时保留原输入形态，交由 ABS_PATH 判定
        return value
    return stripped


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
        # 校验顺序照数据模型从前往后：名称/描述 → 智能体 → 知识库别名 → 目录形态 → 同名唯一
        clean = _validate(name=name, desc=desc, agents=agents, kb=kb)
        clean_dir = _validate_dir(dir_)
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
