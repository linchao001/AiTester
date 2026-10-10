"""知识库工具：经 RemeMemoryManager（记忆层）调用共享 KB 的检索与写入 job。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import ToolException
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


class PrepareKbWriteInput(BaseModel):
    op: Literal["create", "modify"] = Field(
        description="create: new file; modify: overwrite an existing file after user confirmation.")
    path: str = Field(
        description="Markdown path relative to the knowledge-base root, e.g. '_inbox/note.md'. Only .md is accepted.")
    content: str = Field(
        description="Full file content after writing, frontmatter included.")
    summary: str = Field(
        description="One-line description of what this draft does, shown on the user's confirmation card.")


class PrepareKbWriteTool(AiTooler):
    name: str = "prepare_kb_write"
    description: str = (
        "Prepare a knowledge-base write as a user-confirmable draft. This tool never touches disk: "
        "it only validates the target and returns a draft the user must confirm in the UI before "
        "anything is saved. Use it for every knowledge-base write and never claim the write has "
        "happened — say the user needs to confirm the draft card instead."
    )
    args_schema: type[BaseModel] = PrepareKbWriteInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"
    kb_root: Path = Path(".")

    def _run(self, op: str, path: str, content: str, summary: str) -> tuple[str, dict[str, Any]]:
        # 延迟导入：adapters.tools 初始化链上导入 aitester.memory.reme.paths 会经
        # services/__init__ 反向触发 agent_runtime→adapters.tools 循环导入
        from aitester.memory.reme.paths import hidden_segment, mtime_ms

        rel = (path or "").strip().replace("\\", "/").lstrip("/")
        if not rel or "\0" in rel:
            raise ToolException("Invalid path: provide a non-empty path relative to the knowledge base root.")
        if not rel.lower().endswith(".md"):
            raise ToolException("Only Markdown (.md) files can be written to the knowledge base.")
        root = Path(self.kb_root).resolve()  # 纵深防御：默认部署的根可能未规范化（短名/junction）
        if not root.is_dir():
            raise ToolException("Knowledge base root does not exist on disk yet.")
        target = (root / rel).resolve()
        if target != root and root not in target.parents:
            raise ToolException("Invalid path: target escapes the knowledge base root.")
        rel = target.relative_to(root).as_posix()
        # 与 browse 读侧同一隐藏判据：草稿期即拒，杜绝「确认写入后 browse 403」的死路（终审项 2）
        hidden = hidden_segment(tuple(rel.split("/")))
        if hidden:
            raise ToolException(
                f"Invalid path segment '{hidden}': hidden or internal names (dot-prefixed, "
                ".git/__pycache__/node_modules-like, .pyc/.idx-like) cannot be written to the "
                "knowledge base; pick a visible path such as '_inbox/note.md' and redraft."
            )
        if op == "modify":
            if not target.is_file():
                raise ToolException(f"Cannot modify: {rel} does not exist; use op 'create' instead.")
            base = target.read_text(encoding="utf-8", errors="replace")
            mtime = mtime_ms(target.stat())
        else:
            if target.exists():
                raise ToolException(f"Cannot create: {rel} already exists; use op 'modify' or another name.")
            if not target.parent.is_dir():
                raise ToolException(f"Cannot create: parent directory of {rel} does not exist.")
            base, mtime = None, 0
        draft = {"op": op, "path": rel, "abs_display": str(target), "summary": summary,
                 "content": content, "base": base, "mtime": mtime}
        verb = "new file" if op == "create" else "modification"
        return (f"Draft ready ({verb}, NOT yet written): {rel}. "
                "Ask the user to confirm the draft card; nothing is saved until they confirm."), draft
