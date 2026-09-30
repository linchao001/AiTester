from typing import Any

from aitester.adapters.llm import LlmProvider, MockProvider, build_provider
from aitester.config import get_settings
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
        self.provider = provider
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()

    def _complete(
        self, session_id: str, message: str, provider: LlmProvider
    ) -> dict[str, Any]:
        trace: list[str] = ["services"]

        history = self.memory.recall(session_id)
        messages = self.context.build(SYSTEM_PROMPT, history, message)
        trace.append("context")

        reply = run_echo(provider, messages)
        trace += ["orchestration", "adapters"]

        self.memory.save(session_id, "user", message)
        self.memory.save(session_id, "assistant", reply)
        trace.append("memory")

        self.repo.put(f"session:{session_id}", {"session_id": session_id, "last_reply": reply})
        trace.append("storage")

        return {"reply": reply, "trace": trace, "model": provider.model_ref}

    def echo(self, session_id: str, message: str) -> dict[str, Any]:
        """七层 trace 回归链路：永远走 mock，与真实 provider 配置无关。"""
        result = self._complete(session_id, message, MockProvider())
        return {"reply": result["reply"], "trace": result["trace"]}

    def send(self, session_id: str, message: str) -> dict[str, Any]:
        """真实链路：使用注入的 provider，未注入时请求期按当前配置构建。"""
        provider = self.provider or build_provider(get_settings())
        return self._complete(session_id, message, provider)
