"""文件工具公共基类：路径解析、观察记录与「改前必读」守卫。"""

from __future__ import annotations

from pathlib import Path

from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.file_tools.errors import (
    raise_not_observed,
    raise_read_not_found,
    raise_stale_version,
)
from aitester.adapters.tools.file_tools.observation import FileObservationStore, file_version


class FsTool(AiTooler):
    """所有文件工具的公共上下文：工作目录、会话标识与观察记录。"""

    cwd: str = "."
    session_id: str = "default"
    observed: FileObservationStore | None = None

    def _resolve(self, file_path: str) -> Path:
        target = Path(file_path)
        if not target.is_absolute():
            target = Path(self.cwd) / target
        return target.resolve()

    def _mark_observed(self, target: Path) -> None:
        if self.observed is not None:
            self.observed.mark(self.session_id, str(target), file_version(target))

    def _guard_mutation(self, target: Path) -> None:
        """改前必读守卫：未读过 → 拒绝；读过但版本已变 → 要求重新读取。"""
        if self.observed is None:
            return
        path = str(target)
        if not self.observed.has(self.session_id, path):
            raise_not_observed(path)
        try:
            version = file_version(target)
        except OSError:
            raise_read_not_found(path)
        if not self.observed.is_current(self.session_id, path, version):
            raise_stale_version(path)
