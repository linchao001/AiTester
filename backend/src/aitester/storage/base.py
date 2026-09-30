from typing import Any, Protocol


class Repository(Protocol):
    """实体持久化抽象，后续专项开发替换为 SQLite/DB 实现。"""

    def put(self, key: str, value: dict[str, Any]) -> None: ...

    def get(self, key: str) -> dict[str, Any] | None: ...
