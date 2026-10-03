"""stream_graph：事件顺序、round 递增、draft 抢在 finish 前、取消即 stopped 终态。"""

from pathlib import Path
from math import ceil
from typing import Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

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


class _CancelAfterToolCallChunk:
    """把 tool_calls 挂在非末块、取消落在其后：造出「merged 已攥着待派发 tool_calls 却被打断」的窗口。

    streaming_fakes.chunks_from 只把 tool_calls 挂末块，末块在检查点前从不进 merged，
    所以既有假 provider 永远观测不到停止轮其实带着待派发的 tool_calls——
    这条专为其而写：删掉 _stream_round 的 `calls = [] if stopped else …` 守卫即翻红。
    """

    name = "cancel_after_tool_calls"
    model_ref = "cancel_after_tool_calls/model"

    def __init__(self, control: RunControl) -> None:
        self._control = control

    def bind_tools(self, tools: list) -> "_CancelAfterToolCallChunk":
        return self

    def stream_messages(self, messages: list) -> Iterator[AIMessageChunk]:
        yield AIMessageChunk(content="前缀")                 # 取消前已产出、已进 merged 的部分文本
        yield AIMessageChunk(content="", tool_calls=[        # tool_calls 落在非末块，进 merged
            {"name": "write", "args": {"file_path": "x.txt", "content": "v"},
             "id": "c1", "type": "tool_call"}])
        self._control.cancel()                               # 取消落在 tool_calls 块与流末之间
        yield AIMessageChunk(content="")                     # 下一个检查点被丢，不进 merged


def test_stop_after_tool_calls_chunk_drops_pending_calls_keeps_partial_text(
    tmp_path: Path,
) -> None:
    """取消落在「tool_calls 已进 merged」之后：停止轮须报 stopped=True 且丢弃待派发工具调用，
    同时保留取消前已生成的非空文本。

    这是 Task 2 评审遗留的判别性用例：删掉 agent_graph 里 `calls = [] if stopped else …`，
    漏出的 tool_calls 会派发 write（多一条 step、tool_traces 非空、reply 被后续空轮覆盖成 ""、
    x.txt 被真写盘），下面四条断言同时翻红。
    """
    control = RunControl()
    provider = _CancelAfterToolCallChunk(control)
    events = _collect(provider, local_tools(tmp_path), [HumanMessage(content="写一次")],
                      control=control)
    finish = events[-1]
    assert finish["type"] == "finish"
    assert finish["stopped"] is True
    assert finish["tool_traces"] == []                  # 待派发 tool_calls 被丢弃，工具没落地
    assert finish["reply"] == "前缀"                     # 取消前的非空部分文本保留
    assert _events(events, "call") == []               # 停止轮不发起任何 call
    assert not (tmp_path / "x.txt").exists()           # write 从未执行
