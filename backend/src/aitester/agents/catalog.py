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


def find_agent(agent_id: str) -> AgentSpec | None:
    for spec in AGENT_CATALOG:
        if spec.id == agent_id:
            return spec
    return None
