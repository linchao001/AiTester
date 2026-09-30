from typing import Protocol


class ContextBuilder(Protocol):
    """把会话原料装配为编排层可用的 prompt 消息列表。"""

    def build(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> list[dict[str, str]]: ...
