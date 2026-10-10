"""项目运行时目录 .AiTester 收口与 Reme 路径改写。"""

from pathlib import Path

from aitester.project_runtime import (
    PROJECT_RUNTIME_DIRNAME,
    design_root,
    project_runtime_root,
    reme_paths_for_project_cwd,
    session_history_root,
)


def test_runtime_roots(tmp_path: Path):
    assert project_runtime_root(tmp_path) == (tmp_path / ".AiTester").resolve()
    assert session_history_root(tmp_path) == (
        tmp_path / ".AiTester" / "session_history"
    ).resolve()
    assert design_root(tmp_path) == tmp_path / ".AiTester" / "design"
    assert PROJECT_RUNTIME_DIRNAME == ".AiTester"


def test_reme_paths_for_project_cwd_prefixes_and_idempotent():
    raw = "命中 knowledge/business/wiki/a.md 与 daily/2026-10-10.md"
    got = reme_paths_for_project_cwd(raw)
    assert ".AiTester/knowledge/business/wiki/a.md" in got
    assert ".AiTester/daily/2026-10-10.md" in got
    assert reme_paths_for_project_cwd(got) == got
    assert reme_paths_for_project_cwd("") == ""
