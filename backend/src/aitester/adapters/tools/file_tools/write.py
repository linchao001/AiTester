"""write 工具：创建或整体覆盖 UTF-8 文本文件（对齐 dsh-tool-fs/write.ts）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from aitester.adapters.tools.file_tools.diff import compute_hunk_diffs
from aitester.adapters.tools.file_tools.errors import (
    raise_blank_path,
    raise_read_not_text,
    raise_write_failed,
    raise_write_is_dir,
)
from aitester.adapters.tools.file_tools.fs_tool import FsTool


def format_write_output(display_path: str, operation: str) -> str:
    verb = "Created" if operation == "create" else "Updated"
    return f"<path>{display_path}</path>\n<type>file</type>\n<content>\n{verb} file\n</content>"


class WriteInput(BaseModel):
    file_path: str = Field(
        description="Path to write (absolute, or relative to the working directory)."
    )
    content: str = Field(description="Full UTF-8 text content to write.")


class WriteTool(FsTool):
    name: str = "write"
    description: str = (
        "Create or fully replace a UTF-8 text file (parent directories are created automatically). "
        "Read the file before overwriting an existing one; prefer edit for small changes."
    )
    args_schema: type[BaseModel] = WriteInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(self, file_path: str, content: str) -> tuple[str, dict[str, Any]]:
        if not file_path.strip():
            raise_blank_path()
        target = self._resolve(file_path)
        display = str(target)
        if target.is_dir():
            raise_write_is_dir(display)

        exists = target.exists()
        before = ""
        if exists:
            try:
                before = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise_read_not_text(display)
            except OSError as exc:
                raise_write_failed(display, exc)
            self._guard_mutation(target)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise_write_failed(display, exc)

        self._mark_observed(target)
        operation = "update" if exists else "create"
        diffs = compute_hunk_diffs(display, before, content) if exists else []
        artifact = {"operation": operation, "diffs": diffs}
        return format_write_output(display, operation), artifact
