"""provider 装饰器：把计量缝装在所有调用流唯一共用的那一层。

主会话与 case_design 专属图都走 `stream_messages`（`agent_graph.py:98`），子智能体与
评审子走同一个 bound provider，`echo` 走 `complete`——三条出口 + `bind_tools` 都在这里
过一遍账，所以尺只有一把（CM-5）。真值不回写估算样本之外的地方：`note_response(None, …)`
就是一次「这轮没有真值」的声明（R-C1）。
"""

from __future__ import annotations

from typing import Any, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk

from aitester.context import meter
from aitester.context.usage import ContextUsage


class MeteredProvider:
    def __init__(self, inner: Any, usage: ContextUsage) -> None:
        self._inner = inner
        self.usage = usage                      # 窗口读 usage.window：不留第二个副本（R-C2）

    @property
    def name(self) -> str:
        return str(getattr(self._inner, "name", ""))

    @property
    def model_ref(self) -> str:
        return str(getattr(self._inner, "model_ref", ""))

    def bind_tools(self, tools: list[Any]) -> "MeteredProvider":
        return MeteredProvider(self._inner.bind_tools(tools), self.usage)

    def _before(self, messages: list[Any]) -> None:
        self.usage.note_request(meter.estimate_messages(messages))

    def complete(self, messages: list[Any]) -> str:
        self._before(messages)
        return self._inner.complete(messages)

    def invoke_messages(self, messages: list[Any]) -> AIMessage:
        self._before(messages)
        result = self._inner.invoke_messages(messages)
        self.usage.note_response(meter.input_tokens_of(result),
                                 meter.output_tokens_of(result))
        return result

    def stream_messages(self, messages: list[Any]) -> Iterator[AIMessageChunk]:
        self._before(messages)
        real_in: int | None = None
        real_out: int | None = None
        for chunk in self._inner.stream_messages(messages):
            got_in = meter.input_tokens_of(chunk)
            got_out = meter.output_tokens_of(chunk)
            if got_in is not None:
                real_in = got_in
            if got_out is not None:
                real_out = got_out
            yield chunk
        # 只有整条流跑到尽头才谈真值：中途被取消就没有「这一轮的用量」可言
        self.usage.note_response(real_in, real_out)
