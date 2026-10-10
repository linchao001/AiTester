"""KB 实体目录解析与读写侧共用判据：不依赖 reme 导入的纯标准库实现。

判据逐条对齐 reme/knowledge/store.py:57-70（resolve_knowledge_bases_dir）：
配置 kb_bases_dir > 进程环境变量 REME_KNOWLEDGE_BASES_DIR > ~/.reme/knowledge_bases。
隐藏名判据（is_hidden）与 mtime 毫秒化（mtime_ms）由 browse 读侧与 prepare_kb_write
写侧共同消费，杜绝「工具放行、browse 403」的分歧死路。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_ENV_DEFAULT = "REME_KNOWLEDGE_BASES_DIR"

HIDDEN_DIRS = {".git", ".idea", ".vscode", ".locks", "__pycache__", "node_modules", ".obsidian"}
HIDDEN_FILES = {".ds_store", "thumbs.db"}
_NOISE_RE = re.compile(r"\.(pyc|pyo|pack|idx|rev|sample)$")


def resolve_kb_bases_dir(settings: Any) -> Path:
    raw = (getattr(settings, "kb_bases_dir", "") or "").strip()
    if not raw:
        raw = (os.environ.get(_ENV_DEFAULT) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # 默认部署恰是未 resolve 分支（Windows 下 8.3 短名/junction 会误报「越出根」），统一规范化
    return (Path.home() / ".reme" / "knowledge_bases").resolve()


def resolve_kb_root(settings: Any) -> Path:
    return resolve_kb_bases_dir(settings) / settings.kb_id


def is_hidden(name: str) -> bool:
    """单段名隐藏判据：大小写不敏感（Windows 真实大小写不敏感），点前缀与噪音后缀一律隐藏。"""
    lower = name.lower()
    return (lower in HIDDEN_DIRS or lower in HIDDEN_FILES or name.startswith(".")
            or bool(_NOISE_RE.search(lower)))


def hidden_segment(rel_parts: tuple[str, ...]) -> str | None:
    """返回相对路径首个隐藏段（无则 None）；browse 403 与草案工具拒写共用。"""
    return next((seg for seg in rel_parts if is_hidden(seg)), None)


def mtime_ms(st: os.stat_result) -> int:
    """st_mtime_ns → 整毫秒；读写两侧与乐观锁基线的唯一换算口。"""
    return int(st.st_mtime_ns // 1_000_000)
