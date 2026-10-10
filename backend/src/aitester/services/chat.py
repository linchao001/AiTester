import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError
from aitester.adapters.tools.base import AiTooler
from aitester.agents import is_platform_agent
from aitester.case_design.env import CaseDesignEnv
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.context.usage import ContextUsage
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import drop_thread, new_thread_id, run_echo, stream_graph
from aitester.orchestration.auth_rules import DEFAULT_PERM_MODE, validate_perm_mode
from aitester.orchestration.gate import GateContext, build_gate_context
from aitester.orchestration.graph_registry import GraphBuilder
from aitester.orchestration.run_control import RunControl
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.pending import (
    PENDING_GONE_DETAIL,
    PendingEntry,
    PendingGoneError,
    PendingRegistry,
)
from aitester.services.project_config import (
    ProjectConfigError,
    ProjectService,
    dir_exists,
)
from aitester.services.session_locator import SessionLocator
from aitester.services.session_store import (
    MISSING_SESSION_DETAIL,
    SessionStoreError,
    is_session_id,
)
from aitester.storage import InMemoryRepository, Repository

SYSTEM_PROMPT = "你是 AiTester 测试智能体（骨架占位）。"

# 只截 prompt，磁盘保留全量：不截则真实模型下长会话每轮 token 线性上涨（spec 裁定 6）
# 与 frontend/src/pages/chat/utils.ts 的 HISTORY_MAX 同一常数（第 4 片裁定 6）
HISTORY_MAX = 40
# 知识库助手面向 QA：进模型只要短窗多轮（4 轮 ≈ 8 条 user/assistant），不必跟主聊天共用 40
KB_HISTORY_MAX = 8
KB_ASSISTANT_ID = "kb_assistant"


def _history_max_for(agent_id: str) -> int:
    return KB_HISTORY_MAX if agent_id == KB_ASSISTANT_ID else HISTORY_MAX

logger = logging.getLogger(__name__)

# done/step 事件里喂给 UI 的过程块字段：严格取键，缺字段即 KeyError（不兜默认防假绿）
_STEP_KEYS = ("tool", "ok", "round", "detail")
# 磁盘多 result（全量工具输出）；对外 API/SSE 仍只暴露 _STEP_KEYS
_DISK_STEP_KEYS = ("tool", "ok", "round", "detail", "result")

# wait 事件喂给 pending 队列的字段：严格取键，缺字段即 KeyError（与 _STEP_KEYS 同口径）。
# subagent 是 R3 的来源标注：父层 None、子层为卡片出处；pending 表原样透给 /chat/pending。
_WAIT_KEYS = ("call_id", "tool", "action", "target", "command", "cwd", "subagent")


def _public_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: s[k] for k in _STEP_KEYS} for s in steps]


def _steps_for_disk(
    outcome: dict[str, Any] | None, public: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """用 finish.tool_traces 给本段 steps 补 result；续跑时 public 含前段，traces 只覆盖尾部。"""
    traces = (outcome or {}).get("tool_traces")
    if not isinstance(traces, list) or not traces:
        return list(public)
    out = [dict(s) for s in public]
    if len(out) >= len(traces):
        base = len(out) - len(traces)
        for i, t in enumerate(traces):
            out[base + i] = {
                "tool": t["tool"],
                "ok": t["ok"],
                "round": t["round"],
                "detail": t["detail"],
                "result": str(t.get("result", "")),
            }
        return out
    return [
        {
            "tool": t["tool"],
            "ok": t["ok"],
            "round": t["round"],
            "detail": t["detail"],
            "result": str(t.get("result", "")),
        }
        for t in traces
    ]


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
    perm_mode: str = DEFAULT_PERM_MODE
    agent_id: str = ""
    project_id: str = ""
    project_dir: str = ""         # expanduser 再 resolve：判定与续跑守卫都认它
    gate: GateContext | None = None
    case_env: CaseDesignEnv | None = None   # 专属 loop 的注入通道（None=直通）
    # 本回合的上下文累加件（装配根造好、provider/工具共用同一本）：手工装配的挂具实例
    # 没有它 ⇒ None ⇒ 落盘与终帧的 context 键都是 null，界面显「—」
    usage: ContextUsage | None = None


class ChatService:
    """业务门面：装配四层并把一次回合拆成「守门 → 事件流 → 终态落盘」。"""

    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
        agent_runtime: AgentRuntime | None = None,
        sessions: SessionLocator | None = None,
        projects: ProjectService | None = None,
        pending: PendingRegistry | None = None,
        personal: Any | None = None,
    ) -> None:
        self.provider = provider
        self.agent_runtime = agent_runtime
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()
        self.sessions = sessions
        self.projects = projects
        # 待批表：装配位注入（与 run_registry 同处 app.state），单测直调时自持一份
        self.pending = pending if pending is not None else PendingRegistry()
        # Reme 个人记忆（可选）；None=不挂钩子（存量单测 / 未启用）
        self.personal = personal

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

    def _guard_project(self, project_id: str) -> dict[str, Any]:
        """守门只有一段：prepare 与 resume_stream 复用同一函数、同一条 detail（第 2 片教训）。

        复用项目页读侧同一只探测（裁定 3）：展开 ~、绝不 mkdir、吞 (OSError, ValueError)，
        畸形 dir（NUL 走 ValueError）在此同样答「不可达」→ 中文 400，绝不外泄成 500。
        """
        pid = (project_id or "").strip()
        if not pid:
            raise ProjectConfigError("请先选择项目，再发送消息")
        if self.projects is None:
            raise ProjectConfigError("服务未装配项目配置，请通过 create_app 启动后端")
        project = self.projects.get(pid)          # 未知项目 → ConfigNotFoundError → 路由 404
        if not dir_exists(project["dir"]):
            raise ProjectConfigError(
                f"项目「{project['name']}」的目录 {project['dir']} 不存在或不可访问，"
                "请到项目页确认路径"
            )
        return project

    def prepare(
        self, session_id: str, message: str, agent_id: str, project_id: str = "",
        perm_mode: str = DEFAULT_PERM_MODE,
    ) -> PreparedRun:
        """守门 + 装配 + 记忆选择 + 上下文拼装：全部会以 4xx 结束的段落只在这里存在一份。

        项目维度（第 2 片）：可见智能体的会话必须属于一个项目，落点取项目 dir；
        平台功能智能体不属于项目，project_id 一律忽略（spec 裁定 7，/kb 链路依赖此）。
        """
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        mode = validate_perm_mode(perm_mode)      # 第一句：非法档位在任何副作用之前 400
        platform = is_platform_agent(agent_id)
        project: dict[str, Any] | None = None
        pid = ""
        if not platform:
            project = self._guard_project(project_id)
            pid = (project_id or "").strip()
        sid = (session_id or "").strip()
        store = None
        if self.sessions is not None and not platform and pid:
            store = self.sessions.for_agent(pid, agent_id)
        if not sid:
            if store is None:
                raise ProviderConfigError("请指定会话 id 或通过 create_app 装配会话存储")
            sid = store.new_id()
        elif store is not None and is_session_id(sid):
            existing = store.get(sid)
            if existing is None:
                raise SessionStoreError(MISSING_SESSION_DETAIL)
            # 会话归属校验：sess_* 续写前先判等 agent_id，否则任何 agent_id 都能往别人的会话里写；
            # 项目维度不进键（第 2 片裁定 2），归属就靠这两维判等，detail 经路由 SessionStoreError→404 落到用户
            if existing.agent_id != agent_id:
                raise SessionStoreError("会话不属于该智能体")
            # 第二维（第 2 片）：项目归属同判据；平台智能体不落的会话不参与比对
            if existing.project_id != pid:
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
            project_id=pid,
        )

        use_file = store is not None and is_session_id(sid) and not platform
        memory: MemoryStore
        if use_file:
            # 延迟导入：避免顶层 memory.session.file_memory → services → chat 成环
            from aitester.memory.session.file_memory import FileMemoryStore

            memory = FileMemoryStore(store, pid)
        else:
            memory = self.memory

        # 判定与落点认同一个展开：dir 配成 ~/x 时两侧都看到家目录（第 2 片教训的对称面）
        project_dir = (str(Path(project["dir"]).expanduser().resolve())
                       if project is not None else "")
        key = f"{instance.agent_id}:{sid}"
        # 只截进模型的 prompt；记忆/UI 仍可留全量。kb_assistant 用短窗，主聊天仍 HISTORY_MAX。
        history = memory.recall(key)[-_history_max_for(instance.agent_id):]
        messages = _to_langchain_messages(
            self.context.build(instance.system_prompt, history, message)
        )
        if self.personal is not None and not platform and pid:
            hit = self.personal.auto_search_for_turn(
                project_id=pid, agent_id=instance.agent_id, query=message,
            )
            if hit:
                # 插在 system 之后：只影响当回合 LLM 上下文，不落 SessionStore
                insert_at = 1 if messages and isinstance(messages[0], SystemMessage) else 0
                messages.insert(
                    insert_at,
                    SystemMessage(content=f"[aitester_personal_memory]\n{hit}"),
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
            perm_mode=mode,
            agent_id=instance.agent_id,
            project_id=pid,
            project_dir=project_dir,
            gate=build_gate_context(mode, project_dir, key, self.pending.remembered_for(key)),
            case_env=instance.case_env,
            usage=instance.usage,
        )

    def _persist(
        self, prepared: PreparedRun, reply: str, steps: list[dict[str, Any]], stopped: bool
    ) -> dict[str, Any] | None:
        """终态落盘：用户句 + assistant 句（带 steps/stopped/context）+ 仓储快照，
        顺序与迁移前一致。

        返回本回合的上下文快照：done 帧与磁盘那一行必须是**同一份**快照（在 `_persist`
        里算一次、把它交出去），两处各取一次就是「门上的话会说谎」同族。没有账的挂具
        实例（手工装配的 AgentInstance）回 None。
        """
        usage = prepared.usage
        snapshot = usage.snapshot() if usage is not None else None
        prepared.memory.save(prepared.key, "user", prepared.message)
        prepared.memory.save(prepared.key, "assistant", reply,
                             steps=steps, stopped=stopped, context=snapshot)
        self.repo.put(f"session:{prepared.key}",
                      {"session_id": prepared.key, "last_reply": reply})
        if self.personal is not None and prepared.project_id and prepared.agent_id:
            # 按用户回合累计；默认每 5 轮后台 flush（对齐 QwenPaw auto_memory_interval）
            self.personal.note_user_turn(
                project_id=prepared.project_id,
                agent_id=prepared.agent_id,
                session_id=prepared.session_id,
                messages=[
                    {"role": "user", "content": prepared.message},
                    {"role": "assistant", "content": reply},
                ],
            )
        return snapshot

    def stream_turn(
        self, prepared: PreparedRun, control: RunControl | None = None, run_id: str = ""
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

        run_id 非空时它就是图线程 id（P4：pending→resume 复用同一个），空串则自造。
        """
        control = control or RunControl()
        thread_id = run_id or new_thread_id()
        events = stream_graph(
            prepared.build, prepared.provider, prepared.tools, prepared.messages,
            control=control, thread_id=thread_id, gate=prepared.gate,
            case_env=prepared.case_env,
        )
        return self._fold_turn(events, prepared, control, thread_id)

    def _fold_turn(
        self, events: Iterator[dict[str, Any]], prepared: PreparedRun,
        control: RunControl, thread_id: str, entry: PendingEntry | None = None,
    ) -> Iterator[dict[str, Any]]:
        """首回合与续跑共用的一条折叠：collect → 挂起入表 / 终态落盘 → 线程收摊。

        挂起分支 return 在 persist 之前（裁定 8：不落盘），且不发 done——流就在 wait 之后断掉
        （裁定 7）。R8：finish.pending 同时 control.cancelled 时停止优先，落 stopped 截断行。
        """
        # 续跑折叠不是新回合：前一段的过程行与已投递正文要接着算，用户看到的是一条连续回答
        steps: list[dict[str, Any]] = list(entry.steps) if entry is not None else []
        base_prefix = entry.prefix_text if entry is not None else ""
        visible: dict[int, str] = {}
        waiting: list[dict[str, Any]] = []
        outcome: dict[str, Any] | None = None
        try:
            for event in events:
                kind = event["type"]
                if kind == "finish":
                    outcome = event
                    continue
                if kind == "wait":
                    waiting.append({k: event[k] for k in _WAIT_KEYS})
                elif kind == "step" and event.get("subagent") is None:
                    steps.append({k: event[k] for k in _STEP_KEYS})
                elif kind == "delta":
                    r = int(event["round"])
                    visible[r] = visible.get(r, "") + str(event["text"])
                yield event
            if outcome is not None and outcome["pending"] and not control.cancelled:
                self._hold(prepared, thread_id, entry, visible, steps, waiting)
                return
            # stream_graph 保证终帧 finish：走到这里 outcome 必非空，无需兜底
            # R8：待批途中按停止——工具轮的 finish.reply 恒为空串（正文只在无工具轮才写），
            # 落它等于把用户已看到的半句抹成一条「已完成的空回答」：改用已投递前缀并标 stopped。
            hung = bool(outcome["pending"])
            reply = (base_prefix + (visible[max(visible)] if visible else "")
                     if hung else str(outcome["reply"]))
            stopped = True if hung else bool(outcome["stopped"])
            disk_steps = _steps_for_disk(outcome, steps)
            snapshot = self._persist(prepared, reply, disk_steps, stopped)
            # 对外始终无 result：有 traces 时从 disk 剥离；否则沿用收集期的 public steps
            public = (
                _public_steps(disk_steps)
                if any("result" in s for s in disk_steps)
                else steps
            )
            outcome = None               # 已落盘：done 帧后再被 close() 不得二次落盘
            self._release(thread_id, entry)
            stored = None
            if (
                self.sessions is not None
                and prepared.project_id
                and prepared.agent_id
                and is_session_id(prepared.session_id)
            ):
                stored = self.sessions.for_agent(
                    prepared.project_id, prepared.agent_id
                ).get(prepared.session_id)
            yield {
                "type": "done",
                "reply": reply,
                "steps": public,
                "session_id": prepared.session_id,
                "title": stored.title if stored is not None else "",
                "stopped": stopped,
                "context": snapshot,          # 与磁盘那一行同一份快照，只算这一次
            }
        except GeneratorExit:
            control.cancel()
            try:
                for event in events:      # 无人消费也要跑到停笔点，只为拿到 finish
                    if event["type"] == "step" and event.get("subagent") is None:
                        steps.append({k: event[k] for k in _STEP_KEYS})
                    elif event["type"] == "finish":
                        outcome = event
            except Exception:             # 收尾路径的失败绝不能盖掉原始断开
                logger.warning("断开收尾时图未跑完，本次不落截断盘", exc_info=True)
            if outcome is not None:
                # 断开这一路没有 done 帧可带：快照落进磁盘那一行就到此为止（返回值弃掉
                # 是有意的，不是漏接——用户不在，读数只能等他重开会话时从磁盘读）
                self._persist(
                    prepared,
                    base_prefix + (visible[max(visible)] if visible else ""),
                    _steps_for_disk(outcome, steps),
                    stopped=True,
                )
            # 断开 == 停止（第 4 片同语义）：留下的条目一律作废，线程也不再等批准
            self._release(thread_id, entry)
            raise
        except Exception:
            # 图跑挂（provider 故障、事件缺键）：条目和检查点线程都不留。
            # 挂起过的 run 若留着条目，里面那把决策已进 consumed，用户再点续跑就是永久 400。
            self._release(thread_id, entry)
            raise

    def _hold(self, prepared: PreparedRun, thread_id: str,
              entry: PendingEntry | None, visible: dict[int, str],
              steps: list[dict[str, Any]], waiting: list[dict[str, Any]]) -> None:
        """挂起入表：首挂开条目，续跑又撞卡就原地更新（队列按 call_id 去重）。"""
        # 续跑又撞卡：前缀 = 上一段已投递 + 本段新投递（上一段那份就在条目里）
        prefix = ((entry.prefix_text if entry is not None else "")
                  + (visible[max(visible)] if visible else ""))
        if entry is not None:
            self.pending.update_hold(entry, prefix=prefix, steps=steps, waiting=waiting)
            return
        self.pending.open(PendingEntry(
            run_id=thread_id, thread_id=thread_id, prepared=prepared,
            perm_mode=prepared.perm_mode, project_id=prepared.project_id,
            project_dir=prepared.project_dir, session_key=prepared.key,
            session_id=prepared.session_id, agent_id=prepared.agent_id,
            prefix_text=prefix, steps=list(steps), queue=list(waiting)))

    def _release(self, thread_id: str, entry: PendingEntry | None) -> None:
        """终态收摊：条目摘除 + 检查点线程删除。带 checkpointer 后不删就是每轮泄漏一条线程。"""
        if entry is not None:
            self.pending.take(entry.run_id)
        drop_thread(thread_id)

    def resume_stream(self, run_id: str,
                      control: RunControl | None = None) -> tuple[str, Iterator[dict[str, Any]]]:
        """从 gate 的中断处续跑一条已登记的待批。

        守门全部留在 HTTP 空间（第 4 片「守门同步跑」同口径）：PendingGoneError /
        ProjectConfigError / ResumeNotReadyError 都在返回迭代器之前抛出，路由据此回 404/400。
        判定按条目创建时那一档（prepared.gate 是锁档的对象），不读请求当下的字段值。
        messages 在这里只是占位：resume 投的是 Command，图从检查点续，不再吃新输入。
        """
        entry = self.pending.peek(run_id)
        if entry is None:
            raise PendingGoneError(PENDING_GONE_DETAIL)
        self._guard_project(entry.project_id)        # 同函数、同 detail：挂起期间目录可能被删
        self.pending.peek_resume(run_id)   # 只守卫：无决策可喂 → ResumeNotReadyError（R6），不烧决策
        prepared = entry.prepared
        control = control or RunControl()

        def events() -> Iterator[dict[str, Any]]:
            # 决策要在续跑真开跑时才落 consumed：请求死在首帧之前的话，提前烧掉会让下一条
            # 决策喂进上一条的中断位——langgraph 的续跑值按位置匹配（实测），错一位就串味。
            taken = self.pending.take_resume(run_id)
            yield from stream_graph(
                prepared.build, prepared.provider, prepared.tools, prepared.messages,
                control=control, thread_id=entry.thread_id, gate=prepared.gate,
                case_env=prepared.case_env,
                resume={"decision": taken["decision"], "remember": taken["remember"]},
            )

        return prepared.session_id, self._fold_turn(
            events(), prepared, control, entry.thread_id, entry)

    def approve(self, run_id: str, call_id: str, decision: str, remember: bool) -> None:
        """只登记决策：续跑由 resume_stream 触发（批准与续跑分开，双弹层与重复提交才好收敛）。"""
        self.pending.answer(run_id, call_id, decision, remember)

    def cancel_pending(self, run_id: str) -> bool:
        """待批期间的停止（裁定 10 第二条）：原子摘除 → 以已投递前缀落 stopped 行 → 线程收摊。

        这是本片唯一一次在收尾之前落盘，与第 4 片的断开落盘同语义；摘除后 resume 必 404。
        """
        entry = self.pending.take(run_id)
        if entry is None:
            return False
        # 同断开分支：这条路径没有 done 帧，快照只进磁盘那一行（返回值弃掉是有意的）
        self._persist(entry.prepared, entry.prefix_text, entry.steps, True)
        drop_thread(entry.thread_id)
        return True

    def pending_view_for(self, agent_id: str, project_id: str) -> list[PendingEntry]:
        # UI 只按「智能体 × 项目」取数（R14）：挂起会话没落盘，按会话查在刷新后必然查空
        return self.pending.view_for(agent_id, project_id)

    def drop_session(self, session_id: str) -> None:
        """删会话级联：先 flush 未处理 auto_memory，再清 pending / 检查点线程。"""
        if self.personal is not None:
            try:
                self.personal.flush_session(session_id)
            except Exception:
                logger.exception(
                    "auto_memory flush on drop_session failed; soft-skip",
                )
        for thread_id in self.pending.drop_session(session_id):
            drop_thread(thread_id)

    def send(
        self, session_id: str, message: str, agent_id: str, project_id: str = "",
        perm_mode: str = DEFAULT_PERM_MODE,
    ) -> dict[str, Any]:
        """一次性折返壳：prepare + 事件流折回迁移前的响应形状。

        留着它的两个理由：既有 22 处 `svc.send(` 用例是真实链路的回归锁；`trace`/`model`
        两字段只在服务层存活（SSE 协议不带它们，spec 偏离登记 3）。
        """
        prepared = self.prepare(session_id, message, agent_id, project_id, perm_mode)
        reply = ""
        sid = prepared.session_id
        title = ""
        steps: list[dict[str, Any]] = []
        drafts: list[dict[str, Any]] = []
        for event in self.stream_turn(prepared):
            kind = event["type"]
            if kind == "step" and event.get("subagent") is None:
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
