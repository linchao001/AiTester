"""工具基础抽象——基于 LangChain BaseTool，所有内置工具继承此类。"""

from langchain_core.tools import BaseTool


class AiTooler(BaseTool):
    """AiTester 内置工具的公共基类。

    子类需提供：
    - ``name`` / ``description``：面向模型的工具标识与说明
    - ``args_schema``：Pydantic 模型，描述入参形状
    - ``_run()``：实际执行逻辑
    """

    def tool_id(self) -> str:
        """工具注册标识，默认取 name。"""
        return self.name
