class MockProvider:
    name = "mock"
    model_ref = "mock/mock"

    def complete(self, messages: list[dict[str, str]]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "user":
                return f"[mock] {msg['content']}"
        return "[mock]"
