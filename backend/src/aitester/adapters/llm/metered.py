"""provider 装饰器：把计量缝装在所有调用流唯一共用的那一层。

生产链路实际经过这里的只有两条出口：主会话与 case_design 专属图的 `stream_messages`
（`agent_graph.py:98`，子智能体与评审子走同一个 bound provider）和 `echo` 的 `complete`
（`graph.py:18`）；`invoke_messages` 与 `bind_tools` 也一并包上，是因为它们是 provider
协议的其余成员——`MockProvider.stream_messages` 自调的是自己那一份，不经过这层。
三条出口 + `bind_tools` 都在这里过一遍账，所以尺只有一把（CM-5）。真值不回写估算样本
之外的地方：`note_response(None, …)` 就是一次「这轮没有真值」的声明（R-C1）。
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
    def inner(self) -> Any:
        """被包的原始 provider。身份留痕只从这里取，且只从未 bound 的那一个取。"""
        return self._inner

    @property
    def name(self) -> str:
        return str(getattr(self._inner, "name", ""))

    @property
    def model_ref(self) -> str:
        return str(getattr(self._inner, "model_ref", ""))

    def bind_tools(self, tools: list[Any]) -> "MeteredProvider":
        return MeteredProvider(self._inner.bind_tools(tools), self.usage)

    def _before(self, messages: list[Any]) -> None:
        try:
            self.usage.note_request(meter.estimate_messages(messages))
        except Exception as exc:
            # R-C5：尺自己坏了也只许「这一轮没读数」，不许把回合打死。轮次照记——
            # 请求随后仍会发起；样本给 0 并把缺口写进 usage.error，界面看得见。
            self.usage.note_request(0)
            self.usage.error = f"计量失败：{type(exc).__name__}"

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
        finished = False
        try:
            for chunk in self._inner.stream_messages(messages):
                got_in = meter.input_tokens_of(chunk)
                got_out = meter.output_tokens_of(chunk)
                if got_in is not None:
                    real_in = got_in
                if got_out is not None:
                    real_out = got_out
                yield chunk
            finished = True
        finally:
            # 「跑完整条流」是拿真值的唯一资格：断开/取消/上游抛错时，哪怕前面已经见过
            # 带 usage 的块，也一律声明「这轮没有真值」——那一轮的用量压根还没发生完。
            if finished:
                self.usage.note_response(real_in, real_out)
            else:
                self.usage.note_response(None, None)
