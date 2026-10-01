"""模型侧稳定诊断：文案与补救指引对齐 dsh-tool-fs（error.ts 与各工具内联错误）。

模型可见文案一律英文（对齐业界主流工具定义），面向用户的 UI 文案不在此列。
"""

from typing import NoReturn

from langchain_core.tools import ToolException


def raise_blank_path() -> NoReturn:
    raise ToolException("file_path must be a non-empty string")


def raise_read_not_found(display_path: str) -> NoReturn:
    raise ToolException(f'cannot read "{display_path}": not found')


def raise_read_not_regular(display_path: str) -> NoReturn:
    raise ToolException(f'cannot read "{display_path}": not a regular file')


def raise_read_not_text(display_path: str) -> NoReturn:
    raise ToolException(f'cannot read "{display_path}": not a UTF-8 text file')


def raise_read_failed(display_path: str, reason: object) -> NoReturn:
    raise ToolException(f'cannot read "{display_path}": {reason}')


def raise_invalid_offset() -> NoReturn:
    raise ToolException("offset must be a positive integer")


def raise_invalid_limit() -> NoReturn:
    raise ToolException("limit must be a positive integer")


def raise_limit_over_cap(cap: int) -> NoReturn:
    raise ToolException(f"limit must be less than or equal to {cap}")


def raise_offset_out_of_range(offset: int, display_path: str, total_lines: int) -> NoReturn:
    raise ToolException(
        f'offset {offset} is out of range for "{display_path}" ({total_lines} lines)'
    )


def raise_not_observed(display_path: str) -> NoReturn:
    raise ToolException(
        f'cannot modify "{display_path}": file has not been read — read the file, then retry'
    )


def raise_stale_version(display_path: str) -> NoReturn:
    raise ToolException(
        f'file "{display_path}" was modified since it was read — re-read the file, then retry'
    )


def raise_missing_target(display_path: str) -> NoReturn:
    raise ToolException(f'cannot modify "{display_path}": file not found')


def raise_write_is_dir(display_path: str) -> NoReturn:
    raise ToolException(f'cannot write "{display_path}": not a regular file')


def raise_write_failed(display_path: str, reason: object) -> NoReturn:
    raise ToolException(f'cannot write "{display_path}": {reason}')


def raise_edit_no_match(display_path: str) -> NoReturn:
    raise ToolException(f'old_string was not found in "{display_path}"')


def raise_edit_not_unique(count: int, display_path: str) -> NoReturn:
    raise ToolException(
        f'old_string matched {count} times in "{display_path}"; '
        "provide a more specific old_string or set replace_all to true"
    )


def raise_edit_blank_old() -> NoReturn:
    raise ToolException("old_string must be a non-empty string")


def raise_edit_equal_pair() -> NoReturn:
    raise ToolException("old_string and new_string must differ")
