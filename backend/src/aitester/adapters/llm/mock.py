class MockProvider:
    name = "mock"

    def complete(self, messages: list[dict[str, str]]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "user":
                return f"[mock] {msg['content']}"
        return "[mock]"
