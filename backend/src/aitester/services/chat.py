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
from aitester.services.project_config import (
    ProjectConfigError,
    ProjectService,
    dir_exists,
)
from aitester.services.session_store import (
    MISSING_SESSION_DETAIL,
    SessionStore,
    SessionStoreError,
    is_session_id,
)
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
        projects: ProjectService | None = None,
    ) -> None:
        self.provider = provider
        self.agent_runtime = agent_runtime
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()
        self.sessions = sessions
        self.projects = projects

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
            # result 字段最大 50KB，绝不外传给 UI
            steps = [
                {
                    "tool": tt["tool"],
                    "ok": tt["ok"],
                    "round": tt["round"],
                    "detail": tt["detail"],
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

    def send(self, session_id: str, message: str, agent_id: str, project_id: str = "") -> dict[str, Any]:
        """真实链路：装配一次性实例（提示词/模型/工具/拓扑）后按图执行。

        项目维度（第 2 片）：可见智能体的会话必须属于一个项目，落点取项目 dir；
        平台功能智能体不属于项目，project_id 一律忽略（spec 裁定 7，/kb 链路依赖此）。
        """
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        platform = is_platform_agent(agent_id)
        project: dict[str, Any] | None = None
        pid = ""
        if not platform:
            pid = (project_id or "").strip()
            if not pid:
                raise ProjectConfigError("请先选择项目，再发送消息")
            if self.projects is None:
                raise ProjectConfigError("服务未装配项目配置，请通过 create_app 启动后端")
            project = self.projects.get(pid)          # 未知项目 → ConfigNotFoundError → 路由 404
            # 复用项目页读侧同一只探测（裁定 3）：展开 ~、绝不 mkdir、吞 (OSError, ValueError)，
            # 畸形 dir（NUL 走 ValueError）在此同样答「不可达」→ 中文 400，绝不外泄成 500
            if not dir_exists(project["dir"]):
                raise ProjectConfigError(
                    f"项目「{project['name']}」的目录 {project['dir']} 不存在或不可访问，"
                    "请到项目页确认路径"
                )
        sid = (session_id or "").strip()
        if not sid:
            if self.sessions is None or platform:
                raise ProviderConfigError("请指定会话 id 或通过 create_app 装配会话存储")
            sid = self.sessions.new_id()
        elif self.sessions is not None and is_session_id(sid):
            existing = self.sessions.get(sid)
            if existing is None:
                raise SessionStoreError(MISSING_SESSION_DETAIL)
            # 会话归属校验：sess_* 续写前先判等 agent_id，否则任何 agent_id 都能往别人的会话里写；
            # 项目维度不进键（第 2 片裁定 2），归属就靠这两维判等，detail 经路由 SessionStoreError→404 落到用户
            if existing.agent_id != agent_id:
                raise SessionStoreError("会话不属于该智能体")
            # 第二维（第 2 片）：项目归属同判据；平台智能体不落的会话不参与比对
            if not platform and existing.project_id != pid:
                raise SessionStoreError("会话不属于该项目")

        # 装配落点：可见智能体用项目 dir（产出物归位），平台智能体沿用 _build_platform_agent 内部算的 workspace
        instance = self.agent_runtime.build(
            agent_id,
            sid,
            provider_override=self.provider,
            cwd=project["dir"] if project is not None else ".",
        )

        use_file = self.sessions is not None and is_session_id(sid) and not platform
        memory = None
        if use_file:
            # 延迟导入：file_memory 经 services 回指本包，顶层导入会在循环链上炸开（memory/__init__ 顺序约束同源）
            from aitester.memory import FileMemoryStore

            memory = FileMemoryStore(self.sessions, pid)

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
