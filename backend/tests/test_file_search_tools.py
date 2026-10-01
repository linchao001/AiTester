"""文件检索工具测试：grep_search / glob_search 对齐 QwenPaw file_search.py 的契约。

覆盖输出格式（prefix/分组/上下文）、模式语义（字面量/管道 OR/正则/大小写）、
跳过规则（二进制扩展、超大文件、跳过目录）、上限与截断/超时文案。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools import search as search_module
from aitester.adapters.tools.file_tools.search import (
    MAX_FILE_SIZE,
    MAX_MATCHES,
    _format_grep_output,
)


@pytest.fixture
def registry(tmp_path: Path):
    return build_default_registry(cwd=str(tmp_path), session_id="s1")


def _call(tool, args: dict, call_id: str = "call_1") -> ToolMessage:
    result = tool.invoke(
        {"name": tool.name, "args": args, "id": call_id, "type": "tool_call"}
    )
    assert isinstance(result, ToolMessage)
    return result


def _grep(registry):
    return registry.get("grep_search")


def _glob(registry):
    return registry.get("glob_search")


# ---------------------------------------------------------------------------
# grep_search
# ---------------------------------------------------------------------------


def test_grep_literal_match_output_format(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("first\nneedle here\nlast\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "needle"})
    assert msg.content == "a.txt:2:> needle here"
    assert msg.artifact["pattern"] == "needle"
    assert msg.artifact["matches"] == ["a.txt:2:> needle here"]
    assert msg.artifact["status"] == "ok"


def test_grep_no_matches_message(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "zzz"})
    assert msg.content == "No matches found for pattern: zzz"


def test_grep_case_insensitive(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("Needle\n", encoding="utf-8")
    assert _call(_grep(registry), {"pattern": "needle"}).content == (
        "No matches found for pattern: needle"
    )
    msg = _call(_grep(registry), {"pattern": "needle", "case_sensitive": False})
    assert msg.content == "a.txt:1:> Needle"


def test_grep_pipe_alternatives_are_literal_or(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a+b\naxb\nzzz\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "a+b|zzz"})
    # 逐段转义：a+b 字面命中，axb 不命中（证明 + 没被当正则量词）
    assert msg.content == "a.txt:1:> a+b\na.txt:3:> zzz"


def test_grep_regex_mode(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("id=123\nid=ab\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": r"id=\d{3}", "is_regex": True})
    assert msg.content == "a.txt:1:> id=123"


def test_grep_invalid_regex_is_rejected(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ToolException, match="Invalid regex pattern"):
        _call(_grep(registry), {"pattern": "([", "is_regex": True})


def test_grep_blank_pattern_is_rejected(registry) -> None:
    with pytest.raises(ToolException, match="No search `pattern` provided"):
        _call(_grep(registry), {"pattern": ""})


def test_grep_context_lines_mark_hits_and_separate(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("l1\nl2\nHIT\nl4\nl5\nHIT2\nl7\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "^HIT", "is_regex": True, "context_lines": 1})
    assert msg.content == (
        "a.txt:2:  l2\n"
        "a.txt:3:> HIT\n"
        "a.txt:4:  l4\n"
        "---\n"
        "a.txt:5:  l5\n"
        "a.txt:6:> HIT2\n"
        "a.txt:7:  l7\n"
        "---"
    )


def test_grep_context_lines_capped_at_five(registry, tmp_path: Path) -> None:
    lines = [f"l{i}" for i in range(1, 15)]
    lines[7] = "HIT"
    (tmp_path / "a.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "HIT", "context_lines": 99})
    # 上限 5：命中行(cap 后第 8 行)前后各 5 行，去掉被夹掉的第 1、2 行
    assert msg.content.splitlines()[0] == "a.txt:3:  l3"
    assert msg.content.splitlines()[-2] == "a.txt:13:  l13"


def test_grep_include_pattern_filters_by_name(registry, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "needle", "include_pattern": "*.py"})
    assert msg.content == "a.py:1:> needle"


def test_grep_show_file_false_groups_by_file(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\nneedle\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\ntwo\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "needle", "show_file": False})
    assert msg.content == (
        "a.txt\n2:> needle\n---\nb.txt\n1:> needle"
    )


def test_grep_single_file_search_uses_bare_name(registry, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("needle\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "needle", "path": "sub/a.txt"})
    assert msg.content == "a.txt:1:> needle"


def test_grep_skips_binary_oversized_and_skip_dirs(registry, tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "pic.png").write_text("needle\n", encoding="utf-8")
    (tmp_path / "big.txt").write_text(
        "needle" + "x" * MAX_FILE_SIZE, encoding="utf-8"
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.txt").write_text("needle\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "needle"})
    assert msg.content == "ok.txt:1:> needle"


def test_grep_max_files_scanned_cap(registry, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(search_module, "MAX_FILES_SCANNED", 1)
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("needle\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "needle"})
    assert len({line.split(":", 1)[0] for line in msg.content.splitlines()}) == 1


def test_grep_match_limit_truncates_with_footer(registry, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(search_module, "MAX_MATCHES", 3)
    (tmp_path / "a.txt").write_text("hit\n" * 5, encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "hit"})
    assert msg.content.endswith(
        "(Results truncated due to match limit (3). "
        "Try narrowing the search path or using a more specific pattern.)"
    )
    assert msg.artifact["status"] == "truncated: match limit (3)"


def test_grep_output_size_limit_truncates_with_footer(
    registry, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(search_module, "MAX_OUTPUT_CHARS", 30)
    (tmp_path / "a.txt").write_text("hit\n" * 3, encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "hit"})
    assert msg.content.endswith(
        "(Results truncated due to output size limit (~0KB). "
        "Try narrowing the search path or using a more specific pattern.)"
    )


def test_grep_timeout_without_matches_is_error_text(registry, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(search_module, "time", _AdvancingClock())
    (tmp_path / "a.txt").write_text("hit\n", encoding="utf-8")
    msg = _call(_grep(registry), {"pattern": "hit"})
    assert msg.content == (
        "Error: Search timed out after 30s. "
        "Try narrowing the search path or using a more specific pattern."
    )
    assert msg.artifact["status"] == "timeout"


def test_grep_timeout_with_matches_returns_partial() -> None:
    text = _format_grep_output(["a.txt:1:> hit"], "timeout", "hit")
    assert text == (
        "a.txt:1:> hit\n\n"
        "(Partial results — search timed out after 30s. Try narrowing the search scope.)"
    )


def test_grep_path_errors(registry, tmp_path: Path) -> None:
    with pytest.raises(ToolException, match="does not exist"):
        _call(_grep(registry), {"pattern": "x", "path": "missing"})
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    # grep 允许把搜索根指向单个文件
    assert _call(_grep(registry), {"pattern": "x", "path": "a.txt"}).content == (
        "a.txt:1:> x"
    )


# ---------------------------------------------------------------------------
# glob_search
# ---------------------------------------------------------------------------


class _AdvancingClock:
    """每次读取前进 1 小时的假时钟：稳定触发协作式超时检查。"""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += 3600.0
        return self.now


def test_glob_matches_sorted_with_directory_suffix(registry, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("", encoding="utf-8")
    (tmp_path / "readme.md").write_text("", encoding="utf-8")
    msg = _call(_glob(registry), {"pattern": "**/*.py"})
    assert msg.content == "a.py\nb.py\nsub/c.py"
    assert msg.artifact["files"] == ["a.py", "b.py", "sub/c.py"]
    assert msg.artifact["truncated"] is False
    assert _call(_glob(registry), {"pattern": "sub*"}).content == "sub/"


def test_glob_skips_skip_dirs(registry, tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.py").write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hook.py").write_text("", encoding="utf-8")
    msg = _call(_glob(registry), {"pattern": "**/*.py"})
    assert msg.content == "ok.py"


def test_glob_no_matches_message(registry, tmp_path: Path) -> None:
    msg = _call(_glob(registry), {"pattern": "*.nope"})
    assert msg.content == "No files matched pattern: *.nope"


def test_glob_truncates_at_max_matches(registry, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(search_module, "MAX_MATCHES", 2)
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")
    msg = _call(_glob(registry), {"pattern": "*.txt"})
    assert msg.content == "a.txt\nb.txt\n\n(Results truncated at 2 entries.)"
    assert msg.artifact["truncated"] is True


def test_glob_blank_pattern_is_rejected(registry) -> None:
    with pytest.raises(ToolException, match="No glob `pattern` provided"):
        _call(_glob(registry), {"pattern": ""})


def test_glob_requires_directory(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    with pytest.raises(ToolException, match="is not a directory"):
        _call(_glob(registry), {"pattern": "*.txt", "path": "a.txt"})
    with pytest.raises(ToolException, match="does not exist"):
        _call(_glob(registry), {"pattern": "*.txt", "path": "missing"})


def test_glob_rejects_absolute_pattern_cleanly(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    with pytest.raises(ToolException, match="must be relative to the search path"):
        _call(_glob(registry), {"pattern": str(tmp_path / "*.txt")})


def test_glob_timeout_is_error_text(registry, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(search_module, "time", _AdvancingClock())
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    msg = _call(_glob(registry), {"pattern": "*.txt"})
    assert msg.content == (
        "Error: Glob search timed out after 15s. "
        "Try a more specific pattern or narrower search path."
    )
    assert msg.artifact["status"] == "timeout"


# ---------------------------------------------------------------------------
# 注册表与工具契约
# ---------------------------------------------------------------------------


def test_registry_registers_both_search_tools(registry) -> None:
    assert _grep(registry).name == "grep_search"
    assert _glob(registry).name == "glob_search"
    assert "Search file contents by pattern" in _grep(registry).description
    assert "Find files matching a glob pattern" in _glob(registry).description
    assert MAX_MATCHES == 200
