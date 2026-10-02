"""智能体注册表：定义在本包，提示词在 prompts/<id>.md。

`LEGACY_AGENT_IDS` 是一次性迁移表（旧键 → 新键），不是别名机制：状态搬完、旧键随
未知键一起被归一化丢弃后，本表即可删除。
"""

from pathlib import Path
from typing import Any

from aitester.agents.spec import AgentSpec

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(agent_id: str) -> str:
    path = PROMPTS_DIR / f"{agent_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"智能体「{agent_id}」缺少提示词文件：{path}")
    return path.read_text(encoding="utf-8").strip()


AGENT_CATALOG: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="case_design",
        icon="📋",
        name="用例设计智能体",
        desc="读需求与接口文档，产出可直接执行的测试用例并同步用例平台，覆盖等价类、边界值与异常路径。",
        prompt=_load_prompt("case_design"),
        default_tool_ids=(
            "read",
            "write",
            "edit",
            "grep_search",
            "glob_search",
            "web_search",
        ),
    ),
)

DEFAULT_AGENT_STATE: dict[str, dict[str, Any]] = {
    spec.id: {"default_uid": "", "tool_ids": list(spec.default_tool_ids)}
    for spec in AGENT_CATALOG
}

LEGACY_AGENT_IDS: dict[str, str] = {"a1": "case_design"}

KB_ASSISTANT_SPEC = AgentSpec(
    id="kb_assistant",
    icon="📚",
    name="知识库助手",
    desc="知识库页内置助手：检索共享知识库、生成写入草案，用户确认后才落盘。",
    prompt=_load_prompt("kb_assistant"),
    default_tool_ids=(
        "read",
        "grep_search",
        "glob_search",
        "knowledge_search",
        "prepare_kb_write",
    ),
)

# 平台功能智能体：刻意不进 AGENT_CATALOG/DEFAULT_AGENT_STATE——能力配置、设置页、
# 聊天页下拉因此不可见；工具面由 AgentRuntime 强制绑定（spec 裁定②，勿改成走勾选）。
PLATFORM_AGENT_CATALOG: tuple[AgentSpec, ...] = (KB_ASSISTANT_SPEC,)


def is_platform_agent(agent_id: str) -> bool:
    """平台功能智能体唯一判据：会话闭环据此决定「落不落盘」（spec 裁定）。"""
    return any(spec.id == agent_id for spec in PLATFORM_AGENT_CATALOG)


def find_agent(agent_id: str) -> AgentSpec | None:
    for spec in (*AGENT_CATALOG, *PLATFORM_AGENT_CATALOG):
        if spec.id == agent_id:
            return spec
    return None
