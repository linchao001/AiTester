"""智能体领域数据包：定义与提示词，零业务依赖（只 import 标准库）。"""

from aitester.agents.catalog import (
    AGENT_CATALOG,
    DEFAULT_AGENT_STATE,
    KB_ASSISTANT_SPEC,
    LEGACY_AGENT_IDS,
    PLATFORM_AGENT_CATALOG,
    SUBAGENT_CATALOG,
    find_agent,
    find_subagent,
    is_platform_agent,
)
from aitester.agents.spec import AgentSpec

__all__ = [
    "AGENT_CATALOG",
    "DEFAULT_AGENT_STATE",
    "KB_ASSISTANT_SPEC",
    "LEGACY_AGENT_IDS",
    "PLATFORM_AGENT_CATALOG",
    "SUBAGENT_CATALOG",
    "AgentSpec",
    "find_agent",
    "find_subagent",
    "is_platform_agent",
]
