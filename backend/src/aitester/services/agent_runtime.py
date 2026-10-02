"""智能体实例装配：每请求现装现弃，实例不持有状态。

装配器本身是常驻的（无状态服务）：它只做「读数据 → 拼一个一次性实例」。会话历史与
持久记录住在 memory / storage 层并按键隔离，所以切智能体、切会话、切项目都不需要
维护实例生命周期，也没有跨请求可变状态可竞争。
"""

from dataclasses import dataclass
from logging import getLogger
from typing import Any

from aitester.agents import find_agent, is_platform_agent
from aitester.adapters.llm import LlmProvider
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.orchestration.graph_registry import GraphBuilder, get_graph_builder
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

    def build(
        self,
        agent_id: str,
        session_id: str,
        provider_override: LlmProvider | None = None,
    ) -> AgentInstance:
        spec = find_agent(agent_id)
        if spec is None:
            raise ConfigNotFoundError(f"未知智能体「{agent_id}」")

        # 平台功能智能体短路：必须在任何能力配置读取之前（spec 裁定②）——
        # kb_assistant 不在 DEFAULT_AGENT_STATE，走 _agent_state 会误抛 ConfigNotFoundError。
        if is_platform_agent(spec.id):
            return self._build_platform_agent(spec, session_id, provider_override)

        provider: LlmProvider
        if provider_override is not None:
            provider = provider_override  # 注入即短路，不解析模型（测试缝与内部调用同一入口）
        else:
            provider = self._model_config.build_provider(
                self._capability.effective_uid(agent_id)
            )

        state: dict[str, Any] = self._capability.agent_state(agent_id)
        tools: list[AiTooler] = []
        if state["tool_ids"]:
            # 会话键与 memory/storage 的 scoped 键保持一致：会话按智能体隔离，
            # 「改前必读」守卫记录也必须按智能体隔离，否则 A 读过即可解锁 B 会话的写。
            registry = build_default_registry(
                cwd=".",
                session_id=f"{agent_id}:{session_id}",
                observed=self._observations,
                kb=self._kb,
                agent_id=agent_id,
            )
            tools = registry.get_many(state["tool_ids"])

        return AgentInstance(
            agent_id=spec.id,
            system_prompt=spec.prompt,
            provider=provider,
            tools=tools,
            build_graph=get_graph_builder(spec.graph_builder),
        )

    def _build_platform_agent(
        self, spec, session_id: str, provider_override: LlmProvider | None
    ) -> AgentInstance:
        """平台功能智能体：强制绑定 spec.default_tool_ids，不读能力配置勾选状态。

        工具面天然按「清单 ∩ 实际可注册集合」收敛（spec 裁定②）：kb 关闭/未注入时
        knowledge_search / prepare_kb_write 不在注册表，get_many 取交集只剩三件套。
        cwd 固定 default 项目的 workspace 目录（实例池键 ("default", spec.id)，
        会话记忆键 f"{spec.id}:{session_id}" 与守卫键同构），knowledge junction 由
        装配期 best-effort 预热首启实例挂载（冷 workspace 修复，失败不阻断装配）。
        """
        provider: LlmProvider = (
            provider_override
            if provider_override is not None
            else self._model_config.build_provider(self._model_config.default_uid)
        )
        cwd = "."
        if self._kb is not None:
            workspace = self._kb.workspace_dir("default", spec.id)
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
        )
        return AgentInstance(
            agent_id=spec.id,
            system_prompt=spec.prompt,
            provider=provider,
            tools=registry.get_many(list(spec.default_tool_ids)),
            build_graph=get_graph_builder(spec.graph_builder),
        )
