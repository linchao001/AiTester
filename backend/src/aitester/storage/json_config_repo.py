import json
import os
from pathlib import Path
from typing import Any, Protocol


class ConfigStorageError(RuntimeError):
    """配置 JSON 文件读写失败，detail 面向用户且含文件路径。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class JsonConfigRepository(Protocol):
    """运行期配置 JSON 持久化抽象。"""

    def load(self) -> dict[str, Any] | None: ...

    def save(self, config: dict[str, Any]) -> None: ...


class FileJsonConfigRepository:
    """单 JSON 文件实现：同目录临时文件 + os.replace 原子替换。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigStorageError(f"配置文件已损坏：{self._path}（{exc}）") from exc

    def save(self, config: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)
