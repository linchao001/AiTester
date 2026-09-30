from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from aitester.adapters.llm import LlmProvider


class EchoState(TypedDict):
    messages: list[dict[str, str]]
    reply: str


def build_echo_graph(provider: LlmProvider) -> CompiledStateGraph:
    """当前仅一个 llm_node；后续多节点 loop 在此扩展。"""

    def llm_node(state: EchoState) -> dict[str, str]:
        return {"reply": provider.complete(state["messages"])}

    graph = StateGraph(EchoState)
    graph.add_node("llm_node", llm_node)
    graph.add_edge(START, "llm_node")
    graph.add_edge("llm_node", END)
    return graph.compile()


def run_echo(provider: LlmProvider, messages: list[dict[str, str]]) -> str:
    result = build_echo_graph(provider).invoke({"messages": messages, "reply": ""})
    return result["reply"]
