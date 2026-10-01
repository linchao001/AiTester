"""文件工具行为测试：对齐 dsh-tool-fs 的窗口、上限、守卫与输出契约。"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools.observation import FileObservationStore
from aitester.adapters.tools.file_tools.read import READ_LIMIT


@pytest.fixture
def registry(tmp_path: Path):
    return build_default_registry(
        cwd=str(tmp_path), session_id="s1", observed=FileObservationStore()
    )


def _call(tool, args: dict, call_id: str = "call_1") -> ToolMessage:
    result = tool.invoke(
        {"name": tool.name, "args": args, "id": call_id, "type": "tool_call"}
    )
    assert isinstance(result, ToolMessage)
    return result


# ---------- read ----------


def test_read_envelope_with_line_numbers(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    msg = _call(registry.get("read"), {"file_path": "a.txt"})
    assert "<path>" in msg.content and "<type>file</type>" in msg.content
    assert "1: alpha" in msg.content
    assert "3: gamma" in msg.content
    assert "(End of file - total 3 lines)" in msg.content
    assert msg.artifact["totalLines"] == 3
    assert msg.artifact["lines"][0] == {"number": 1, "text": "alpha"}


def test_read_paginates_with_offset_and_limit(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text(
        "\n".join(f"l{i}" for i in range(1, 11)), encoding="utf-8"
    )
    msg = _call(registry.get("read"), {"file_path": "a.txt", "offset": 3, "limit": 2})
    assert "3: l3" in msg.content and "4: l4" in msg.content
    assert "5: l5" not in msg.content
    assert "(Showing lines 3-4 of 10." in msg.content
    assert "offset=5" in msg.content
    assert msg.artifact["offset"] == 3
    assert [line["number"] for line in msg.artifact["lines"]] == [3, 4]


def test_read_empty_file_with_offset_one(registry, tmp_path: Path) -> None:
    (tmp_path / "e.txt").write_text("", encoding="utf-8")
    msg = _call(registry.get("read"), {"file_path": "e.txt"})
    assert "(End of file - total 0 lines)" in msg.content


def test_read_offset_out_of_range(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    with pytest.raises(ToolException, match="is out of range"):
        _call(registry.get("read"), {"file_path": "a.txt", "offset": 5})


def test_read_rejects_invalid_offset_and_limit(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    read = registry.get("read")
    with pytest.raises(ToolException, match="offset must be a positive integer"):
        _call(read, {"file_path": "a.txt", "offset": 0})
    with pytest.raises(ToolException, match="limit must be a positive integer"):
        _call(read, {"file_path": "a.txt", "limit": 0})
    with pytest.raises(ToolException, match=f"limit must be less than or equal to {READ_LIMIT}"):
        _call(read, {"file_path": "a.txt", "limit": READ_LIMIT + 1})


def test_read_truncates_overlong_line(registry, tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_text("x" * 3000, encoding="utf-8")
    msg = _call(registry.get("read"), {"file_path": "long.txt"})
    assert "line truncated to" in msg.content
    assert msg.artifact["totalLines"] == 1


def test_read_byte_cap_truncates_window(registry, tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("\n".join(["y" * 1000] * 100), encoding="utf-8")
    msg = _call(registry.get("read"), {"file_path": "big.txt"})
    assert "Output capped" in msg.content
    assert msg.artifact["totalLines"] == 100
    assert len(msg.artifact["lines"]) < 100


def test_read_missing_file_and_directory(registry, tmp_path: Path) -> None:
    with pytest.raises(ToolException, match="not found"):
        _call(registry.get("read"), {"file_path": "nope.txt"})
    (tmp_path / "d").mkdir()
    with pytest.raises(ToolException, match="not a regular file"):
        _call(registry.get("read"), {"file_path": "d"})


def test_read_non_utf8_file(registry, tmp_path: Path) -> None:
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ToolException, match="not a UTF-8 text file"):
        _call(registry.get("read"), {"file_path": "bin.dat"})


# ---------- write ----------


def test_write_creates_file_with_confirmation(registry, tmp_path: Path) -> None:
    msg = _call(
        registry.get("write"), {"file_path": "new/one.txt", "content": "hello"}
    )
    assert "Created file" in msg.content
    assert (tmp_path / "new" / "one.txt").read_text(encoding="utf-8") == "hello"
    assert msg.artifact["operation"] == "create"
    assert msg.artifact["diffs"] == []


def test_write_overwrite_requires_read_then_reports_diff(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    with pytest.raises(ToolException, match="has not been read"):
        _call(registry.get("write"), {"file_path": "a.txt", "content": "v2"})

    _call(registry.get("read"), {"file_path": "a.txt"})
    msg = _call(registry.get("write"), {"file_path": "a.txt", "content": "v2"})
    assert "Updated file" in msg.content
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v2"
    assert msg.artifact["operation"] == "update"
    assert msg.artifact["diffs"][0]["oldText"] == "v1"
    assert msg.artifact["diffs"][0]["newText"] == "v2"


def test_write_stale_version_requires_reread(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    _call(registry.get("read"), {"file_path": "a.txt"})
    (tmp_path / "a.txt").write_text("v2-external-change", encoding="utf-8")

    with pytest.raises(ToolException, match="was modified since it was read"):
        _call(registry.get("write"), {"file_path": "a.txt", "content": "v3"})

    _call(registry.get("read"), {"file_path": "a.txt"})
    msg = _call(registry.get("write"), {"file_path": "a.txt", "content": "v3"})
    assert "Updated file" in msg.content


def test_write_twice_without_reread_is_allowed(registry, tmp_path: Path) -> None:
    write = registry.get("write")
    _call(write, {"file_path": "n.txt", "content": "first"})
    msg = _call(write, {"file_path": "n.txt", "content": "second"})
    assert "Updated file" in msg.content


# ---------- edit ----------


def test_edit_unique_replacement(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    with pytest.raises(ToolException, match="has not been read"):
        _call(
            registry.get("edit"),
            {"file_path": "a.txt", "old_string": "world", "new_string": "WORLD"},
        )

    _call(registry.get("read"), {"file_path": "a.txt"})
    msg = _call(
        registry.get("edit"),
        {"file_path": "a.txt", "old_string": "world", "new_string": "WORLD"},
    )
    assert "has been updated successfully" in msg.content
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello WORLD"
    assert msg.artifact["diffs"][0]["newText"] == "hello WORLD"


def test_edit_not_unique_fails_then_replace_all(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("aaa\naaa\nbbb", encoding="utf-8")
    _call(registry.get("read"), {"file_path": "a.txt"})

    with pytest.raises(ToolException, match="matched 2 times"):
        _call(
            registry.get("edit"),
            {"file_path": "a.txt", "old_string": "aaa", "new_string": "ccc"},
        )

    msg = _call(
        registry.get("edit"),
        {
            "file_path": "a.txt",
            "old_string": "aaa",
            "new_string": "ccc",
            "replace_all": True,
        },
    )
    assert "All occurrences were successfully replaced" in msg.content
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "ccc\nccc\nbbb"


def test_edit_argument_validation(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("abc", encoding="utf-8")
    edit = registry.get("edit")
    with pytest.raises(ToolException, match="old_string must be a non-empty string"):
        _call(edit, {"file_path": "a.txt", "old_string": "", "new_string": "x"})
    with pytest.raises(ToolException, match="must differ"):
        _call(edit, {"file_path": "a.txt", "old_string": "abc", "new_string": "abc"})


def test_edit_missing_file(registry) -> None:
    with pytest.raises(ToolException, match="file not found"):
        _call(
            registry.get("edit"),
            {"file_path": "nope.txt", "old_string": "a", "new_string": "b"},
        )


def test_edit_no_match(registry, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("abc", encoding="utf-8")
    _call(registry.get("read"), {"file_path": "a.txt"})
    with pytest.raises(ToolException, match="was not found in"):
        _call(
            registry.get("edit"),
            {"file_path": "a.txt", "old_string": "zzz", "new_string": "y"},
        )


def test_observation_is_per_session(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
    store = FileObservationStore()

    s1 = build_default_registry(cwd=str(tmp_path), session_id="s1", observed=store)
    s2 = build_default_registry(cwd=str(tmp_path), session_id="s2", observed=store)

    _call(s1.get("read"), {"file_path": "a.txt"})
    with pytest.raises(ToolException, match="has not been read"):
        _call(s2.get("write"), {"file_path": "a.txt", "content": "v2"})
