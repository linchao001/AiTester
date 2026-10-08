"""工具基础抽象——基于 LangChain BaseTool，所有内置工具继承此类。"""

from typing import Any

from langchain_core.tools import BaseTool

from aitester.context.budget import cap_result
from aitester.context.usage import ContextUsage


class AiTooler(BaseTool):
    """AiTester 内置工具的公共基类。

    子类需提供：
    - ``name`` / ``description``：面向模型的工具标识与说明
    - ``args_schema``：Pydantic 模型，描述入参形状
    - ``_run()``：实际执行逻辑

    ``usage`` 是装配层注入的上下文读数累加件（可为 None）：截断在这里记账，
    与 provider 侧的计量共用同一个对象，所以「一回合」只有一本账。
    """

    usage: ContextUsage | None = None

    def tool_id(self) -> str:
        """工具注册标识，默认取 name。"""
        return self.name

    def run(self, tool_input: Any, *args: Any, **kwargs: Any) -> Any:
        return cap_result(super().run(tool_input, *args, **kwargs),
                          tool=self.name, usage=self.usage)

    async def arun(self, tool_input: Any, *args: Any, **kwargs: Any) -> Any:
        return cap_result(await super().arun(tool_input, *args, **kwargs),
                          tool=self.name, usage=self.usage)
