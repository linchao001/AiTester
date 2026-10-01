from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError
from aitester.adapters.tools.base import AiTooler
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_agent, run_echo
from aitester.services.model_config import ModelConfigService
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
        model_config: ModelConfigService | None = None,
    ) -> None:
        self.provider = provider
        self.model_config = model_config
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()

    def _complete(
        self,
        session_id: str,
        message: str,
        provider: LlmProvider,
        tools: list[AiTooler] | None = None,
    ) -> dict[str, Any]:
        trace: list[str] = ["services"]

        history = self.memory.recall(session_id)
        messages = self.context.build(SYSTEM_PROMPT, history, message)
        trace.append("context")

        if tools:
            lc_messages = _to_langchain_messages(messages)
            agent_result = run_agent(provider, tools, lc_messages)
            reply = agent_result["reply"]
            trace += ["orchestration", "adapters"]
            for tt in agent_result["tool_traces"]:
                trace.append(f"tool:{tt['tool']}")
        else:
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

    def send(
        self,
        session_id: str,
        message: str,
        tools: list[AiTooler] | None = None,
    ) -> dict[str, Any]:
        """真实链路：注入 provider 优先，否则请求期按运行期配置的默认模型构建。"""
        provider = self.provider
        if provider is None:
            if self.model_config is None:
                raise ProviderConfigError("服务未装配模型配置，请通过 create_app 启动后端")
            provider = self.model_config.build_default_provider()
        return self._complete(session_id, message, provider, tools=tools)


def _to_langchain_messages(messages: list[dict[str, str]]) -> list:
    result = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result
