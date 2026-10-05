"""流式专项的测试替身：把「剧本 AIMessage」切成节点消费的 chunk 序列。

真模型给的是 tool_call_chunks（参数增量）；剧本给的是已解析好的 tool_calls，直接挂到最后一块
即可——P8 实测累加后 merged.tool_calls 就是这份清单，没必要在测试里模拟半截 JSON。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.orchestration.run_control import RunControl

CHUNK_CHARS = 4


def chunks_from(message: AIMessage) -> Iterator[AIMessageChunk]:
    text = str(message.content or "")
    calls = list(message.tool_calls or [])
    pieces = [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)] or [""]
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        yield AIMessageChunk(
            content=piece,
            tool_calls=calls if last else [],
            additional_kwargs=dict(message.additional_kwargs) if last else {},
        )


class ChunkedStreamMixin:
    """已有 invoke_messages 剧本的假 provider 加这个基类即得 stream_messages。"""

    def stream_messages(self, messages: list) -> Iterable[AIMessageChunk]:
        yield from chunks_from(self.invoke_messages(messages))  # type: ignore[attr-defined]


class ScriptedProvider(ChunkedStreamMixin):
    """严格剧本 provider：给「父—子同一条链」的多回合脚本用。

    与 test_agent_graph.ScriptedProvider / test_chat_auth.ScriptProvider 的差别：
    那两位剧本见底后静默回 "done"（旧用例的宽松口径）；父—子同链回合多，
    漏写一回合会被静默 "done" 假绿盖住，故本类见底即响亮失败。
    剧本元素可以是 Exception：轮到它就抛，模拟 provider 侧故障；
    `calls` 记录每次喂进来的消息表（断子体独立入参用）。
    """

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage | Exception]) -> None:
        self._script = list(script)
        self.calls: list[list] = []

    def bind_tools(self, tools: list) -> "ScriptedProvider":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        self.calls.append(list(messages))
        if not self._script:
            raise AssertionError("剧本见底：嵌套链还有回合没写进 script")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CancelAfterProvider:
    """在第 n 块产出时置取消位：验「chunk 之间」的检查点真的生效。

    自带 stream_messages 逐块置位，不必借 ChunkedStreamMixin 把 invoke 剧本切块，
    故不再继承该基类（原先继承后又覆写 stream_messages，基类那份即成死码）。
    """

    name = "cancel"
    model_ref = "cancel/model"

    def __init__(self, message: AIMessage, control: RunControl, after: int = 1) -> None:
        self._message = message
        self._control = control
        self._after = after

    def complete(self, messages: list) -> str:
        return str(self._message.content)

    def bind_tools(self, tools: list) -> "CancelAfterProvider":
        return self

    def stream_messages(self, messages: list) -> Iterator[AIMessageChunk]:
        for i, chunk in enumerate(chunks_from(self._message)):
            if i == self._after:
                self._control.cancel()
            yield chunk


def local_tools(tmp_path: Path) -> list:
    """与 test_agent_graph._tools 同款：cwd 落在 tmp_path，零网络、只碰 tmp。"""
    registry = build_default_registry(
        cwd=str(tmp_path), session_id="s1", observed=FileObservationStore()
    )
    return registry.as_langchain_tools()


def sse_frames(resp) -> list[tuple[str, dict]]:
    """把 SSE 响应体读成 (事件名, 载荷) 表；节奏不在断言范围，见 TestClient 缓冲实测。"""
    out: list[tuple[str, dict]] = []
    event = ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line.split(":", 1)[1].strip())))
    return out
