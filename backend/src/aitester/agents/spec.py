"""一个智能体的静态定义：数据缝的最小单元。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    icon: str
    name: str
    desc: str
    prompt: str
    default_tool_ids: tuple[str, ...]
    graph_builder: str = "react"
