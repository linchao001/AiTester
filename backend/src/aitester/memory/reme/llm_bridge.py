"""从 ModelConfigService 解析与主对话同源的 LlmProvider，供 Reme as_llm 注入。

禁止另造 AgentScope OpenAIChatModel；Reme 0.4.1.13+ 经 LangChainChatModel 适配。
"""

from __future__ import annotations

import logging
from typing import Any

from aitester.adapters.llm.base import LlmProvider

logger = logging.getLogger(__name__)


def resolve_chat_provider(
    model_config: Any,
    *,
    agent_id: str = "",
    capability: Any | None = None,
) -> LlmProvider:
    """按智能体有效 uid（无则全局 default_uid）构建 AiTester 聊天 provider。"""
    uid = ""
    if capability is not None and agent_id:
        try:
            uid = str(capability.effective_uid(agent_id) or "")
        except Exception:
            logger.debug("effective_uid failed for %s; fall back to default", agent_id)
            uid = ""
    if not uid:
        uid = str(getattr(model_config, "default_uid", "") or "")
    return model_config.build_provider(uid)
