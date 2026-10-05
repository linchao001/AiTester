"""边界执法判据：类别只由工具 id 决定，判定与展示全是纯函数。

纯函数是 gate 可安全重跑的前提（P5 教训：含副作用的节点体重跑会把副作用再执行一遍），
也是本片最容易实现错的地方，所以判定表、展示文案与逐字 detail 全集中在这一层。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitester.adapters.tools.file_tools.fs_tool import resolve_path

PERM_MODES: tuple[str, str, str] = ("free", "boundary", "strict")
DEFAULT_PERM_MODE = "free"

# 逐字取自 spec「错误处理」表：改一个字，单测与走查会同时对不上
PERM_MODE_DETAIL = "无效的权限模式，请选择自由权限、只批界外或严格权限"


class PermModeError(ValueError):
    """权限模式非法。路由按 400 落 `.detail`（`_GUARD_MAP` 认这个属性）。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def validate_perm_mode(perm_mode: str) -> str:
    """空串按默认档：`/kb` 与旧客户端根本不发这个字段（裁定 9）。"""
    mode = (perm_mode or DEFAULT_PERM_MODE).strip()
    if mode not in PERM_MODES:
        raise PermModeError(PERM_MODE_DETAIL)
    return mode


# 与 services/capability_config.py 的 TOOL_CATALOG 十件对齐；不新增危险度字段（两套真相禁令）
WRITE_IDS: tuple[str, ...] = ("write", "edit")
SHELL_IDS: tuple[str, ...] = ("pwsh", "bash")
KB_WRITE_IDS: tuple[str, ...] = ("save_to_knowledge",)
READ_ONLY_IDS: tuple[str, ...] = ("read", "grep_search", "glob_search", "web_search",
                                  "knowledge_search", "prepare_kb_write")

# 会过闸门的工具全集：并行扇出的安全判据只看这个集合，不看档位——档位是运行期的，
# 而「这一路子会不会挂起」必须在塑形时就定（R4/R14）
SUSPENDABLE_IDS: frozenset[str] = frozenset(WRITE_IDS + SHELL_IDS + KB_WRITE_IDS)


def face_can_suspend(tool_ids: Iterable[str]) -> bool:
    """这副工具面里有没有会挂起的工具（写 / 命令 / 知识库写）——有就不能并行派发。"""
    return not SUSPENDABLE_IDS.isdisjoint(tool_ids)


def tool_ids_for(perm_mode: str) -> tuple[str, ...]:
    """该档会过闸门的工具 id（测试与说明用；判定本身仍逐条走 `needs_approval`）。"""
    if perm_mode == "free":
        return ()
    if perm_mode == "strict":
        return WRITE_IDS + SHELL_IDS + KB_WRITE_IDS
    return WRITE_IDS + SHELL_IDS


@dataclass(frozen=True)
class AuthTarget:
    category: str        # "write" | "edit" | "shell" | "knowledge"
    action: str          # 卡片上的中文动作
    target: str          # 界内相对项目根；界外原样（解析后的绝对路径不外泄）
    command: str         # 命令全文（批准前必须看全，DETAIL_MAX 不适用）
    cwd: str             # 命令的工作目录（ShellInput.cwd，可空）
    remember_key: str    # 记住表键（R5：外层再按会话分组）


def _root(project_dir: str) -> Path:
    return Path(project_dir).resolve()


def _inside(project_dir: str, raw: str) -> bool:
    """界内判定：与展示、执行同一个解析口，resolve 后再比根。"""
    return resolve_path(project_dir, raw).is_relative_to(_root(project_dir))


def _shown(project_dir: str, resolved: Path, raw: str) -> str:
    """界内给相对项目根的展示串，界外给模型原文——KB 卡片不外泄 abs_display 的同一条口径。"""
    root = _root(project_dir)
    try:
        return str(resolved.relative_to(root))
    except ValueError:                       # 符号链接等 resolve 后仍不同根：按界外处理
        return raw


def plan_target(tool_id: str, args: dict[str, Any], project_dir: str) -> AuthTarget | None:
    """执法对象 → 卡片展示与记住所需的一切；只读类与未知工具返回 None（不拦）。"""
    payload = args or {}
    if tool_id in WRITE_IDS:
        category = "write" if tool_id == "write" else "edit"
        raw = str(payload.get("file_path") or "")
        resolved = resolve_path(project_dir, raw)
        inside = resolved.is_relative_to(_root(project_dir))
        return AuthTarget(
            category=category,
            action=f"{'写入' if category == 'write' else '修改'}项目目录"
                   f"{'内' if inside else '外'}的文件",
            target=_shown(project_dir, resolved, raw),
            command="", cwd="",
            remember_key=f"{tool_id}|{resolved}",
        )
    if tool_id in SHELL_IDS:
        command = str(payload.get("command") or "")
        cwd = str(payload.get("cwd") or "")
        return AuthTarget(category="shell", action="执行命令", target=cwd,
                          command=command, cwd=cwd, remember_key=f"{tool_id}|{command}")
    if tool_id in KB_WRITE_IDS:
        # 写入路径由服务端构造，无「界外」语义：boundary 放行、strict 挂（裁定 6 表 + 偏离 5）
        return AuthTarget(category="knowledge", action="写入知识库", target="", command="",
                          cwd="", remember_key=f"{tool_id}|{str(payload.get('title') or '')}")
    return None


def needs_approval(tool_id: str, args: dict[str, Any], perm_mode: str,
                   project_dir: str, remembered: set[str]) -> bool:
    """唯一判据口。`free` 一律 False（默认档零行为）；记住表命中一律 False。"""
    if perm_mode == "free":
        return False
    plan = plan_target(tool_id, args or {}, project_dir)
    if plan is None:
        return False
    if perm_mode == "boundary":
        # 只批界外：界内写入与知识库写入直接放行；shell 一律往下走（裁定 6）
        if plan.category == "knowledge":
            return False
        if plan.category in ("write", "edit") and _inside(
                project_dir, str((args or {}).get("file_path") or "")):
            return False
    return plan.remember_key not in remembered
