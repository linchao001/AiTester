"""行为缝：图构建器注册表。

默认智能体都走 `react`（下面的工具循环）；循环范式不同的智能体在这里注册自己的
LangGraph 拓扑。构建器名属于开发者面，未注册即代码缺陷，不做运行期兜底。
"""

from aitester.orchestration.agent_graph import GraphBuilder, build_agent_graph

GRAPH_BUILDERS: dict[str, GraphBuilder] = {"react": build_agent_graph}


def get_graph_builder(name: str) -> GraphBuilder:
    builder = GRAPH_BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"未注册的图构建器「{name}」，请在 GRAPH_BUILDERS 注册")
    return builder
