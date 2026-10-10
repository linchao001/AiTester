"""会话真相：index.json 存元信息，<session_id>.jsonl 逐行追加消息。

双文件是有意的：jsonl 追加只写一行、不重写全量历史，但 append 需整读该会话 jsonl 重算
message_count（非 O(1)，代价随会话长度线性上升）；列表只需 index，不碰正文。
index 走 FileJsonConfigRepository（同目录 tmp + os.replace），与项目/模型配置同一原子写口。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from logging import getLogger
from pathlib import Path
from typing import Any

from aitester.storage import ConfigStorageError, FileJsonConfigRepository

logger = getLogger(__name__)

SESSION_ID_RE = re.compile(r"^sess_[0-9a-f]{8}$")
TITLE_MAX = 16
DEFAULT_TITLE = "新会话"
MISSING_SESSION_DETAIL = "会话不存在或已被删除"


def is_session_id(value: str) -> bool:
    """sess_ 形态是唯一真相口：非此形态的传入 id 一律按临时键处理（spec 兼容裁定）。"""
    return bool(SESSION_ID_RE.match(value or ""))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    """本地时区 ISO-8601（含偏移），对齐 Reme / AgentScope Msg.created_at。"""
    return datetime.now().astimezone().isoformat()


def _iso_from_ms(ms: int) -> str:
    if ms <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000).astimezone().isoformat()
    except (OSError, OverflowError, ValueError):
        # Windows 对接近 epoch 的极小毫秒会 OSError；老测试夹具里的 ts=1/2 走这条
        return ""


def _ms_from_iso(value: str) -> int:
    text = (value or "").strip()
    if not text:
        return 0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, OSError, OverflowError):
        return 0


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


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
    project_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatMessage:
    """消息行：磁盘写 Reme 最低可用字段 + AiTester 扩展；内存保留 ts 供 API。

    Reme 最低可用：name / role / content / created_at / id。
    磁盘不再写 ts（1A）；读侧由 created_at 反算，老行只有 ts 时再合成 created_at。
    """

    role: str
    content: str
    ts: int  # 仅内存/API；to_dict 不落盘
    name: str = ""
    id: str = ""
    created_at: str = ""
    agent_id: str = ""
    session_id: str = ""
    steps: list[dict[str, Any]] | None = None
    # 第 4 片：被停止的回答。老 jsonl 行没这个键 → from_dict 读缺省 False，零迁移
    stopped: bool = False
    # 上下文管理第一片：本回合的上下文读数快照。老 jsonl 行没这个键 → from_dict 读缺省
    # None，零迁移（与 stopped 同一先例）
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        # 顺序对齐 Reme 样例，其后是 AiTester 扩展；刻意不含 ts
        return {
            "name": self.name or self.role,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "id": self.id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "steps": self.steps,
            "stopped": self.stopped,
            "context": self.context,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> ChatMessage:
        role = str(raw.get("role") or "")
        name = str(raw.get("name") or "") or role
        msg_id = str(raw.get("id") or "")
        created_at = str(raw.get("created_at") or "")
        ts = _ms_from_iso(created_at) if created_at else 0
        if ts <= 0:
            # 老行：只有 epoch ms 的 ts → 反算 ISO，供内存形态对齐 Reme
            try:
                ts = int(raw.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0
            if ts > 0 and not created_at:
                created_at = _iso_from_ms(ts)
        steps = raw.get("steps")
        raw_context = raw.get("context")
        return ChatMessage(
            role=role,
            content=str(raw.get("content") or ""),
            ts=ts,
            name=name,
            id=msg_id,
            created_at=created_at,
            agent_id=str(raw.get("agent_id") or ""),
            session_id=str(raw.get("session_id") or ""),
            steps=steps if isinstance(steps, list) else None,
            stopped=raw.get("stopped") is True,   # 只认真 True，脏数据不伪装成被停止
            context=raw_context if isinstance(raw_context, dict) else None,
        )


def open_session_store(project_dir: str | Path, agent_id: str) -> SessionStore:
    """项目空间唯一真相：{dir}/.AiTester/session_history/<agent_id>/。"""
    from aitester.project_runtime import session_history_root

    root = session_history_root(project_dir) / agent_id
    return SessionStore(root, agent_id=agent_id)


@dataclass
class _Index:
    version: int = 1
    sessions: list[dict[str, Any]] = field(default_factory=list)


class SessionStore:
    """本类只在单进程内加锁串行读写会话目录；跨进程共用同一目录不受支持（见 README 单进程约束）。"""

    def __init__(self, root: Path, agent_id: str = "") -> None:
        self._root = Path(root)
        self._agent_id = (agent_id or "").strip()
        self._repo = FileJsonConfigRepository(self._root / "index.json")
        self._lock = threading.Lock()
        with self._lock:
            self._index = self._load_index()

    def _load_index(self) -> _Index:
        try:
            raw = self._repo.load()
        except ConfigStorageError:
            # 自愈返回空索引前先把坏文件留档：下一次写盘会整文件覆盖，用户全部会话从列表消失
            # 且不可恢复，而正文其实原地躺着——留档是本期唯一的救济窗口
            self._archive_broken_index()
            return _Index()
        # 「合法 JSON 但结构坏」与「不可解析」是同一个毁数据形态：下一次写盘同样整文件覆盖，所以两条分支也先留档
        if not isinstance(raw, dict):
            self._archive_broken_index()
            return _Index()
        items = raw.get("sessions")
        if not isinstance(items, list):
            self._archive_broken_index()
            return _Index()
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
            if self._agent_id and agent_id != self._agent_id:
                logger.warning(
                    "会话索引中 %s 行 agent_id=%s 与目录归属 %s 不符，已丢弃该行",
                    sid, agent_id, self._agent_id,
                )
                continue
            project_id = str(item.get("project_id") or "")
            if not project_id:
                # 项目归属自第 2 片起是硬字段：缺了就没有任何列表能安全地显示它。
                # 与坏行同口径——丢一行必须留话，正文 .jsonl 原地不动，用户还能从日志找回
                logger.warning("会话索引中 %s 行缺少项目归属（第 2 片前的旧数据），已丢弃该行", sid)
                continue
            try:
                created = int(item.get("created_at") or 0)
                record = {
                    "id": sid,
                    "agent_id": agent_id,
                    "project_id": project_id,
                    "title": str(item.get("title") or DEFAULT_TITLE)[:TITLE_MAX * 4] or DEFAULT_TITLE,
                    "created_at": created,
                    "updated_at": int(item.get("updated_at") or created) or created,
                    "message_count": max(0, int(item.get("message_count") or 0)),
                }
            except (ValueError, TypeError) as exc:
                # 坏一行丢一行：字段类型坏但 JSON 合法时，_load_index 跑在 create_app 导入期，
                # 不吞掉就会让整进程起不来（main.py 模块级 app = create_app()）。
                # 丢行必须留话：下一次写盘会把这行从文件里永久抹掉，无日志就成无迹可查的静默删除
                logger.warning("会话索引中 %s 行的字段类型不可用，已丢弃该行：%s", sid, exc)
                continue
            sessions.append(record)
        return _Index(sessions=sessions)

    def _archive_broken_index(self) -> None:
        """把损坏的 index.json 改名为 index.json.bad-<ms>；best-effort，改名失败也不再抛。"""
        src = self._root / "index.json"
        if not src.exists():
            # 文件不存在时 repo.load 回 None、不走 ConfigStorageError，首次启动不落到这里
            return
        bad = src.with_name(f"index.json.bad-{_now_ms()}")
        try:
            src.rename(bad)
        except OSError:
            return  # 留档只是尽力而为，改名失败绝不能反过来把启动打断
        logger.warning("会话索引不可用，已留档 %s，本次以空索引启动", bad)

    def _save_index(self) -> None:
        # 调用方必须持锁；索引丢了正文还在，但本期不提供从 .jsonl 重建索引的路径，故坏索引先留档
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

    def create(self, session_id: str, agent_id: str, project_id: str, first_message: str) -> Session:
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
                "project_id": project_id,
                "title": _title_from(first_message),
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
            }
            self._index.sessions.append(record)
            self._save_index()
            self._path(session_id).touch()
            return Session(**record)

    def list(self, agent_id: str, project_id: str) -> list[Session]:
        # 双条件过滤：列表是「这个项目下这个智能体的会话」，缺一即跨项目串列（spec 裁定 1/2）
        # project_id 无默认值：漏传项目必须是 TypeError，而不是静默匹配空归属的跨项目泄漏路径
        rows = [
            s for s in self._index.sessions
            if s["agent_id"] == agent_id and s["project_id"] == project_id
        ]
        rows.sort(key=lambda s: (s["updated_at"], s["created_at"]), reverse=True)
        return [Session(**s) for s in rows]

    def count_by_project(self, project_id: str) -> int:
        # 与 delete_by_project 读同一份 self._index.sessions：那边锁内增删，这边就得持锁读——
        # 这个数字喂给删除确认弹窗，读到中间态就报出与实际不符的会话数。
        # 锁不可重入，但本方法体只遍历索引（不碰 _save_index/messages/append 等再加锁的路径），
        # 调用方只有项目路由，且不在任何 with self._lock 之内
        with self._lock:
            return sum(1 for s in self._index.sessions if s["project_id"] == project_id)

    def delete_by_project(self, project_id: str) -> int:
        with self._lock:
            rows = [s for s in self._index.sessions if s["project_id"] == project_id]
            if not rows:
                return 0
            for row in rows:
                self._index.sessions.remove(row)
            # 与 delete 同款刻意顺序：先写索引再 unlink，宁可留孤儿正文也不留指向不存在文件的悬空行
            self._save_index()
            for row in rows:
                self._path(row["id"]).unlink(missing_ok=True)
            return len(rows)

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
            if not isinstance(raw, dict):
                continue
            if not str(raw.get("agent_id") or "") or not str(raw.get("session_id") or ""):
                logger.warning("会话 %s 消息行缺少 agent_id/session_id，已跳过", session_id)
                continue
            out.append(ChatMessage.from_dict(raw))
        return out

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        steps: list[dict[str, Any]] | None = None,
        stopped: bool = False,
        context: dict[str, Any] | None = None,
    ) -> None:
        path = self._path(session_id)
        created_at = _now_iso()
        ts = _ms_from_iso(created_at) or _now_ms()
        # jsonl 先写、index 后记：中途崩溃只丢一次计数更新，消息本身不丢
        with self._lock:
            record = self._index_by_id().get(session_id)
            if record is None:
                raise SessionStoreError(MISSING_SESSION_DETAIL)
            agent_id = self._agent_id or str(record.get("agent_id") or "")
            line = ChatMessage(
                role=role, content=content, ts=ts,
                name=role, id=_new_message_id(), created_at=created_at,
                agent_id=agent_id, session_id=session_id,
                steps=steps or None, stopped=stopped, context=context,
            ).to_dict()
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
            # 先写索引再 unlink 是刻意顺序：宁可留下可回收的孤儿 .jsonl，也不留指向不存在文件的
            # 悬空行——后者会让列表里冒出一条点开就坏的会话，孤儿正文事后还能清
            self._save_index()
            self._path(session_id).unlink(missing_ok=True)
            return True
