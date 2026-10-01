from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError
from aitester.adapters.tools.base import AiTooler
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_echo, run_graph
from aitester.orchestration.graph_registry import GraphBuilder
from aitester.services.agent_runtime import AgentRuntime
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
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self.provider = provider
        self.agent_runtime = agent_runtime
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()

    def _complete(
        self,
        key: str,
        message: str,
        provider: LlmProvider,
        system_prompt: str,
        build: GraphBuilder | None = None,
        tools: list[AiTooler] | None = None,
    ) -> dict[str, Any]:
        trace: list[str] = ["services"]

        history = self.memory.recall(key)
        messages = self.context.build(system_prompt, history, message)
        trace.append("context")

        trace += ["orchestration", "adapters"]
        if build is None:
            reply = run_echo(provider, messages)
        else:
            result = run_graph(build, provider, tools or [], _to_langchain_messages(messages))
            reply = result["reply"]
            for tt in result["tool_traces"]:
                trace.append(f"tool:{tt['tool']}")

        self.memory.save(key, "user", message)
        self.memory.save(key, "assistant", reply)
        trace.append("memory")

        self.repo.put(f"session:{key}", {"session_id": key, "last_reply": reply})
        trace.append("storage")

        return {"reply": reply, "trace": trace, "model": provider.model_ref}

    def echo(self, session_id: str, message: str) -> dict[str, Any]:
        """七层 trace 回归链路：永远走 mock，与真实 provider 配置无关。"""
        result = self._complete(session_id, message, MockProvider(), SYSTEM_PROMPT)
        return {"reply": result["reply"], "trace": result["trace"]}

    def send(
        self,
        session_id: str,
        message: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """真实链路：装配一次性实例（提示词/模型/工具/拓扑）后按图执行。"""
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        instance = self.agent_runtime.build(
            agent_id, session_id, provider_override=self.provider
        )
        return self._complete(
            f"{instance.agent_id}:{session_id}",
            message,
            instance.provider,
            instance.system_prompt,
            build=instance.build_graph,
            tools=instance.tools,
        )


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
