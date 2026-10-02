from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError
from aitester.adapters.tools.base import AiTooler
from aitester.agents import is_platform_agent
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_echo, run_graph
from aitester.orchestration.graph_registry import GraphBuilder
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.session_store import SessionStore, SessionStoreError, is_session_id
from aitester.storage import InMemoryRepository, Repository

SYSTEM_PROMPT = "你是 AiTester 测试智能体（骨架占位）。"

# 只截 prompt，磁盘保留全量：不截则真实模型下长会话每轮 token 线性上涨（spec 裁定 6）
HISTORY_MAX = 40


class ChatService:
    """业务门面：装配四层并记录穿透 trace。"""

    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
        agent_runtime: AgentRuntime | None = None,
        sessions: SessionStore | None = None,
    ) -> None:
        self.provider = provider
        self.agent_runtime = agent_runtime
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()
        self.sessions = sessions

    def _complete(
        self,
        key: str,
        message: str,
        provider: LlmProvider,
        system_prompt: str,
        build: GraphBuilder | None = None,
        tools: list[AiTooler] | None = None,
        memory: MemoryStore | None = None,
    ) -> dict[str, Any]:
        # 装配层传入的记忆实例优先；None 表示回退默认进程内记忆，不回写 self.memory
        memory = memory or self.memory
        trace: list[str] = ["services"]
        drafts: list = []

        history = memory.recall(key)[-HISTORY_MAX:]
        messages = self.context.build(system_prompt, history, message)
        trace.append("context")

        steps: list[dict] = []
        trace += ["orchestration", "adapters"]
        if build is None:
            reply = run_echo(provider, messages)
        else:
            result = run_graph(build, provider, tools or [], _to_langchain_messages(messages))
            reply = result["reply"]
            drafts = result.get("drafts", [])
            for tt in result["tool_traces"]:
                trace.append(f"tool:{tt['tool']}")
            # result 字段最大 50KB，绝不外传给 UI；Task 5 前 run_graph 尚无 ok/round/detail，缺省兜住防 KeyError
            steps = [
                {
                    "tool": tt["tool"],
                    "ok": tt.get("ok", True),
                    "round": tt.get("round", 0),
                    "detail": tt.get("detail", ""),
                }
                for tt in result["tool_traces"]
            ]

        memory.save(key, "user", message)
        memory.save(key, "assistant", reply, steps=steps)
        trace.append("memory")

        self.repo.put(f"session:{key}", {"session_id": key, "last_reply": reply})
        trace.append("storage")

        return {
            "reply": reply,
            "trace": trace,
            "model": provider.model_ref,
            "drafts": drafts,
            "steps": steps,
        }

    def echo(self, session_id: str, message: str) -> dict[str, Any]:
        """七层 trace 回归链路：永远走 mock，与真实 provider 配置无关。"""
        result = self._complete(session_id, message, MockProvider(), SYSTEM_PROMPT)
        return {"reply": result["reply"], "trace": result["trace"]}

    def send(self, session_id: str, message: str, agent_id: str) -> dict[str, Any]:
        """真实链路：装配一次性实例（提示词/模型/工具/拓扑）后按图执行。

        会话 id 三态：空 → 服务端生成并延迟落盘；sess_* 已存在 → 续写；其余形态
        （kb-console 等临时键）→ 不建会话、走进程内记忆，行为与既有专项一致。
        """
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        sid = (session_id or "").strip()
        if not sid:
            if self.sessions is None or is_platform_agent(agent_id):
                raise ProviderConfigError("请指定会话 id 或通过 create_app 装配会话存储")
            sid = self.sessions.new_id()
        elif self.sessions is not None and is_session_id(sid) and self.sessions.get(sid) is None:
            raise SessionStoreError("会话不存在或已被删除")

        instance = self.agent_runtime.build(agent_id, sid, provider_override=self.provider)

        use_file = (
            self.sessions is not None
            and is_session_id(sid)
            and not is_platform_agent(agent_id)
        )
        memory = None
        if use_file:
            # 延迟导入：file_memory 经 services 回指本包，顶层导入会在循环链上炸开（memory/__init__ 顺序约束同源）
            from aitester.memory import FileMemoryStore

            memory = FileMemoryStore(self.sessions)

        result = self._complete(
            f"{instance.agent_id}:{sid}",
            message,
            instance.provider,
            instance.system_prompt,
            build=instance.build_graph,
            tools=instance.tools,
            memory=memory,
        )
        stored = self.sessions.get(sid) if self.sessions is not None else None
        result["session_id"] = sid
        result["title"] = stored.title if stored is not None else ""
        return result


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
