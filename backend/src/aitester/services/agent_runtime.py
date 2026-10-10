"""智能体实例装配：每请求现装现弃，实例不持有状态。

装配器本身是常驻的（无状态服务）：它只做「读数据 → 拼一个一次性实例」。会话历史与
持久记录住在 memory / storage 层并按键隔离，所以切智能体、切会话、切项目都不需要
维护实例生命周期，也没有跨请求可变状态可竞争。
"""

from dataclasses import dataclass
from logging import getLogger
from typing import Any

from aitester.agents import SUBAGENT_CATALOG, find_agent, find_subagent, is_platform_agent
from aitester.adapters.llm import LlmProvider
from aitester.adapters.llm.metered import MeteredProvider
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.adapters.tools.subagent_tools import TaskTool, build_task_tool
from aitester.case_design.env import CaseDesignEnv
from aitester.context.usage import ContextUsage
from aitester.orchestration.auth_rules import face_can_suspend
from aitester.orchestration.graph_registry import GraphBuilder, get_graph_builder
from aitester.orchestration.subagent import ChildRuntime, drive_child
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService

logger = getLogger(__name__)


@dataclass(frozen=True)
class AgentInstance:
    agent_id: str
    system_prompt: str
    provider: LlmProvider
    tools: list[AiTooler]
    build_graph: GraphBuilder
    case_env: CaseDesignEnv | None = None     # 专属 loop 的落点与 KB；None=直通（A10）
    # 本回合的上下文累加件：provider 侧的计量与工具侧的闸门共用它（CM-6）
    usage: ContextUsage | None = None


class AgentRuntime:
    """装配器（组合根）：产出一次性 AgentInstance。"""

    def __init__(
        self,
        capability: CapabilityConfigService,
        model_config: ModelConfigService,
        observations: FileObservationStore | None = None,
        kb=None,
    ) -> None:
        self._capability = capability
        self._model_config = model_config
        self._observations = observations if observations is not None else FileObservationStore()
        self._kb = kb

    def _metered(self, provider: LlmProvider, usage: ContextUsage) -> LlmProvider:
        """包一层计量。注入替身（provider_override）也照包，否则离线端到端永远量不到。
        窗口不从参数进——它已经在 usage 上（一回合一个窗口，R-C2 不许第二个副本）。"""
        return MeteredProvider(provider, usage)

    def _metered_pair(self, provider: LlmProvider,
                      uid: str) -> tuple[LlmProvider, ContextUsage]:
        """新回合开局：窗口在这里读一次、账在这里造一本、缝在这里包一层。别无第二处。"""
        usage = ContextUsage(self._model_config.window_for(uid))
        return MeteredProvider(provider, usage), usage

    def build(
        self,
        agent_id: str,
        session_id: str,
        provider_override: LlmProvider | None = None,
        cwd: str = ".",
        project_id: str = "",
    ) -> AgentInstance:
        spec = find_agent(agent_id)
        if spec is None:
            raise ConfigNotFoundError(f"未知智能体「{agent_id}」")

        # 平台功能智能体短路：必须在任何能力配置读取之前（spec 裁定②）——
        # kb_assistant 不在 DEFAULT_AGENT_STATE，走 _agent_state 会误抛 ConfigNotFoundError。
        if is_platform_agent(spec.id):
            return self._build_platform_agent(spec, session_id, provider_override)

        provider: LlmProvider
        uid: str
        if provider_override is not None:
            provider, uid = provider_override, ""      # 注入即短路，不解析模型（测试缝与内部调用同一入口）
            # uid 留空串：window_for("") == 0 ⇒ 离线挂具的窗口天然是「未知」，前端显示「—」。
            # 短路语义（注入替身绝不去读能力/模型配置）一个字都没改。
        else:
            uid = self._capability.effective_uid(agent_id)
            provider = self._model_config.build_provider(uid)
        provider, usage = self._metered_pair(provider, uid)

        pid = (project_id or "").strip() or "default"
        state: dict[str, Any] = self._capability.agent_state(agent_id)
        tools: list[AiTooler] = []
        if state["tool_ids"]:
            # 会话键与 memory/storage 的 scoped 键保持一致：会话按智能体隔离，
            # 「改前必读」守卫记录也必须按智能体隔离，否则 A 读过即可解锁 B 会话的写。
            registry = build_default_registry(
                cwd=cwd,
                session_id=f"{agent_id}:{session_id}",
                observed=self._observations,
                kb=self._kb,
                agent_id=agent_id,
                project_id=pid,
                task=self._task_tool(session_id, cwd, provider_override, usage, pid),
                usage=usage,
            )
            tools = registry.get_many(state["tool_ids"])

        # 专属 loop 的激活判据（A10）：图名是唯一判据；替身无开关（_NoopKbManager）视作
        # 未启用——缺省 False 而非注册表闸门那侧的 True，直通是存量服务测试零改动的前提
        case_env = None
        if (spec.graph_builder == "case_design_loop" and self._kb is not None
                and bool(getattr(self._kb, "is_enabled", False))):
            case_env = CaseDesignEnv(project_dir=cwd, kb=self._kb, project_id=pid)

        return AgentInstance(
            agent_id=spec.id,
            system_prompt=spec.prompt,
            provider=provider,
            tools=tools,
            build_graph=get_graph_builder(spec.graph_builder),
            case_env=case_env,
            usage=usage,
        )

    def _task_tool(self, session_id: str, cwd: str,
                   provider_override: LlmProvider | None,
                   usage: ContextUsage,
                   project_id: str = "default") -> TaskTool:
        """task 工具实例：roster 来自子智能体目录，build_child 现装现弃子实例。

        子面与父面同源不同表：工具来自子自己的能力勾选（设置可调），cwd 认同父项目
        落点（子写界外仍走父审批通道——子 gate 读同一份 configurable）；子注册表
        不传 task=（R7：深度 1 是装配锁）；kb 注入父装配同一份（T7：评审子
        knowledge_search 要查证业务信息；未启用时注册表自动不注册，面按既有口径收敛）。

        parallel 表由同一份能力勾选推导（R14）：只读面 → 可并行扇出；用户一旦给子
        勾上写 / 命令 / 知识库写，该子自动退回「一轮一个」的串行通路——这不是新加的
        闸门，是「会挂起的子没法并行续跑」这条机制事实，且在设置页看得见、改得动。

        `usage` 是父回合那本账：子面不另起一账（CM-6），子的 provider 与工具都写回
        同一本——一回合（含它派出去的所有子）在终帧只有一份读数。
        """
        faces = {spec.id: self._capability.agent_state(spec.id)["tool_ids"]
                 for spec in SUBAGENT_CATALOG}
        roster = {spec.id: {"name": spec.name, "desc": spec.desc, "tools": faces[spec.id]}
                  for spec in SUBAGENT_CATALOG}
        parallel = {spec.id: not face_can_suspend(faces[spec.id])
                    for spec in SUBAGENT_CATALOG}

        def build_child(sub_agent_id: str) -> ChildRuntime:
            spec = find_subagent(sub_agent_id)
            if spec is None:
                raise ValueError(f"未知子智能体「{sub_agent_id}」")
            raw: LlmProvider = (
                provider_override
                if provider_override is not None
                else self._model_config.build_provider(
                    self._capability.effective_uid(spec.id))
            )
            provider: LlmProvider = self._metered(raw, usage)   # 共用父账本（CM-6）
            tools: list[AiTooler] = []
            tool_ids = self._capability.agent_state(spec.id)["tool_ids"]
            if tool_ids:
                registry = build_default_registry(
                    cwd=cwd,
                    session_id=f"{spec.id}:{session_id}",
                    observed=self._observations,
                    kb=self._kb,          # T7：评审子 knowledge_search 与父同一份 KB（原为 kb=None）
                    agent_id=spec.id,
                    project_id=project_id,
                    usage=usage,          # 子面工具同一条闸同一本账
                )
                tools = registry.get_many(tool_ids)
            return ChildRuntime(
                agent_id=spec.id,
                system_prompt=spec.prompt,
                provider=provider,
                tools=tools,
                build_graph=get_graph_builder(spec.graph_builder),
            )

        return build_task_tool(roster, build_child, drive_child, parallel, usage=usage)

    def _build_platform_agent(
        self, spec, session_id: str, provider_override: LlmProvider | None
    ) -> AgentInstance:
        """平台功能智能体：强制绑定 spec.default_tool_ids，不读能力配置勾选状态。

        工具面天然按「清单 ∩ 实际可注册集合」收敛（spec 裁定②）：kb 关闭/未注入时
        knowledge_search / prepare_kb_write 不在注册表，get_many 取交集只剩三件套。
        cwd 固定平台 fallback workspace（data/workspaces/_platform，与 Reme 池键对齐），
        会话记忆键 f"{spec.id}:{session_id}" 与守卫键同构），knowledge junction 由
        装配期 best-effort 预热首启实例挂载（冷 workspace 修复，失败不阻断装配）。
        """
        provider: LlmProvider
        uid: str
        if provider_override is not None:
            provider, uid = provider_override, ""      # 注入即短路，不解析模型（同 build 口径）
        else:
            uid = self._model_config.default_uid
            provider = self._model_config.build_provider(uid)
        provider, usage = self._metered_pair(provider, uid)
        cwd = "."
        if self._kb is not None:
            workspace = self._kb.workspace_dir("default")
            workspace.mkdir(parents=True, exist_ok=True)
            cwd = str(workspace)
            # 热挂载（终审项 4）：knowledge junction 由 reme 实例首启（mount_knowledge，
            # 仅发生在 manager._start_app）创建；此处 best-effort 跑一次 status job 把
            # 首启提前到装配期，令首轮 read/grep/glob 不再看到空目录。
            # 失败绝不影响 build：实例坏时后续 knowledge_search 自会按 503/错误文案收敛。
            try:
                self._kb.run_job_sync("status", project_id="default", agent_id=spec.id)
            except Exception:  # 预热仅尽力而为，任何异常记录后继续装配
                logger.warning("kb_assistant 实例预热失败（不影响装配）", exc_info=True)
        registry = build_default_registry(
            cwd=cwd,
            session_id=f"{spec.id}:{session_id}",
            observed=self._observations,
            kb=self._kb,
            agent_id=spec.id,
            project_id="default",
            usage=usage,
        )
        return AgentInstance(
            agent_id=spec.id,
            system_prompt=spec.prompt,
            provider=provider,
            tools=registry.get_many(list(spec.default_tool_ids)),
            build_graph=get_graph_builder(spec.graph_builder),
            usage=usage,
        )
