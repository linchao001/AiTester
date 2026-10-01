"""read 工具：读取 UTF-8 文本文件，返回带行号的内容窗口（对齐 dsh-tool-fs/read.ts）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from aitester.adapters.tools.file_tools.errors import (
    raise_blank_path,
    raise_invalid_limit,
    raise_invalid_offset,
    raise_limit_over_cap,
    raise_offset_out_of_range,
    raise_read_failed,
    raise_read_not_found,
    raise_read_not_regular,
    raise_read_not_text,
)
from aitester.adapters.tools.file_tools.fs_tool import FsTool

READ_LIMIT = 2000
READ_MAX_LINE_LENGTH = 2000
READ_MAX_BYTES = 50 * 1024


@dataclass
class ReadWindow:
    lines: list[dict[str, Any]]
    total_lines: int
    truncated_by_bytes: bool


def _scan_file(target: Path, offset: int, limit: int) -> ReadWindow:
    """逐行扫描全文（流式，内存有界），返回行窗口并保持精确总行数。"""
    lines: list[dict[str, Any]] = []
    total = 0
    output_bytes = 0
    truncated = False
    with target.open("r", encoding="utf-8") as fh:
        for raw in fh:
            if raw.endswith("\n"):
                raw = raw[:-1]
            if raw.endswith("\r"):
                raw = raw[:-1]
            total += 1
            if truncated or total < offset or len(lines) >= limit:
                continue
            text = (
                raw
                if len(raw) <= READ_MAX_LINE_LENGTH
                else f"{raw[:READ_MAX_LINE_LENGTH]}... (line truncated to {READ_MAX_LINE_LENGTH} chars)"
            )
            size = len(text.encode("utf-8")) + (1 if lines else 0)
            if output_bytes + size > READ_MAX_BYTES:
                truncated = True
                continue
            output_bytes += size
            lines.append({"number": total, "text": text})
    return ReadWindow(lines=lines, total_lines=total, truncated_by_bytes=truncated)


def format_read_output(display_path: str, offset: int, window: ReadWindow) -> str:
    end_line = window.lines[-1]["number"] if window.lines else max(0, offset - 1)
    if window.truncated_by_bytes:
        footer = (
            f"(Output capped. Showing lines {offset}-{end_line}. "
            f"Use offset={end_line + 1} to continue.)"
        )
    elif end_line < window.total_lines:
        footer = (
            f"(Showing lines {offset}-{end_line} of {window.total_lines}. "
            f"Use offset={end_line + 1} to continue.)"
        )
    else:
        footer = f"(End of file - total {window.total_lines} lines)"
    if window.lines:
        body = "\n".join(f"{line['number']}: {line['text']}" for line in window.lines)
        body = f"{body}\n\n{footer}"
    else:
        body = footer
    return f"<path>{display_path}</path>\n<type>file</type>\n<content>\n{body}\n</content>"


class ReadInput(BaseModel):
    file_path: str = Field(
        description="Path to the file to read (absolute, or relative to the working directory)."
    )
    offset: int = Field(default=1, description="1-based first line to return. Defaults to 1.")
    limit: int = Field(
        default=READ_LIMIT,
        description=f"Maximum number of lines to return. Defaults to {READ_LIMIT}, the maximum allowed.",
    )


class ReadTool(FsTool):
    name: str = "read"
    description: str = (
        "Read a UTF-8 text file and return line-numbered content. "
        "Use offset/limit to continue through large files; each window's footer tells you the next offset."
    )
    args_schema: type[BaseModel] = ReadInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(
        self, file_path: str, offset: int = 1, limit: int = READ_LIMIT
    ) -> tuple[str, dict[str, Any]]:
        if not file_path.strip():
            raise_blank_path()
        if not isinstance(offset, int) or offset < 1:
            raise_invalid_offset()
        if not isinstance(limit, int) or limit < 1:
            raise_invalid_limit()
        if limit > READ_LIMIT:
            raise_limit_over_cap(READ_LIMIT)

        target = self._resolve(file_path)
        display = str(target)
        if not target.exists():
            raise_read_not_found(display)
        if target.is_dir():
            raise_read_not_regular(display)

        try:
            window = _scan_file(target, offset, limit)
        except UnicodeDecodeError:
            raise_read_not_text(display)
        except OSError as exc:
            raise_read_failed(display, exc)

        if (
            not window.truncated_by_bytes
            and offset > window.total_lines
            and not (window.total_lines == 0 and offset == 1)
        ):
            raise_offset_out_of_range(offset, display, window.total_lines)

        self._mark_observed(target)
        artifact = {
            "path": display,
            "offset": offset,
            "lines": window.lines,
            "totalLines": window.total_lines,
        }
        return format_read_output(display, offset, window), artifact
