"""create_agent 折流：产出与 stream_graph 同一套事件词表（delta/call/step/draft/finish），
并在本条流里补齐计量（create_agent 绕过 MeteredProvider，用量须在这里落账）。

真模型给的是增量 tool_call_chunks；剧本给已解析 tool_calls，用 chunks_from 挂末块即可
（与 streaming_fakes 同口径，P8 实测累加后 merged.tool_calls 就是这份清单）。
"""

from pathlib import Path
from typing import Any, Iterator, Sequence

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from aitester.context.usage import ACTUAL, ESTIMATED, ContextUsage
from aitester.orchestration import stream_create_agent_graph
from aitester.orchestration.agent_graph import make_create_agent_builder
from aitester.orchestration.run_control import RunControl
from streaming_fakes import chunks_from, local_tools


class _ScriptChatModel(BaseChatModel):
    """create_agent 专用假模型：剧本逐轮，_generate/_stream 双实现；bind_tools 原样返回自身。"""

    def __init__(self, script: list[AIMessage], **kw: Any) -> None:
        super().__init__(**kw)
        self._script = list(script)

    @property
    def _llm_type(self) -> str:
        return "script"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "_ScriptChatModel":
        return self

    def _next(self) -> AIMessage:
        return self._script.pop(0) if self._script else AIMessage(content="done")

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for chunk in chunks_from(self._next()):
            if run_manager:
                run_manager.on_llm_new_token(str(chunk.content), chunk=chunk)
            yield ChatGenerationChunk(message=chunk)


class _ScriptProvider:
    """只暴露 base_chat_model 的最小 provider：create_agent 取的就是这个真 BaseChatModel。"""

    name = "script"
    model_ref = "script/model"

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    @property
    def base_chat_model(self) -> BaseChatModel:
        return self._model


def _collect(model: BaseChatModel, tools: list, messages: list, control=None, usage=None):
    return list(stream_create_agent_graph(
        make_create_agent_builder(), _ScriptProvider(model), tools, messages,
        control=control, usage=usage))


def _events(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


def _write_call(name: str, content: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "write", "args": {"file_path": name, "content": content},
         "id": f"c-{name}", "type": "tool_call"}])


def test_plain_answer_streams_deltas_then_finish() -> None:
    model = _ScriptChatModel([AIMessage(content="知识库就绪")])
    events = _collect(model, [], [HumanMessage(content="打个招呼")])
    deltas = _events(events, "delta")
    assert "".join(d["text"] for d in deltas) == "知识库就绪"
    finish = events[-1]
    assert finish["type"] == "finish"
    assert finish["reply"] == "知识库就绪"
    assert finish["stopped"] is False and finish["pending"] is False
    assert finish["tool_traces"] == [] and finish["drafts"] == []


def test_call_and_step_pair_up_then_final_reply(tmp_path: Path) -> None:
    model = _ScriptChatModel([
        AIMessage(content="核查中", tool_calls=[{"name": "write", "args": {
            "file_path": "a.txt", "content": "v"}, "id": "c1", "type": "tool_call"}]),
        _write_call("b.txt", "v2"),
        AIMessage(content="两步完成"),
    ])
    events = _collect(model, local_tools(tmp_path), [HumanMessage(content="写两次")])
    kinds = [e["type"] for e in events]
    # 工具轮的 call/step 成对出现，且都在最终正文之前
    assert kinds[0] == "delta" and "call" in kinds and "step" in kinds
    assert kinds.index("step") < kinds.index("finish")
    calls = _events(events, "call")
    steps = _events(events, "step")
    assert [c["tool"] for c in calls] == ["write", "write"]
    assert all(s["ok"] is True for s in steps)
    assert calls[0]["detail"] == '{"file_path": "a.txt", "content": "v"}'
    finish = events[-1]
    assert finish["reply"] == "两步完成"          # 取最后一个无工具轮正文（与 react 同口径）
    assert [t["tool"] for t in finish["tool_traces"]] == ["write", "write"]
    assert "result" not in steps[0]              # result 只进 finish，不外泄到 step


def test_prepare_kb_write_emits_draft_before_finish(tmp_path: Path) -> None:
    from aitester.adapters.tools.kb_tools import PrepareKbWriteTool

    model = _ScriptChatModel([
        AIMessage(content="", tool_calls=[{"name": "prepare_kb_write", "args": {
            "title": "N", "content": "# N", "bucket": "business/wiki", "summary": "新建"},
            "id": "c1", "type": "tool_call"}]),
        AIMessage(content="草案已生成，请点确认"),
    ])
    events = _collect(model, [PrepareKbWriteTool(kb_root=tmp_path)],
                      [HumanMessage(content="记一笔")])
    kinds = [e["type"] for e in events]
    assert "draft" in kinds
    assert kinds.index("draft") < kinds.index("finish")
    assert events[kinds.index("draft")]["draft"]["path"] == "business/wiki/n.md"
    assert events[-1]["drafts"]                   # 草案也进 finish 汇总


def test_prepare_kb_maintenance_emits_draft_via_generalized_emitter(tmp_path: Path) -> None:
    """泛化后的发射器不再硬编码 prepare_kb_write：凡 artifact.kind 为 save/delete/edit/frontmatter
    均折成 draft 事件。此处用 delete 验证维护草案同样走草案通道（零写盘）。"""
    from aitester.adapters.tools.kb_tools import PrepareKbDeleteTool

    model = _ScriptChatModel([
        AIMessage(content="", tool_calls=[{"name": "prepare_kb_delete", "args": {
            "path": "knowledge/business/wiki/n.md", "summary": "删掉"},
            "id": "c1", "type": "tool_call"}]),
        AIMessage(content="草案已生成，请点确认"),
    ])
    events = _collect(model, [PrepareKbDeleteTool()],
                      [HumanMessage(content="删这个")])
    kinds = [e["type"] for e in events]
    assert "draft" in kinds
    draft = events[kinds.index("draft")]["draft"]
    assert draft["kind"] == "delete"
    assert draft["path"] == "knowledge/business/wiki/n.md"


def test_control_cancel_breaks_stream_and_marks_stopped(tmp_path: Path) -> None:
    """create_agent 在图事件边界停笔：取消已发生则本轮不再投递任何 delta/call/step，
    只收一个 stopped=True、reply 为空的 finish（正文截断留 react 路径细测）。
    """
    control = RunControl()
    control.cancel()
    model = _ScriptChatModel([AIMessage(content="0123456789")])
    events = _collect(model, local_tools(tmp_path), [HumanMessage(content="说吧")], control=control)
    assert _events(events, "delta") == []
    assert _events(events, "call") == [] and _events(events, "step") == []
    finish = events[-1]
    assert finish["type"] == "finish" and finish["stopped"] is True
    assert finish["reply"] == "" and finish["tool_traces"] == []
    assert len(events) == 1


def test_metering_falls_in_this_stream(tmp_path: Path) -> None:
    """create_agent 绕过 MeteredProvider：用量须在本条流里 note_request + note_response。"""
    usage = ContextUsage(window=8000)

    class _UsageModel(_ScriptChatModel):
        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="答案",
                usage_metadata={"input_tokens": 111, "output_tokens": 22, "total_tokens": 133}))

    _collect(_UsageModel([AIMessage(content="答案")]), [], [HumanMessage(content="问")], usage=usage)
    assert usage.rounds == 1                        # note_request 恰好一次
    assert usage.spent_input == 111 and usage.spent_output == 22
    assert usage.occupancy_source == ACTUAL         # 收尾块带 usage_metadata → 真值


def test_metering_without_usage_metadata_stays_estimated() -> None:
    usage = ContextUsage(window=8000)
    _collect(_ScriptChatModel([AIMessage(content="答复")]), [],
             [HumanMessage(content="问")], usage=usage)
    assert usage.rounds == 1
    assert usage.occupancy_source == ESTIMATED      # 假模型无 usage_metadata：不谎报真值
