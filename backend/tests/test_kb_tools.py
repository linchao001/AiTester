from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.kb_tools import PrepareKbWriteTool
from aitester.interaction.schemas import KbDraft
from aitester.memory.reme.manager import RemeMemoryManager


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
