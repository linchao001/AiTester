"""会话真相：index.json 存元信息，<session_id>.jsonl 逐行追加消息。

双文件是有意的：每次发消息重写全量历史是写放大，append 是 O(1)；列表只需 index。
index 走 FileJsonConfigRepository（同目录 tmp + os.replace），与项目/模型配置同一原子写口。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aitester.storage import FileJsonConfigRepository

SESSION_ID_RE = re.compile(r"^sess_[0-9a-f]{8}$")
TITLE_MAX = 16
DETAIL_MAX = 80
DEFAULT_TITLE = "新会话"
MISSING_SESSION_DETAIL = "会话不存在或已被删除"


def is_session_id(value: str) -> bool:
    """sess_ 形态是唯一真相口：非此形态的传入 id 一律按临时键处理（spec 兼容裁定）。"""
    return bool(SESSION_ID_RE.match(value or ""))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _title_from(first_message: str) -> str:
    text = " ".join((first_message or "").split())
    return text[:TITLE_MAX] or DEFAULT_TITLE


class SessionStoreError(RuntimeError):
    """会话读写失败，交互层映射 404，detail 面向用户且可照做。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class Session:
    id: str
    agent_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatMessage:
    role: str
    content: str
    ts: int
    steps: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "ChatMessage":
        steps = raw.get("steps")
        return ChatMessage(
            role=str(raw.get("role") or ""),
            content=str(raw.get("content") or ""),
            ts=int(raw.get("ts") or 0),
            steps=steps if isinstance(steps, list) else None,
        )


@dataclass
class _Index:
    version: int = 1
    sessions: list[dict[str, Any]] = field(default_factory=list)


class SessionStore:
    """会话目录的唯一读写者；index 重写由一把锁串行化（路由 sync def 跑在线程池）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._repo = FileJsonConfigRepository(self._root / "index.json")
        self._lock = threading.Lock()
        with self._lock:
            self._index = self._load_index()

    def _load_index(self) -> _Index:
        raw = self._repo.load()
        if not isinstance(raw, dict):
            return _Index()
        items = raw.get("sessions")
        items = items if isinstance(items, list) else []
        sessions: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or "")
            if not is_session_id(sid) or any(s["id"] == sid for s in sessions):
                continue
            agent_id = str(item.get("agent_id") or "")
            if not agent_id:
                continue
            created = int(item.get("created_at") or 0)
            sessions.append({
                "id": sid,
                "agent_id": agent_id,
                "title": str(item.get("title") or DEFAULT_TITLE)[:TITLE_MAX * 4] or DEFAULT_TITLE,
                "created_at": created,
                "updated_at": int(item.get("updated_at") or created) or created,
                "message_count": max(0, int(item.get("message_count") or 0)),
            })
        return _Index(sessions=sessions)

    def _save_index(self) -> None:
        # 调用方必须持锁；jsonl 是消息真相，index 损坏可由 messages() 兜底重建计数
        self._repo.save({"version": 1, "sessions": self._index.sessions})

    def _path(self, session_id: str) -> Path:
        if not is_session_id(session_id):
            raise SessionStoreError(MISSING_SESSION_DETAIL)
        return self._root / f"{session_id}.jsonl"

    def new_id(self) -> str:
        while True:
            sid = f"sess_{uuid.uuid4().hex[:8]}"
            if sid not in self._index_by_id():
                return sid

    def _index_by_id(self) -> dict[str, dict[str, Any]]:
        return {s["id"]: s for s in self._index.sessions}

    def create(self, session_id: str, agent_id: str, first_message: str) -> Session:
        if not is_session_id(session_id):
            raise SessionStoreError(MISSING_SESSION_DETAIL)
        with self._lock:
            existing = self._index_by_id().get(session_id)
            if existing is not None:  # 幂等：重入不覆盖标题与 created_at
                return Session(**existing)
            now = _now_ms()
            record = {
                "id": session_id,
                "agent_id": agent_id,
                "title": _title_from(first_message),
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
            }
            self._index.sessions.append(record)
            self._save_index()
            self._path(session_id).touch()
            return Session(**record)

    def list(self, agent_id: str) -> list[Session]:
        rows = [s for s in self._index.sessions if s["agent_id"] == agent_id]
        rows.sort(key=lambda s: (s["updated_at"], s["created_at"]), reverse=True)
        return [Session(**s) for s in rows]

    def get(self, session_id: str) -> Session | None:
        record = self._index_by_id().get(session_id)
        return Session(**record) if record else None

    def messages(self, session_id: str) -> list[ChatMessage]:
        path = self._path(session_id)
        if not path.exists():
            return []
        out: list[ChatMessage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue  # 半行（进程被杀）跳过，坏一行不该让整段历史读不出
            if isinstance(raw, dict):
                out.append(ChatMessage.from_dict(raw))
        return out

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        path = self._path(session_id)
        ts = _now_ms()
        line = ChatMessage(role=role, content=content, ts=ts, steps=steps or None).to_dict()
        # jsonl 先写、index 后记：中途崩溃只丢一次计数更新，消息本身不丢
        with self._lock:
            record = self._index_by_id().get(session_id)
            if record is None:
                raise SessionStoreError(MISSING_SESSION_DETAIL)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            record["updated_at"] = max(ts, record["created_at"])
            record["message_count"] = len(self.messages(session_id))
            self._save_index()

    def delete(self, session_id: str) -> bool:
        with self._lock:
            record = self._index_by_id().get(session_id)
            if record is None:
                return False
            self._index.sessions.remove(record)
            self._save_index()
            self._path(session_id).unlink(missing_ok=True)
            return True
