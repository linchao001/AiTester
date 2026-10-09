from typing import Protocol


class MemoryStore(Protocol):
    """会话记忆抽象，最小实现为进程内存储（重启即失）。"""

    # steps：工具过程痕迹，只有文件实现需要持久化；实现者不接受该形参即断链
    # stopped：第 4 片的停止标注，同样只有文件实现有持久语义
    # context：上下文管理第一片的回合读数快照，同样只有文件实现有持久语义
    def save(self, session_id: str, role: str, content: str,
             steps: list[dict] | None = None, stopped: bool = False,
             context: dict | None = None) -> None: ...

    def recall(self, session_id: str) -> list[dict[str, str]]: ...
