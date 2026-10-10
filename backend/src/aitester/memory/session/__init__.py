"""会话聊天记录（非 Reme）：UI 与短上下文；也是 auto_memory 的入参数据源。

FileMemoryStore 延迟导出，避免经 services 回指 memory 成环。
"""

from aitester.memory.session.base import MemoryStore
from aitester.memory.session.in_memory import InMemoryMemoryStore

__all__ = ["FileMemoryStore", "InMemoryMemoryStore", "MemoryStore"]


def __getattr__(name: str):
    if name == "FileMemoryStore":
        from aitester.memory.session.file_memory import FileMemoryStore
        return FileMemoryStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
