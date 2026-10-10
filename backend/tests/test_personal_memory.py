from types import SimpleNamespace

from aitester.memory.reme.personal import PersonalMemory


class FakeMgr:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.injected: list = []

    def run_job_sync(self, name, **kw):
        self.calls.append((name, kw))
        return SimpleNamespace(success=True, answer="hit text", metadata={})

    def inject_llm(self, model, **kw):
        self.injected.append((model, kw))


class FakeModels:
    default_uid = "deepseek/deepseek-flash"

    def build_provider(self, uid: str):
        return SimpleNamespace(uid=uid, invoke_messages=lambda m: None, bind_tools=lambda t: self)


def _settings(**kw):
    base = dict(
        personal_memory_enabled=True,
        auto_memory_search_enabled=True,
        auto_memory_enabled=True,
        auto_memory_search_limit=5,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_auto_search_respects_flag():
    mgr = FakeMgr()
    personal = PersonalMemory(mgr, _settings(auto_memory_search_enabled=False), FakeModels())
    assert personal.auto_search_for_turn(
        project_id="p", agent_id="a", query="q",
    ) == ""
    assert mgr.calls == []


def test_auto_search_calls_search_job():
    mgr = FakeMgr()
    personal = PersonalMemory(mgr, _settings(), FakeModels())
    assert personal.auto_search_for_turn(
        project_id="p1", agent_id="case_design", query="订单",
    ) == "hit text"
    assert mgr.calls[0][0] == "search"
    assert mgr.calls[0][1]["query"] == "订单"
    assert mgr.calls[0][1]["project_id"] == "p1"


def test_auto_memory_injects_provider_and_hashes_session():
    mgr = FakeMgr()
    personal = PersonalMemory(mgr, _settings(), FakeModels())
    personal.auto_memory(
        project_id="p",
        agent_id="a",
        session_id="sess_abc",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ],
    )
    assert len(mgr.injected) == 1
    name, kwargs = mgr.calls[-1]
    assert name == "auto_memory"
    assert kwargs["session_id"].startswith("aitsid_sha256_")
    assert len(kwargs["messages"]) == 2


def test_search_failure_soft_returns_empty():
    class Boom(FakeMgr):
        def run_job_sync(self, name, **kw):
            raise RuntimeError("boom")

    personal = PersonalMemory(Boom(), _settings(), FakeModels())
    assert personal.auto_search_for_turn(
        project_id="p", agent_id="a", query="q",
    ) == ""
