from pathlib import Path
from types import SimpleNamespace

from aitester.adapters.tools import build_default_registry
from aitester.services.kb.manager import RemeKbManager


class _FakeKb:
    def __init__(self, root: Path):
        self.calls = []
        self.kb_root_dir = root  # prepare_kb_write 注册时读取实体根

    def run_job_sync(self, name, *, project_id="default", agent_id="console", timeout=60.0, **kwargs):
        self.calls.append((name, agent_id, kwargs))
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
    reg = build_default_registry(cwd=".", kb=kb, agent_id="case_design")
    out = reg.get("knowledge_search").invoke({"query": "热词", "limit": 2})
    assert "测试节点" in out
    assert kb.calls == [("knowledge_search", "case_design", {"query": "热词", "limit": 2, "bucket": "all"})]

    out2 = reg.get("save_to_knowledge").invoke({"title": "t", "content": "c"})
    assert out2
    assert kb.calls[1] == ("save_to_knowledge", "case_design", {"title": "t", "content": "c", "bucket": "business/wiki"})


def test_kb_disabled_manager_registers_no_tools(tmp_path):
    """kb_enabled=False：manager 实例存在也必须对模型隐藏 KB 工具面。"""
    disabled = RemeKbManager(settings=_manager_settings(tmp_path, False), data_dir=tmp_path)
    reg = build_default_registry(cwd=".", kb=disabled)
    assert reg.get("knowledge_search") is None
    assert reg.get("save_to_knowledge") is None


def test_kb_enabled_manager_registers_tools(tmp_path):
    enabled = RemeKbManager(settings=_manager_settings(tmp_path, True), data_dir=tmp_path)
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


import pytest
from langchain_core.tools import ToolException

from aitester.adapters.tools.kb_tools import PrepareKbWriteTool


class _StubRootKb:
    is_enabled = True

    def __init__(self, root: Path):
        self.kb_root_dir = root

    def run_job_sync(self, name, **kwargs):  # pragma: no cover
        raise AssertionError("prepare_kb_write 不应触发任何 job")


def _tool(tmp_path):
    (tmp_path / "business" / "wiki").mkdir(parents=True)
    (tmp_path / "business" / "wiki" / "x.md").write_text("old", encoding="utf-8")
    return PrepareKbWriteTool(kb_root=tmp_path)


def test_prepare_modify_draft_zero_write(tmp_path):
    tool = _tool(tmp_path)
    x = tmp_path / "business" / "wiki" / "x.md"
    out, art = tool._run(op="modify", path="business/wiki/x.md", content="new", summary="改一句")
    assert x.read_text(encoding="utf-8") == "old"  # 零写盘
    assert art["op"] == "modify" and art["base"] == "old"
    assert art["mtime"] == int(x.stat().st_mtime_ns // 1_000_000)
    assert art["abs_display"] == str(x.resolve()) and art["content"] == "new"
    assert "confirm" in out.lower()  # 模型侧英文确认句


def test_prepare_create_draft(tmp_path):
    tool = _tool(tmp_path)
    (tmp_path / "_inbox").mkdir()  # create 要求父目录存在（fixture 只建了 business/wiki）
    out, art = tool._run(op="create", path="_inbox/n.md", content="# N", summary="新建")
    assert art["base"] is None and art["mtime"] == 0 and art["op"] == "create"
    assert not (tmp_path / "_inbox" / "n.md").exists()  # 零写盘
    assert "NOT yet written" in out


def test_prepare_validation_errors(tmp_path):
    tool = _tool(tmp_path)
    with pytest.raises(ToolException):
        tool._run(op="modify", path="../escape.md", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="modify", path="business/wiki/x.txt", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="create", path="business/wiki/x.md", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="modify", path="ghost.md", content="c", summary="s")
    with pytest.raises(ToolException):
        tool._run(op="create", path="ghostdir/x.md", content="c", summary="s")


def test_prepare_rejects_hidden_segments(tmp_path):
    # 终审项 2：与 browse 读侧同判据——草案期即拒（模型可读英文文案自纠），
    # 杜绝「工具放行→用户确认→browse 403→卡片死路」
    tool = _tool(tmp_path)
    for p in (".scratch/x.md", "business/.git/x.md", "__pycache__/x.md",
              "business/.GIT/x.md", "business/wiki/.Hidden.md"):
        with pytest.raises(ToolException, match="hidden or internal"):
            tool._run(op="create", path=p, content="c", summary="s")
    with pytest.raises(ToolException, match="hidden or internal"):
        tool._run(op="modify", path="business/.locks/x.md", content="c", summary="s")


def test_draft_artifact_conforms_to_kb_draft_contract(tmp_path):
    # 终审项 5：用真实工具产物校验契约（含 summary/abs_display 逐字段钉死），不手抄 dict
    from aitester.interaction.schemas import KbDraft

    tool = _tool(tmp_path)
    (tmp_path / "_inbox").mkdir()
    out_modify, art_modify = tool._run(
        op="modify", path="business/wiki/x.md", content="new", summary="改一句")
    out_create, art_create = tool._run(
        op="create", path="_inbox/n.md", content="# N", summary="新建")

    d1 = KbDraft.model_validate(art_modify)
    assert d1.op == "modify" and d1.path == "business/wiki/x.md"
    assert d1.abs_display == art_modify["abs_display"] and d1.summary == "改一句"
    assert d1.content == "new" and d1.base == "old" and d1.mtime > 0

    d2 = KbDraft.model_validate(art_create)
    assert d2.op == "create" and d2.path == "_inbox/n.md"
    assert d2.base is None and d2.mtime == 0 and d2.summary == "新建"
    assert "NOT yet written" in out_modify and "NOT yet written" in out_create


def test_registry_registers_prepare_kb_write(tmp_path):
    reg = build_default_registry(cwd=".", kb=_StubRootKb(tmp_path), agent_id="kb_assistant")
    assert reg.get("prepare_kb_write") is not None
    assert reg.get("prepare_kb_write").kb_root == tmp_path
