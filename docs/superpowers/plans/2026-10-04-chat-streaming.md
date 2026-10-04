# 聊天页 · 第 4 片：流式与停止 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让智能体回复逐 token 流到界面（SSE），并提供服务端真停：`POST /api/chat/send/stream` + `POST /api/chat/stop` 取代一次性 `POST /api/chat/send`。

**Architecture:** 模型适配器加流式通道（`stream_messages`），图节点体改为逐 chunk 累加并实时写 `custom` 流，`stream_graph` 把 `custom`+`updates` 两路折成 7 类事件，`ChatService` 拆成「守门（prepare）+ 事件流与落盘（stream_turn）」，路由把事件序列化成 SSE 帧。取消走 `RunControl`（一个 `threading.Event`），由 `RunRegistry` 按 `run_id` 持有，`run_graph` 与 `send` 都变成事件流的薄壳——一份实现，两种消费。

**Tech Stack:** Python 3.11+ / uv / FastAPI 0.142 / langgraph 1.2.12（`StateGraph`、`ToolNode`、`stream_mode=["custom","updates"]`、`get_stream_writer`）/ langchain-core 1.6.6（`AIMessageChunk` 累加）/ pytest + starlette TestClient；React 18 + Vite 5.4 TS（`fetch` + `ReadableStream` 手解 SSE，`EventSource` 不能 POST 故不用）。

**Spec:** `docs/superpowers/specs/2026-10-04-chat-streaming-design.md`（本计划逐条实现其裁定 1-6、事件表、契约表与偏离登记；实施时两份一起读）

## Global Constraints

- 后端基线 **448 passed**（2026-10-04 实测：`cd backend && uv run pytest -q`）。每个任务收尾都要回到全绿，且**既有测试的断言一字不改**（`test_agent_graph.py` 是本片流式核心的回归锁；假 provider 只允许「加方法」，不允许「改断言」）。
- 前端门禁：`cd frontend && npm run build` 0 error。本期无前端测试框架（既有裁定），故新增逻辑必须落在能一眼读死的纯函数里。
- UI 约定：全中文文案、**0 处 `zhb`**、**0 个死按钮**、不做纯图标开关钮、不新增色值（只用 `App.css` 里已有的变量与色）。
- detail 一律中文且可照做；守门文案与判据**只许存在一份**（`chat.py` 的 `prepare`），流式路径不得重写一遍（第 2 片消费对称性教训）。
- 不加任何「代理缓冲保险丝」：P7 已实测 vite proxy 不缓冲、不压缩 SSE → 不写 `X-Accel-Buffering`、不关 gzip。
- `AIMessageChunk` 在本版本**没有 `.message`**（P8 实测）→ 节点返回体必须是 `AIMessage(content=…, tool_calls=…, additional_kwargs=…)` 重建。
- 取消**不是异常**：命中即正常收尾，统一由 `finish{stopped:true}` / `done{stopped:true}` 表达（裁定 2）。
- 单进程约束、明文落盘、无鉴权：沿用第 1/2/3 片既有范围，本片不补。
- 用户自己的后端在 `127.0.0.1:8000`、vite 在 `[::1]:5173`——**本计划所有任务都不得占用或杀掉这两个端口**；真机走查由用户本人自起端口执行。
- 提交只在本地；`git push` 需用户另行授权。

## 文件结构（先定边界，再切任务）

| 文件 | 责任 | 任务 |
|---|---|---|
| `backend/src/aitester/adapters/llm/{base,mock,openai_compat}.py` | 模型侧流式通道：产出 `AIMessageChunk` 序列，失败包法与 `invoke_messages` 一致 | T1 |
| `backend/src/aitester/orchestration/run_control.py`（新增） | 取消载体 + 进节点的 config 键名 | T2 |
| `backend/tests/streaming_fakes.py`（新增） | 假 provider 的 chunk 化 + 测试用本地工具装配（6 处既有假 provider 共用） | T2 |
| `backend/src/aitester/orchestration/agent_graph.py` | 节点体逐 chunk 流（写 `delta`/`turn`）+ `stream_graph` 事件折返 + `run_graph` 薄壳 | T2、T3 |
| `backend/src/aitester/services/session_store.py` + `memory/{base,in_memory,file_memory}.py` + `interaction/{schemas,sessions}.py` | `stopped` 字段链：落盘 → 记忆 → 读侧 schema | T4 |
| `backend/src/aitester/services/run_registry.py`（新增）+ `main.py` | 在途 run 的注册表与装配位 | T5 |
| `backend/src/aitester/services/chat.py` | `prepare`（守门，只此一份）+ `stream_turn`（事件流与终态落盘）+ `send`（折叠壳，保留 `trace`/`model`） | T6 |
| `backend/src/aitester/interaction/router.py` | SSE 序列化、`/chat/send/stream`、`/chat/stop`、删 `/chat/send` | T7 |
| `frontend/src/api/client.ts` + `frontend/src/pages/chat/streamState.ts`（新增） | 传输层：流解析、停止请求、事件折叠纯函数 | T8 |
| `frontend/src/pages/ChatPage.tsx` + `pages/chat/{MessageList,Composer}.tsx` + `App.css` | 聊天页事件驱动渲染、live 气泡、停止钮 | T9 |
| `frontend/src/pages/KbPage.tsx` + `pages/kb/KbAssistantPane.tsx` | `/kb` 助手迁到同一条流；最后一个消费方迁走后删 `chatSend`/`SendResponse` | T10 |
| `frontend/src/pages/chat/utils.ts` | 上下文 meter 与后端 `HISTORY_MAX` 对齐 | T11 |
| `README.md` | 端点清单清扫（`:33-35` 仍写着 `/api/chat/send`） | T12 |

任务顺序有硬依赖：T1 → T2 → T3（先让节点体在 invoke 下等价，再换成 stream，两道门禁各锁一次）；T4 独立可先行；T5 → T6 → T7 串成一条（服务层先于传输层）；T8 → T9 → T10 → T11 为前端链（client 先于消费者）。

---

### Task 1: 模型侧流式通道 `stream_messages`

**Files:**
- Modify: `backend/src/aitester/adapters/llm/base.py:8-18`
- Modify: `backend/src/aitester/adapters/llm/mock.py:21-27`
- Modify: `backend/src/aitester/adapters/llm/openai_compat.py:48-56`
- Test: `backend/tests/test_llm_streaming.py`（新建）

**Interfaces:**
- Consumes: `LlmProvider.invoke_messages`、`ProviderError(detail)`、`OpenAICompatProvider(_client=…)` 的注入缝
- Produces: `LlmProvider.stream_messages(messages: list[Any]) -> Iterator[AIMessageChunk]`；`aitester.adapters.llm.mock.MOCK_CHUNK_CHARS = 4`（测试常数，Task 3 的条数断言依赖它）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_llm_streaming.py`：

```python
"""模型侧流式通道：协议新增 stream_messages，三个实现各自的行为。"""

from math import ceil

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from aitester.adapters.llm import MockProvider, ProviderError
from aitester.adapters.llm.openai_compat import OpenAICompatProvider


def test_mock_stream_yields_4_char_chunks_and_concatenates_exactly() -> None:
    chunks = list(MockProvider().stream_messages([{"role": "user", "content": "生成用例"}]))
    full = "[mock] 生成用例"
    assert chunks, "流式必须至少产出一块，空流等于没有正文"
    assert all(isinstance(c, AIMessageChunk) for c in chunks)
    assert "".join(str(c.content) for c in chunks) == full
    assert len(chunks) == ceil(len(full) / 4)      # 粒度是测试常数，与真实 token 边界无关


def test_mock_stream_chunk_piecing_is_deterministic() -> None:
    chunks = list(MockProvider().stream_messages([{"role": "user", "content": "abcd"}]))
    assert [str(c.content) for c in chunks] == ["[moc", "k] a", "bcd"]


class _ScriptChat:
    """假的 ChatOpenAI：只实现 stream，用来验透传与异常包装。"""

    def __init__(self, chunks=(), error: Exception | None = None) -> None:
        self._chunks = list(chunks)
        self._error = error
        self.seen: list[object] = []

    def stream(self, messages):
        self.seen.append(messages)
        if self._error is not None:
            raise self._error
        yield from self._chunks


def _provider(chat: _ScriptChat) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="deepseek", api_key="sk-SECRET", base_url="", model="v4", _client=chat
    )


def test_openai_compat_stream_passes_messages_through() -> None:
    chat = _ScriptChat([AIMessageChunk(content="你"), AIMessageChunk(content="好")])
    out = list(_provider(chat).stream_messages([HumanMessage(content="hi")]))
    assert [str(c.content) for c in out] == ["你", "好"]
    assert type(chat.seen[0][0]).__name__ == "HumanMessage"


def test_openai_compat_stream_failure_is_provider_error_with_key_masked() -> None:
    # 与 invoke_messages 同一包法：不许出现「invoke 报中文、stream 报裸异常」
    chat = _ScriptChat(error=RuntimeError("invalid api key sk-SECRET"))
    with pytest.raises(ProviderError) as exc_info:
        list(_provider(chat).stream_messages([]))
    assert "sk-SECRET" not in exc_info.value.detail
    assert "调用 deepseek/v4 失败" in exc_info.value.detail
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_llm_streaming.py -q`
Expected: 4 failed，`AttributeError: 'MockProvider' object has no attribute 'stream_messages'`（openai 两条同因）。

- [ ] **Step 3: 协议加方法**

`backend/src/aitester/adapters/llm/base.py` 全文替换为：

```python
from __future__ import annotations

from typing import Any, Iterator, Protocol

from langchain_core.messages import AIMessage, AIMessageChunk


class LlmProvider(Protocol):
    """模型提供商适配器协议（对齐 provider 化模型配置方向）。"""

    name: str
    model_ref: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...

    def bind_tools(self, tools: list[Any]) -> LlmProvider: ...

    def invoke_messages(self, messages: list[Any]) -> AIMessage: ...

    # 流式通道：节点体逐 chunk 消费。实现者不接受该形参即断链（与 memory.save 的 steps 同口径）
    def stream_messages(self, messages: list[Any]) -> Iterator[AIMessageChunk]: ...
```

- [ ] **Step 4: MockProvider 实现**

`backend/src/aitester/adapters/llm/mock.py` 全文替换为：

```python
from __future__ import annotations

from typing import Any, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk

# 定长切片只为「条数可断言」，与真实 token 边界无关（spec 风险节登记）
MOCK_CHUNK_CHARS = 4


class MockProvider:
    name = "mock"
    model_ref = "mock/mock"

    def complete(self, messages: list[dict[str, str]]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "user":
                return f"[mock] {msg['content']}"
        return "[mock]"

    def bind_tools(self, tools: list[Any]) -> MockProvider:
        return MockProvider()

    def invoke_messages(self, messages: list[Any]) -> AIMessage:
        for msg in reversed(messages):
            content = getattr(msg, "content", "") or ""
            role = getattr(msg, "type", "")
            if role == "human":
                return AIMessage(content=f"[mock] {content}")
        return AIMessage(content="[mock]")

    def stream_messages(self, messages: list[Any]) -> Iterator[AIMessageChunk]:
        text = str(self.invoke_messages(messages).content or "")
        for i in range(0, len(text), MOCK_CHUNK_CHARS):
            yield AIMessageChunk(content=text[i:i + MOCK_CHUNK_CHARS])
```

- [ ] **Step 5: OpenAICompatProvider 实现**

`backend/src/aitester/adapters/llm/openai_compat.py` 里把 `invoke_messages` 之后追加（并把首行 import 补成 `from typing import Any, Iterator`、`from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, get_buffer_string`——`AIMessageChunk` 只在类型标注里用，若 ruff 抱怨未用则去掉它，函数体不需要）：

```python
    def stream_messages(self, messages: list[Any]) -> Iterator[AIMessageChunk]:
        # 失败包法与 invoke_messages 逐字相同：同一条「调用 {model_ref} 失败: …」+ key 打星
        try:
            for chunk in self._client.stream(messages):
                yield chunk
        except Exception as exc:
            raw = f"调用 {self.model_ref} 失败: {exc}".replace(self._api_key, "***")
            raise ProviderError(raw) from exc
```

- [ ] **Step 6: 跑到通过 + 全量门禁**

Run: `cd backend && uv run pytest tests/test_llm_streaming.py -q` → 4 passed
Run: `cd backend && uv run pytest -q` → **452 passed**（448 + 4，本任务只加不改）

- [ ] **Step 7: 提交**

```bash
git add backend/src/aitester/adapters/llm backend/tests/test_llm_streaming.py
git commit -m "feat(llm): 模型侧流式通道 stream_messages——Mock 定长切片、兼容端点透传同口径包错"
```

---

### Task 2: `RunControl` + 节点体改为逐 chunk 流

**Files:**
- Create: `backend/src/aitester/orchestration/run_control.py`
- Modify: `backend/src/aitester/orchestration/agent_graph.py:1-69`（imports、两个节点体）
- Modify: `backend/src/aitester/orchestration/__init__.py`（导出 `RunControl`、`RUN_CONTROL_KEY`）
- Create: `backend/tests/streaming_fakes.py`
- Modify: `backend/tests/test_agent_graph.py:24-40`（`ScriptedProvider` 加 mixin；**断言一字不改**）
- Modify: `backend/tests/test_orchestration.py:19-34`、`backend/tests/test_api.py:154-166,201-212`、`backend/tests/test_chat_service.py:111-126,305-317`（同 5 处假 provider）
- Test: `backend/tests/test_run_control.py`（新建）

**Interfaces:**
- Consumes: T1 的 `provider.stream_messages`
- Produces: `RunControl`（`.cancelled` 属性、`.cancel()`）、`RUN_CONTROL_KEY = "aitester_run_control"`、`tests/streaming_fakes.py::{chunks_from, ChunkedStreamMixin, CancelAfterProvider, local_tools}`；节点写出的 custom 载荷形态 `{"type":"delta","round":int,"text":str}` 与 `{"type":"turn","round":int,"text":str,"stopped":bool,"tool_calls":[{"id","name","args"}]}`（T3 的 `stream_graph` 按此折返）

**为什么本任务先不动 `run_graph`：** 节点换成流式累加后，`graph.invoke` 路径仍然出等价结果（`get_stream_writer()` 在非流式运行下是 no-op，P3 实测）。于是「节点体等价」由 `test_agent_graph.py` 在 invoke 下先锁一次，T3 再把执行切成 stream，两道门禁各验一半——避免出现 invoke/stream 两套节点行为那条第 2 片教训。

- [ ] **Step 1: 写共用假 provider 件**

新建 `backend/tests/streaming_fakes.py`：

```python
"""流式专项的测试替身：把「剧本 AIMessage」切成节点消费的 chunk 序列。

真模型给的是 tool_call_chunks（参数增量）；剧本给的是已解析好的 tool_calls，直接挂到最后一块
即可——P8 实测累加后 merged.tool_calls 就是这份清单，没必要在测试里模拟半截 JSON。
"""

from __future__ import annotations

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


class CancelAfterProvider(ChunkedStreamMixin):
    """在第 n 块产出时置取消位：验「chunk 之间」的检查点真的生效。"""

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

    def invoke_messages(self, messages: list) -> AIMessage:
        return self._message

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
```

- [ ] **Step 2: 给 6 处既有假 provider 加上流式方法**

每处都是「加一个基类」或「加一个方法」，测试体与断言一字不动：

`tests/test_agent_graph.py`：在 import 段补 `from streaming_fakes import ChunkedStreamMixin`（与既有用例同目录，pytest 的 rootdir 已在 `backend`，`tests/` 在路径上；若报 `ModuleNotFoundError`，改用 `from tests.streaming_fakes import ChunkedStreamMixin` 并在 `backend/tests/__init__.py` 不存在时保持第一种写法），然后：

```python
class ScriptedProvider(ChunkedStreamMixin):
    """按剧本逐条返回 AIMessage：先工具调用，后最终回复。"""
```

`tests/test_orchestration.py`：`class _ToolCallingProvider(ChunkedStreamMixin):`
`tests/test_api.py`：`class _SpyProvider(ChunkedStreamMixin):`（`:154`）、`class FailingProvider(ChunkedStreamMixin):`（`:201`）
`tests/test_chat_service.py`：两处 `class _SpyProvider(ChunkedStreamMixin):`（`:111`、`:305`）

`FailingProvider.invoke_messages` 抛 `ProviderError` → mixin 的 `stream_messages` 在迭代时同样抛出，`test_send_upstream_failure_returns_502` 的 502 断言在本任务后仍成立（异常照样传到路由的 except 分支）。

Run: `cd backend && uv run pytest tests/test_agent_graph.py tests/test_orchestration.py tests/test_api.py tests/test_chat_service.py -q`
Expected: 全绿（此时节点体还没改，mixin 只是多一个没人调的方法）。

- [ ] **Step 3: 写失败测试（RunControl + P6 注入钉）**

新建 `backend/tests/test_run_control.py`：

```python
"""取消信号：RunControl 本体，以及 P6 那条前提的持久钉——对象真能经 config.configurable 进节点。"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from aitester.orchestration import build_agent_graph, run_agent
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl
from streaming_fakes import ChunkedStreamMixin, local_tools


class _ToolCallingProvider(ChunkedStreamMixin):
    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, message: AIMessage) -> None:
        self._message = message

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_ToolCallingProvider":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        return self._message


def _write_call(file_name: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "write", "args": {"file_path": file_name, "content": "v"},
         "id": "c1", "type": "tool_call"}])


def test_run_control_is_a_latch() -> None:
    control = RunControl()
    assert control.cancelled is False
    control.cancel()
    control.cancel()                      # 幂等：重复置位不炸
    assert control.cancelled is True


def test_control_reaches_the_node_and_drops_pending_tool_calls(tmp_path: Path) -> None:
    """P6 钉：取消对象经 config.configurable 读得到，且命中即丢弃待派发的 tool_calls。

    工具没派发 == write 没执行 == tmp_path 里没有文件：这条断言同时是「已停的一轮不再写盘」。
    """
    control = RunControl()
    control.cancel()
    graph = build_agent_graph(_ToolCallingProvider(_write_call("x.txt")), local_tools(tmp_path))
    out = graph.invoke({"messages": [HumanMessage(content="写文件")]},
                       config={"configurable": {RUN_CONTROL_KEY: control}})
    last = out["messages"][-1]
    assert last.content == ""
    assert last.tool_calls == []          # 丢弃后 should_continue 直接 END
    assert not (tmp_path / "x.txt").exists()


def test_no_control_in_config_runs_the_round_normally(tmp_path: Path) -> None:
    # 不注入取消对象（echo 路径与单测直调）必须照旧跑完：判据是 None 而非缺键即报错
    graph = build_agent_graph(_ToolCallingProvider(_write_call("y.txt")), local_tools(tmp_path))
    out = graph.invoke({"messages": [HumanMessage(content="写文件")]})
    assert out["messages"][-1].tool_calls == [] or (tmp_path / "y.txt").exists()
    assert (tmp_path / "y.txt").read_text(encoding="utf-8") == "v"
```

- [ ] **Step 4: 跑到失败**

Run: `cd backend && uv run pytest tests/test_run_control.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'aitester.orchestration.run_control'`。

- [ ] **Step 5: 实现 `run_control.py`**

新建 `backend/src/aitester/orchestration/run_control.py`：

```python
"""在途回合的取消载体：一个 Event，不是异常。

带已生成的产物收尾是正常终态（spec 裁定 2），所以这里没有 TurnCancelled 这类异常类型。
放 orchestration 而非 services：节点体要 import 它，依赖方向必须停在 adapters/orchestration 之下。
"""

from __future__ import annotations

import threading

# 进 LangGraph config.configurable 的键：值是整个 RunControl 对象（P6 实测活对象可读）
RUN_CONTROL_KEY = "aitester_run_control"


class RunControl:
    """只有一格开关的取消位。置位幂等，读侧每 chunk 一次。"""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()
```

`backend/src/aitester/orchestration/__init__.py` 全文替换为（本任务先只导出 `RunControl`/`RUN_CONTROL_KEY`，`stream_graph` 由 T3 补进来）：

```python
"""编排 loop 层：LangGraph 状态图。"""

from aitester.orchestration.agent_graph import AgentState, build_agent_graph, run_agent, run_graph
from aitester.orchestration.graph import EchoState, build_echo_graph, run_echo
from aitester.orchestration.graph_registry import GRAPH_BUILDERS, GraphBuilder, get_graph_builder
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl

__all__ = [
    "GRAPH_BUILDERS",
    "RUN_CONTROL_KEY",
    "AgentState",
    "EchoState",
    "GraphBuilder",
    "RunControl",
    "build_agent_graph",
    "build_echo_graph",
    "get_graph_builder",
    "run_agent",
    "run_echo",
    "run_graph",
]
```

- [ ] **Step 6: 节点体改成流（含两个图分支）**

`backend/src/aitester/orchestration/agent_graph.py`：把 import 段补成

```python
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
```

并在 `DETAIL_MAX = 80` 之后、`build_agent_graph` 之前插入三个模块级私有函数：

```python
def _run_control(config: RunnableConfig | None):
    """从注入的 RunnableConfig 取取消对象；没注入就是 None（echo、单测直调、无 stop 的路径）。"""
    if not config:
        return None
    return (config.get("configurable") or {}).get(RUN_CONTROL_KEY)


def _round_no(state: AgentState) -> int:
    """轮次 = 已落地的 ToolMessage 条数 + 1（P6 实测与 run_graph 现有 round_no 语义一致）。"""
    return 1 + sum(1 for m in state["messages"] if isinstance(m, ToolMessage))


def _stream_round(provider: Any, messages: list[BaseMessage], round_no: int, control) -> AIMessage:
    """逐 chunk 累加并实时下发 delta；取消即在下一个检查点停笔。

    停笔时丢弃待派发的 tool_calls——否则「用户已经按了停止」的一轮还会继续开工具、写文件。
    返回普通 AIMessage（P8：本版本 AIMessageChunk 没有 .message，必须显式重建），
    这样 run_graph 既有的 isinstance(msg, AIMessage) 判据不受子类型干扰。
    """
    writer = get_stream_writer()
    merged = AIMessageChunk(content="")
    text = ""
    stopped = False
    for chunk in provider.stream_messages(messages):
        if control is not None and control.cancelled:
            stopped = True
            break
        piece = str(chunk.content or "")
        if piece:
            text += piece
            writer({"type": "delta", "round": round_no, "text": piece})
        merged = merged + chunk
    calls = [] if stopped else list(merged.tool_calls)
    content = text if stopped else str(merged.content)
    writer({
        "type": "turn",
        "round": round_no,
        "text": content,
        "stopped": stopped,
        "tool_calls": [
            {"id": c.get("id"), "name": c["name"], "args": c["args"]} for c in calls
        ],
    })
    return AIMessage(
        content=content,
        tool_calls=calls,
        additional_kwargs=dict(merged.additional_kwargs),
    )
```

`build_agent_graph` 里两个节点体替换为（拓扑、边、`should_continue` 一字不改）：

```python
    if not tools:
        def answer_node(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
            return {"messages": [_stream_round(
                provider, state["messages"], _round_no(state), _run_control(config)
            )]}
```

```python
    def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        return {"messages": [_stream_round(
            bound, state["messages"], _round_no(state), _run_control(config)
        )]}
```

> `GraphBuilder` 的签名（`Callable[[LlmProvider, list[AiTooler]], CompiledStateGraph]`）**不动**——P6 实测节点形参与 `get_config()` 两条路都读得到 configurable，形参是官方写法。

- [ ] **Step 7: 跑到通过 + 回归锁 + 全量门禁**

Run: `cd backend && uv run pytest tests/test_run_control.py -q` → 3 passed
Run: `cd backend && uv run pytest tests/test_agent_graph.py tests/test_orchestration.py -q` → **既有用例一字未改、全绿**（这是「流式累加 ≡ 一次性 invoke」的那道锁）
Run: `cd backend && uv run pytest -q` → **455 passed**（448 + T1 的 4 + 本任务 3）

- [ ] **Step 8: 提交**

```bash
git add backend/src/aitester/orchestration backend/tests/streaming_fakes.py backend/tests/test_run_control.py backend/tests/test_agent_graph.py backend/tests/test_orchestration.py backend/tests/test_api.py backend/tests/test_chat_service.py
git commit -m "feat(orchestration): 节点体逐 chunk 流 + RunControl——取消即停笔并丢弃待派发工具调用"
```

---

### Task 3: `stream_graph` 事件流 + `run_graph` 折返薄壳

**Files:**
- Modify: `backend/src/aitester/orchestration/agent_graph.py`（`run_graph` 整段替换 + 新增 `stream_graph`/`_detail_of`）
- Modify: `backend/src/aitester/orchestration/__init__.py`（导出 `stream_graph`）
- Test: `backend/tests/test_stream_graph.py`（新建）

**Interfaces:**
- Consumes: T2 的 custom 载荷（`delta`/`turn`）、`updates` 分片里的 `{"tools": {"messages": [ToolMessage]}}`、`RUN_CONTROL_KEY`
- Produces: `stream_graph(build: GraphBuilder, provider: LlmProvider, tools: list[AiTooler], messages: list[BaseMessage], control: RunControl | None = None) -> Iterator[dict[str, Any]]`，事件 `{"type": "delta"|"call"|"step"|"draft"|"finish", …}`；`finish` 恒为最后一条且形如 `{reply, tool_traces, drafts, stopped}`。`run_graph(build, provider, tools, messages) -> {"reply","tool_traces","drafts"}` 语义与返回值一字不变

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_stream_graph.py`：

```python
"""stream_graph：事件顺序、round 递增、draft 抢在 finish 前、取消即 stopped 终态。"""

from pathlib import Path
from math import ceil

from langchain_core.messages import AIMessage, HumanMessage

from aitester.orchestration import build_agent_graph, run_graph, stream_graph
from aitester.orchestration.run_control import RunControl
from streaming_fakes import CancelAfterProvider, ChunkedStreamMixin, local_tools
from aitester.adapters.llm import MockProvider


def _collect(provider, tools, messages, control=None):
    return list(stream_graph(build_agent_graph, provider, tools, messages, control=control))


def _events(events, kind):
    return [e for e in events if e["type"] == kind]


def test_plain_answer_streams_4_char_deltas_then_finish() -> None:
    events = _collect(MockProvider(), [], [HumanMessage(content="生成用例")])
    deltas = _events(events, "delta")
    assert "".join(d["text"] for d in deltas) == "[mock] 生成用例"
    assert len(deltas) == ceil(len("[mock] 生成用例") / 4)
    assert all(d["round"] == 1 for d in deltas)          # 无工具：全程第 1 轮
    assert events[-1]["type"] == "finish"
    assert events[-1]["reply"] == "[mock] 生成用例"
    assert events[-1]["stopped"] is False


class _Scripted(ChunkedStreamMixin):
    """剧本逐轮：先调工具，再出最终回复。"""

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Scripted":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        return self._script.pop(0) if self._script else AIMessage(content="done")


def _write_call(name: str, content: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "write", "args": {"file_path": name, "content": content},
         "id": f"c-{name}", "type": "tool_call"}])


def test_call_and_step_pair_up_with_increasing_rounds(tmp_path: Path) -> None:
    provider = _Scripted([
        _write_call("a.txt", "v1"),
        _write_call("b.txt", "v2"),
        AIMessage(content="两步完成"),
    ])
    events = _collect(provider, local_tools(tmp_path), [HumanMessage(content="写两次")])
    kinds = [e["type"] for e in events]
    assert kinds[:4] == ["call", "step", "call", "step"]
    assert kinds[-1] == "finish"
    calls = _events(events, "call")
    steps = _events(events, "step")
    assert [c["round"] for c in calls] == [1, 2]
    assert [s["round"] for s in steps] == [1, 2]         # 轮次按「已落地 ToolMessage + 1」递增
    assert [c["tool"] for c in calls] == ["write", "write"]
    assert all(s["ok"] is True for s in steps)
    assert calls[0]["detail"] == '{"file_path": "a.txt", "content": "v1"}'
    finish = events[-1]
    assert finish["reply"] == "两步完成"
    assert [t["tool"] for t in finish["tool_traces"]] == ["write", "write"]
    assert finish["tool_traces"][0]["result"]            # result 只进 finish，不进 step 事件
    assert "result" not in steps[0]


def test_draft_event_arrives_before_finish(tmp_path: Path) -> None:
    from aitester.adapters.tools.kb_tools import PrepareKbWriteTool

    (tmp_path / "_inbox").mkdir()
    provider = _Scripted([
        AIMessage(content="", tool_calls=[{"name": "prepare_kb_write", "args": {
            "op": "create", "path": "_inbox/n.md", "content": "# N", "summary": "新建"},
            "id": "c1", "type": "tool_call"}]),
        AIMessage(content="草案已生成，请点确认"),
    ])
    events = _collect(provider, [PrepareKbWriteTool(kb_root=tmp_path)],
                      [HumanMessage(content="记一笔")])
    kinds = [e["type"] for e in events]
    assert "draft" in kinds
    assert kinds.index("draft") < kinds.index("finish")
    assert events[kinds.index("draft")]["draft"]["path"] == "_inbox/n.md"


def test_control_hit_stops_deltas_and_marks_finish_stopped(tmp_path: Path) -> None:
    control = RunControl()
    provider = CancelAfterProvider(AIMessage(content="0123456789"), control, after=1)
    events = _collect(provider, [], [HumanMessage(content="说吧")], control=control)
    deltas = _events(events, "delta")
    assert [d["text"] for d in deltas] == ["0123"]        # 第 2 块在检查点被丢
    finish = events[-1]
    assert finish["stopped"] is True
    assert finish["reply"] == "0123"                      # 留已生成前缀
    assert [e["type"] for e in events].count("finish") == 1
    assert events[-1] is finish                           # finish 之后不再有任何事件


def test_run_graph_is_a_fold_over_stream_events(tmp_path: Path) -> None:
    """一条实现两种消费：run_graph 的返回形状必须与折返前逐字相同。"""
    provider = _Scripted([_write_call("c.txt", "v"), AIMessage(content="已写入")])
    folded = run_graph(build_agent_graph, provider, local_tools(tmp_path),
                       [HumanMessage(content="写文件")])
    assert folded["reply"] == "已写入"
    assert [t["tool"] for t in folded["tool_traces"]] == ["write"]
    assert set(folded["tool_traces"][0]) == {"tool", "result", "ok", "round", "detail"}
    assert folded["drafts"] == []
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_stream_graph.py -q`
Expected: FAIL，`ImportError: cannot import name 'stream_graph' from 'aitester.orchestration'`。

- [ ] **Step 3: 写 `stream_graph` 并把 `run_graph` 改成薄壳**

`backend/src/aitester/orchestration/agent_graph.py`：把现有 `run_graph`（`"""按指定一轮…` 到 `return {"reply": reply, "tool_traces": tool_traces, "drafts": drafts}`）整段替换为：

```python
def _detail_of(call: dict[str, Any]) -> str:
    """过程块的参数摘要：与迁移前逐字同口径（JSON 序列化后截 DETAIL_MAX）。"""
    try:
        return json.dumps(call.get("args") or {}, ensure_ascii=False)[:DETAIL_MAX]
    except (TypeError, ValueError):
        return str(call.get("args"))[:DETAIL_MAX]  # 非常规 args（非 JSON 可序列化）不退化成报错，UI 只截一行


def stream_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
    control: RunControl | None = None,
) -> Iterator[dict[str, Any]]:
    """按指定拓扑执行一轮，把执行过程实时折成事件流。

    两路合成：节点内 get_stream_writer() 写的 custom（delta/turn）给正文与工具发起，
    graph.stream 的 updates 分片给 ToolMessage（step/draft）。二者按写入顺序到达（P6 实测）。
    末条恒为 finish：{reply, tool_traces, drafts, stopped}。
    """
    graph = build(provider, tools)
    # 只有真带 control 时才注入 config：空 config 等于「没有取消对象」，与 invoke 直调同形
    config = {"configurable": {RUN_CONTROL_KEY: control}} if control is not None else {}
    stream = graph.stream(
        {"messages": messages}, config=config, stream_mode=["custom", "updates"]
    )

    reply = ""
    round_no = 0
    stopped = False
    tool_traces: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    calls_by_id: dict[str, dict[str, Any]] = {}

    for mode, payload in stream:
        if mode == "custom":
            kind = payload.get("type")
            if kind == "delta":
                yield {"type": "delta", "round": payload["round"], "text": payload["text"]}
            elif kind == "turn":
                round_no = max(round_no, int(payload["round"]))
                stopped = stopped or bool(payload["stopped"])
                calls = payload.get("tool_calls") or []
                if calls:
                    for call in calls:
                        calls_by_id[str(call.get("id"))] = call
                        yield {
                            "type": "call",
                            "tool": call["name"],
                            "round": payload["round"],
                            "detail": _detail_of(call),
                        }
                elif payload.get("text"):
                    # 与迁移前同口径：后写的非工具轮 content 覆盖前面的（中间轮文本因此不落盘）
                    reply = str(payload["text"])
            continue

        produced = payload.get("tools") or {}
        for msg in produced.get("messages") or []:
            if not isinstance(msg, ToolMessage):
                continue
            detail = _detail_of(calls_by_id.get(str(msg.tool_call_id), {}))
            trace = {
                "tool": msg.name or "",
                "result": str(msg.content),
                "ok": str(getattr(msg, "status", "success")) != "error",
                "round": round_no,
                "detail": detail,
            }
            tool_traces.append(trace)
            # step 事件是 UI 过程块的契约字段，严格取键、不外泄 result（services 层一直不吃 result）
            yield {
                "type": "step",
                "tool": trace["tool"],
                "ok": trace["ok"],
                "round": trace["round"],
                "detail": trace["detail"],
            }
            # prepare_kb_write 的草案走 artifact 通道（模型不可见），只发给 UI 确认
            if msg.name == "prepare_kb_write" and getattr(msg, "artifact", None):
                drafts.append(msg.artifact)
                yield {"type": "draft", "draft": msg.artifact}

    yield {
        "type": "finish",
        "reply": reply,
        "tool_traces": tool_traces,
        "drafts": drafts,
        "stopped": stopped,
    }


def run_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """一次性折返壳：把事件流折回 {reply, tool_traces, drafts}。

    形状与迁移前逐字相同（tool_traces 每项 {tool, result, ok, round, detail}，
    services/chat.py 按严格取键消费，缺字段即 KeyError，不兜默认防假绿）。
    留着它只为让既有测试继续锁住流式核心——一份实现，两种消费。
    """
    out: dict[str, Any] = {"reply": "", "tool_traces": [], "drafts": []}
    for event in stream_graph(build, provider, tools, messages):
        if event["type"] == "finish":
            out = {
                "reply": event["reply"],
                "tool_traces": event["tool_traces"],
                "drafts": event["drafts"],
            }
    return out
```

import 段补 `from typing import Any, Callable, Iterator`，并加 `from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl`（`RunControl` 仅用于类型标注）。

`backend/src/aitester/orchestration/__init__.py`：`from aitester.orchestration.agent_graph import AgentState, build_agent_graph, run_agent, run_graph, stream_graph`，并在 `__all__` 里加 `"stream_graph"`。

- [ ] **Step 4: 跑到通过 + 回归锁 + 全量门禁**

Run: `cd backend && uv run pytest tests/test_stream_graph.py -q` → 5 passed
Run: `cd backend && uv run pytest tests/test_agent_graph.py -q` → **既有用例一字未改、全绿**（此时它们已经在锁 stream_graph）
Run: `cd backend && uv run pytest -q` → **460 passed**（455 + 5）

- [ ] **Step 5: 提交**

```bash
git add backend/src/aitester/orchestration backend/tests/test_stream_graph.py
git commit -m "feat(orchestration): stream_graph 事件流——run_graph 改为事件折返薄壳，一份实现两种消费"
```

---

### Task 4: `stopped` 落盘链（store → memory → 读侧 schema）

**Files:**
- Modify: `backend/src/aitester/services/session_store.py:66-84`（`ChatMessage`）、`:260-280`（`append`）
- Modify: `backend/src/aitester/memory/base.py:7-9`、`backend/src/aitester/memory/in_memory.py:5-8`、`backend/src/aitester/memory/file_memory.py:24-35`
- Modify: `backend/src/aitester/interaction/schemas.py:239-243`（`ChatMessageInfo`）
- Modify: `backend/src/aitester/interaction/sessions.py:51-62`（`_to_message`）
- Test: `backend/tests/test_session_store.py`、`backend/tests/test_file_memory.py`、`backend/tests/test_api_chat_sessions.py`（各追加用例）

**Interfaces:**
- Consumes: 既有 `SessionStore.append`、`MemoryStore.save`
- Produces: `SessionStore.append(session_id, role, content, steps=None, stopped=False)`、`ChatMessage.stopped: bool`、`MemoryStore.save(..., steps=None, stopped=False)`、`ChatMessageInfo.stopped: bool`（读侧只增不删，向后兼容）；T6 的落盘与 T8 的前端字段依赖它

- [ ] **Step 1: 写失败测试**

`backend/tests/test_session_store.py` 末尾追加：

```python
def test_stopped_roundtrip_and_old_row_default_false(tmp_path) -> None:
    """零迁移：第 4 片之前落盘的行没有 stopped 字段，读侧必须答 False 而不是炸。"""
    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    store.create(sid, "case_design", "proj_11111111", "退款用例")
    store.append(sid, "assistant", "半截回答",
                 steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}],
                 stopped=True)
    rows = store.messages(sid)
    assert rows[-1].stopped is True
    assert rows[-1].to_dict()["stopped"] is True

    path = tmp_path / "sessions" / f"{sid}.jsonl"
    legacy = {"role": "assistant", "content": "第 4 片前的行", "ts": 1}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(legacy, ensure_ascii=False) + "\n")
    assert store.messages(sid)[-1].stopped is False


def test_append_defaults_to_not_stopped(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    store.create(sid, "case_design", "proj_11111111", "订单")
    store.append(sid, "user", "生成用例")
    assert store.messages(sid)[-1].stopped is False
```

> 若该文件顶部还没有 `import json`，补上（`from pathlib import Path` 已在）。

`backend/tests/test_file_memory.py` 末尾追加：

```python
def test_stopped_flag_persists_on_assistant_row(tmp_path) -> None:
    """标注进得了磁盘：memory.save 漏传 stopped 就是静默丢标注（四层链任一环都锁一条）。"""
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, "proj_11111111")
    mem.save("case_design:sess_abcdef01", "user", "生成用例")
    mem.save("case_design:sess_abcdef01", "assistant", "半截回答",
             steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}],
             stopped=True)
    rows = store.messages("sess_abcdef01")
    assert rows[-1].stopped is True
    assert rows[-1].steps == [{"tool": "read", "ok": True, "round": 1, "detail": "{}"}]


def test_recall_returns_truncated_text_verbatim(tmp_path) -> None:
    """被停止的那条进下一轮 prompt 时是截断原文——不加「（已停止）」，标注只进 UI。"""
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, "proj_11111111")
    mem.save("case_design:sess_11112222", "assistant", "已生成的前缀", stopped=True)
    assert mem.recall("case_design:sess_11112222") == [
        {"role": "assistant", "content": "已生成的前缀"}]
```

> 该文件顶部的 import 需含 `from aitester.services.session_store import SessionStore`；缺就补。

`backend/tests/test_api_chat_sessions.py` 末尾追加：

```python
def test_messages_endpoint_exposes_stopped(tmp_path) -> None:
    client, sid, _ = _seed(tmp_path)
    client.app.state.sessions.append(sid, "assistant", "半截回答", stopped=True)
    rows = client.get(f"/api/chat/sessions/{sid}/messages").json()["messages"]
    assert rows[-1]["stopped"] is True
    assert rows[0]["stopped"] is False      # 老行/未标注的行默认 False
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_session_store.py tests/test_file_memory.py tests/test_api_chat_sessions.py -q`
Expected: FAIL，`TypeError: append() got an unexpected keyword argument 'stopped'`（第一条即炸，其余同因）。

- [ ] **Step 3: `session_store.py` 落盘侧**

`ChatMessage` 数据类替换为：

```python
@dataclass
class ChatMessage:
    role: str
    content: str
    ts: int
    steps: list[dict[str, Any]] | None = None
    # 第 4 片：被停止的回答。老 jsonl 行没这个键 → from_dict 读缺省 False，零迁移
    stopped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> ChatMessage:
        steps = raw.get("steps")
        return ChatMessage(
            role=str(raw.get("role") or ""),
            content=str(raw.get("content") or ""),
            ts=int(raw.get("ts") or 0),
            steps=steps if isinstance(steps, list) else None,
            stopped=raw.get("stopped") is True,   # 只认真 True，脏数据不伪装成被停止
        )
```

`append` 替换为：

```python
    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        steps: list[dict[str, Any]] | None = None,
        stopped: bool = False,
    ) -> None:
        path = self._path(session_id)
        ts = _now_ms()
        line = ChatMessage(
            role=role, content=content, ts=ts, steps=steps or None, stopped=stopped
        ).to_dict()
```

函数体其余行（注释、加锁、写文件、`updated_at`/`message_count`/`_save_index`）一字不动。

- [ ] **Step 4: 记忆层三处**

`backend/src/aitester/memory/base.py` 全文替换：

```python
from typing import Protocol


class MemoryStore(Protocol):
    """会话记忆抽象，最小实现为进程内存储（重启即失）。"""

    # steps：工具过程痕迹，只有文件实现需要持久化；实现者不接受该形参即断链
    # stopped：第 4 片的停止标注，同样只有文件实现有持久语义
    def save(self, session_id: str, role: str, content: str,
             steps: list[dict] | None = None, stopped: bool = False) -> None: ...

    def recall(self, session_id: str) -> list[dict[str, str]]: ...
```

`backend/src/aitester/memory/in_memory.py` 全文替换：

```python
class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[dict[str, str]]] = {}

    def save(self, session_id: str, role: str, content: str,
             steps: list[dict] | None = None, stopped: bool = False) -> None:
        # 骨架实现不建模过程块与停止标注：两者都是真实链路产物，进程内记忆只保 role/content
        self._messages.setdefault(session_id, []).append({"role": role, "content": content})

    def recall(self, session_id: str) -> list[dict[str, str]]:
        return list(self._messages.get(session_id, []))
```

`backend/src/aitester/memory/file_memory.py` 的 `save` 替换为：

```python
    def save(
        self,
        session_id: str,
        role: str,
        content: str,
        steps: list[dict] | None = None,
        stopped: bool = False,
    ) -> None:
        agent_id, sid = self._split(session_id)
        if self._store.get(sid) is None:
            # 首条消息建会话（延迟落盘裁定）：失败发送不留 0 消息幽灵会话
            self._store.create(sid, agent_id, self._project_id, content if role == "user" else "")
        self._store.append(sid, role, content, steps, stopped)
```

- [ ] **Step 5: 读侧 schema 与装配**

`interaction/schemas.py` 的 `ChatMessageInfo` 替换为：

```python
class ChatMessageInfo(BaseModel):
    role: str
    content: str
    ts: int
    steps: list[StepInfo] | None = None
    stopped: bool = False      # 读侧字段只增不删：GET /{sid}/messages 向后兼容
```

`interaction/sessions.py` 的 `_to_message` 返回行替换为：

```python
    return ChatMessageInfo(
        role=m.role, content=m.content, ts=m.ts, steps=steps or None,
        stopped=m.stopped,
    )
```

- [ ] **Step 6: 跑到通过 + 全量门禁**

Run: `cd backend && uv run pytest tests/test_session_store.py tests/test_file_memory.py tests/test_api_chat_sessions.py -q` → 全绿
Run: `cd backend && uv run pytest -q` → **465 passed**（460 + 本任务 5 条新用例）

- [ ] **Step 7: 提交**

```bash
git add backend/src/aitester/services/session_store.py backend/src/aitester/memory backend/src/aitester/interaction/schemas.py backend/src/aitester/interaction/sessions.py backend/tests
git commit -m "feat(persistence): stopped 字段链——落盘/记忆/读侧 schema 全链透传，老行缺省 False"
```

---

### Task 5: `RunRegistry` 与装配位

**Files:**
- Create: `backend/src/aitester/services/run_registry.py`
- Modify: `backend/src/aitester/main.py:60-76`
- Modify: `docs/superpowers/specs/2026-10-04-chat-streaming-design.md`（偏离登记补一行）
- Test: `backend/tests/test_run_registry.py`（新建）

**Interfaces:**
- Consumes: T2 的 `RunControl`
- Produces: `aitester.services.run_registry.{RunRegistry, new_run_id}`——`start(run_id) -> RunControl`、`cancel(run_id) -> bool`、`finish(run_id) -> None`；`app.state.run_registry`（T7 的路由从这里取注册表，与 `app.state.sessions` 同款取法）

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_run_registry.py`：

```python
"""在途 run 注册表：命中/未命中/结束后取消，三条语义。"""

from aitester.services.run_registry import RunRegistry, new_run_id


def test_start_then_cancel_hits_the_control() -> None:
    runs = RunRegistry()
    rid = new_run_id()
    control = runs.start(rid)
    assert runs.cancel(rid) is True
    assert control.cancelled is True          # 取消位落在同一片内存上，不是副本


def test_cancel_unknown_or_finished_returns_false() -> None:
    runs = RunRegistry()
    rid = new_run_id()
    runs.start(rid)
    runs.finish(rid)
    assert runs.cancel(rid) is False          # 已结束的回答：路由据此回 404
    assert runs.cancel(new_run_id()) is False


def test_run_ids_are_unique_and_runs_are_independent() -> None:
    runs = RunRegistry()
    a, b = new_run_id(), new_run_id()
    assert a != b
    ca, cb = runs.start(a), runs.start(b)
    runs.cancel(a)
    assert ca.cancelled is True
    assert cb.cancelled is False              # 停一条不能波及另一条
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_run_registry.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'aitester.services.run_registry'`。

- [ ] **Step 3: 实现注册表**

新建 `backend/src/aitester/services/run_registry.py`：

```python
"""在途回合注册表：run_id → RunControl，进程内内存态。

重启即在途 run 消失——此时 /api/chat/stop 回 404，而界面早已在终态之前断开，无害（spec 契约表）。
不做持久化也不做鉴权：服务只绑 127.0.0.1、单用户本机（第 1/2 片同口径限制）。
"""

from __future__ import annotations

import threading
import uuid

from aitester.orchestration.run_control import RunControl


def new_run_id() -> str:
    return uuid.uuid4().hex


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunControl] = {}
        self._lock = threading.Lock()

    def start(self, run_id: str) -> RunControl:
        control = RunControl()
        with self._lock:
            self._runs[run_id] = control
        return control

    def cancel(self, run_id: str) -> bool:
        """置位并回「有没有命中」；未命中 = 这条回答已经结束或从未存在。"""
        with self._lock:
            control = self._runs.get(run_id)
        if control is None:
            return False
        control.cancel()
        return True

    def finish(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
```

- [ ] **Step 4: 挂到 app.state**

`backend/src/aitester/main.py`：import 段加 `from aitester.services.run_registry import RunRegistry`，并在 `application.state.sessions = sessions` 之后、`chat_service = ChatService(...)` 之前插入：

```python
    # 在途回合注册表：路由持它（stream_turn 只收 RunControl 形参，服务不认识 run_id）
    application.state.run_registry = RunRegistry()
```

- [ ] **Step 5: 跑到通过 + 登记一处 spec 偏差**

`docs/superpowers/specs/2026-10-04-chat-streaming-design.md` 的「偏离登记」表末追加一行（契约表写的是「注入 ChatService」，实际按 spec 自己的 `stream_turn(prepared, control)` 签名只挂 `app.state`——服务层不该多一个它用不上的字段）：

```markdown
| 9 | 装配 | `RunRegistry` 只挂 `app.state.run_registry` 由路由取用，不注入 `ChatService` | 契约表已把 `control` 定为 `stream_turn` 的形参；服务再持一份注册表就是无用字段（「0 个死按钮」的字段版） |
```

- [ ] **Step 6: 全量门禁 + 提交**

Run: `cd backend && uv run pytest -q` → **468 passed**（465 + 3）

```bash
git add backend/src/aitester/services/run_registry.py backend/src/aitester/main.py backend/tests/test_run_registry.py docs/superpowers/specs/2026-10-04-chat-streaming-design.md
git commit -m "feat(chat): RunRegistry 在途回合注册表 + app.state 装配位"
```

---

### Task 6: `ChatService.prepare` / `stream_turn` / `send` 折叠壳

**Files:**
- Modify: `backend/src/aitester/services/chat.py`（`send` 拆三段：`prepare` + `stream_turn` + `_persist` + `send` 壳；`_complete` 收敛成 echo 专用）
- Modify: `backend/tests/test_chat_service.py:232-243`（`_RecordingService` 从抓 `_complete` 改成抓 `prepare`；**三条用例的断言一字不改**）
- Test: `backend/tests/test_chat_stream.py`（新建）

**Interfaces:**
- Consumes: T3 的 `stream_graph`、T2 的 `RunControl`、T4 的 `memory.save(..., stopped=…)`
- Produces: `aitester.services.chat.PreparedRun`（dataclass，字段 `key/session_id/message/provider/system_prompt/build/tools/memory/messages`）、`ChatService.prepare(session_id, message, agent_id, project_id="") -> PreparedRun`（异常与 detail 与迁移前 `send` 逐字相同）、`ChatService.stream_turn(prepared, control=None) -> Iterator[dict]`（事件 `delta|call|step|draft|done`）、`ChatService.send(...)` 返回值仍含 `reply/trace/model/drafts/steps/session_id/title`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_chat_stream.py`：

```python
"""服务层事件流：守门只有一段、终态才落盘、断开也落截断盘、失败一条不落。"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderError
from aitester.config import Settings
from aitester.memory import FileMemoryStore
from aitester.orchestration import build_agent_graph
from aitester.orchestration.run_control import RUN_CONTROL_KEY, RunControl
from aitester.services import ChatService
from aitester.services.agent_runtime import AgentInstance, AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ModelConfigService
from aitester.services.project_config import ProjectConfigError, ProjectService
from aitester.services.session_store import SessionStore
from aitester.storage import FileJsonConfigRepository
from streaming_fakes import CancelAfterProvider, ChunkedStreamMixin, local_tools


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "reqs"
    root.mkdir()
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    return svc, svc.create(name="订单系统", desc="", dir_=str(root), agents=["case_design"])["id"], root


def _runtime(tmp_path: Path) -> AgentRuntime:
    """真实 AgentRuntime + provider_override：零网络，工具面是真注册表。"""
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "m.json"), Settings(_env_file=None))
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "c.json"), model_config)
    return AgentRuntime(capability, model_config)


def _service(tmp_path: Path, svc_proj, provider=None) -> ChatService:
    return ChatService(provider=provider or MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=SessionStore(tmp_path / "sessions"), projects=svc_proj)


def test_prepare_and_send_guard_share_one_detail(tmp_path, project) -> None:
    """第 2 片消费对称性的直接应用：流式路径不得重写一遍守门文案。"""
    svc_proj, pid, _ = project
    svc = _service(tmp_path, svc_proj)
    with pytest.raises(ProjectConfigError) as a:
        svc.prepare("", "hi", "case_design", "")
    with pytest.raises(ProjectConfigError) as b:
        svc.send("", "hi", "case_design", "")
    assert a.value.detail == b.value.detail == "请先选择项目，再发送消息"


def test_stream_turn_yields_events_then_persists_on_done(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                      sessions=store, projects=svc_proj)
    prepared = svc.prepare("", "生成用例", "case_design", pid)
    events = list(svc.stream_turn(prepared))
    assert events[0]["type"] == "delta"
    done = events[-1]
    assert done["type"] == "done" and done["reply"] == "[mock] 生成用例"
    assert done["steps"] == [] and done["stopped"] is False
    assert done["session_id"] == prepared.session_id
    assert done["title"] == "生成用例"                 # 首条消息建会话，标题取用户第一句
    rows = store.messages(prepared.session_id)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[-1].stopped is False
    assert svc.repo.get(f"session:case_design:{prepared.session_id}")[
        "last_reply"] == "[mock] 生成用例"


def test_control_cancel_marks_done_stopped_and_persists_prefix(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    control = RunControl()
    provider = CancelAfterProvider(AIMessage(content="0123456789"), control, after=1)
    svc = _service(tmp_path, svc_proj, provider=provider)
    prepared = svc.prepare("", "说吧", "case_design", pid)
    done = list(svc.stream_turn(prepared, control=control))[-1]
    assert done["type"] == "done" and done["stopped"] is True
    assert done["reply"] == "0123"                     # 留已生成前缀，标注只进 stopped 字段
    row = store.messages(prepared.session_id)[-1]
    assert row.content == "0123" and row.stopped is True


def test_disconnect_persists_truncated_row(tmp_path, project) -> None:
    """浏览器断开 == 生成器 close()：界面不看了，磁盘仍要留下他看到的那半截。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _service(tmp_path, svc_proj)                # MockProvider："[mock] 生成用例" 共 3 块
    prepared = svc.prepare("", "生成用例", "case_design", pid)
    stream = svc.stream_turn(prepared)
    assert next(stream)["type"] == "delta"            # 只消费第一帧就断开
    stream.close()
    rows = store.messages(prepared.session_id)
    assert rows[-1].role == "assistant"
    assert rows[-1].stopped is True
    assert rows[-1].content == "[moc"                 # 停笔点在第二块的检查位：落的就是第一块


class _Boom(ChunkedStreamMixin):
    name = "boom"
    model_ref = "boom/model"

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Boom":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        raise ProviderError("调用 boom/model 失败: HTTP 401")


def test_stream_failure_persists_nothing(tmp_path, project) -> None:
    """流中 ProviderError 原样上抛（路由转 error 事件），且一条也不落盘——与迁移前一致。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _service(tmp_path, svc_proj, provider=_Boom())
    prepared = svc.prepare("", "hi", "case_design", pid)
    with pytest.raises(ProviderError):
        list(svc.stream_turn(prepared))
    assert store.list("case_design", pid) == []       # 失败发送不留 0 消息幽灵会话


def test_send_fold_keeps_trace_and_model(tmp_path, project) -> None:
    """一次性壳保留 trace/model（SSE 协议不带它们，服务层与既有断言仍要）。"""
    svc_proj, pid, _ = project
    svc = _service(tmp_path, svc_proj)
    result = svc.send("s1", "生成用例", "case_design", pid)
    assert result["trace"] == [
        "services", "context", "orchestration", "adapters", "memory", "storage"]
    assert result["model"] == "mock/mock"
    assert result["reply"] == "[mock] 生成用例"
    assert result["steps"] == [] and result["drafts"] == []
    assert result["session_id"] == "s1" and result["title"] == ""


def test_send_fold_keeps_tool_trace_entries(tmp_path, project) -> None:
    """工具轮的 trace 追加顺序照旧：adapters → tool:<name> → memory → storage。"""
    svc_proj, pid, root = project

    class _Scripted(ChunkedStreamMixin):
        name = "scripted"
        model_ref = "scripted/model"

        def __init__(self, script: list[AIMessage]) -> None:
            self._script = list(script)

        def complete(self, messages: list) -> str:
            return ""

        def bind_tools(self, tools: list) -> "_Scripted":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            return self._script.pop(0)

    provider = _Scripted([
        AIMessage(content="", tool_calls=[{"name": "write", "args": {
            "file_path": "t.txt", "content": "v"}, "id": "c1", "type": "tool_call"}]),
        AIMessage(content="已写入"),
    ])
    runtime = SimpleNamespace(build=lambda agent_id, session_id, provider_override=None, cwd=".":
                             AgentInstance(agent_id=agent_id,
                                           system_prompt=find_agent("case_design").prompt,
                                           provider=provider, tools=local_tools(root),
                                           build_graph=build_agent_graph))
    svc = ChatService(sessions=SessionStore(tmp_path / "sessions"),
                      projects=svc_proj, agent_runtime=runtime)
    result = svc.send("s1", "写文件", "case_design", pid)
    assert result["trace"] == ["services", "context", "orchestration", "adapters",
                               "tool:write", "memory", "storage"]
    assert result["steps"] == [{"tool": "write", "ok": True, "round": 1,
                                "detail": '{"file_path": "t.txt", "content": "v"}'}]


def test_node_receives_control_via_config_injected_by_service(tmp_path, project) -> None:
    """服务层注入的 config 形态真能被节点读到（端到端钉，防 run_registry/节点两侧键名漂移）。"""
    seen: list[bool] = []
    control = RunControl()

    class _Spy(ChunkedStreamMixin):
        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages: list) -> str:
            return ""

        def bind_tools(self, tools: list) -> "_Spy":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            return AIMessage(content="ok")

        def stream_messages(self, messages: list):
            cfg: RunnableConfig = __import__(
                "langgraph.config", fromlist=["get_config"]).get_config()
            seen.append((cfg.get("configurable") or {}).get(RUN_CONTROL_KEY) is control)
            return super().stream_messages(messages)

    svc_proj, pid, _ = project
    svc = _service(tmp_path, svc_proj, provider=_Spy())
    prepared = svc.prepare("", "hi", "case_design", pid)
    list(svc.stream_turn(prepared, control=control))
    assert seen and all(seen)
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_chat_stream.py -q`
Expected: FAIL，`AttributeError: 'ChatService' object has no attribute 'prepare'`。

- [ ] **Step 3: 重写 `services/chat.py`**

整文件替换为（**守卫段的每一行 detail 与判据从原 `send` 原样搬来，一字不改**）：

```python
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from aitester.adapters.llm import LlmProvider, MockProvider, ProviderConfigError
from aitester.adapters.tools.base import AiTooler
from aitester.agents import is_platform_agent
from aitester.context import ContextBuilder, PassthroughContextBuilder
from aitester.memory import InMemoryMemoryStore, MemoryStore
from aitester.orchestration import run_echo, stream_graph
from aitester.orchestration.graph_registry import GraphBuilder
from aitester.orchestration.run_control import RunControl
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.project_config import (
    ProjectConfigError,
    ProjectService,
    dir_exists,
)
from aitester.services.session_store import (
    MISSING_SESSION_DETAIL,
    SessionStore,
    SessionStoreError,
    is_session_id,
)
from aitester.storage import InMemoryRepository, Repository

SYSTEM_PROMPT = "你是 AiTester 测试智能体（骨架占位）。"

# 只截 prompt，磁盘保留全量：不截则真实模型下长会话每轮 token 线性上涨（spec 裁定 6）
HISTORY_MAX = 40

logger = logging.getLogger(__name__)

# done/step 事件里喂给 UI 与磁盘的过程块字段：严格取键，缺字段即 KeyError（不兜默认防假绿）
_STEP_KEYS = ("tool", "ok", "round", "detail")


@dataclass(frozen=True)
class PreparedRun:
    """守门已过、装配已完成的回合：路由拿到它才允许开始推流。

    字段一次给齐（含已拼好的 messages），流开始后不再有任何 4xx 判据。
    """

    key: str                      # 记忆键 f"{agent_id}:{session_id}"
    session_id: str
    message: str
    provider: LlmProvider
    system_prompt: str
    build: GraphBuilder
    tools: list[AiTooler]
    memory: MemoryStore
    messages: list[BaseMessage]


class ChatService:
    """业务门面：装配四层并把一次回合拆成「守门 → 事件流 → 终态落盘」。"""

    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
        agent_runtime: AgentRuntime | None = None,
        sessions: SessionStore | None = None,
        projects: ProjectService | None = None,
    ) -> None:
        self.provider = provider
        self.agent_runtime = agent_runtime
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()
        self.sessions = sessions
        self.projects = projects

    def _complete(
        self,
        key: str,
        message: str,
        provider: LlmProvider,
        system_prompt: str,
    ) -> dict[str, Any]:
        """echo 专用的一次性回合：七层 trace 是它的验收物，故与 send 路径分开留。"""
        trace: list[str] = ["services", "context", "orchestration", "adapters"]
        history = self.memory.recall(key)[-HISTORY_MAX:]
        messages = self.context.build(system_prompt, history, message)
        reply = run_echo(provider, messages)

        self.memory.save(key, "user", message)
        self.memory.save(key, "assistant", reply)
        trace.append("memory")
        self.repo.put(f"session:{key}", {"session_id": key, "last_reply": reply})
        trace.append("storage")
        return {"reply": reply, "trace": trace, "model": provider.model_ref}

    def echo(self, session_id: str, message: str) -> dict[str, Any]:
        """七层 trace 回归链路：永远走 mock，与真实 provider 配置无关。"""
        result = self._complete(session_id, message, MockProvider(), SYSTEM_PROMPT)
        return {"reply": result["reply"], "trace": result["trace"]}

    def prepare(
        self, session_id: str, message: str, agent_id: str, project_id: str = ""
    ) -> PreparedRun:
        """守门 + 装配 + 记忆选择 + 上下文拼装：全部会以 4xx 结束的段落只在这里存在一份。

        项目维度（第 2 片）：可见智能体的会话必须属于一个项目，落点取项目 dir；
        平台功能智能体不属于项目，project_id 一律忽略（spec 裁定 7，/kb 链路依赖此）。
        """
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        platform = is_platform_agent(agent_id)
        project: dict[str, Any] | None = None
        pid = ""
        if not platform:
            pid = (project_id or "").strip()
            if not pid:
                raise ProjectConfigError("请先选择项目，再发送消息")
            if self.projects is None:
                raise ProjectConfigError("服务未装配项目配置，请通过 create_app 启动后端")
            project = self.projects.get(pid)          # 未知项目 → ConfigNotFoundError → 路由 404
            # 复用项目页读侧同一只探测（裁定 3）：展开 ~、绝不 mkdir、吞 (OSError, ValueError)，
            # 畸形 dir（NUL 走 ValueError）在此同样答「不可达」→ 中文 400，绝不外泄成 500
            if not dir_exists(project["dir"]):
                raise ProjectConfigError(
                    f"项目「{project['name']}」的目录 {project['dir']} 不存在或不可访问，"
                    "请到项目页确认路径"
                )
        sid = (session_id or "").strip()
        if not sid:
            if self.sessions is None or platform:
                raise ProviderConfigError("请指定会话 id 或通过 create_app 装配会话存储")
            sid = self.sessions.new_id()
        elif self.sessions is not None and is_session_id(sid):
            existing = self.sessions.get(sid)
            if existing is None:
                raise SessionStoreError(MISSING_SESSION_DETAIL)
            # 会话归属校验：sess_* 续写前先判等 agent_id，否则任何 agent_id 都能往别人的会话里写；
            # 项目维度不进键（第 2 片裁定 2），归属就靠这两维判等，detail 经路由 SessionStoreError→404 落到用户
            if existing.agent_id != agent_id:
                raise SessionStoreError("会话不属于该智能体")
            # 第二维（第 2 片）：项目归属同判据；平台智能体不落的会话不参与比对
            if not platform and existing.project_id != pid:
                raise SessionStoreError("会话不属于该项目")

        # 装配落点：可见智能体用项目 dir（产出物归位），平台智能体沿用 _build_platform_agent 内部算的 workspace
        instance = self.agent_runtime.build(
            agent_id,
            sid,
            provider_override=self.provider,
            # 验真与落点必须认同一个展开结果，否则 `~` 项目会被 `mkdir` 建成字面 `~` 目录树
            # （dir_exists 先 expanduser 才答「可达」，fs_tool._resolve 却从不展 `~`）：只在这里展开，
            # 落盘数据与 UI 仍是用户输入的原始形态，无迁移
            cwd=str(Path(project["dir"]).expanduser()) if project is not None else ".",
        )

        use_file = self.sessions is not None and is_session_id(sid) and not platform
        memory: MemoryStore
        if use_file:
            # 延迟导入：file_memory 经 services 回指本包，顶层导入会在循环链上炸开（memory/__init__ 顺序约束同源）
            from aitester.memory import FileMemoryStore

            memory = FileMemoryStore(self.sessions, pid)
        else:
            memory = self.memory

        key = f"{instance.agent_id}:{sid}"
        history = memory.recall(key)[-HISTORY_MAX:]
        messages = _to_langchain_messages(
            self.context.build(instance.system_prompt, history, message)
        )
        return PreparedRun(
            key=key,
            session_id=sid,
            message=message,
            provider=instance.provider,
            system_prompt=instance.system_prompt,
            build=instance.build_graph,
            tools=instance.tools,
            memory=memory,
            messages=messages,
        )

    def _persist(
        self, prepared: PreparedRun, reply: str, steps: list[dict[str, Any]], stopped: bool
    ) -> None:
        """终态落盘：用户句 + assistant 句（带 steps/stopped）+ 仓储快照，顺序与迁移前一致。"""
        prepared.memory.save(prepared.key, "user", prepared.message)
        prepared.memory.save(prepared.key, "assistant", reply, steps=steps, stopped=stopped)
        self.repo.put(f"session:{prepared.key}",
                      {"session_id": prepared.key, "last_reply": reply})

    def stream_turn(
        self, prepared: PreparedRun, control: RunControl | None = None
    ) -> Iterator[dict[str, Any]]:
        """事件流 + 终态落盘：done 之前一条也不写盘（失败不留痕，与迁移前同口径）。

        消费者断开（GeneratorExit，等价于关页/刷新）是唯一「无人消费也要落盘」的情形：
        置取消位后把内层图跑到停笔点，只为拿到 finish 把截断内容标 stopped 落下去——
        用户重开会话看到的正是他被打断的那一版。
        """
        events = stream_graph(
            prepared.build, prepared.provider, prepared.tools, prepared.messages, control=control
        )
        steps: list[dict[str, Any]] = []
        outcome: dict[str, Any] | None = None
        try:
            for event in events:
                if event["type"] == "finish":
                    outcome = event
                    continue
                if event["type"] == "step":
                    steps.append({k: event[k] for k in _STEP_KEYS})
                yield event
            if outcome is None:
                # 图没产出 finish 属装配缺陷，但界面不能永久挂在 busy 上：按空回合收尾
                outcome = {"reply": "", "tool_traces": [], "drafts": [], "stopped": True}
            reply = str(outcome["reply"])
            stopped = bool(outcome["stopped"])
            self._persist(prepared, reply, steps, stopped)
            stored = (
                self.sessions.get(prepared.session_id)
                if self.sessions is not None else None
            )
            yield {
                "type": "done",
                "reply": reply,
                "steps": steps,
                "session_id": prepared.session_id,
                "title": stored.title if stored is not None else "",
                "stopped": stopped,
            }
        except GeneratorExit:
            if control is not None:
                control.cancel()
            try:
                for event in events:      # 无人消费也要跑到停笔点，只为拿到 finish
                    if event["type"] == "step":
                        steps.append({k: event[k] for k in _STEP_KEYS})
                    elif event["type"] == "finish":
                        outcome = event
            except Exception:             # 收尾路径的失败绝不能盖掉原始断开
                logger.warning("断开收尾时图未跑完，本次不落截断盘", exc_info=True)
            if outcome is not None:
                self._persist(prepared, str(outcome["reply"]), steps, stopped=True)
            raise

    def send(
        self, session_id: str, message: str, agent_id: str, project_id: str = ""
    ) -> dict[str, Any]:
        """一次性折返壳：prepare + 事件流折回迁移前的响应形状。

        留着它的两个理由：既有 22 处 `svc.send(` 用例是真实链路的回归锁；`trace`/`model`
        两字段只在服务层存活（SSE 协议不带它们，spec 偏离登记 3）。
        """
        prepared = self.prepare(session_id, message, agent_id, project_id)
        reply = ""
        sid = prepared.session_id
        title = ""
        steps: list[dict[str, Any]] = []
        drafts: list[dict[str, Any]] = []
        for event in self.stream_turn(prepared):
            kind = event["type"]
            if kind == "step":
                steps.append({k: event[k] for k in _STEP_KEYS})
            elif kind == "draft":
                drafts.append(event["draft"])
            elif kind == "done":
                reply = event["reply"]
                sid = event["session_id"]
                title = event["title"]
        # trace 与迁移前逐字同序：services/context/orchestration/adapters → tool:* → memory → storage
        trace = ["services", "context", "orchestration", "adapters"]
        trace += [f"tool:{s['tool']}" for s in steps]
        trace += ["memory", "storage"]
        return {
            "reply": reply,
            "trace": trace,
            "model": prepared.provider.model_ref,
            "drafts": drafts,
            "steps": steps,
            "session_id": sid,
            "title": title,
        }


def _to_langchain_messages(messages: list[dict[str, str]]) -> list:
    result = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result
```

> 删掉的旧物：`run_graph` 的 import 与调用、`_complete` 的 `build/tools/memory` 形参与工具分支。`json` 若最终未用请一并从 import 去掉（ruff 会报）。

- [ ] **Step 4: 迁 `_RecordingService`（断言不动）**

`backend/tests/test_chat_service.py:232-243` 替换为：

```python
class _RecordingService(ChatService):
    """抓 prepare 产出的 memory 实例：装配裁定（文件/进程内）只能在此处验。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seen_memory: list[object] = []

    def prepare(self, session_id: str, message: str, agent_id: str,
                project_id: str = ""):
        prepared = super().prepare(session_id, message, agent_id, project_id)
        self.seen_memory.append(prepared.memory)
        return prepared
```

三条消费用例（`test_send_with_empty_session_id_generates_sess_id`、`test_send_with_temporary_key_stays_in_memory`、`test_platform_agent_never_uses_file_memory`）与它们的 `svc.seen_memory[0]` 断言一字不改。

- [ ] **Step 5: 跑到通过 + 全量门禁**

Run: `cd backend && uv run pytest tests/test_chat_stream.py tests/test_chat_service.py -q` → 全绿（`test_chat_service.py` 23 条一字未改）
Run: `cd backend && uv run pytest -q` → **475 passed**（468 + 7）

- [ ] **Step 6: 提交**

```bash
git add backend/src/aitester/services/chat.py backend/tests/test_chat_stream.py backend/tests/test_chat_service.py
git commit -m "feat(chat): prepare/stream_turn 分段——守门只留一份，事件流终态落盘、断开落截断盘"
```

---

### Task 7: SSE 路由、`/chat/stop`、删一次性 `/chat/send` 与 HTTP 用例迁移

**Files:**
- Modify: `backend/src/aitester/interaction/router.py:1-92`
- Modify: `backend/src/aitester/interaction/schemas.py:42-50`（删 `SendResponse`，加停止收发两型）
- Modify: `backend/tests/test_api.py`（6 处调用点）
- Modify: `backend/tests/test_api_chat_sessions.py`（6 处调用点 + `_FakeGraph`）
- Modify: `backend/tests/test_kb_api.py`（3 处调用点）
- Test: `backend/tests/test_chat_stream_api.py`（新建）

**Interfaces:**
- Consumes: T6 的 `service.prepare/stream_turn`、T5 的 `app.state.run_registry`、T4 的 `stopped`
- Produces: `POST /api/chat/send/stream` → `text/event-stream`，帧形 `event: <type>\ndata: <单行 JSON>\n\n`，事件 `start|delta|call|step|draft|done|error`；`POST /api/chat/stop` → `{ok:true}` / 404 `这条回答已经结束`；`/api/chat/send` 不再注册

- [ ] **Step 1: 先量一条事实——未注册路径给的是 404，不是 405**

Run: `cd backend && uv run python -c "from fastapi.testclient import TestClient; from aitester.main import create_app; import tempfile, pathlib; d=pathlib.Path(tempfile.mkdtemp()); c=TestClient(create_app(model_config_path=d/'m.json', capability_config_path=d/'c.json', projects_path=d/'p.json', sessions_dir=d/'s')); print(c.post('/api/chat/send-legacy', json={'message':'x'}).status_code)"`
Expected: `404`。→ 本期「没有一次性口」的锁只能断 404（`/api/chat/send` 这条路径上删除后没有任何方法注册；第 3 片的 405 锁之所以是 405，是因为同路径上还有 GET/PUT）。Step 6 按 405 补登记。

- [ ] **Step 2: 写失败测试（新契约）**

新建 `backend/tests/test_chat_stream_api.py`：

```python
"""SSE 传输层契约：帧序列、守门仍在流前、流中失败走事件、stop 命中在途 run。

注意：starlette TestClient 会把响应体整段缓冲后才交出来（实测每帧到达时刻相同），
所以这里只断「帧的顺序与内容」，不断节奏——逐字观感属真机走查。
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService
from streaming_fakes import ChunkedStreamMixin


class _NoopKbManager:
    kb_root_dir = "kb-root"

    def start(self):
        pass

    def close_all(self, timeout: float = 30.0):
        pass

    async def run_job(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})

    def run_job_sync(self, name, *, project_id="default", agent_id="console", **kwargs):
        return SimpleNamespace(success=True, answer="ok", metadata={})


def _app(tmp_path: Path, provider=None):
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )
    if provider is not None:
        application.state.chat_service = ChatService(
            provider=provider,
            agent_runtime=application.state.agent_runtime,
            sessions=application.state.sessions,
            projects=application.state.project_config,
        )
    return application


def _pid(application, tmp_path: Path) -> str:
    root = tmp_path / "reqs"
    root.mkdir(exist_ok=True)
    return application.state.project_config.create(
        name="订单系统", desc="", dir_=str(root), agents=["case_design"])["id"]


def _frames(resp) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    event = ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line.split(":", 1)[1].strip())))
    return out


def _stream(client: TestClient, payload: dict) -> list[tuple[str, dict]]:
    with client.stream("POST", "/api/chat/send/stream", json=payload) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        return _frames(resp)


def test_stream_event_sequence_and_single_terminal(tmp_path: Path) -> None:
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    events = _stream(client, {"message": "生成用例", "agent_id": "case_design",
                              "project_id": pid})
    kinds = [e for e, _ in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"                      # 唯一终态
    assert kinds.count("done") == 1 and "error" not in kinds
    start = dict(events[0][1])
    assert start["session_id"].startswith("sess_")
    assert len(start["run_id"]) == 32               # uuid4().hex：停止要用它
    assert "".join(d["text"] for e, d in events if e == "delta") == "[mock] 生成用例"
    done = events[-1][1]
    assert done["reply"] == "[mock] 生成用例"
    assert done["steps"] == [] and done["stopped"] is False
    assert done["title"] == "生成用例"
    sid = done["session_id"]
    rows = client.get(f"/api/chat/sessions/{sid}/messages").json()["messages"]
    assert [r["role"] for r in rows] == ["user", "assistant"]


def test_guard_still_plain_http_with_zero_frames(tmp_path: Path) -> None:
    """守门未过 = 普通 4xx 且一帧都不发：前端沿用既有 ApiError→toast 路径，detail 逐字不变。"""
    application = _app(tmp_path)
    client = TestClient(application)
    r = client.post("/api/chat/send/stream", json={"message": "hi", "agent_id": "case_design"})
    assert r.status_code == 400
    assert r.json()["detail"] == "请先选择项目，再发送消息"
    pid = _pid(application, tmp_path)
    r2 = client.post("/api/chat/send/stream",
                     json={"session_id": "sess_deadbeef", "message": "hi",
                           "agent_id": "case_design", "project_id": pid})
    assert r2.status_code == 404
    assert r2.json()["detail"] == "会话不存在或已被删除"


class _Boom(ChunkedStreamMixin):
    name = "fake"
    model_ref = "fake/model-x"

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Boom":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        raise RuntimeError("调用 fake/model-x 失败: HTTP 401")

    def stream_messages(self, messages: list):
        raise RuntimeError("调用 fake/model-x 失败: HTTP 401")


def test_provider_failure_travels_as_error_event(tmp_path: Path) -> None:
    """流开始后 HTTP 已是 200，失败只能走 error 事件（迁移前这里是 502）。"""
    application = _app(tmp_path, provider=_Boom())
    pid = _pid(application, tmp_path)
    events = _stream(TestClient(application),
                     {"message": "hi", "agent_id": "case_design", "project_id": pid})
    kinds = [e for e, _ in events]
    assert kinds[-1] == "error"
    assert "fake/model-x" in events[-1][1]["detail"]
    # 失败不落盘：一条也不写（与迁移前同口径）
    assert TestClient(application).get(
        "/api/chat/sessions", params={"agent_id": "case_design", "project_id": pid}
    ).json() == {"sessions": []}


class _Slow(ChunkedStreamMixin):
    """每块之间睡 50ms × 40 块：给 stop 一个两秒的在途窗口，够慢机也来得及。"""

    name = "slow"
    model_ref = "slow/model"

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Slow":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        return AIMessage(content="x" * 160)

    def stream_messages(self, messages: list):
        for chunk in chunks_from_text("x" * 160):
            time.sleep(0.05)
            yield chunk


def chunks_from_text(text: str):
    from langchain_core.messages import AIMessageChunk
    for i in range(0, len(text), 4):
        yield AIMessageChunk(content=text[i:i + 4])


def test_stop_hits_an_in_flight_run(tmp_path: Path) -> None:
    application = _app(tmp_path, provider=_Slow())
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    with client.stream("POST", "/api/chat/send/stream",
                       json={"message": "慢一点", "agent_id": "case_design",
                             "project_id": pid}) as resp:
        assert resp.status_code == 200
        line_event, start = "", {}
        for line in resp.iter_lines():        # 只读到 start 帧就发停止
            if line.startswith("event:"):
                line_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                start = json.loads(line.split(":", 1)[1].strip())
                break
        assert line_event == "start"
        r = client.post("/api/chat/stop", json={"run_id": start["run_id"]})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        rest = _frames(resp)
    assert rest[-1][0] == "done"
    assert rest[-1][1]["stopped"] is True     # 停止走 done{stopped:true}，不是第三条终态


def test_stop_unknown_run_returns_404(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    r = client.post("/api/chat/stop", json={"run_id": "0" * 32})
    assert r.status_code == 404
    assert r.json()["detail"] == "这条回答已经结束"


def test_one_shot_send_endpoint_is_gone(tmp_path: Path) -> None:
    """裁定 1 的接口锁：一次性口整体不存在（Step 1 实测未注册路径回 404 而非 405）。"""
    client = TestClient(_app(tmp_path))
    assert client.post("/api/chat/send", json={"message": "hi"}).status_code == 404
```

- [ ] **Step 3: 跑到失败**

Run: `cd backend && uv run pytest tests/test_chat_stream_api.py -q`
Expected: FAIL — 第一条即 `/api/chat/send/stream` 404（新端点还没注册）。

- [ ] **Step 4: schema 收口**

`backend/src/aitester/interaction/schemas.py`：删除 `SendResponse` 整个类（`:42-50`），在 `SendRequest` 之后插入：

```python
class StreamStopRequest(BaseModel):
    # start 事件里下发的 uuid4().hex；已结束或不存在的 run 一律 404
    run_id: str


class StreamStopResponse(BaseModel):
    ok: bool
```

同文件 `KbDraft`、`StepInfo` 不动（SSE 载荷仍用它们校验）。

- [ ] **Step 5: 路由换传输**

`backend/src/aitester/interaction/router.py`：import 段补

```python
import json
import logging
from typing import Any, Iterator

from fastapi.responses import StreamingResponse

from aitester.interaction.schemas import (
    ..., StreamStopRequest, StreamStopResponse, ...
)   # 从 import 列表里去掉 SendResponse
from aitester.services.chat import PreparedRun
from aitester.services.run_registry import new_run_id
```

模块级加：

```python
logger = logging.getLogger(__name__)

# 流开始前 = HTTP，流开始后 = error 事件（spec 错误口径表）
_GUARD_MAP = (
    (ConfigNotFoundError, 404),
    (ProjectConfigError, 400),
    (SessionStoreError, 404),
    (ProviderConfigError, 400),
)
```

删除整个 `chat_send` 端点（`@router.post("/chat/send", response_model=SendResponse)` 到 `return SendResponse(...)`），替换为：

```python
def _frame(event: str, data: dict[str, Any]) -> str:
    # data 必须单行：SSE 按行切帧，负载里的裸换行会把一帧撕成两帧
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _draft_frame(artifact: Any) -> str | None:
    """草案逐条容错（终审项 5 原口径）：畸形草案丢一条，不砸整条流。"""
    try:
        draft = KbDraft.model_validate(artifact)
    except ValidationError:
        logger.warning("丢弃畸形草案：%r", artifact)
        return None
    return _frame("draft", {"draft": draft.model_dump()})


def _step_payload(step: dict[str, Any]) -> dict[str, Any]:
    # steps 严格构造、不做逐条容错：事件恒为本轮 stream_graph 新产物，畸形即装配 bug，须响亮失败
    return StepInfo(tool=step["tool"], ok=step["ok"], round=step["round"],
                    detail=step["detail"]).model_dump()


@router.post("/chat/send/stream")
def chat_send_stream(req: SendRequest, request: Request) -> StreamingResponse:
    """真实链路的唯一传输：守门同步跑，过后逐事件推流，终态恒为一条 done 或一条 error。"""
    service: ChatService = request.app.state.chat_service
    runs = request.app.state.run_registry
    try:
        prepared = service.prepare(req.session_id, req.message, req.agent_id, req.project_id)
    except ProviderError as exc:
        # ProviderConfigError 是 ProviderError 子类：400 文案与迁移前逐字相同
        for klass, code in _GUARD_MAP:
            if isinstance(exc, klass):
                raise HTTPException(status_code=code, detail=exc.detail) from exc
        raise HTTPException(status_code=502, detail=exc.detail) from exc

    run_id = new_run_id()
    control = runs.start(run_id)

    def frames() -> Iterator[str]:
        try:
            yield _frame("start", {"run_id": run_id, "session_id": prepared.session_id})
            try:
                for event in service.stream_turn(prepared, control=control):
                    kind = event["type"]
                    if kind == "draft":
                        frame = _draft_frame(event["draft"])
                        if frame is not None:
                            yield frame
                    elif kind == "step":
                        yield _frame(kind, _step_payload(event))
                    elif kind == "done":
                        yield _frame(kind, {
                            "reply": event["reply"],
                            "steps": [_step_payload(s) for s in event["steps"]],
                            "session_id": event["session_id"],
                            "title": event["title"],
                            "stopped": event["stopped"],
                        })
                    else:                                   # delta / call
                        yield _frame(kind, {k: v for k, v in event.items() if k != "type"})
            except ProviderError as exc:
                # 流中失败：HTTP 已经 200，只能走事件；detail 原样（key 已在 provider 侧打星）
                yield _frame("error", {"detail": exc.detail})
        finally:
            runs.finish(run_id)

    return StreamingResponse(frames(), media_type="text/event-stream")


@router.post("/chat/stop", response_model=StreamStopResponse)
def chat_stop(req: StreamStopRequest, request: Request) -> StreamStopResponse:
    """服务端真停：置取消位；节点在下一个检查点停笔，截断内容照落盘。"""
    if not request.app.state.run_registry.cancel(req.run_id):
        raise HTTPException(status_code=404, detail="这条回答已经结束")
    return StreamStopResponse(ok=True)
```

> `_GUARD_MAP` 的写法保持了迁移前五条 except 分支的码值对应（`ConfigNotFoundError`→404、`ProjectConfigError`→400、`SessionStoreError`→404、`ProviderConfigError`→400），并把「其他 `ProviderError`」留成 502——`prepare` 实际只抛这四类，502 分支是同一映射的收尾，不新增判据。

- [ ] **Step 6: 迁 15 处既有 HTTP 调用点**（实测 `grep -rn "api/chat/send" backend/tests`：`test_api.py` 6 + `test_api_chat_sessions.py` 6 + `test_kb_api.py` 3。spec 测试策略里的「22 处」数的是服务层 `svc.send(` 用例，那是另一批，本任务不改）

`tests/test_api.py`：文件顶部加共用小助手（放在 `ALL_LAYERS` 之后）：

```python
def _stream_reply(client, payload: dict) -> dict:
    """POST /api/chat/send/stream 折成「终态载荷 dict」：迁移期让既有断言只改一行。"""
    import json as _json

    with client.stream("POST", "/api/chat/send/stream", json=payload) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        done: dict = {}
        deltas: list[str] = []
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = _json.loads(line.split(":", 1)[1].strip())
            if line.startswith("data:") and "text" in data and "round" in data:
                deltas.append(data["text"])
            elif "reply" in data:
                done = data
        done["deltas"] = deltas
        return done
```

逐处替换（断言语义不变，只换调用方式）：
- `test_send_uses_injected_provider_and_reports_model`（`:94-102`）：整段 `resp = ... / body = resp.json() / assert body["reply"]...` 替换为

```python
    body = _stream_reply(TestClient(application),
                         {"session_id": "s2", "message": "生成用例", "project_id": pid})
    assert body["reply"] == "[mock] 生成用例"
    # trace/model 随一次性端点一起消失（spec 裁定 1 与偏离登记 3）：
    # 七层穿透的验收物仍由 echo 用例锁（test_chat_echo_traverses_all_seven_layers）
    assert body["deltas"] and "".join(body["deltas"]) == "[mock] 生成用例"
```

- `test_send_with_unknown_agent_returns_404`、`test_send_with_legacy_agent_id_returns_404`、`test_send_without_configured_default_returns_400`：把 `client.post("/api/chat/send", json=…)` 改成 `client.post("/api/chat/send/stream", json=…)`，其余断言（404/404/400 + detail 文案）一字不改——守门仍在流前，这两条本就断的是普通 HTTP。
- `test_send_uses_agent_prompt_and_default_agent_id`（`:182-188`）：`resp` 两行改成 `assert _stream_reply(TestClient(application), {"session_id": "s4", "message": "生成登录用例", "project_id": pid})["reply"] == "[spy] 收到"`，`seen` 断言保留。
- `test_send_upstream_failure_returns_502`（`:200-232`）：函数改名 `test_send_upstream_failure_travels_as_error_event`，断言改成「200 流 + 末帧 detail 含 `fake/model-x`」：

```python
    with TestClient(application).stream(
        "POST", "/api/chat/send/stream", json={"message": "hi", "project_id": pid}
    ) as resp:
        assert resp.status_code == 200          # 流已开始，失败只能走事件
        detail = [json.loads(line.split(":", 1)[1].strip())["detail"]
                  for line in resp.iter_lines() if line.startswith("data: ")][-1]
    assert "fake/model-x" in detail
```

`tests/test_api_chat_sessions.py`：
- `:163`、`:179`、`:191`、`:232`、`:240`、`:278` 六处 URL 从 `/api/chat/send` 改 `/api/chat/send/stream`，并用同一个 `_stream_reply` 思路取终态（该文件顶部自带一份小助手 `_send_body(client, payload)`，返回 done 载荷 dict；`body["session_id"]/["title"]/["steps"]` 断言全部原样保留）。
- 404 的两处（`:191` 不存在会话、`:232` 跨智能体）继续断普通 HTTP 状态码与 detail，一字不改。
- `test_send_returns_nonempty_steps_at_http_level` 的 `_FakeGraph`（`:255-262`）改成事件协议的假图——这条用例从「锁 invoke」变成「锁 stream 折返」，正是本片要的迁移：

```python
    class _FakeGraph:
        def stream(self, state, *, config=None, stream_mode=None):
            yield ("custom", {"type": "turn", "round": 1, "text": "", "stopped": False,
                              "tool_calls": [{"id": "c1", "name": "read",
                                              "args": {"path": "a.md"}}]})
            yield ("updates", {"tools": {"messages": [
                ToolMessage(content="内容", tool_call_id="c1", name="read",
                            status="success")]}})
            yield ("custom", {"type": "turn", "round": 2, "text": "完成",
                              "stopped": False, "tool_calls": []})
```

并把该用例的 `client.post("/api/chat/send", …)` 换成读流到 `done`，`body["steps"] == [{"tool": "read", "ok": True, "round": 1, "detail": '{"path": "a.md"}'}]` 这条断言保持原样。

`tests/test_kb_api.py`：三条草案用例的 `SimpleNamespace(send=…)` 替身改成「prepare + stream_turn」两段替身，例：

```python
def test_chat_send_returns_drafts(tmp_path):
    app = create_app(
        model_config_path=tmp_path / "m.json", capability_config_path=tmp_path / "c.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None), kb_manager=_RecordingKbManager())
    draft = {"op": "create", "path": "a.md", "abs_display": "P", "summary": "s",
             "content": "c", "base": None, "mtime": 0}
    app.state.chat_service = SimpleNamespace(
        prepare=lambda sid, msg, aid, project_id="": SimpleNamespace(session_id="s9"),
        stream_turn=lambda prepared, control=None: iter([
            {"type": "draft", "draft": draft},
            {"type": "done", "reply": "r", "steps": [], "session_id": "s9",
             "title": "", "stopped": False},
        ]),
    )
    with TestClient(app) as c:
        with c.stream("POST", "/api/chat/send/stream", json={"message": "写点什么"}) as r:
            events = _kb_frames(r)          # 本文件顶部的 (事件名, 载荷) 解析助手
    assert events[-1][0] == "done"
    assert [e for e, _ in events].count("draft") == 1
    assert events[1][1]["draft"]["path"] == "a.md"
```

另两条同构改写：`drafts_defaults_empty` 变成「流里没有 draft 事件」（`assert "draft" not in [e for e, _ in events]`）；`skips_malformed_drafts` 变成「`stream_turn` 依次产出 `[draft(畸形), draft(合法), done]`，事件表只剩一条合法草案且 `done.reply == "回复还在"`」——草案逐条容错与「不砸整条流」两条口径原样迁到流上。

- [ ] **Step 7: 跑到通过 + 全量门禁**

Run: `cd backend && uv run pytest tests/test_chat_stream_api.py -q` → 7 passed
Run: `cd backend && uv run pytest -q` → **482 passed**（= T6 收尾 475 + 新增 7；既有 HTTP 用例只换传输方式、不改断言语义，故不减不加）
> 若总数与此不符：先数清「新增/改名」条数并在提交信息里写明差额来源，不许为凑数删用例。

- [ ] **Step 8: 补 spec 偏离登记**

`docs/superpowers/specs/2026-10-04-chat-streaming-design.md` 偏离登记表末追加：

```markdown
| 10 | 测试 | 一次性口的锁是 404（不是测试策略写的 405） | Step 1 实测：删除后 `/api/chat/send` 这条路径上没有任何方法注册，FastAPI 对未注册路径回 404；第 3 片能锁 405 是因为同路径还有 GET/PUT |
```

- [ ] **Step 9: 提交**

```bash
git add backend/src/aitester/interaction backend/tests/test_chat_stream_api.py backend/tests/test_api.py backend/tests/test_api_chat_sessions.py backend/tests/test_kb_api.py docs/superpowers/specs/2026-10-04-chat-streaming-design.md
git commit -m "feat(api): SSE 传输端点 /chat/send/stream 与 /chat/stop——删一次性 /chat/send"
```

---

### Task 8: 前端传输层——SSE 解析 + 事件折叠纯函数

**Files:**
- Modify: `frontend/src/api/client.ts`（在 `SendResponse`/`chatSend` 之后新增流式接口；`ChatMessage` 加可选 `stopped`）
- Create: `frontend/src/pages/chat/streamState.ts`

**Interfaces:**
- Consumes: T7 的 SSE 帧格式 `event: <type>\ndata: <单行 JSON>\n\n` 与 7 类事件（spec 事件表）；本文件既有的 `ApiError`、`JSON_HEADERS`、`KbDraft`、`ChatStep`
- Produces（T9/T10 逐字按此消费）:
  - `type StreamEvent`（判别字段是 `type`，值域 `start|delta|call|step|draft|done|error`）
  - `interface StreamBody { session_id: string; message: string; agent_id: string; project_id: string }`
  - `chatSendStream(body: StreamBody, onEvent: (ev: StreamEvent) => void, signal?: AbortSignal): Promise<void>`
  - `chatStop(runId: string): Promise<{ ok: boolean }>`
  - `newStreamState(): StreamingState` / `applyEvent(state: StreamingState, ev: StreamEvent): StreamingState` / `finalize(state: StreamingState, done: DoneEvent): FinalizedTurn` / `liveText(state: StreamingState | null): string` / `isWaiting(state: StreamingState | null): boolean`
  - `interface FinalizedTurn { content: string; steps: ChatStep[]; drafts: KbDraft[]; sessionId: string; title: string; stopped: boolean }`

> **为什么删 `chatSend`/`SendResponse` 不在本任务**：本任务门禁是 `npm run build`，而 `ChatPage.tsx` 与 `KbPage.tsx` 此刻还在调 `chatSend`——先删就红。删除动作放在 T10（最后一个消费方迁走的那一步），与既有「每任务收尾门禁必绿」的约定同构。本任务只新增，不删不改既有导出。

- [ ] **Step 1: 写事件折叠纯函数**

新建 `frontend/src/pages/chat/streamState.ts`（无 React、无 DOM，只为让折叠规则能一眼读死）：

```ts
import type { ChatStep, KbDraft, StreamEvent } from "../../api/client";

/** done 事件的类型别名：finalize 只吃终态，签名写死比 Extract 更好读。 */
export type DoneEvent = Extract<StreamEvent, { type: "done" }>;

/** 一轮 agent 输出的 live 累积。toolCalled 由该轮的 call 事件置真，
 *  它是「这一轮不是最终答复」的唯一判据（中间轮文本靠它折进过程块）。 */
export interface LiveRound { round: number; text: string; toolCalled: boolean }

/** 已宣告但结果未回的调用：过程块里的 ⏳ 行。 */
export interface PendingCall { key: string; tool: string; round: number; detail: string }

export interface StreamingState {
  runId: string;
  sessionId: string;
  title: string;
  rounds: LiveRound[];
  pending: PendingCall[];
  steps: ChatStep[];
  drafts: KbDraft[];
  stopped: boolean;
  terminal: boolean;     // done/error 已收到：之后的事件一律忽略（终态恒为一条）
  done: DoneEvent | null; // 终态事件本身随状态走——页面不得另设闭包局部变量接终态（TS 对闭包内赋值的收窄不可靠）
  fail: string;          // error 事件的 detail；HTTP 层失败由调用方自己写进界面，不进这里
}

export function newStreamState(): StreamingState {
  return { runId: "", sessionId: "", title: "", rounds: [], pending: [], steps: [], drafts: [],
    stopped: false, terminal: false, done: null, fail: "" };
}

const callKey = (round: number, tool: string) => `${round}::${tool}`;

/** 逐事件折叠，返回新对象（React state 直用；同 key 重复调用按 FIFO 摘第一个，
 *  ToolNode 回 ToolMessage 的顺序与 tool_calls 一致，故同名同轮也不会错配）。 */
export function applyEvent(state: StreamingState, ev: StreamEvent): StreamingState {
  if (state.terminal) return state;
  switch (ev.type) {
    case "start":
      return { ...state, runId: ev.run_id, sessionId: ev.session_id || state.sessionId };
    case "delta": {
      const at = state.rounds.findIndex((r) => r.round === ev.round);
      const rounds = at < 0
        ? [...state.rounds, { round: ev.round, text: ev.text, toolCalled: false }]
        : state.rounds.map((r, i) => (i === at ? { ...r, text: r.text + ev.text } : r));
      return { ...state, rounds };
    }
    case "call": {
      const key = callKey(ev.round, ev.tool);
      return {
        ...state,
        rounds: state.rounds.map((r) => (r.round === ev.round ? { ...r, toolCalled: true } : r)),
        pending: [...state.pending, { key, tool: ev.tool, round: ev.round, detail: ev.detail }],
      };
    }
    case "step": {
      const key = callKey(ev.round, ev.tool);
      const at = state.pending.findIndex((p) => p.key === key);
      return {
        ...state,
        pending: at < 0 ? state.pending : state.pending.filter((_, i) => i !== at),
        steps: [...state.steps, { tool: ev.tool, ok: ev.ok, round: ev.round, detail: ev.detail }],
      };
    }
    case "draft":
      return { ...state, drafts: [...state.drafts, ev.draft] };
    case "done":
      return { ...state, terminal: true, done: ev, stopped: ev.stopped,
        sessionId: ev.session_id || state.sessionId, title: ev.title || state.title };
    case "error":
      return { ...state, terminal: true, fail: ev.detail };   // detail 随状态回给调用方，界面按失败撤气泡
  }
}

export interface FinalizedTurn {
  content: string;
  steps: ChatStep[];
  drafts: KbDraft[];
  sessionId: string;
  title: string;
  stopped: boolean;
}

/** 终态折叠：正文只认服务端 done.reply（与落盘逐字相同），live 文本一律不作为 content。 */
export function finalize(state: StreamingState, done: DoneEvent): FinalizedTurn {
  // 中间轮正文只在界面上活一次：折成 📝 行进过程块。不折进 content——进了就污染复制件，
  // 也会被喂进下一轮 prompt（spec 裁定 4）；落盘行里始终只有 done.reply。
  const notes: ChatStep[] = state.rounds
    .filter((r) => r.toolCalled && r.text)
    .map((r) => ({ tool: "📝", ok: true, round: r.round, detail: r.text }));
  // 残留 pending 是「宣告了但没跑完」的调用：渲染成 ✓/✗ 都是假话，且 done.steps 才是落盘口径，
  // 故随 live 状态一起丢弃（重开会话看到的过程块与这里落库的那份一致）。
  return {
    content: done.reply,
    steps: [...done.steps, ...notes].sort((a, b) => a.round - b.round),
    drafts: state.drafts,
    sessionId: done.session_id || state.sessionId,
    title: done.title || state.title,
    stopped: done.stopped,
  };
}

/** 当前该显示的正文：最后一个有文本的轮次。 */
export function liveText(state: StreamingState | null): string {
  if (!state) return "";
  for (let i = state.rounds.length - 1; i >= 0; i -= 1) {
    if (state.rounds[i].text) return state.rounds[i].text;
  }
  return "";
}

/** 三点占位的判据：连接活着、模型还没开口、也还没有任何过程行。 */
export function isWaiting(state: StreamingState | null): boolean {
  if (!state) return true;
  return state.rounds.length === 0 && state.pending.length === 0 && state.steps.length === 0;
}
```

- [ ] **Step 2: 写传输层**

`frontend/src/api/client.ts` 在 `chatSend` 之后追加（`chatSend`/`SendResponse` 暂留，见上方说明）：

```ts
/** 一条 SSE 帧的落地形态：`event:` 名进 type，`data:` 的单行 JSON 摊平进来（与 router `_frame` 一一对应）。 */
type Frame<T extends string, P> = { type: T } & P;
export type StreamEvent =
  | Frame<"start", { run_id: string; session_id: string }>
  | Frame<"delta", { round: number; text: string }>
  | Frame<"call", { tool: string; round: number; detail: string }>
  | Frame<"step", { tool: string; ok: boolean; round: number; detail: string }>
  | Frame<"draft", { draft: KbDraft }>
  | Frame<"done", { reply: string; steps: ChatStep[]; session_id: string; title: string; stopped: boolean }>
  | Frame<"error", { detail: string }>;

export interface StreamBody {
  session_id: string;
  message: string;
  agent_id: string;
  project_id: string;
}

const STREAM_EVENTS = new Set(["start", "delta", "call", "step", "draft", "done", "error"]);

/** POST + 流解析：EventSource 不能带 JSON body，WebSocket 又是多余的语义，故 fetch + getReader 手解。
 *  守门未过时后端回普通 JSON（400/404/502），照 apiFetch 口径抛 ApiError；守门过后才有事件。 */
export async function chatSendStream(
  body: StreamBody,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch("/api/chat/send/stream", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body), signal });
  if (!resp.ok) {
    let detail = `请求失败: HTTP ${resp.status}`;
    let data: unknown = null;
    try {
      const parsed = (await resp.json()) as { detail?: unknown };
      data = parsed;
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // 响应体不是 JSON 时保留默认错误文案
    }
    throw new ApiError(detail, resp.status, data);
  }
  if (!resp.body) throw new ApiError("浏览器未提供响应流，无法接收流式回复", resp.status, null);

  const feed = (frame: string) => {
    let name = "";
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data = line.slice(5).trim();
    }
    if (!STREAM_EVENTS.has(name)) return;   // 未知事件静默忽略（spec 事件表：前端只认这 7 类）
    let payload: object;
    try {
      payload = JSON.parse(data) as object;
    } catch {
      return;                               // 坏帧丢一条，不砸整条流（与后端草案逐条容错同口径）
    }
    onEvent({ ...payload, type: name } as StreamEvent);
  };

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep = buf.indexOf("\n\n");
    while (sep >= 0) {
      feed(buf.slice(0, sep));
      buf = buf.slice(sep + 2);
      sep = buf.indexOf("\n\n");
    }
  }
  buf += decoder.decode();          // 尾包 flush：最后一帧可能不带终止空行
  if (buf.trim()) feed(buf);
}

/** 服务端真停：置取消位。404「这条回答已经结束」是正常竞态，调用方按竞态处理。 */
export function chatStop(runId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>("/api/chat/stop", {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ run_id: runId }) });
}
```

同文件把 `ChatMessage` 补一个可选字段（历史行读回来的 `stopped` 由后端 `ChatMessageInfo` 给；user 行与乐观气泡无此语义，故可选）：

```ts
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  ts: number;
  steps: ChatStep[] | null;
  /** 被停止的 assistant 行：只进 UI 挂「（已停止）」，不进 content。 */
  stopped?: boolean;
}
```

- [ ] **Step 3: 桌检对拍（本期无前端测试框架，沿用既有裁定；表内值即走查时的期望）**

Run: `cd frontend && npm run build` → 0 error。
逐行核对 `applyEvent`/`finalize` 对下面这条脚本化事件序列的产出（读代码填表，错了当场改）：

| 事件（按序） | rounds | pending | steps | drafts |
|---|---|---|---|---|
| `start{run_id:"r1",session_id:""}` | `[]` | `[]` | `[]` | `[]` |
| `delta{round:1,text:"先读"}` | `[{1,"先读",false}]` | `[]` | `[]` | `[]` |
| `delta{round:1,text:"需求"}` | `[{1,"先读需求",false}]` | `[]` | `[]` | `[]` |
| `call{tool:"read",round:1,detail:"a.md"}` | `[{1,"先读需求",true}]` | `[1::read]` | `[]` | `[]` |
| `step{tool:"read",ok:true,round:1,detail:"a.md"}` | 同上 | `[]` | `[{read,true,1,"a.md"}]` | `[]` |
| `draft{draft:…}` | 同上 | `[]` | 同上 | `[…1 条]` |
| `delta{round:2,text:"完成"}` | `[…,{2,"完成",false}]` | `[]` | 同上 | 同上 |
| `done{reply:"完成",steps:[read],session_id:"s9",title:"t",stopped:false}` | 不变 | 不变 | 不变 | 不变 |

`finalize` 的产出必须是 `content:"完成"`、`steps` 含 2 行——`done.steps` 的 `read` 行之后折出第 1 轮的 `{tool:"📝", ok:true, round:1, detail:"先读需求"}`（第 1 轮收到 `call` → `toolCalled=true`，其中间轮文本在 `done` 时折进过程块，spec:119/:167；第 2 轮是最终答复，`toolCalled=false`，不折）、`sessionId:"s9"`、`title:"t"`、`stopped:false`；此时状态上 `terminal=true`、`done` 就是那条 done 事件（页面靠 `state.done` 判终态，不靠自设局部变量）。（Task 12 校正：原文「只含 1 条 `read`」系 prose 笔误，T8 桌检 check.cjs 实测即 2 条，代码正确勿改动。）
再把 `delta{round:2}` 换成 `delta{round:3}` 并让第 2 轮带 `call`：`finalize` 的 `steps` 必须在真实步骤之后多一条 `{tool:"📝", ok:true, round:2, detail:"完成"}`。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/api/client.ts frontend/src/pages/chat/streamState.ts
git commit -m "feat(chat-stream): 前端传输层——SSE 手解 + 事件折叠纯函数 streamState"
```

---

### Task 9: 聊天页接线——live 气泡、逐条步骤、■ 停止钮

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx:203-234`（`send` 改事件驱动）+ 新增 refs/state + `:411-436` 两处 JSX 传参
- Modify: `frontend/src/pages/chat/MessageList.tsx:14-39`（`Props` 加 `live`、`Steps` 加 pending）、`:41-44`（滚底依赖）、`:86-91`（占位/live 气泡）
- Modify: `frontend/src/pages/chat/Composer.tsx:5-18`（`Props`）、`:76-81`（发送钮位）
- Modify: `frontend/src/App.css:150-153`（`.t-step.pending`）、`:202-205`（`.live-caret`）、`:231-235`（`.btn-send.stop`）

**Interfaces:**
- Consumes: T8 的 `chatSendStream`、`chatStop`、`StreamEvent`、`newStreamState`/`applyEvent`/`finalize`、`StreamingState`，以及 `liveText`/`isWaiting`/`PendingCall`（MessageList 侧）
- Produces: 聊天页在流式期间的完整渲染契约（`live` 为空或全空 → 三点占位；有内容 → live 气泡 + caret + 过程块逐条）

- [ ] **Step 1: ChatPage 改成事件驱动**

import 补（两个来源拆两行；只列 ChatPage 真用到的符号——`tsconfig` 开了 `noUnusedLocals`，多一个未用导入就红）：

```ts
import {
  ApiError, chatSendStream, chatStop, deleteChatSession, getCapabilities, getModels,
  getProjects, getSessionMessages, getSessions,
  type AgentInfo, type ChatMessage, type ChatSession, type HealthResponse,
  type Project, type StreamEvent,
} from "../api/client";
import { applyEvent, finalize, newStreamState, type StreamingState } from "./chat/streamState";
```
> `liveText` / `isWaiting` 只在 MessageList 用（Step 2），不要在 ChatPage 里顺手导入。

在 `const busyRef = useRef(false);` 之后加：

```ts
  const [live, setLive] = useState<StreamingState | null>(null);
  const liveRef = useRef<StreamingState | null>(null);   // 终态折叠要同步读到最后一帧
  const [stopRequested, setStopRequested] = useState(false);
  const stopRequestedRef = useRef(false);
  const runIdRef = useRef("");
  const abortRef = useRef<AbortController | null>(null);
```

`send`（`:203-234`）整段替换：

```ts
  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || !guard() || !agentId || !projectId) return;
    busyRef.current = true;
    setBusy(true);
    // 乐观气泡按对象身份撤，不按文案匹配：同一句话在历史里出现过时，按内容 filter 会把旧的那条一起删掉
    const optimistic: ChatMessage = { role: "user", content: text, ts: Date.now(), steps: null };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");
    liveRef.current = newStreamState();
    setLive(liveRef.current);
    runIdRef.current = "";
    stopRequestedRef.current = false;
    setStopRequested(false);
    const controller = new AbortController();
    abortRef.current = controller;
    let errMsg = "";                        // 只装「非事件」的失败（HTTP 守门 / 断流），终态一律从 state 读
    const onEvent = (ev: StreamEvent) => {
      if (ev.type === "start") runIdRef.current = ev.run_id;
      const cur = liveRef.current ?? newStreamState();
      liveRef.current = applyEvent(cur, ev);   // done/error 也照折：终态与 detail 都随状态回给下面
      setLive(liveRef.current);
    };
    let aborted = false;
    try {
      await chatSendStream(
        { session_id: activeId ?? "", message: text, agent_id: agentId, project_id: projectId },
        onEvent, controller.signal);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") aborted = true;
      else errMsg = err instanceof ApiError ? err.message : String(err);
    }
    // 换页/卸载导致的 abort：连接已断，后端走 GeneratorExit 落截断盘，本页不再改任何 state
    if (aborted) return;
    abortRef.current = null;
    busyRef.current = false;
    setBusy(false);
    setStopRequested(false);
    runIdRef.current = "";
    const st = liveRef.current;
    const done = st ? st.done : null;
    if (!done && !errMsg) errMsg = st?.fail || "连接中断，本条回答未完成";
    if (done && st) {
      const f = finalize(st, done);
      liveRef.current = null;
      setLive(null);
      setMessages((prev) => [...prev, {
        role: "assistant", content: f.content, ts: Date.now(), steps: f.steps, stopped: f.stopped,
      }]);
      if (f.sessionId !== activeId) setActiveId(f.sessionId);
      setWsSeq((n) => n + 1);            // 模型可能刚写了产出物：工作区树静默重拉（被停止也照拉）
      const rows = await reloadSessions();
      // 新建会话后标题由服务端定，用返回的 title 就地补齐，避免等整表刷新才可见
      const mine = rows.find((r) => r.id === f.sessionId);
      if (mine && f.title && mine.title !== f.title) {
        setSessions((prev) => prev.map((r) => (r.id === mine.id ? { ...r, title: f.title } : r)));
      }
      return;
    }
    // 失败必须可见：撤掉乐观 user 气泡并回填原文，不让用户对着「发出去了却没回」的空框
    liveRef.current = null;
    setLive(null);
    setMessages((prev) => prev.filter((m) => m !== optimistic));
    setInput(text);
    toast(errMsg);
  }, [activeId, agentId, guard, input, projectId, reloadSessions, toast]);
```

紧随其后加停止动作，并在卸载收尾里带上 abort：

```ts
  const stop = useCallback(() => {
    if (stopRequestedRef.current) return;
    const rid = runIdRef.current;
    // start 事件还没到时没有 run_id 可停：给一句可见反馈，而不是让按钮空转成死控件
    if (!rid) { toast("还在建立连接，请稍候"); return; }
    stopRequestedRef.current = true;
    setStopRequested(true);
    chatStop(rid).catch((err: unknown) => {
      // 404「这条回答已经结束」是与终态并发的正常竞态：界面随后自己收到 done，这里只把原因说出来
      toast(err instanceof ApiError ? err.message : String(err));
    });
  }, [toast]);

  useEffect(() => () => {
    window.clearTimeout(toastTimer.current);
    abortRef.current?.abort();   // 离开页面即断流：后端收 GeneratorExit，落截断行并标 stopped
  }, []);
```
> 这两条分别替换原有的 `useEffect(() => () => { window.clearTimeout(toastTimer.current); }, []);`（`:171`）——不要留下两个卸载 effect 各清一半。

JSX 传参（`:411-436`）：`<MessageList … busy={busy} live={live} … />`、`<Composer … stopRequested={stopRequested} onStop={stop} … />`。

- [ ] **Step 2: MessageList 接 live**

`Props` 加 `live: StreamingState | null;`（并 `import { isWaiting, liveText, type PendingCall, type StreamingState } from "./streamState";`——本文件今天的 import 只有 `./utils` 与 `../kb/utils`），`Steps` 改成带 pending：

```tsx
function Steps({ steps, pending }: { steps: ChatStep[]; pending?: PendingCall[] }) {
  const rows = pending ?? [];
  if (!steps.length && !rows.length) return null;  // 无工具调用时整个过程块不渲染（spec 裁定 4）
  return (
    // 有 ⏳ 行时强制展开：真机行为定义写着「工具轮次期间过程块可见且逐条增长」；
    // pending 清空后 prop 变 undefined，用户此前的开合状态不再被受控属性抢走
    <details className="thinking" open={rows.length ? true : undefined}>
      <summary>🔧 执行过程</summary>
      {steps.map((s, i) => (
        <div className="t-step" key={`${s.round}-${s.tool}-${i}`}>
          <span className="n">{i + 1}.</span>
          <span>{s.tool} · {s.ok ? "成功" : "失败"}</span>
          <span className="args">{s.detail}</span>
        </div>
      ))}
      {rows.map((p, i) => (
        <div className="t-step pending" key={`p-${p.key}-${i}`}>
          <span className="n">{steps.length + i + 1}.</span>
          <span>{p.tool} · ⏳</span>
          <span className="args">{p.detail}</span>
        </div>
      ))}
    </details>
  );
}
```

滚底依赖改 `[p.messages, p.busy, p.live]`；尾部占位块（`:86-91`）替换：

```tsx
      {p.busy && isWaiting(p.live) && (
        <div className="msg agent">
          <div className="who"><span className="avatar">Ai</span>AiTester</div>
          <span className="typing"><i /><i /><i /></span>
        </div>
      )}
      {p.busy && p.live && !isWaiting(p.live) && (
        <div className="msg agent">
          <div className="who"><span className="avatar">Ai</span>AiTester</div>
          <Steps steps={p.live.steps} pending={p.live.pending} />
          <div className="body md-preview"
            dangerouslySetInnerHTML={{ __html: mdRender(liveText(p.live)) }} />
          {/* 光标独立成行：mdRender 出的是块级元素，塞进同一段落会被浏览器的
              非法嵌套纠正规则挪位 */}
          <span className="live-caret" />
        </div>
      )}
```
历史 assistant 行的条尾加标注（`🗀 {fmtTime(m.ts)}` 之后）：`{m.stopped ? <span>（已停止）</span> : null}`。

- [ ] **Step 3: Composer 的停止钮位**

`Props` 加 `stopRequested: boolean; onStop: () => void;`，发送钮那段（`:76-81`）替换：

```tsx
          {p.busy ? (
            /* busy 时钮位换成停止：带文字不裸图标（UI 约定），点下就置灰防二次点击，
               终态到达后 busy 落真 → 变回 ↑（验收清单「0 个死按钮」那条） */
            <button className="btn-send stop" disabled={p.stopRequested}
              title={p.stopRequested ? "停止中…" : "停止生成"} onClick={p.onStop}>■ 停止</button>
          ) : (
            <button
              className={`btn-send${canSend ? " on" : ""}`}
              title={p.sendBlock || "发送"}
              disabled={!canSend}
              onClick={p.onSubmit}
            >↑</button>
          )}
```

- [ ] **Step 4: App.css 三条样式（零新增色值）**

`.t-step` 规则块（`:150-152`）之后加：

```css
  /* 流式中的过程行：call 已宣告、结果未回（裁定 1「步骤逐条」) */
  .t-step.pending{opacity:.65}
```
`.typing` 关键帧之后（`:205` 后）加：

```css
  /* live 气泡光标：只借既有 blink 关键帧与 --primary，不新增色值 */
  .live-caret{display:block;width:7px;height:14px;border-radius:2px;background:var(--primary);margin-top:2px;animation:blink 1.2s infinite}
```
`.btn-send.on`（`:235`）之后加：

```css
  /* 停止钮复用发送钮位：34px 圆撑不下「■ 停止」，故改成圆角胶囊；色值全取自家与 :569 的禁用态 */
  .btn-send.stop{width:auto;height:34px;padding:0 12px;border-radius:17px;background:#efe8dc;color:var(--fail);font-size:12.5px;font-weight:600}
  .btn-send.stop:disabled{background:#f7f4ee;color:#a9a196;cursor:not-allowed}
```

- [ ] **Step 5: 门禁 + 提交**

Run: `cd frontend && npm run build` → 0 error。
Run: `cd backend && uv run pytest -q` → **482 passed**（本任务零后端改动，全绿才算没误伤）。

```bash
git add frontend/src/pages/ChatPage.tsx frontend/src/pages/chat/MessageList.tsx frontend/src/pages/chat/Composer.tsx frontend/src/App.css
git commit -m "feat(chat-stream): 聊天页接线——逐 token live 气泡、⏳→✓ 过程行、■ 停止钮"
```

---

### Task 10: `/kb` 助手迁到同一条流 + 删一次性前端口

**Files:**
- Modify: `frontend/src/pages/KbPage.tsx:274-313`（助手栏 state 与 `ask`）
- Modify: `frontend/src/pages/kb/KbAssistantPane.tsx:15-33`（`KbChatMsg` 加 `stopped`、props 加停止动作）、`:79-104`（标注）、`:111-121`（发送/停止钮位）
- Modify: `frontend/src/api/client.ts`（删 `chatSend` 与 `SendResponse`）
- Modify: `frontend/src/App.css:253-260`（`.ws-btn:disabled`）

**Interfaces:**
- Consumes: T8 的 `chatSendStream`/`chatStop`/`newStreamState`/`applyEvent`/`finalize`/`liveText`
- Produces: `KbChatMsg.stopped?: boolean`；`KbAssistantPaneProps` 增 `stopRequested: boolean` 与 `onStop: () => void`

- [ ] **Step 1: KbPage 助手栏改流式**

import 换源（`chatSend` 在 Step 3 才从 client.ts 删掉，这里先换成流式两函数，中间态 build 仍绿）：

```ts
import {
  ApiError, chatSendStream, chatStop,
  type KbDraft,
  // ……本文件原有其余 kb*/ws* 导入照旧
} from "../api/client";
import { applyEvent, finalize, liveText, newStreamState } from "../chat/streamState";
```

`ask`（`:299-313`）整段替换，并在旁边补 stop；卸载清理那个 `useEffect`（`:272`）里带上 `kbAbortRef.current?.abort()`（与聊天页同构：离开 `/kb` 即断流，后端不再无人消费地跑完）：

```ts
  /** brief Step 3 ask 原样转写：session_id/agent_id 固定值不可改（后端记忆键 kb_assistant:kb-console）。
   *  project_id 传空串——平台助手不属于项目，后端对该字段短路忽略。kb-console 是临时键：
   *  流式照旧，但会话行不写 jsonl，停止后不留痕（spec 真机行为定义）。 */
  const ask = useCallback(async (text: string) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    runIdRef.current = "";
    stopRequestedRef.current = false;
    setStopRequested(false);
    setMsgs((prev) => [...prev, { who: "me", text }, { who: "ai", text: "思考中…", pending: true }]);
    const st = newStreamState();
    const controller = new AbortController();
    kbAbortRef.current = controller;
    let errMsg = "";
    try {
      await chatSendStream(
        { session_id: "kb-console", message: text, agent_id: "kb_assistant", project_id: "" },
        (ev) => {
          if (ev.type === "start") runIdRef.current = ev.run_id;
          Object.assign(st, applyEvent(st, ev));   // 折叠就地推进：本栏只需一份累加器
          if (st.terminal) return;                 // 终态不 patch 气泡，交给下面的收尾一次落定
          // live 文本进「思考中…」占位气泡（pending 保持真），草案卡在流内即刻挂上
          setLastAi(liveText(st) || "思考中…", st.drafts, true);
        }, controller.signal);
    } catch (err) {
      // 离开 /kb 触发的 abort：组件已不在，连接已断，后端走 GeneratorExit 收尾
      if (err instanceof DOMException && err.name === "AbortError") return;
      errMsg = err instanceof ApiError ? err.message : String(err);
    }
    kbAbortRef.current = null;
    busyRef.current = false;
    setBusy(false);
    const done = st.done;
    if (done) {
      const f = finalize(st, done);
      replaceLastAi(f.content, f.drafts, false, f.stopped);
      return;
    }
    // 与迁移前同款：失败进气泡（红色），不抢 toast
    replaceLastAi(errMsg || st.fail || "连接中断，助手未完成", [], true);
  }, [replaceLastAi, setLastAi]);

  const stopAsk = useCallback(() => {
    if (stopRequestedRef.current) return;
    const rid = runIdRef.current;
    if (!rid) { toast("还在建立连接，请稍候"); return; }
    stopRequestedRef.current = true;
    setStopRequested(true);
    chatStop(rid).catch((err: unknown) => { toast(err instanceof ApiError ? err.message : String(err)); });
  }, [toast]);
```

`replaceLastAi` 扩一个终态参数，并抽出流内用的 `setLastAi`（两者共用同一段「逆序找 pending ai 气泡」的定位逻辑，别再复制第三遍）：

```ts
  /** 原型 :2682-2685 —— 用最新回复（+逐条草案卡 / 错误行）更新「思考中…」占位气泡。
   *  keepPending=true 时气泡仍是 pending 态（流式中的 live 更新），false 即终态替换。 */
  const setLastAi = useCallback((
    text: string, drafts: KbDraft[], keepPending: boolean, error = false, stopped = false,
  ) => {
    setMsgs((prev) => {
      const next = [...prev];
      const msg: KbChatMsg = {
        who: "ai", text,
        pending: keepPending || undefined,
        error: error || undefined,
        stopped: stopped || undefined,
        drafts: drafts.length ? drafts.map((d) => ({ draft: d, state: "pending" as const })) : undefined,
      };
      let i = next.length - 1;
      while (i >= 0 && !(next[i].who === "ai" && next[i].pending)) i--;
      if (i >= 0) next[i] = msg;
      else if (!keepPending) next.push(msg);   // 终态却找不到占位（异常路径）：宁可追加，也不能让已到手的回复消失
      return next;
    });
  }, []);

  const replaceLastAi = useCallback((text: string, drafts: KbDraft[], error = false, stopped = false) => {
    setLastAi(text, drafts, false, error, stopped);
  }, [setLastAi]);
```
> 助手栏 state 处补四行（与聊天页同名同义）：`const runIdRef = useRef(""); const stopRequestedRef = useRef(false); const [stopRequested, setStopRequested] = useState(false); const kbAbortRef = useRef<AbortController | null>(null);`。
> `setLastAi` 定义要放在 `ask` 之前（`ask` 的 deps 里引用它）。
> `kb-console` 是临时键（`use_file=False`），所以停止/断流都不留痕——spec 真机行为定义写明了这条差异，别在走查时把它当 bug。

- [ ] **Step 2: KbAssistantPane 渲染标注与停止钮**

先把 `KbPage.tsx` 里那处 `<KbAssistantPane … />` 补两个 prop：`stopRequested={stopRequested}`、`onStop={stopAsk}`（缺一个就是 TS 报错，别漏）。
`KbChatMsg` 加 `stopped?: boolean;`；`KbAssistantPaneProps` 加 `stopRequested: boolean; onStop: () => void;`。
非 error 非 pending 分支之后（`:91` 的 `mdRender` 那段所在三元之后、`m.drafts?.map` 之前）插一条：

```tsx
            {m.stopped ? (
              <span style={{ color: "var(--text-2)" }}>（已停止）</span>
            ) : null}
```
底部 bar（`:118-120`）的发送按钮换成同位二选一：

```tsx
          {busy ? (
            <button className="ws-btn" disabled={stopRequested}
              title={stopRequested ? "停止中…" : "停止生成"} onClick={onStop}>■ 停止</button>
          ) : (
            <button className="ws-btn main" disabled={busy} onClick={send}>发送</button>
          )}
```

`.ws-btn.main:hover`（`:260`）之后加禁用态（色值取 `:569` 现成的两枚，不新增）：

```css
  .ws-btn:disabled{color:#a9a196;border-color:var(--line);background:#f7f4ee;cursor:not-allowed}
```

- [ ] **Step 3: 删掉一次性前端口（裁定 1 在界面侧的落点）**

`frontend/src/api/client.ts` 删除 `export interface SendResponse {…}` 与 `export function chatSend(…)`（`:296-312`）。
Run: `grep -rn "chatSend\b\|SendResponse" frontend/src` → **0 命中**（有命中即说明还有消费方没迁完，回到 Step 1/2 补，不许留兼容壳）。

- [ ] **Step 4: 门禁 + 提交**

Run: `cd frontend && npm run build` → 0 error。
Run: `cd backend && uv run pytest -q` → **482 passed**。

```bash
git add frontend/src/api/client.ts frontend/src/pages/KbPage.tsx frontend/src/pages/kb/KbAssistantPane.tsx frontend/src/App.css
git commit -m "feat(chat-stream): /kb 助手切流式 + 删前端一次性 chatSend（传输收敛成一条）"
```

---

### Task 11: 上下文 meter 口径对齐 `HISTORY_MAX = 40`

**Files:**
- Modify: `frontend/src/pages/chat/utils.ts:12-23`

**Interfaces:**
- Consumes: 无（纯函数自身）
- Produces: `export const HISTORY_MAX = 40`（T12 的 README 走查与后续片次引用同名常数）

- [ ] **Step 1: 改常数与切片**

```ts
/** 与后端 `services/chat.py:30` 的 `HISTORY_MAX` 同一常数：两侧口径不一致，meter 就会把
 *  后端根本不会带上的历史算进占用（第 1 片登记、第 2 片转来的欠账，第 4 片裁定 6 收口）。 */
export const HISTORY_MAX = 40;

/** 原型 :1399-1409 —— 系统提示词 + 历史 + 当前输入的占用估算；历史只数最近 HISTORY_MAX 条。 */
export function contextUsage(args: {
  systemPrompt: string;
  history: { content: string }[];
  input: string;
  cap: number;
}): { used: number; cap: number; pct: number } {
  const used = args.history.slice(-HISTORY_MAX).reduce(
    (acc, m) => acc + estTokens(m.content), estTokens(args.systemPrompt)) + estTokens(args.input || "");
  const cap = args.cap > 0 ? args.cap : 0;
  return { used, cap, pct: cap ? Math.min(100, Math.round((used / cap) * 100)) : 0 };
}
```
后端 `chat.py` 的 `HISTORY_MAX` 定义处补一行对偶注释：`# 与 frontend/src/pages/chat/utils.ts 的 HISTORY_MAX 同一常数（第 4 片裁定 6）`。

- [ ] **Step 2: 门禁 + 提交**

Run: `cd frontend && npm run build` → 0 error。
Run: `cd backend && uv run pytest -q` → **482 passed**（只加注释）。

```bash
git add frontend/src/pages/chat/utils.ts backend/src/aitester/services/chat.py
git commit -m "feat(chat-stream): 上下文 meter 只数最近 40 条——与后端 HISTORY_MAX 对齐"
```

---

### Task 12: 全量门禁、README 端点清扫与真机走查交接

**Files:**
- Modify: `README.md:35-37`

- [ ] **Step 1: 后端全量**

Run: `cd backend && uv run pytest -q` → **482 passed**（基线 448 + T1 4 + T2 3 + T3 5 + T4 5 + T5 3 + T6 7 + T7 7）。
若数字不符：先 `uv run pytest -q --collect-only | wc -l` 定位差在哪一层的增删，**不许为凑数删既有用例或改断言**。

- [ ] **Step 2: 前端门禁**

Run: `cd frontend && npm run build` → 0 error、0 warning 新增。

- [ ] **Step 3: README 端点清单**

`README.md:35-37` 那条 `- POST /api/chat/send：…` 整条替换：

```markdown
- `POST /api/chat/send/stream`：真实 LLM 链路的唯一传输，按 `agent_id` 现装一个一次性智能体实例
  （提示词 / 有效模型 / 携带工具 / 图拓扑），该智能体的默认模型可用则用、否则回落全局默认，未知 `agent_id` 返回 404。
  守门在流开始前同步跑完（配置缺失 / 项目不可达 → 普通 400·404，detail 与迁移前逐字相同），
  过后响应 `text/event-stream`，逐 token 推 `start / delta / call / step / draft / done / error` 七类事件，
  终态恒为一条（`done`，或被停止时 `done{stopped:true}`；流中模型失败 → `error`）
- `POST /api/chat/stop`：`{run_id}` 置取消位终止在途回答；`run_id` 已结束返回 404「这条回答已经结束」，
  已生成的部分文本与已完成步骤照旧落盘，会话行标 `stopped`
```

- [ ] **Step 4: 交真机走查（不由本计划代跑）**

后端 `127.0.0.1:8000` 与 vite `[::1]:5173` 由用户本人自起——**任何任务都不得占用或杀掉这两个端口**；`scripts/dev.ps1` 会杀 8000，禁止运行。
真实 LLM 段（逐 token 观感、带工具调用的流、停止响应延迟、刷新/关页落盘）按既有约定**须用户当面授权后才发起**；本节验收判据 = spec「验收清单」全 8 条。
零成本且真零副作用的只有这一条：
`curl -i -s -X POST http://localhost:5173/api/chat/send -H 'Content-Type: application/json' -d '{}'` → 404（一次性口确实没了；未注册路径不进任何 handler，不落会话行。Git Bash 要先 `export MSYS2_ARG_CONV_EXCL='*'` 才不被改写路径，主机名用 `localhost`——vite 只监听 `[::1]`，`127.0.0.1:5173` 连不上）。
**不要**拿 mock 模型去 curl `/chat/send/stream` 来「看逐字」：MockProvider 的 4 字符切片在毫秒内全部到达，看着就是攒一坨，只会得出假结论；而且它会往 `backend/data/sessions` 落一条真会话行，污染用户自己的会话列表。帧序与单行 JSON 已由 T7 的 `TestClient.stream` 锁死，「逐 token 到达」的真实节奏只能靠真机带真实模型那一次走查（P5 项）。

- [ ] **Step 5: 提交**

```bash
git add README.md
git commit -m "docs(readme): 端点清单换成 /chat/send/stream 与 /chat/stop"
```

---

## 计划自检（写完通读一遍，逐条对 spec）

**1. 裁定覆盖**：粒度→T2/T3（delta/call/step/draft 事件）+ T8/T9（逐 token 渲染、⏳→✓）；服务端真停→T2 `RunControl` + T5 `RunRegistry` + T7 `/chat/stop` + T9/T10 停止钮；范围（聊天页+`/kb`）→T9、T10；截断留痕→T4 字段链 + T6 落盘 + T9/T10 的「（已停止）」；裁定 1（删一次性 `send`）→T7 后端删与锁、T10 前端删与 grep 锁；裁定 2（meter 对齐）→T11。spec 六条裁定与偏离登记 1–7 全部有任务落点，无 TBD。

**2. 占位符扫描**：无「TBD/类似第 N 片/适当处理」。两处刻意的「同构改写」句（T7 Step 6 剩余调用点、T10 的 state 声明）都给了判据与同名符号，不含新逻辑。

**3. 类型一致性**：事件载荷键名在三处必须逐字相同——T3 `stream_graph` 产出、T7 router 序列化（`_frame(kind, …)` 去掉 `type`）、T8 `StreamEvent` 变体。`done` 的 `steps` 是 `StepInfo.model_dump()`（`tool/ok/round/detail`），与 T4 之前既有 `ChatStep` 同形。`finalize` 返回的 `FinalizedTurn.sessionId/title` 只被 T9（`setActiveId`、标题补齐）与 T10（只落文本，忽略这两字段——`kb-console` 无会话行）消费。`RunControl`/`RUN_CONTROL_KEY` 只在 T2/T3/T5/T6/T7 出现，键名与常量都在 T2 定义、后续按名引用。

**4. 已知偏离（实施时按 QODER.md 登记，勿静默）**：
- T8 的 `chatSend` 删除动作落在 T10 —— 为保每任务 `npm run build` 必绿；spec 未规定删除时机。
- T9 的 live 气泡把「📝 中间轮文本」折进界面 steps 而不落盘 —— spec 风险节已写明「重开后只剩最终回复」，本计划不扩语义。
- T2 起 `run_graph` 与 `send` 都变成事件流薄壳：`test_agent_graph.py` 的既有断言因此成了流式核心的回归锁，这也是 spec「一份实现，两种消费」的落点，非偏离。
- 计划新增偏离登记 9（`RunRegistry` 只挂 `app.state`）与 10（一次性口的锁是 404 不是 405）分别在 T5、T7 的收尾步骤写回 spec。

**5. 测试总数链**：448 → 452（T1）→ 455（T2）→ 460（T3）→ 465（T4）→ 468（T5）→ 475（T6）→ **482（T7）** → 482（T8–T12，纯前端 + 注释）。每个任务的门禁数字与上一任务相差 = 该任务新增用例数，T8 之后不变。

