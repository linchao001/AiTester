from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.kb_tools import (
    PrepareKbDeleteTool,
    PrepareKbEditTool,
    PrepareKbFrontmatterTool,
    PrepareKbWriteTool,
    RecallSessionHistoryTool,
    filter_frontmatter_keys,
    guard_knowledge_file_path,
)
from aitester.interaction.schemas import KbDraft
from aitester.memory.reme.manager import RemeMemoryManager
from aitester.services.session_store import SessionStore


class _FakeKb:
    def __init__(self, root: Path):
        self.calls = []
        self.kb_root_dir = root  # prepare_kb_write 注册时读取实体根

    def run_job_sync(self, name, *, project_id="default", agent_id="console", timeout=60.0, **kwargs):
        self.calls.append((name, project_id, agent_id, kwargs))
        return SimpleNamespace(success=True, answer="命中：测试节点", metadata={})


class _TimeoutKb(_FakeKb):
    def run_job_sync(self, name, *, project_id="default", agent_id="console", timeout=60.0, **kwargs):
        raise TimeoutError()  # concurrent.futures Future.result(timeout) 的原生异常


def _manager_settings(tmp_path, kb_enabled: bool) -> SimpleNamespace:
    # kb_bases_dir/kb_id 为 resolve_kb_root 必填项：显式指到 tmp，避免读环境变量
    return SimpleNamespace(
        kb_enabled=kb_enabled,
        data_dir=str(tmp_path),
        kb_id="test_kb",
        kb_bases_dir=str(tmp_path),
    )


def test_registry_without_kb_has_no_kb_tools():
    reg = build_default_registry(cwd=".")
    assert reg.get("knowledge_search") is None


def test_kb_tools_run_against_manager(tmp_path):
    kb = _FakeKb(tmp_path)
    reg = build_default_registry(
        cwd=".", kb=kb, agent_id="case_design", project_id="proj_x",
    )
    out = reg.get("knowledge_search").invoke({"query": "热词", "limit": 2})
    assert "测试节点" in out
    assert kb.calls == [
        ("knowledge_search", "proj_x", "case_design",
         {"query": "热词", "limit": 2, "bucket": "all"}),
    ]

    out2 = reg.get("save_to_knowledge").invoke({"title": "t", "content": "c"})
    assert out2
    assert kb.calls[1] == (
        "save_to_knowledge", "proj_x", "case_design",
        {"title": "t", "content": "c", "bucket": "business/wiki"},
    )


def test_knowledge_search_rewrites_paths_under_aitester(tmp_path):
    class _PathKb(_FakeKb):
        def run_job_sync(self, name, *, project_id="default", agent_id="console",
                         timeout=60.0, **kwargs):
            return SimpleNamespace(
                success=True,
                answer="命中 knowledge/business/wiki/a.md",
                metadata={},
            )

    reg = build_default_registry(cwd=".", kb=_PathKb(tmp_path), project_id="p1")
    out = reg.get("knowledge_search").invoke({"query": "x"})
    assert out == "命中 .AiTester/knowledge/business/wiki/a.md"


def test_kb_disabled_manager_registers_no_tools(tmp_path):
    """kb_enabled=False：manager 实例存在也必须对模型隐藏 KB 工具面。"""
    disabled = RemeMemoryManager(settings=_manager_settings(tmp_path, False), data_dir=tmp_path)
    reg = build_default_registry(cwd=".", kb=disabled)
    assert reg.get("knowledge_search") is None
    assert reg.get("save_to_knowledge") is None


def test_kb_enabled_manager_registers_tools(tmp_path):
    enabled = RemeMemoryManager(settings=_manager_settings(tmp_path, True), data_dir=tmp_path)
    reg = build_default_registry(cwd=".", kb=enabled)
    assert reg.get("knowledge_search") is not None
    assert reg.get("save_to_knowledge") is not None


def test_kb_tool_timeout_yields_non_empty_error(tmp_path):
    reg = build_default_registry(cwd=".", kb=_TimeoutKb(tmp_path))
    out = reg.get("knowledge_search").invoke({"query": "热词"})
    assert out.strip()
    assert "timed out" in out
    out2 = reg.get("save_to_knowledge").invoke({"title": "t", "content": "c"})
    assert out2.strip()
    assert "timed out" in out2


class _StubRootKb:
    is_enabled = True

    def __init__(self, root: Path):
        self.kb_root_dir = root

    def run_job_sync(self, name, **kwargs):  # pragma: no cover
        raise AssertionError("prepare_kb_write 不应触发任何 job")


def _tool(tmp_path: Path) -> PrepareKbWriteTool:
    (tmp_path / "business" / "wiki").mkdir(parents=True)
    return PrepareKbWriteTool(kb_root=tmp_path)


def test_prepare_reme_draft_zero_write(tmp_path):
    tool = _tool(tmp_path)
    out, art = tool._run(
        title="热词说明", content="正文一段", bucket="business/wiki", summary="记一笔",
    )
    assert "NOT yet written" in out
    assert "save_to_knowledge" in out
    assert art["title"] == "热词说明"
    assert art["content"] == "正文一段"
    assert art["bucket"] == "business/wiki"
    assert art["summary"] == "记一笔"
    assert art["op"] == "create"
    assert art["path"].startswith("business/wiki/")
    assert art["mtime"] == 0 and art["base"] is None
    # 零写盘：桶目录仍只有我们建的空目录
    assert list((tmp_path / "business" / "wiki").glob("*.md")) == []


def test_prepare_marks_modify_when_title_exists(tmp_path):
    tool = _tool(tmp_path)
    (tmp_path / "business" / "wiki" / "x.md").write_text(
        '---\nname: "热词说明"\nbucket: business/wiki\n---\n\n# 热词说明\n',
        encoding="utf-8",
    )
    _, art = tool._run(
        title="热词说明", content="补一句", bucket="wiki", summary="补",
    )
    assert art["op"] == "modify"
    assert art["bucket"] == "business/wiki"  # legacy flat → canonical


def test_prepare_validation_errors(tmp_path):
    tool = _tool(tmp_path)
    with pytest.raises(ToolException, match="title"):
        tool._run(title="  ", content="c", bucket="business/wiki", summary="s")
    with pytest.raises(ToolException, match="content"):
        tool._run(title="t", content="", bucket="business/wiki", summary="s")
    with pytest.raises(ToolException, match="Invalid bucket"):
        tool._run(title="t", content="c", bucket="_inbox", summary="s")
    with pytest.raises(ToolException, match="Invalid bucket"):
        tool._run(title="t", content="c", bucket="not/a/bucket", summary="s")


def test_draft_artifact_conforms_to_kb_draft_contract(tmp_path):
    tool = _tool(tmp_path)
    _, art = tool._run(
        title="节点A", content="正文", bucket="business/wiki", summary="摘要",
    )
    d = KbDraft.model_validate(art)
    assert d.title == "节点A" and d.bucket == "business/wiki"
    assert d.content == "正文" and d.summary == "摘要"
    assert d.op == "create" and d.path.startswith("business/wiki/")


def test_registry_registers_prepare_kb_write(tmp_path):
    reg = build_default_registry(cwd=".", kb=_StubRootKb(tmp_path), agent_id="kb_assistant")
    assert reg.get("prepare_kb_write") is not None
    assert reg.get("prepare_kb_write").kb_root == tmp_path


# —— recall_session_history：默认上下文不含历史，智能体按需读回落盘 jsonl ——

def _store_with(tmp_path, rows):
    """建一个有真实会话正文的 SessionStore：只 append 的行会被 messages() 读回。"""
    store = SessionStore(tmp_path / "hist", agent_id="kb_assistant")
    sid = "sess_aaaaaaaa"
    store.create(sid, "kb_assistant", "proj", rows[0][1] if rows else "hi")
    for role, content in rows:
        store.append(sid, role, content)
    return store, sid


def test_recall_without_store_or_sid_degrades():
    """无存储或临时键：给模型一句可读降级，不抛。"""
    assert "No persisted" in RecallSessionHistoryTool()._run()
    assert "No persisted" in RecallSessionHistoryTool(store=None, session_id="")._run()


def test_recall_reads_back_persisted_history(tmp_path):
    store, sid = _store_with(tmp_path, [
        ("user", "导出 P0 用例清单"),
        ("assistant", "已导出到 test 目录"),
        ("user", "再查一下热词"),
        ("assistant", "热词命中 3 条"),
    ])
    out = RecallSessionHistoryTool(store=store, session_id=sid)._run()
    assert "user: 导出 P0 用例清单" in out
    assert "assistant: 热词命中 3 条" in out


def test_recall_filters_by_query(tmp_path):
    store, sid = _store_with(tmp_path, [
        ("user", "热词是什么"),
        ("assistant", "回答A"),
        ("user", "另外一个话题"),
    ])
    out = RecallSessionHistoryTool(store=store, session_id=sid)._run(query="热词")
    assert "热词是什么" in out
    assert "另外一个话题" not in out
    assert "回答A" not in out


def test_recall_limit_truncates_to_most_recent(tmp_path):
    rows = [("user" if i % 2 == 0 else "assistant", f"消息{i}") for i in range(10)]
    store, sid = _store_with(tmp_path, rows)
    out = RecallSessionHistoryTool(store=store, session_id=sid)._run(limit=2)
    lines = [line for line in out.splitlines() if line]
    assert lines == ["user: 消息8", "assistant: 消息9"]


def test_recall_empty_session_reports_no_match(tmp_path):
    store, sid = _store_with(tmp_path, [("user", "你好"), ("assistant", "在")])
    out = RecallSessionHistoryTool(store=store, session_id=sid)._run(query="不存在的关键词")
    assert "no matching" in out


# —— KB 维护护栏：路径只允许 knowledge/ 子树内的一个具体文件（Blocker1 / High3） ——

def test_guard_normalizes_both_path_spellings():
    assert guard_knowledge_file_path("knowledge/business/wiki/a.md") == "knowledge/business/wiki/a.md"
    # 检索结果展示口径 .AiTester/knowledge/... 必须归一到 job 口径 knowledge/...
    assert guard_knowledge_file_path(".AiTester/knowledge/business/wiki/a.md") == "knowledge/business/wiki/a.md"
    assert guard_knowledge_file_path("\\knowledge\\business\\wiki\\a.md") == "knowledge/business/wiki/a.md"
    assert guard_knowledge_file_path("./knowledge/business/wiki/a.md") == "knowledge/business/wiki/a.md"


@pytest.mark.parametrize("bad", [
    "knowledge",                       # 整根
    "knowledge/business",              # 顶层桶
    "knowledge/business/wiki",         # 发布桶目录（无扩展名）
    "knowledge/business/wiki/",        # 尾斜杠：叶子空
])
def test_guard_blocks_root_bucket_and_folder(bad):
    with pytest.raises(ToolException):
        guard_knowledge_file_path(bad)


@pytest.mark.parametrize("bad", [
    "", "   ",                                   # 空
    "other/a.md",                                # 不在 knowledge/ 子树
    "knowledge/../../etc/passwd",                # .. 越界
    "/absolute/knowledge/a.md",                  # 绝对路径剥前导 / 后仍要求 knowledge 打头
    "knowledge/business/wiki",                   # 目录（无扩展名）
])
def test_guard_rejects_traversal_and_outside(bad):
    with pytest.raises(ToolException):
        guard_knowledge_file_path(bad)


def test_filter_frontmatter_keys_strips_dedups_and_drops_protected():
    assert filter_frontmatter_keys([" tags ", "owner", "tags", ""]) == ["tags", "owner"]
    # 结构性 key 被剔除；只剩受保护 key 时清洗后为空即拒
    with pytest.raises(ToolException):
        filter_frontmatter_keys(["name", "id", "type", "path", "source"])
    with pytest.raises(ToolException):
        filter_frontmatter_keys([])


# —— 三个维护草案工具：零写盘、按 kind 出草案、护栏/参数校验前置 ——

class _NoJobKb(_StubRootKb):
    def run_job_sync(self, name, **kwargs):  # pragma: no cover
        raise AssertionError("维护草案工具不应触发任何 job")


def test_prepare_delete_zero_write(tmp_path):
    out, art = PrepareKbDeleteTool()._run(path="knowledge/business/wiki/a.md", summary="删掉")
    assert "NOT yet deleted" in out
    assert art["kind"] == "delete" and art["path"] == "knowledge/business/wiki/a.md"
    assert art["summary"] == "删掉"


def test_prepare_delete_blocks_folder(tmp_path):
    with pytest.raises(ToolException):
        PrepareKbDeleteTool()._run(path="knowledge/business/wiki")


def test_prepare_edit_zero_write_and_requires_old(tmp_path):
    out, art = PrepareKbEditTool()._run(
        path="knowledge/business/wiki/a.md", old="foo", new="bar", summary="改")
    assert "NOT yet written" in out
    assert art["kind"] == "edit" and art["old"] == "foo" and art["new"] == "bar"
    # new='' 合法（删命中文本）；old 空即拒
    _, art2 = PrepareKbEditTool()._run(path="knowledge/business/wiki/a.md", old="x", new="")
    assert art2["new"] == ""
    with pytest.raises(ToolException, match="old"):
        PrepareKbEditTool()._run(path="knowledge/business/wiki/a.md", old="")


def test_prepare_frontmatter_zero_write_and_protected_key(tmp_path):
    out, art = PrepareKbFrontmatterTool()._run(
        path="knowledge/business/wiki/a.md", keys=["tags", "owner"])
    assert "NOT yet written" in out
    assert art["kind"] == "frontmatter" and art["keys"] == ["tags", "owner"]
    with pytest.raises(ToolException):
        PrepareKbFrontmatterTool()._run(path="knowledge/business/wiki/a.md", keys=["name"])


@pytest.mark.parametrize("art", [
    PrepareKbDeleteTool()._run(path="knowledge/business/wiki/a.md")[1],
    PrepareKbEditTool()._run(path="knowledge/business/wiki/a.md", old="x", new="y")[1],
    PrepareKbFrontmatterTool()._run(path="knowledge/business/wiki/a.md", keys=["tags"])[1],
])
def test_maintenance_draft_conforms_to_kb_draft_contract(art):
    # 草案经路由 _draft_frame 严格校验：维护类缺 title/content/bucket，靠 KbDraft 默认值兜住
    d = KbDraft.model_validate(art)
    assert d.kind == art["kind"]
    assert d.title == "" and d.content == ""


def test_registry_registers_prepare_maintenance_tools(tmp_path):
    reg = build_default_registry(cwd=".", kb=_NoJobKb(tmp_path), agent_id="kb_assistant")
    for tid in ("prepare_kb_delete", "prepare_kb_edit", "prepare_kb_frontmatter"):
        assert reg.get(tid) is not None
    # 旧直连落盘工具名彻底消失（结构上收回模型手里的写盘能力）
    assert reg.get("delete_kb_file") is None
    assert reg.get("edit_kb_file") is None
    assert reg.get("delete_kb_frontmatter") is None
