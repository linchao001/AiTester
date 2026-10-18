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


class RecallHistoryInput(BaseModel):
    limit: int = Field(
        default=20, ge=1, le=200,
        description="How many most-recent messages of the current conversation to pull back.")
    query: str = Field(
        default="",
        description="Optional keyword; when set, only messages containing it (case-insensitive) are returned.")


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


# frontmatter 结构性 key：删了会让节点失去身份/类型/落点，维护工具不得移除。
_PROTECTED_FM_KEYS = frozenset({"name", "id", "type", "path", "source"})


def _normalize_kb_path(path: str) -> str:
    """去首尾空白、统一分隔符、剥前导 ./ 与前导 /；把展示口径 .AiTester/knowledge 归一到 job 口径 knowledge。

    模型从 knowledge_search 拿到的路径经 reme_paths_for_project_cwd 改写为 .AiTester/knowledge/...（junction
    展示名），而 reme file_io job 期望相对 workspace 的 knowledge/...；两口径在此收敛为一个。
    """
    raw = (path or "").strip().replace("\\", "/").lstrip("/")
    while raw.startswith("./"):
        raw = raw[2:]
    if raw.startswith(".AiTester/"):
        raw = raw[len(".AiTester/"):]
    return raw


def guard_knowledge_file_path(path: str) -> str:
    """KB 维护工具/端点的路径护栏：目标必须是 knowledge/ 子树内的一个「具体文件」。

    - 归一化两口径（knowledge/... 与展示用 .AiTester/knowledge/...）；
    - 拒空、拒 '..' 越界、拒绝对路径（首段必须是 knowledge）；
    - 必须是文件级目标：≥3 段且叶子段带扩展名——挡掉删/改 knowledge 根或整个发布桶目录；
    - 合规返回归一后的 'knowledge/...' 相对路径，否则抛 ToolException（LangChain 回给模型可读错误，
      端点侧捕获后转 400）。执行期真正的落盘边界由 reme 自身的 workspace 约束再兜一层。
    """
    raw = _normalize_kb_path(path)
    if not raw:
        raise ToolException("Invalid path: provide a knowledge-relative file path such as "
                            "'knowledge/business/wiki/node.md'.")
    parts = raw.split("/")
    if any(p == ".." for p in parts):
        raise ToolException("Invalid path: '..' is not allowed; stay inside the knowledge/ tree.")
    if parts[0] != "knowledge":
        raise ToolException(
            "Path must be under 'knowledge/...' (relative to the project workspace), "
            f"got {raw!r}.")
    leaf = parts[-1]
    if len(parts) < 3 or "." not in leaf or leaf.startswith(".") or leaf.endswith("."):
        raise ToolException(
            "Path must point to a specific knowledge file (e.g. "
            "'knowledge/business/wiki/node.md'); targeting the knowledge root or a whole "
            "bucket/folder is not allowed.")
    return "/".join(parts)


def filter_frontmatter_keys(keys: list[str]) -> list[str]:
    """清洗 front-matter key：strip、去空、去重、剔除受保护的结构 key。清洗后为空即拒。"""
    out: list[str] = []
    seen: set[str] = set()
    for k in keys or []:
        if not isinstance(k, str):
            continue
        s = k.strip()
        if not s or s.lower() in _PROTECTED_FM_KEYS or s in seen:
            continue
        seen.add(s)
        out.append(s)
    if not out:
        raise ToolException(
            "No removable front-matter key: provide at least one key, and structural keys ("
            + ", ".join(sorted(_PROTECTED_FM_KEYS)) + ") cannot be removed.")
    return out


class PrepareKbDeleteInput(BaseModel):
    path: str = Field(
        description="Workspace-relative path of the KB file to delete under knowledge/... "
                    "(e.g. knowledge/business/wiki/node.md, or the .AiTester/knowledge/... form "
                    "returned by knowledge_search). Must be a single file, not a folder.")
    summary: str = Field(
        default="", description="One-line note shown on the user's confirmation card.")


class PrepareKbDeleteTool(AiTooler):
    """产出 KB 文件硬删草案：零写盘，确认后由前端调 /api/kb/delete 走 reme delete job。"""

    name: str = "prepare_kb_delete"
    description: str = (
        "Prepare a knowledge-base file DELETE draft for user confirmation. This tool never "
        "touches disk: it only validates the path (must be one file under knowledge/..., not a "
        "folder or the knowledge root) and returns a delete card. The file is removed from disk "
        "and its chunks pruned from all search indexes ONLY after the user confirms, via "
        "Reme's delete job. Use this for every KB delete; never claim the file was removed. "
        "Deleting affects all projects sharing this knowledge base."
    )
    args_schema: type[BaseModel] = PrepareKbDeleteInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(self, path: str, summary: str = "") -> tuple[str, dict[str, Any]]:
        rel = guard_knowledge_file_path(path)
        draft = {
            "kind": "delete",
            "path": rel,
            "abs_display": rel,
            "summary": (summary or "").strip(),
        }
        return (
            f"Delete draft ready (NOT yet deleted): path={rel!r}. Ask the user to confirm the "
            "delete card; nothing is removed until they confirm (UI will run the Reme delete job)."
        ), draft


class PrepareKbEditInput(BaseModel):
    path: str = Field(
        description="Workspace-relative path of the KB markdown file under knowledge/... "
                    "(knowledge/... or the .AiTester/knowledge/... form from knowledge_search).")
    old: str = Field(description="Literal text to find in the file body (must be non-empty).")
    new: str = Field(
        default="",
        description="Replacement text. Pass an empty string to delete every match. "
                    "Front matter is never touched.")
    summary: str = Field(
        default="", description="One-line note shown on the user's confirmation card.")


class PrepareKbEditTool(AiTooler):
    """产出 KB 正文替换草案：零写盘，确认后由前端调 /api/kb/edit 走 reme edit job（护 frontmatter）。"""

    name: str = "prepare_kb_edit"
    description: str = (
        "Prepare a knowledge-base file BODY edit draft (find-and-replace) for user "
        "confirmation. This tool never touches disk: it validates path/old and returns an edit "
        "card. On confirm the UI runs Reme's edit job, which replaces every occurrence of 'old' "
        "with 'new' in the file BODY only (the YAML front matter is parsed out and preserved "
        "verbatim; new='' deletes the matched text). Read the file first via knowledge_search so "
        "the card shows a faithful change. Use this for every KB edit; never claim the edit is done."
    )
    args_schema: type[BaseModel] = PrepareKbEditInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(self, path: str, old: str, new: str = "", summary: str = "") -> tuple[str, dict[str, Any]]:
        rel = guard_knowledge_file_path(path)
        if not old:
            raise ToolException("Invalid old: provide the non-empty text to replace.")
        draft = {
            "kind": "edit",
            "path": rel,
            "abs_display": rel,
            "old": old,
            "new": new,
            "summary": (summary or "").strip(),
        }
        verb = "remove" if new == "" else "replace"
        return (
            f"Edit draft ready (NOT yet written, verb={verb}): path={rel!r}. Ask the user to "
            "confirm the edit card; nothing changes on disk until they confirm."
        ), draft


class PrepareKbFrontmatterInput(BaseModel):
    path: str = Field(
        description="Workspace-relative path of the KB markdown file under knowledge/... "
                    "(knowledge/... or the .AiTester/knowledge/... form from knowledge_search).")
    keys: list[str] = Field(
        description="Front-matter keys to remove, e.g. ['tags', 'owner']. Body text is untouched. "
                    "Structural keys (name/id/type/path/source) are rejected.")
    summary: str = Field(
        default="", description="One-line note shown on the user's confirmation card.")


class PrepareKbFrontmatterTool(AiTooler):
    """产出删 frontmatter 字段草案：零写盘，确认后由前端调 /api/kb/frontmatter_delete 走 reme job。"""

    name: str = "prepare_kb_frontmatter"
    description: str = (
        "Prepare a knowledge-base front-matter field DELETE draft for user confirmation. This "
        "tool never touches disk: it validates the path and the requested keys (structural keys "
        "like name/id/type/path/source cannot be removed) and returns a card. On confirm the UI "
        "runs Reme's frontmatter_delete job, which removes the listed keys from the file's YAML "
        "front matter and leaves the body untouched (rewriting only when a key is actually "
        "present). Use this for every front-matter change; never claim it is done."
    )
    args_schema: type[BaseModel] = PrepareKbFrontmatterInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(self, path: str, keys: list[str], summary: str = "") -> tuple[str, dict[str, Any]]:
        rel = guard_knowledge_file_path(path)
        cleaned = filter_frontmatter_keys(keys)
        draft = {
            "kind": "frontmatter",
            "path": rel,
            "abs_display": rel,
            "keys": cleaned,
            "summary": (summary or "").strip(),
        }
        return (
            f"Front-matter delete draft ready (NOT yet written): path={rel!r} keys={cleaned}. "
            "Ask the user to confirm the card; nothing changes until they confirm."
        ), draft


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
            "kind": "save",
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


class RecallSessionHistoryTool(AiTooler):
    """按需召回当前会话的既往对话：默认上下文不含历史，智能体判断需要时调本工具读回落盘历史。

    读源是本项目下本会话的 SessionStore jsonl（构造时注入 store + session_id）；
    工具调用发生在回合内，本次 user 尚未落盘（done 才写），故读回的都是既往回合。
    只读 role/content（历史正文），不外泄 steps/context 等过程细节给模型。
    """

    name: str = "recall_session_history"
    description: str = (
        "Recall prior turns of THIS conversation from disk. The assistant runs with an "
        "empty conversation history by default (past turns are persisted but NOT injected "
        "into context). Call this only when you actually need earlier exchanges to answer — "
        "it returns the most recent messages of the current session, optionally filtered by "
        "a keyword. Do not call it for self-contained questions."
    )
    args_schema: type[BaseModel] = RecallHistoryInput
    store: Any = None
    session_id: str = ""

    def _run(self, limit: int = 20, query: str = "", **_: Any) -> str:
        if self.store is None or not self.session_id:
            return "No persisted conversation history is available for this session."
        try:
            messages = self.store.messages(self.session_id)
        except Exception as exc:  # 读盘异常不得打断回合：给模型一句可读的降级说明
            return f"Failed to read session history: {type(exc).__name__}."
        rows = [{"role": m.role, "content": m.content} for m in messages]
        keyword = (query or "").strip().lower()
        if keyword:
            rows = [r for r in rows if keyword in (r["content"] or "").lower()]
        rows = rows[-max(1, int(limit)):] if rows else rows
        if not rows:
            return "(no matching prior messages in this session)"
        lines = [f"{r['role']}: {r['content']}" for r in rows if r["content"]]
        return "\n".join(lines) if lines else "(no matching prior messages in this session)"
