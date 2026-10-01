"""知识库工具：经 RemeKbManager 调用共享 KB 的检索与写入 job。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from aitester.adapters.tools.base import AiTooler


class KbSearchInput(BaseModel):
    query: str = Field(description="Search keywords or a question.")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of results to return.")
    bucket: str = Field(default="all", description="Scope: all / business / test / business/wiki, etc.")


class KbSaveInput(BaseModel):
    title: str = Field(description="Title of the knowledge node.")
    content: str = Field(description="Body of the knowledge node (Markdown).")
    bucket: str = Field(default="business/wiki", description="Publish bucket, e.g. business/wiki, test/test_design.")


class KbSearchTool(AiTooler):
    name: str = "knowledge_search"
    description: str = (
        "Search the global shared knowledge base (e.g. zhb_kb) and return matched "
        "knowledge nodes with their source paths. Consult it before writing test "
        "cases or answering business questions."
    )
    args_schema: type[BaseModel] = KbSearchInput
    kb: Any = None
    agent_id: str = "console"

    def _run(self, query: str, limit: int = 5, bucket: str = "all", **_: Any) -> str:
        try:
            resp = self.kb.run_job_sync(
                "knowledge_search", agent_id=self.agent_id, query=query, limit=limit, bucket=bucket,
            )
        except TimeoutError:
            return "Knowledge base search timed out; retry with a narrower query."
        if not resp.success:
            return f"Knowledge base search failed: {resp.answer}"
        if isinstance(resp.answer, str) and resp.answer.strip():
            return resp.answer
        return json.dumps(resp.metadata, ensure_ascii=False, default=str)


class KbSaveTool(AiTooler):
    name: str = "save_to_knowledge"
    description: str = (
        "Write one established knowledge node into a shared knowledge base publish "
        "bucket. The write affects all projects and other agents; ask the user for "
        "explicit confirmation before writing."
    )
    args_schema: type[BaseModel] = KbSaveInput
    kb: Any = None
    agent_id: str = "console"

    def _run(self, title: str, content: str, bucket: str = "business/wiki", **_: Any) -> str:
        try:
            resp = self.kb.run_job_sync(
                "save_to_knowledge", agent_id=self.agent_id, title=title, content=content, bucket=bucket,
            )
        except TimeoutError:
            return "Knowledge base save timed out; check whether the node was written before retrying."
        head = "Saved to knowledge base" if resp.success else "Knowledge base save failed"
        body = resp.answer if isinstance(resp.answer, str) else json.dumps(resp.answer, ensure_ascii=False, default=str)
        return f"{head}: {body}"
