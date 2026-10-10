"""服务层：业务门面。

ChatService 延迟导出，避免 ``memory.session.file_memory → services → chat → memory`` 成环。
"""

from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ModelConfigService

__all__ = ["CapabilityConfigService", "ChatService", "ModelConfigService"]


def __getattr__(name: str):
    if name == "ChatService":
        from aitester.services.chat import ChatService
        return ChatService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
