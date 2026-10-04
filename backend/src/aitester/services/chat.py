import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError
from aitester.adapters.tools.base import AiTooler
from aitester.agents import is_platform_agent
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_echo, stream_graph
from aitester.orchestration.graph_registry import GraphBuilder
from aitester.orchestration.run_control import RunControl
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

logger = logging.getLogger(__name__)

# done/step 事件里喂给 UI 与磁盘的过程块字段：严格取键，缺字段即 KeyError（不兜默认防假绿）
_STEP_KEYS = ("tool", "ok", "round", "detail")


@dataclass(frozen=True)
class PreparedRun:
    """守门已过、装配已完成的回合：路由拿到它才允许开始推流。

    字段一次给齐（含已拼好的 messages），流开始后不再有任何 4xx 判据。
    """

    key: str                      # 记忆键 f"{agent_id}:{session_id}"
    session_id: str
    message: str
    provider: LlmProvider
    system_prompt: str
    build: GraphBuilder
    tools: list[AiTooler]
    memory: MemoryStore
    messages: list[BaseMessage]


class ChatService:
    """业务门面：装配四层并把一次回合拆成「守门 → 事件流 → 终态落盘」。"""

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
    ) -> dict[str, Any]:
        """echo 专用的一次性回合：七层 trace 是它的验收物，故与 send 路径分开留。"""
        trace: list[str] = ["services", "context", "orchestration", "adapters"]
        history = self.memory.recall(key)[-HISTORY_MAX:]
        messages = self.context.build(system_prompt, history, message)
        reply = run_echo(provider, messages)

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

    def prepare(
        self, session_id: str, message: str, agent_id: str, project_id: str = ""
    ) -> PreparedRun:
        """守门 + 装配 + 记忆选择 + 上下文拼装：全部会以 4xx 结束的段落只在这里存在一份。

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
            # 验真与落点必须认同一个展开结果，否则 `~` 项目会被 `mkdir` 建成字面 `~` 目录树
            # （dir_exists 先 expanduser 才答「可达」，fs_tool._resolve 却从不展 `~`）：只在这里展开，
            # 落盘数据与 UI 仍是用户输入的原始形态，无迁移
            cwd=str(Path(project["dir"]).expanduser()) if project is not None else ".",
        )

        use_file = self.sessions is not None and is_session_id(sid) and not platform
        memory: MemoryStore
        if use_file:
            # 延迟导入：file_memory 经 services 回指本包，顶层导入会在循环链上炸开（memory/__init__ 顺序约束同源）
            from aitester.memory import FileMemoryStore

            memory = FileMemoryStore(self.sessions, pid)
        else:
            memory = self.memory

        key = f"{instance.agent_id}:{sid}"
        history = memory.recall(key)[-HISTORY_MAX:]
        messages = _to_langchain_messages(
            self.context.build(instance.system_prompt, history, message)
        )
        return PreparedRun(
            key=key,
            session_id=sid,
            message=message,
            provider=instance.provider,
            system_prompt=instance.system_prompt,
            build=instance.build_graph,
            tools=instance.tools,
            memory=memory,
            messages=messages,
        )

    def _persist(
        self, prepared: PreparedRun, reply: str, steps: list[dict[str, Any]], stopped: bool
    ) -> None:
        """终态落盘：用户句 + assistant 句（带 steps/stopped）+ 仓储快照，顺序与迁移前一致。"""
        prepared.memory.save(prepared.key, "user", prepared.message)
        prepared.memory.save(prepared.key, "assistant", reply, steps=steps, stopped=stopped)
        self.repo.put(f"session:{prepared.key}",
                      {"session_id": prepared.key, "last_reply": reply})

    def stream_turn(
        self, prepared: PreparedRun, control: RunControl | None = None
    ) -> Iterator[dict[str, Any]]:
        """事件流 + 终态落盘：done 之前一条也不写盘（失败不留痕，与迁移前同口径）。

        消费者断开（GeneratorExit，等价于关页/刷新）是唯一「无人消费也要落盘」的情形：
        置取消位后把内层图排干到停笔点拿 finish（后续轮与工具派发也停在这里），
        落盘的 assistant 行取已交付给用户的 delta 前缀并标 stopped——
        用户重开会话看到的正是他被打断的那一版。

        control 为 None 时自持一个：断开收尾靠的就是「置位后图能停在检查点」，
        没有取消对象就没有停笔点，截断落盘无从谈起（brief Step 3 原码漏了这步，
        Step 1 的断开用例钉的就是自持语义）。

        落盘正文取「已交付给消费者的 delta 前缀」而非 finish 的 reply：langgraph 同步流
        在一个 next() 里跑完整个节点，GeneratorExit 送达时图侧早已产完——用户看到的只有
        已经推出去的那半截，钉死的断开用例（"[moc"）锁的就是交付前缀口径。
        """
        control = control or RunControl()
        events = stream_graph(
            prepared.build, prepared.provider, prepared.tools, prepared.messages, control=control
        )
        steps: list[dict[str, Any]] = []
        # visible[round] = 已 yield 出去的该轮 delta 文本；断开时取最后一轮的前缀落盘
        visible: dict[int, str] = {}
        outcome: dict[str, Any] | None = None
        try:
            for event in events:
                if event["type"] == "finish":
                    outcome = event
                    continue
                if event["type"] == "step":
                    steps.append({k: event[k] for k in _STEP_KEYS})
                elif event["type"] == "delta":
                    r = int(event["round"])
                    visible[r] = visible.get(r, "") + str(event["text"])
                yield event
            if outcome is None:
                # 图没产出 finish 属装配缺陷，但界面不能永久挂在 busy 上：按空回合收尾
                outcome = {"reply": "", "tool_traces": [], "drafts": [], "stopped": True}
            reply = str(outcome["reply"])
            stopped = bool(outcome["stopped"])
            self._persist(prepared, reply, steps, stopped)
            stored = (
                self.sessions.get(prepared.session_id)
                if self.sessions is not None else None
            )
            yield {
                "type": "done",
                "reply": reply,
                "steps": steps,
                "session_id": prepared.session_id,
                "title": stored.title if stored is not None else "",
                "stopped": stopped,
            }
        except GeneratorExit:
            control.cancel()
            try:
                for event in events:      # 无人消费也要跑到停笔点，只为拿到 finish
                    if event["type"] == "step":
                        steps.append({k: event[k] for k in _STEP_KEYS})
                    elif event["type"] == "finish":
                        outcome = event
            except Exception:             # 收尾路径的失败绝不能盖掉原始断开
                logger.warning("断开收尾时图未跑完，本次不落截断盘", exc_info=True)
            if outcome is not None:
                prefix = visible[max(visible)] if visible else ""
                self._persist(prepared, prefix, steps, stopped=True)
            raise

    def send(
        self, session_id: str, message: str, agent_id: str, project_id: str = ""
    ) -> dict[str, Any]:
        """一次性折返壳：prepare + 事件流折回迁移前的响应形状。

        留着它的两个理由：既有 22 处 `svc.send(` 用例是真实链路的回归锁；`trace`/`model`
        两字段只在服务层存活（SSE 协议不带它们，spec 偏离登记 3）。
        """
        prepared = self.prepare(session_id, message, agent_id, project_id)
        reply = ""
        sid = prepared.session_id
        title = ""
        steps: list[dict[str, Any]] = []
        drafts: list[dict[str, Any]] = []
        for event in self.stream_turn(prepared):
            kind = event["type"]
            if kind == "step":
                steps.append({k: event[k] for k in _STEP_KEYS})
            elif kind == "draft":
                drafts.append(event["draft"])
            elif kind == "done":
                reply = event["reply"]
                sid = event["session_id"]
                title = event["title"]
        # trace 与迁移前逐字同序：services/context/orchestration/adapters → tool:* → memory → storage
        trace = ["services", "context", "orchestration", "adapters"]
        trace += [f"tool:{s['tool']}" for s in steps]
        trace += ["memory", "storage"]
        return {
            "reply": reply,
            "trace": trace,
            "model": prepared.provider.model_ref,
            "drafts": drafts,
            "steps": steps,
            "session_id": sid,
            "title": title,
        }


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
