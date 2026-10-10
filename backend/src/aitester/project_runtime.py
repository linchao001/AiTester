"""项目运行时目录：AiTester 在绑定项目下的全部平台产物收口到 ``.AiTester/``。

智能体文件工具 cwd 仍为项目根；Reme workspace 相对路径经
``reme_paths_for_project_cwd`` 改写成相对项目根可读形式。
不自动迁移历史散落目录（session_history/、knowledge/ 等）。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_RUNTIME_DIRNAME = ".AiTester"

# Reme 在 workspace 根下创建的顶层名（检索/笔记返回时常以这些为相对前缀）
_REME_WORKSPACE_TOPS = (
    "knowledge",
    "daily",
    "digest",
    "metadata",
    "session",
    "mem_session",
    "resource",
)

_REME_REL_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_./\\-])("
    + "|".join(re.escape(n) for n in _REME_WORKSPACE_TOPS)
    + r")/"
)


def project_runtime_root(project_dir: str | Path) -> Path:
    """``{project.dir}/.AiTester``（已 expanduser + resolve）。"""
    return Path(project_dir).expanduser().resolve() / PROJECT_RUNTIME_DIRNAME


def session_history_root(project_dir: str | Path) -> Path:
    return project_runtime_root(project_dir) / "session_history"


def design_root(project_dir: str | Path) -> Path:
    """用例设计产物根；与 CaseDesignEnv.design 对齐（不做 resolve，保持相对构造）。"""
    return Path(project_dir) / PROJECT_RUNTIME_DIRNAME / "design"


def knowledge_mount(project_dir: str | Path) -> Path:
    """项目内知识库挂载点：``{project.dir}/.AiTester/knowledge``（通常为指向实体的 junction）。"""
    return project_runtime_root(project_dir) / "knowledge"


def reme_paths_for_project_cwd(text: str) -> str:
    """把 Reme workspace 相对路径前缀改写成相对项目根（cwd）的 ``.AiTester/...``。

    已带 ``.AiTester/`` 前缀的不会二次改写。
    """
    if not text:
        return text
    return _REME_REL_PREFIX_RE.sub(rf"{PROJECT_RUNTIME_DIRNAME}/\1/", text)
