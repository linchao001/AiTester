from typing import Any


class InMemoryRepository:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)
