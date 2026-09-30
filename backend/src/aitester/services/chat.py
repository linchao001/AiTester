from typing import Any

from aitester.adapters.llm import LlmProvider, MockProvider
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_echo
from aitester.storage import InMemoryRepository, Repository

SYSTEM_PROMPT = "你是 AiTester 测试智能体（骨架占位）。"


class ChatService:
    """业务门面：装配四层并记录穿透 trace。"""

    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
    ) -> None:
        self.provider = provider or MockProvider()
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()

    def echo(self, session_id: str, message: str) -> dict[str, Any]:
        trace: list[str] = ["services"]

        history = self.memory.recall(session_id)
        messages = self.context.build(SYSTEM_PROMPT, history, message)
        trace.append("context")

        reply = run_echo(self.provider, messages)
        trace += ["orchestration", "adapters"]

        self.memory.save(session_id, "user", message)
        self.memory.save(session_id, "assistant", reply)
        trace.append("memory")

        self.repo.put(f"session:{session_id}", {"session_id": session_id, "last_reply": reply})
        trace.append("storage")

        return {"reply": reply, "trace": trace}
