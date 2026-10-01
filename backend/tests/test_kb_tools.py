from types import SimpleNamespace

from aitester.adapters.tools import build_default_registry


class _FakeKb:
    def __init__(self):
        self.calls = []

    def run_job_sync(self, name, *, project_id="default", agent_id="console", timeout=60.0, **kwargs):
        self.calls.append((name, agent_id, kwargs))
        return SimpleNamespace(success=True, answer="命中：测试节点", metadata={})


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
