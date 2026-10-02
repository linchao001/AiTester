class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[dict[str, str]]] = {}

    def save(self, session_id: str, role: str, content: str,
             steps: list[dict] | None = None) -> None:
        # 骨架实现不建模过程块：steps 是真实链路产物，进程内记忆只保 role/content
        self._messages.setdefault(session_id, []).append({"role": role, "content": content})

    def recall(self, session_id: str) -> list[dict[str, str]]:
        return list(self._messages.get(session_id, []))
