"""知识库工具：经 RemeKbManager 调用共享 KB 的检索与写入 job。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from aitester.adapters.tools.base import AiTooler


class KbSearchInput(BaseModel):
    query: str = Field(description="检索关键词或问题")
    limit: int = Field(default=5, ge=1, le=20, description="最多返回条数")
    bucket: str = Field(default="all", description="范围：all / business / test / business/wiki 等")


class KbSaveInput(BaseModel):
    title: str = Field(description="知识节点标题")
    content: str = Field(description="知识节点正文（Markdown）")
    bucket: str = Field(default="business/wiki", description="发布桶，如 business/wiki、test/test_design")


class KbSearchTool(AiTooler):
    name: str = "knowledge_search"
    description: str = (
        "检索全局共享知识库（智会宝 zhb_kb 等），返回命中的知识节点与出处路径。"
        "编写用例、回答业务问题前先用它查证已有知识。"
    )
    args_schema: type[BaseModel] = KbSearchInput
    kb: Any = None
    agent_id: str = "console"

    def _run(self, query: str, limit: int = 5, bucket: str = "all", **_: Any) -> str:
        resp = self.kb.run_job_sync(
            "knowledge_search", agent_id=self.agent_id, query=query, limit=limit, bucket=bucket,
        )
        if not resp.success:
            return f"知识库检索失败：{resp.answer}"
        if isinstance(resp.answer, str) and resp.answer.strip():
            return resp.answer
        return json.dumps(resp.metadata, ensure_ascii=False, default=str)


class KbSaveTool(AiTooler):
    name: str = "save_to_knowledge"
    description: str = (
        "把一个确定成立的知识节点写入共享知识库发布桶。写入会影响所有项目与其他智能体，"
        "内容必须经用户确认或来自已验证事实。"
    )
    args_schema: type[BaseModel] = KbSaveInput
    kb: Any = None
    agent_id: str = "console"

    def _run(self, title: str, content: str, bucket: str = "business/wiki", **_: Any) -> str:
        resp = self.kb.run_job_sync(
            "save_to_knowledge", agent_id=self.agent_id, title=title, content=content, bucket=bucket,
        )
        head = "已写入知识库" if resp.success else "写入知识库失败"
        body = resp.answer if isinstance(resp.answer, str) else json.dumps(resp.answer, ensure_ascii=False, default=str)
        return f"{head}：{body}"
