"""智能体实例装配：每请求现装现弃，实例不持有状态。

装配器本身是常驻的（无状态服务）：它只做「读数据 → 拼一个一次性实例」。会话历史与
持久记录住在 memory / storage 层并按键隔离，所以切智能体、切会话、切项目都不需要
维护实例生命周期，也没有跨请求可变状态可竞争。
"""

from dataclasses import dataclass
from typing import Any

from aitester.agents import find_agent
from aitester.adapters.llm import LlmProvider
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.orchestration.graph_registry import GraphBuilder, get_graph_builder
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService


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
