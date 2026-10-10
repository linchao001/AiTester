"""知识库别名注册表：项目配置只存别名，真实 reme 知识库 id 在此集中解析。

脱敏线：`resolve_kb_id` 的结果（如 zhb_kb）与实体根路径只供服务端使用，任何 API 响应
与界面文案一律不得回显。本期检索/写盘链路仍走 `settings.kb_id`（知识库全局单份裁定），
本模块承担项目 `kb` 字段的写时校验口径，并作为后续多 KB 专项的接线点。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aitester.memory.reme.paths import resolve_kb_bases_dir

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
