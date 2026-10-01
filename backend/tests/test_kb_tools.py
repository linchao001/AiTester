from types import SimpleNamespace

from aitester.adapters.tools import build_default_registry
from aitester.services.kb.manager import RemeKbManager


class _FakeKb:
    def __init__(self):
        self.calls = []

    def run_job_sync(self, name, *, project_id="default", agent_id="console", timeout=60.0, **kwargs):
        self.calls.append((name, agent_id, kwargs))
        return SimpleNamespace(success=True, answer="命中：测试节点", metadata={})


class _TimeoutKb(_FakeKb):
    def run_job_sync(self, name, *, project_id="default", agent_id="console", timeout=60.0, **kwargs):
        raise TimeoutError()  # concurrent.futures Future.result(timeout) 的原生异常


def _manager_settings(tmp_path, kb_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(kb_enabled=kb_enabled, data_dir=str(tmp_path))


def test_registry_without_kb_has_no_kb_tools():
    reg = build_default_registry(cwd=".")
    assert reg.get("knowledge_search") is None


def test_kb_tools_run_against_manager():
    kb = _FakeKb()
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


def test_kb_tool_timeout_yields_non_empty_error():
    reg = build_default_registry(cwd=".", kb=_TimeoutKb())
    out = reg.get("knowledge_search").invoke({"query": "热词"})
    assert out.strip()
    assert "timed out" in out
    out2 = reg.get("save_to_knowledge").invoke({"title": "t", "content": "c"})
    assert out2.strip()
    assert "timed out" in out2
