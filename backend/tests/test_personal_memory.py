import time
from types import SimpleNamespace

from aitester.memory.reme.messages import to_reme_session_id
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
        auto_memory_interval=5,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _drain(personal: PersonalMemory, timeout: float = 2.0) -> None:
    personal._task_queue.join()
    deadline = time.time() + timeout
    while time.time() < deadline and personal._task_queue.unfinished_tasks:
        time.sleep(0.01)


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
    assert kwargs["session_id"] == to_reme_session_id(
        "sess_abc", agent_id="a",
    )
    assert kwargs["session_id"] != to_reme_session_id(
        "sess_abc", agent_id="other",
    )
    assert len(kwargs["messages"]) == 2


def test_search_failure_soft_returns_empty():
    class Boom(FakeMgr):
        def run_job_sync(self, name, **kw):
            raise RuntimeError("boom")

    personal = PersonalMemory(Boom(), _settings(), FakeModels())
    assert personal.auto_search_for_turn(
        project_id="p", agent_id="a", query="q",
    ) == ""


def test_note_user_turn_respects_interval():
    mgr = FakeMgr()
    personal = PersonalMemory(mgr, _settings(auto_memory_interval=5), FakeModels())
    for i in range(4):
        personal.note_user_turn(
            project_id="p",
            agent_id="a",
            session_id="sess_x",
            turn_marker=f"m{i}",
            messages=[
                {"role": "user", "content": f"u{i}"},
                {"role": "assistant", "content": f"a{i}"},
            ],
        )
    assert mgr.calls == []
    personal.note_user_turn(
        project_id="p",
        agent_id="a",
        session_id="sess_x",
        turn_marker="m4",
        messages=[
            {"role": "user", "content": "u4"},
            {"role": "assistant", "content": "a4"},
        ],
    )
    _drain(personal)
    auto_calls = [c for c in mgr.calls if c[0] == "auto_memory"]
    assert len(auto_calls) == 1
    assert len(auto_calls[0][1]["messages"]) == 10


def test_note_user_turn_interval_one_every_turn():
    mgr = FakeMgr()
    personal = PersonalMemory(mgr, _settings(auto_memory_interval=1), FakeModels())
    personal.note_user_turn(
        project_id="p",
        agent_id="a",
        session_id="sess_y",
        turn_marker="m0",
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ],
    )
    _drain(personal)
    assert any(c[0] == "auto_memory" for c in mgr.calls)


def test_interval_disabled_never_schedules():
    mgr = FakeMgr()
    personal = PersonalMemory(
        mgr, _settings(auto_memory_interval=0), FakeModels(),
    )
    personal.note_user_turn(
        project_id="p",
        agent_id="a",
        session_id="sess_z",
        turn_marker="m0",
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ],
    )
    time.sleep(0.05)
    assert mgr.calls == []


def test_flush_session_enqueues_pending():
    mgr = FakeMgr()
    personal = PersonalMemory(mgr, _settings(auto_memory_interval=5), FakeModels())
    personal.note_user_turn(
        project_id="p",
        agent_id="a",
        session_id="sess_del",
        turn_marker="m0",
        messages=[
            {"role": "user", "content": "keep"},
            {"role": "assistant", "content": "me"},
        ],
    )
    assert mgr.calls == []
    personal.flush_session("sess_del")
    _drain(personal)
    auto_calls = [c for c in mgr.calls if c[0] == "auto_memory"]
    assert len(auto_calls) == 1
    assert auto_calls[0][1]["messages"][0]["content"] == "keep"
