"""会话行 ↔ Reme Msg dict；session_id 稳定 hash（对齐 QwenPaw，Windows 文件名安全）。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

_REME_SESSION_ID_PREFIX = "aitsid_sha256_"


def to_reme_session_id(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{_REME_SESSION_ID_PREFIX}{digest}"


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def to_reme_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 {role, content, ...} 行转为 Reme auto_memory 可吃的 Msg dict 列表。"""
    out: list[dict[str, Any]] = []
    for raw in rows:
        role = str(raw.get("role") or "user")
        content = str(raw.get("content") or "")
        if not content.strip():
            continue
        name = str(raw.get("name") or "") or role
        msg_id = str(raw.get("id") or "") or f"msg_{uuid.uuid4().hex}"
        created_at = str(raw.get("created_at") or "") or _iso_now()
        out.append({
            "name": name,
            "role": role,
            "content": content,
            "created_at": created_at,
            "id": msg_id,
        })
    return out
