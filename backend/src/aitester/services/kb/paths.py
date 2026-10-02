"""KB 实体目录解析：不依赖 reme 导入的纯标准库实现。

判据逐条对齐 reme/knowledge/store.py:57-70（resolve_knowledge_bases_dir）：
配置 kb_bases_dir > 进程环境变量 REME_KNOWLEDGE_BASES_DIR > ~/.reme/knowledge_bases。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ENV_DEFAULT = "REME_KNOWLEDGE_BASES_DIR"


def resolve_kb_bases_dir(settings: Any) -> Path:
    raw = (getattr(settings, "kb_bases_dir", "") or "").strip()
    if not raw:
        raw = (os.environ.get(_ENV_DEFAULT) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".reme" / "knowledge_bases"


def resolve_kb_root(settings: Any) -> Path:
    return resolve_kb_bases_dir(settings) / settings.kb_id
