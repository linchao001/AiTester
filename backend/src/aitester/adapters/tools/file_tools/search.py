"""文件检索工具：grep_search（内容检索）与 glob_search（按名查找文件）。

对齐 QwenPaw ``agents/tools/file_search.py``：跳过目录、大小与数量上限、
输出格式、诊断文案逐条对齐。差异仅在调度——本工程 agent loop 是同步的，
超时用进场时记下的截止时刻做协作式检查（QwenPaw 用 asyncio 线程 + cancel 事件），
语义映射：grep 超时且有命中 → 部分结果 + 超时提示（同其协作分支）；grep 超时且
无命中 → 超时错误文案；glob 超时 → 一律超时错误文案。

错误通道沿用本工程口径：入参类错误（空模式、路径不存在、非法正则）抛
``ToolException``；超时是运行期结果，与命令执行工具一致以文本返回而不中断 loop。
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from aitester.adapters.tools.file_tools.fs_tool import FsTool

BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
        ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".flac", ".wav",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".pyc", ".pyo", ".class", ".o", ".a",
    }
)

SKIP_DIRS = frozenset(
    {
        ".git", ".svn", ".hg", "node_modules", "__pycache__", ".tox", ".nox",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv", ".eggs",
        "dist", "build", ".next", ".nuxt",
    }
)

MAX_MATCHES = 200
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB
MAX_CONTEXT_LINES = 5
MAX_OUTPUT_CHARS = 50_000  # ~50 KB
MAX_FILES_SCANNED = 10_000
GREP_TIMEOUT_SECS = 30
GLOB_TIMEOUT_SECS = 15


def _is_text_file(path: Path) -> bool:
    """启发式过滤：已知二进制扩展名或超过 2 MB 的文件不进入检索。"""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return False
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return False
    except OSError:
        return False
    return True


def _relative_display(target: Path, root: Path) -> str:
    """相对路径展示，分隔符统一为正斜杠（Windows 也如此）。"""
    try:
        return str(target.relative_to(root)).replace(os.sep, "/")
    except ValueError:
        return str(target).replace(os.sep, "/")


def _compile_search_pattern(pattern: str, is_regex: bool, flags: int) -> re.Pattern[str]:
    """编译检索模式。

    非正则时按字面量处理；含 ``|`` 时按 ``grep -E`` 的 OR 语义逐段转义后拼接。
    """
    if is_regex:
        expr = pattern
    elif "|" in pattern:
        parts = [part for part in pattern.split("|") if part]
        if not parts:
            expr = re.escape(pattern)
        else:
            expr = "|".join(re.escape(part) for part in parts)
    else:
        expr = re.escape(pattern)
    return re.compile(expr, flags)


def _append_output_line(
    matches: list[str], total_chars: int, line: str
) -> tuple[bool, int]:
    """在命中数与输出体量上限内追加一行；放不下则返回 False。"""
    if len(matches) >= MAX_MATCHES:
        return False, total_chars
    projected_total = total_chars + len(line) + 1
    if projected_total > MAX_OUTPUT_CHARS:
        return False, total_chars
    matches.append(line)
    return True, projected_total


def _format_grep_line(
    disp_path: str, ln: int, content: str, is_hit: bool, *, show_file: bool
) -> str:
    prefix = ">" if is_hit else " "
    if show_file:
        return f"{disp_path}:{ln}:{prefix} {content}"
    return f"{ln}:{prefix} {content}"


def _emit_file_header_if_needed(
    disp_path: str,
    *,
    show_file: bool,
    single_file: bool,
    headers_emitted: set[str],
    matches: list[str],
    total_chars: int,
) -> tuple[bool, int]:
    """show_file=False 的多文件检索：按文件分组，组间以 ``---`` 分隔。"""
    if show_file or single_file or disp_path in headers_emitted:
        return True, total_chars

    if headers_emitted and not (matches and matches[-1] == "---"):
        success, total_chars = _append_output_line(matches, total_chars, "---")
        if not success:
            return False, total_chars

    success, total_chars = _append_output_line(matches, total_chars, disp_path)
    if not success:
        return False, total_chars

    headers_emitted.add(disp_path)
    return True, total_chars


def _emit_match_entries(
    entries: list[tuple[int, str, bool]],
    disp_path: str,
    matches: list[str],
    total_chars: int,
    *,
    show_file: bool,
    single_file: bool,
    headers_emitted: set[str],
) -> tuple[bool, int]:
    """把一批 ``(行号, 内容, 是否命中)`` 条目格式化后追加到输出。"""
    if not entries:
        return True, total_chars

    success, total_chars = _emit_file_header_if_needed(
        disp_path,
        show_file=show_file,
        single_file=single_file,
        headers_emitted=headers_emitted,
        matches=matches,
        total_chars=total_chars,
    )
    if not success:
        return False, total_chars

    for ln, content, is_hit in entries:
        entry = _format_grep_line(disp_path, ln, content, is_hit, show_file=show_file)
        success, total_chars = _append_output_line(matches, total_chars, entry)
        if not success:
            return False, total_chars

    return True, total_chars


def _output_context_for_hit(
    hit_line_no: int,
    line_buffer: deque[tuple[int, str]],
    display_path: str,
    context_lines: int,
    matches: list[str],
    total_chars: int,
    *,
    show_file: bool,
    single_file: bool,
    headers_emitted: set[str],
) -> tuple[bool, int]:
    """输出命中行前后各 context_lines 行，命中行前缀 ``>``，其余为空格。"""
    hit_idx = next(
        idx for idx, (line_no, _) in enumerate(line_buffer) if line_no == hit_line_no
    )

    slice_start = max(0, hit_idx - context_lines)
    slice_end = min(len(line_buffer), hit_idx + context_lines + 1)

    buffer_slice = list(line_buffer)[slice_start:slice_end]
    entries: list[tuple[int, str, bool]] = [
        (line_no, line_content, line_no == hit_line_no)
        for line_no, line_content in buffer_slice
    ]

    success, total_chars = _emit_match_entries(
        entries,
        display_path,
        matches,
        total_chars,
        show_file=show_file,
        single_file=single_file,
        headers_emitted=headers_emitted,
    )
    if not success:
        return False, total_chars

    if context_lines > 0:
        success, total_chars = _append_output_line(matches, total_chars, "---")
        if not success:
            return False, total_chars

    return True, total_chars


def _truncated_status(matches: list[str]) -> str:
    if len(matches) >= MAX_MATCHES:
        return f"truncated: match limit ({MAX_MATCHES})"
    return f"truncated: output size limit (~{MAX_OUTPUT_CHARS // 1000}KB)"


def _walk_and_grep(  # noqa: C901
    search_root: Path,
    regex: re.Pattern[str],
    context_lines: int,
    deadline: float,
    include_pattern: str | None,
    show_file: bool = True,
) -> tuple[list[str], str]:
    """检索单文件或整棵目录树，返回 ``(输出行, 状态)``。

    状态为 ``"ok"``、``"truncated:…"`` 或 ``"timeout"``；超时返回已收集的部分结果。
    """
    context_lines = min(max(context_lines, 0), MAX_CONTEXT_LINES)
    single_file = search_root.is_file()

    matches: list[str] = []
    total_chars = 0
    status = "ok"
    headers_emitted: set[str] = set()

    if single_file:
        file_iter: list[Path] = [search_root]
    else:
        file_iter = []
        for dirpath, dirnames, filenames in os.walk(search_root, followlinks=False):
            if time.monotonic() > deadline:
                status = "timeout"
                break
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fname in filenames:
                fp = Path(dirpath) / fname
                if not _is_text_file(fp):
                    continue
                if include_pattern and not fnmatch.fnmatch(fname, include_pattern):
                    continue
                file_iter.append(fp)
                if len(file_iter) >= MAX_FILES_SCANNED:
                    break
            if len(file_iter) >= MAX_FILES_SCANNED:
                break
        file_iter.sort()

    # 滑动窗口：context_lines 行前文 + 命中行 + context_lines 行后文
    window_size = context_lines * 2 + 1
    middle_idx = context_lines

    for file_path in file_iter:
        if time.monotonic() > deadline:
            status = "timeout"
            break

        display_path = (
            file_path.name if single_file else _relative_display(file_path, search_root)
        )

        sliding_window: deque[tuple[int, str]] = deque(maxlen=window_size)
        hit_indices: list[int] = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                line_no = 0
                for line in f:
                    if line_no % 1000 == 0 and time.monotonic() > deadline:
                        status = "timeout"
                        break

                    line_no = line_no + 1
                    line_content = line.rstrip("\n").rstrip("\r")
                    is_match = bool(regex.search(line_content))

                    sliding_window.append((line_no, line_content))
                    if is_match:
                        hit_indices.append(len(sliding_window) - 1)

                    if len(sliding_window) == window_size:
                        # 窗口左侧、已集齐后文的命中先输出
                        while hit_indices and hit_indices[0] < middle_idx:
                            hit_line = sliding_window[hit_indices.pop(0)][0]
                            success, total_chars = _output_context_for_hit(
                                hit_line,
                                sliding_window,
                                display_path,
                                context_lines,
                                matches,
                                total_chars,
                                show_file=show_file,
                                single_file=single_file,
                                headers_emitted=headers_emitted,
                            )
                            if not success:
                                status = _truncated_status(matches)
                                break

                        if status != "ok":
                            break
                        # 窗口正中的命中此时前后文都齐了
                        if hit_indices and hit_indices[0] == middle_idx:
                            hit_line = sliding_window[hit_indices.pop(0)][0]
                            success, total_chars = _output_context_for_hit(
                                hit_line,
                                sliding_window,
                                display_path,
                                context_lines,
                                matches,
                                total_chars,
                                show_file=show_file,
                                single_file=single_file,
                                headers_emitted=headers_emitted,
                            )
                            if not success:
                                status = _truncated_status(matches)
                                break
                        # 窗口前移一行，命中下标随之减一
                        sliding_window.popleft()
                        hit_indices = [i - 1 for i in hit_indices if i > 0]

                if status != "ok":
                    break

                # EOF 后窗口里残余的命中（文件头尾附近的）
                for hit_idx in hit_indices:
                    hit_line_no = sliding_window[hit_idx][0]
                    success, total_chars = _output_context_for_hit(
                        hit_line_no,
                        sliding_window,
                        display_path,
                        context_lines,
                        matches,
                        total_chars,
                        show_file=show_file,
                        single_file=single_file,
                        headers_emitted=headers_emitted,
                    )
                    if not success:
                        status = _truncated_status(matches)
                        break

        except OSError:
            continue

        if status != "ok":
            break

    return matches, status


def _walk_and_glob(
    search_root: Path, pattern: str, deadline: float
) -> tuple[list[str], bool, bool]:
    """遍历 ``search_root.glob(pattern)``，返回 ``(路径行, 是否截断, 是否超时)``。"""
    results: list[str] = []
    truncated = False
    timed_out = False

    try:
        for entry in search_root.glob(pattern):
            if time.monotonic() > deadline:
                timed_out = True
                break
            try:
                parts = entry.relative_to(search_root).parts
            except ValueError:
                parts = ()
            if any(p in SKIP_DIRS for p in parts):
                continue
            display_path = _relative_display(entry, search_root)
            suffix = "/" if entry.is_dir() else ""
            results.append(f"{display_path}{suffix}")
            if len(results) >= MAX_MATCHES:
                truncated = True
                break
    except NotImplementedError as exc:
        # Python 3.11 的 Path.glob 不支持绝对模式：给出可照做的诊断而不是裸异常
        raise ToolException(
            'Error: glob `pattern` must be relative to the search path (e.g. "**/*.json").'
        ) from exc
    except OSError:
        pass

    results.sort()
    return results, truncated, timed_out


def _format_grep_output(matches: list[str], status: str, pattern: str) -> str:
    if status.startswith("truncated:"):
        reason = status.split(":", 1)[1].strip()
        if matches:
            return (
                "\n".join(matches)
                + f"\n\n(Results truncated due to {reason}. "
                "Try narrowing the search path or using a more specific pattern.)"
            )
        return (
            f"Results truncated due to {reason}. "
            "Try narrowing the search path or using a more specific pattern."
        )
    if not matches:
        if status == "timeout":
            return (
                f"Error: Search timed out after {GREP_TIMEOUT_SECS}s. "
                "Try narrowing the search path or using a more specific pattern."
            )
        return f"No matches found for pattern: {pattern}"
    text = "\n".join(matches)
    if status == "timeout":
        text += (
            f"\n\n(Partial results — search timed out after {GREP_TIMEOUT_SECS}s. "
            "Try narrowing the search scope.)"
        )
    return text


def _format_glob_output(results: list[str], truncated: bool, pattern: str) -> str:
    if not results:
        return f"No files matched pattern: {pattern}"
    text = "\n".join(results)
    if truncated:
        text += f"\n\n(Results truncated at {MAX_MATCHES} entries.)"
    return text


class _SearchTool(FsTool):
    """检索工具公共上下文：路径缺省落在工作目录，glob 要求目录。"""

    def _resolve_search_root(self, path: str | None, *, require_dir: bool) -> Path:
        try:
            search_root = self._resolve(path) if path else self._resolve(".")
        except (OSError, ValueError) as exc:
            raise ToolException(
                f"Error: Cannot access path {path or '.'} — {exc}"
            ) from exc
        try:
            exists = search_root.exists()
        except OSError as exc:
            raise ToolException(
                f"Error: Cannot access path {search_root} — {exc}"
            ) from exc
        if not exists:
            raise ToolException(f"Error: The path {search_root} does not exist.")
        if require_dir and not search_root.is_dir():
            raise ToolException(f"Error: The path {search_root} is not a directory.")
        return search_root


class GrepSearchInput(BaseModel):
    pattern: str = Field(description="Search string (or regex when is_regex is true).")
    path: str | None = Field(
        default=None,
        description="File or directory to search in. Defaults to the working directory.",
    )
    is_regex: bool = Field(
        default=False,
        description="Treat pattern as a regular expression. Defaults to false.",
    )
    case_sensitive: bool = Field(
        default=True, description="Case-sensitive matching. Defaults to true."
    )
    context_lines: int = Field(
        default=0,
        description=(
            "Context lines before and after each match (like grep -C). "
            "Defaults to 0. Capped at 5."
        ),
    )
    include_pattern: str | None = Field(
        default=None,
        description=(
            "Only search files whose name matches this glob (e.g. \"*.py\"). "
            "Defaults to none (all text files)."
        ),
    )
    show_file: bool = Field(
        default=True,
        description=(
            "Include the file path on every output line. Defaults to true. When false, "
            "multi-file results group matches by file with the path shown once per file "
            "and --- between file groups."
        ),
    )


class GrepSearchTool(_SearchTool):
    name: str = "grep_search"
    description: str = (
        "Search file contents by pattern, recursively. Relative paths resolve from the "
        "working directory. Output format: path:line_number: content.\n\n"
        "When show_file is false, each match line omits the file path prefix "
        "(line_number:> content). For multi-file searches, the file path is printed once "
        "before that file's matches, with --- separating file groups."
    )
    args_schema: type[BaseModel] = GrepSearchInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(
        self,
        pattern: str,
        path: str | None = None,
        is_regex: bool = False,
        case_sensitive: bool = True,
        context_lines: int = 0,
        include_pattern: str | None = None,
        show_file: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        if not pattern:
            raise ToolException("Error: No search `pattern` provided.")
        search_root = self._resolve_search_root(path, require_dir=False)
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = _compile_search_pattern(pattern, is_regex, flags)
        except re.error as exc:
            raise ToolException(f"Error: Invalid regex pattern — {exc}") from exc

        deadline = time.monotonic() + GREP_TIMEOUT_SECS
        match_lines, status = _walk_and_grep(
            search_root, regex, context_lines, deadline, include_pattern, show_file
        )
        text = _format_grep_output(match_lines, status, pattern)
        artifact = {
            "pattern": pattern,
            "path": str(search_root),
            "matches": match_lines,
            "status": status,
        }
        return text, artifact


class GlobSearchInput(BaseModel):
    pattern: str = Field(
        description='Glob pattern to match (e.g. "*.py", "**/*.json").'
    )
    path: str | None = Field(
        default=None,
        description="Root directory to search from. Defaults to the working directory.",
    )


class GlobSearchTool(_SearchTool):
    name: str = "glob_search"
    description: str = (
        'Find files matching a glob pattern (e.g. "*.py", "**/*.json"). '
        "Relative paths resolve from the working directory."
    )
    args_schema: type[BaseModel] = GlobSearchInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(self, pattern: str, path: str | None = None) -> tuple[str, dict[str, Any]]:
        if not pattern:
            raise ToolException("Error: No glob `pattern` provided.")
        search_root = self._resolve_search_root(path, require_dir=True)

        deadline = time.monotonic() + GLOB_TIMEOUT_SECS
        results, truncated, timed_out = _walk_and_glob(search_root, pattern, deadline)
        status = "timeout" if timed_out else "ok"
        if timed_out:
            text = (
                f"Error: Glob search timed out after {GLOB_TIMEOUT_SECS}s. "
                "Try a more specific pattern or narrower search path."
            )
        else:
            text = _format_glob_output(results, truncated, pattern)
        artifact = {
            "pattern": pattern,
            "path": str(search_root),
            "files": results,
            "truncated": truncated,
            "status": status,
        }
        return text, artifact
