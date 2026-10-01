"""edit 工具：按字面量精确替换编辑已存在的文件（对齐 dsh-tool-fs/edit.ts）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from aitester.adapters.tools.file_tools.diff import compute_hunk_diffs
from aitester.adapters.tools.file_tools.errors import (
    raise_blank_path,
    raise_edit_blank_old,
    raise_edit_equal_pair,
    raise_edit_no_match,
    raise_edit_not_unique,
    raise_missing_target,
    raise_read_not_text,
    raise_write_failed,
)
from aitester.adapters.tools.file_tools.fs_tool import FsTool


def format_edit_output(display_path: str, replace_all: bool) -> str:
    if replace_all:
        return f"The file {display_path} has been updated. All occurrences were successfully replaced."
    return f"The file {display_path} has been updated successfully."


class EditInput(BaseModel):
    file_path: str = Field(
        description="Path to edit (absolute, or relative to the working directory)."
    )
    old_string: str = Field(description="Literal text to replace.")
    new_string: str = Field(
        description="Literal replacement text. Use an empty string to delete the match."
    )
    replace_all: bool = Field(
        default=False,
        description="Replace all matches. Defaults to false; when false, old_string must appear exactly once.",
    )


class EditTool(FsTool):
    name: str = "edit"
    description: str = (
        "Edit an existing UTF-8 text file by replacing literal text. "
        "old_string must appear exactly once unless replace_all is true. "
        "Read the file before editing."
    )
    args_schema: type[BaseModel] = EditInput
    response_format: Literal["content", "content_and_artifact"] = "content_and_artifact"

    def _run(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if not file_path.strip():
            raise_blank_path()
        if not old_string:
            raise_edit_blank_old()
        if old_string == new_string:
            raise_edit_equal_pair()

        target = self._resolve(file_path)
        display = str(target)
        if not target.exists():
            raise_missing_target(display)
        try:
            before = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise_read_not_text(display)
        except OSError as exc:
            raise_write_failed(display, exc)

        self._guard_mutation(target)

        count = before.count(old_string)
        if count == 0:
            raise_edit_no_match(display)
        if count > 1 and not replace_all:
            raise_edit_not_unique(count, display)

        after = (
            before.replace(old_string, new_string)
            if replace_all
            else before.replace(old_string, new_string, 1)
        )
        try:
            target.write_text(after, encoding="utf-8")
        except OSError as exc:
            raise_write_failed(display, exc)

        self._mark_observed(target)
        artifact = {"diffs": compute_hunk_diffs(display, before, after)}
        return format_edit_output(display, replace_all), artifact
