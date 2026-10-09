from pathlib import Path

from aitester.services.project_config import ProjectService
from aitester.services.session_locator import SessionLocator
from aitester.services.session_store import open_session_store
from aitester.storage import FileJsonConfigRepository


def _projects(tmp_path: Path) -> tuple[ProjectService, str, Path]:
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "p.json"))
    root = tmp_path / "work"
    root.mkdir()
    pid = svc.create(name="t", desc="", dir_=str(root), agents=["case_design"])["id"]
    return svc, pid, root


def test_for_agent_writes_under_project_session_history(tmp_path) -> None:
    svc, pid, root = _projects(tmp_path)
    loc = SessionLocator(svc)
    store = loc.for_agent(pid, "case_design")
    sid = store.new_id()
    store.create(sid, "case_design", pid, "q")
    assert (root / "session_history" / "case_design" / "index.json").is_file()


def test_count_by_project_sums_sessions_across_agents(tmp_path) -> None:
    svc, pid, root = _projects(tmp_path)
    a = open_session_store(root, "case_design")
    b = open_session_store(root, "other_agent")
    for store, agent in ((a, "case_design"), (b, "other_agent")):
        sid = store.new_id()
        store.create(sid, agent, pid, "x")
    assert SessionLocator(svc).count_by_project(pid) == 2


def test_count_by_project_missing_history_is_zero(tmp_path) -> None:
    svc, pid, _root = _projects(tmp_path)
    assert SessionLocator(svc).count_by_project(pid) == 0


def test_for_agent_reuses_same_store_instance(tmp_path) -> None:
    svc, pid, _root = _projects(tmp_path)
    loc = SessionLocator(svc)
    assert loc.for_agent(pid, "case_design") is loc.for_agent(pid, "case_design")
