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
        "knowledge nodes with their source paths. Paths are relative to the project "
        "root (cwd), under .AiTester/knowledge/.... Consult it before writing test "
        "cases or answering business questions."
    )
    args_schema: type[BaseModel] = KbSearchInput
    kb: Any = None
    agent_id: str = "console"
    project_id: str = ""

    def _run(self, query: str, limit: int = 5, bucket: str = "all", **_: Any) -> str:
        from aitester.project_runtime import reme_paths_for_project_cwd

        try:
            resp = self.kb.run_job_sync(
                "knowledge_search",
                project_id=self.project_id,
                agent_id=self.agent_id,
                query=query,
                limit=limit,
                bucket=bucket,
            )
        except TimeoutError:
            return "Knowledge base search timed out; retry with a narrower query."
        if not resp.success:
            return f"Knowledge base search failed: {resp.answer}"
        if isinstance(resp.answer, str) and resp.answer.strip():
            return reme_paths_for_project_cwd(resp.answer)
        return reme_paths_for_project_cwd(
            json.dumps(resp.metadata, ensure_ascii=False, default=str)
        )


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
    project_id: str = ""

    def _run(self, title: str, content: str, bucket: str = "business/wiki", **_: Any) -> str:
        try:
            resp = self.kb.run_job_sync(
                "save_to_knowledge",
                project_id=self.project_id,
                agent_id=self.agent_id,
                title=title,
                content=content,
                bucket=bucket,
            )
        except TimeoutError:
            return "Knowledge base save timed out; check whether the node was written before retrying."
        head = "Saved to knowledge base" if resp.success else "Knowledge base save failed"
        body = resp.answer if isinstance(resp.answer, str) else json.dumps(resp.answer, ensure_ascii=False, default=str)
        return f"{head}: {body}"


class PrepareKbWriteInput(BaseModel):
    title: str = Field(description="Knowledge node title (Reme save_to_knowledge title).")
    content: str = Field(
        description="Node body / summary text for Reme save_to_knowledge (Markdown body, not a full file).")
    bucket: str = Field(
        default="business/wiki",
        description="Published bucket, e.g. business/wiki, test/test_cases, test/test_design.")
    summary: str = Field(
        description="One-line description of what this draft does, shown on the user's confirmation card.")


def _canonicalize_bucket(bucket: str) -> str:
    """对齐 Reme 发布桶；非法桶直接拒，避免确认后 save 静默落到默认桶。"""
    raw = (bucket or "").strip().replace("\\", "/").strip("/")
    try:
        from reme.knowledge.store import canonicalize_published_bucket
    except ImportError as exc:  # pragma: no cover — 运行时必有 reme
        raise ToolException("Reme is unavailable; cannot validate knowledge bucket.") from exc
    canon = canonicalize_published_bucket(raw)
    if canon is None:
        raise ToolException(
            "Invalid bucket for Reme save_to_knowledge. Use a published bucket such as "
            "business/wiki, business/procedure, test/test_cases, or test/test_design "
            "(legacy flat names like 'wiki' are also accepted)."
        )
    return canon


def _display_path(bucket: str, title: str) -> str:
    """草案卡展示用相对路径提示（最终落点以 Reme integrate 为准）。"""
    try:
        from reme.knowledge.dream import _slugify
        slug = _slugify(title) or "node"
    except Exception:
        slug = "node"
    return f"{bucket}/{slug}.md"


class PrepareKbWriteTool(AiTooler):
    """产出 Reme ``save_to_knowledge`` 草案：零写盘，确认后由前端调 /api/kb/save。"""

    name: str = "prepare_kb_write"
    description: str = (
        "Prepare a Reme save_to_knowledge draft for user confirmation. This tool never "
        "touches disk and never calls save_to_knowledge: it only validates title/content/bucket "
        "and returns a draft card. After the user confirms, the UI saves via Reme. "
        "Use this for every knowledge-base write; never claim the write has happened."
    )
    args_schema: type[BaseModel] = PrepareKbWriteInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"
    kb_root: Path = Path(".")

    def _run(
        self, title: str, content: str, bucket: str = "business/wiki", summary: str = "",
    ) -> tuple[str, dict[str, Any]]:
        name = (title or "").strip()
        body = (content or "").strip()
        note = (summary or "").strip()
        if not name:
            raise ToolException("Invalid title: provide a non-empty knowledge node title.")
        if not body:
            raise ToolException("Invalid content: provide non-empty node body/summary text.")
        canon = _canonicalize_bucket(bucket)
        root = Path(self.kb_root).resolve()
        rel = _display_path(canon, name)
        abs_display = str((root / rel).resolve()) if root.exists() else rel
        # 同名节点是否已发布：仅影响卡上「新建/修改」徽标；真正合并由 Reme save 决定
        op = "modify" if _title_likely_published(root, name, canon) else "create"
        draft = {
            "op": op,
            "title": name,
            "content": body,
            "bucket": canon,
            "summary": note,
            "path": rel,
            "abs_display": abs_display,
            "base": None,
            "mtime": 0,
        }
        verb = "update" if op == "modify" else "new node"
        return (
            f"Draft ready ({verb}, NOT yet written): title={name!r} bucket={canon}. "
            "Ask the user to confirm the draft card; nothing is saved until they confirm "
            "(UI will call Reme save_to_knowledge)."
        ), draft


def _title_likely_published(root: Path, title: str, bucket: str) -> bool:
    """轻量提示：桶目录下是否已有同名 frontmatter name（失败当新建）。"""
    folder = root / bucket
    if not folder.is_dir():
        return False
    needle = f'name: "{title}"'
    needle_alt = f"name: '{title}'"
    try:
        for path in folder.rglob("*.md"):
            if not path.is_file():
                continue
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:800]
            except OSError:
                continue
            if needle in head or needle_alt in head:
                return True
    except OSError:
        return False
    return False
