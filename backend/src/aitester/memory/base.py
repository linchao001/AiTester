from typing import Protocol


class MemoryStore(Protocol):
    """会话记忆抽象，最小实现为进程内存储（重启即失）。"""

    def save(self, session_id: str, role: str, content: str) -> None: ...

    def recall(self, session_id: str) -> list[dict[str, str]]: ...
