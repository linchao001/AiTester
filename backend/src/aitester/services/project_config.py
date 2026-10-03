"""项目注册表：JSON 落盘为唯一真相，校验与不可改裁定集中在服务层。

知识库字段只存别名（默认 `kb`），真实 reme 知识库 id 与实体根路径由 `services/kb/aliases`
解析且永不出现在返回值里（脱敏裁定）。本地文件目录只校验绝对路径形态与危险根闭集，
不 stat 存在性、不建目录——浏览… 由前端拼接，路径真相归用户。
读侧另有一只纯只读探测 `dir_exists`：它 stat 目录回答「还在不在」，同样绝不 mkdir，
也不做任何路径包含判定（边界执法是后续片的事）。
"""

from __future__ import annotations

import copy
import os
import re
import uuid
from pathlib import Path
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


POSIX_SYSTEM_ROOTS = frozenset({
    "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/boot", "/dev", "/home", "/root",
})

# Windows 侧一律取环境变量，不硬编码盘符：机器可能装在不同的系统盘
_WIN_ROOT_ENV_VARS = ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")


def _resolve_or_none(path: Path) -> Path | None:
    """resolve 一把，解析不了就什么候选也不给。

    用户输入里的 NUL 字节会让 os.stat 抛 ValueError（不是 OSError），环境变量里的畸形值同理：
    这类路径不该外泄成 500，也不该参与危险根比对——吞掉异常返回 None 即「不产生候选」。
    """
    try:
        return path.resolve()
    except (OSError, ValueError):
        return None


def dangerous_root_reason(dir_: str) -> str | None:
    """四类危险根判据（spec 裁定 6）：命中即返回中文原因，全部不命中返回 None。

    只判四类闭集，不维护黑名单——判据是「文件系统根 / 家目录本身 / 环境变量给出的 Windows
    系统目录 / 写死的 POSIX 系统目录」，第四条之外一律放行，用户填 D:/work 这类容器目录是他的选择。
    """
    raw = _clean_dir(dir_)
    if not raw:
        return "请填写本地文件目录"
    # 第四类在清洗后的「原始形态」上判等、不 resolve：这样 POSIX 系统根在任何平台都执行得到，
    # 也不受 NUL 等畸形字符让 resolve 抛错的影响（相等匹配，不做前缀/祖先包含）
    raw_posix = Path(raw).as_posix().rstrip("/")
    if raw_posix in POSIX_SYSTEM_ROOTS:
        return f"不能把系统目录「{raw_posix}」作为项目目录，请选择项目自己的目录"
    try:
        target = Path(raw).expanduser().resolve()
    except (OSError, ValueError):
        return None  # 解析不了不等于危险：形态校验已把住入口，此处不额外拦人
    if target.parent == target:
        return f"不能把整个磁盘「{target}」作为项目目录，请选择盘下的具体目录"
    home = _resolve_or_none(Path.home())
    if home is not None and target == home:
        return f"不能把用户主目录「{target}」作为项目目录，请选择其下的具体项目目录"
    system_roots = {
        resolved
        for resolved in (
            _resolve_or_none(Path(value))
            for value in (os.environ.get(v) for v in _WIN_ROOT_ENV_VARS)
            if value
        )
        if resolved is not None
    }
    if target in system_roots:
        return f"不能把系统目录「{target}」作为项目目录，请选择项目自己的目录"
    return None


def dir_exists(dir_: str) -> bool:
    """只读探测：存在且是目录。绝不 mkdir——write 工具会建目录，探测若也建就分不出「用户填错」与「用户还没建」。

    读侧判据，不参与创建校验（创建只校验形态与危险根，见 `_validate_dir`）。含 NUL 字节的 dir 会让
    文件系统调用抛 ValueError 而不是 OSError，畸形/超长路径抛 OSError：两者一律答「不可达」，
    绝不把用户填的 dir 外泄成 500。
    """
    if not (dir_ or "").strip():
        return False
    try:
        return Path(dir_).expanduser().is_dir()
    except (OSError, ValueError):
        return False


def _validate_dir(dir_: str) -> str:
    """只校验形态（绝对路径）与危险根，不 stat、不建目录。仅创建时调用——dir 冻结后不再重复校验。"""
    clean = _clean_dir(dir_)
    if not clean:
        raise ProjectConfigError("请填写本地文件目录")
    if not ABS_PATH.match(clean):
        raise ProjectConfigError("目录必须是绝对路径，例如 D:/work/projects/order-system")
    if len(clean) > DIR_MAX:
        raise ProjectConfigError(f"本地文件目录不能超过 {DIR_MAX} 字")
    reason = dangerous_root_reason(clean)
    if reason:
        raise ProjectConfigError(reason)
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
        # 会话数与目录可达性是「项目 × 会话」的组合事实，由路由层用 SessionStore 现算（第 2 片接真值）
        return [copy.deepcopy(p) for p in self._config["projects"]]

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
