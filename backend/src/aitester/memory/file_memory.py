"""文件记忆：把 MemoryStore 的 (键, role, content) 落到 SessionStore。

键形态是刻意的耦合：ChatService 传 f"{agent_id}:{session_id}"，此处按首个冒号切分。
agent_id 与 sess_* 字符集均不含冒号，故切分无歧义（spec 裁定，测试锁住）。
"""

from __future__ import annotations

from aitester.services.session_store import SessionStore, is_session_id


class FileMemoryStore:
    """落盘记忆：会话键不变，项目归属由构造期带入（第 2 片裁定 2：只作归属字段，不进键）。"""

    def __init__(self, store: SessionStore, project_id: str) -> None:
        self._store = store
        self._project_id = project_id

    @staticmethod
    def _split(key: str) -> tuple[str, str]:
        agent_id, _, session_id = key.partition(":")
        return agent_id, session_id

    def save(
        self,
        session_id: str,
        role: str,
        content: str,
        steps: list[dict] | None = None,
        stopped: bool = False,
    ) -> None:
        agent_id, sid = self._split(session_id)
        if self._store.get(sid) is None:
            # 首条消息建会话（延迟落盘裁定）：失败发送不留 0 消息幽灵会话
            self._store.create(sid, agent_id, self._project_id, content if role == "user" else "")
        self._store.append(sid, role, content, steps, stopped)

    def recall(self, session_id: str) -> list[dict[str, str]]:
        _, sid = self._split(session_id)
        if not is_session_id(sid):
            return []
        return [
            {"role": m.role, "content": m.content}
            for m in self._store.messages(sid)
        ]
