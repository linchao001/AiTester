"""结构化 hunk 差异：对齐 dsh-tool-fs/diff.ts（3 行上下文、纯插入 oldText 为 null）。"""

import difflib

DIFF_CONTEXT = 3


def compute_hunk_diffs(path: str, before: str, after: str) -> list[dict[str, object]]:
    """计算 before → after 的逐 hunk 差异；文本相同返回空列表。"""
    matcher = difflib.SequenceMatcher(
        None, before.split("\n"), after.split("\n"), autojunk=False
    )
    diffs: list[dict[str, object]] = []
    for group in matcher.get_grouped_opcodes(DIFF_CONTEXT):
        old_lines: list[str] = []
        new_lines: list[str] = []
        for _tag, i1, i2, j1, j2 in group:
            old_lines.extend(matcher.a[i1:i2])
            new_lines.extend(matcher.b[j1:j2])
        diffs.append(
            {
                "path": path,
                "oldText": "\n".join(old_lines) if old_lines else None,
                "newText": "\n".join(new_lines),
            }
        )
    return diffs
