"""会话级文件观察记录：支撑「改前必读」守卫与陈旧检测（对齐 dsh 的 fs/observed 机制）。"""

from __future__ import annotations

from pathlib import Path


def file_version(target: Path) -> str:
    """文件版本：大小 + mtime（纳秒），等价 dsh stat 返回的 version。"""
    st = target.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


class FileObservationStore:
    """按 (会话, 路径) 记录最近一次成功读取/写入后的文件版本。"""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], str] = {}

    def mark(self, session_id: str, path: str, version: str) -> None:
        self._versions[(session_id, path)] = version

    def has(self, session_id: str, path: str) -> bool:
        return (session_id, path) in self._versions

    def is_current(self, session_id: str, path: str, version: str) -> bool:
        return self._versions.get((session_id, path)) == version
