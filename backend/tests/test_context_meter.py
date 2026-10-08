from __future__ import annotations

import math
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from aitester.context import meter


class _FakeEnc:
    """一个 token = 一个字符，好让断言是整数而不是「近似」。"""

    def encode(self, text, disallowed_special=()):
        return list(text)


def setup_function(_):
    meter.reset_for_tests()


def test_fallback_estimate_without_vocab_and_never_blocks():
    assert meter.is_warm() is False
    assert meter.estimate_text("x" * 3000) == math.ceil(3000 / 3 * 1.15)   # 1150
    assert meter.estimate_text("") == 1        # 空串也至少 1，别让一条空消息算成 0


def test_warm_disabled_by_env_records_reason_and_returns_false(monkeypatch):
    monkeypatch.setenv(meter.WARM_ENV, "0")
    assert meter.warm() is False
    assert meter.is_warm() is False
    assert "AITESTER_TOKEN_WARM" in (meter.warm_error() or "")


def test_warm_is_single_flight_and_idempotent(monkeypatch):
    calls: list[str] = []
    monkeypatch.setitem(
        __import__("sys").modules, "tiktoken",
        SimpleNamespace(get_encoding=lambda name: (calls.append(name), _FakeEnc())[1]),
    )
    assert meter.warm() is True
    assert meter.warm() is True
    assert calls == [meter.ENCODING_NAME]      # 只取一次
    assert meter.estimate_text("abcd") == math.ceil(4 * 1.15)   # 5：真词表路径


def test_warm_failure_falls_back_without_raising(monkeypatch):
    def boom(name):
        raise OSError("no network")
    monkeypatch.setitem(__import__("sys").modules, "tiktoken",
                       SimpleNamespace(get_encoding=boom))
    assert meter.warm() is False
    assert "词表不可用" in (meter.warm_error() or "")
    assert meter.estimate_text("hello") == math.ceil(5 / 3 * 1.15)     # 兜底照样可用


def test_mixed_cjk_ascii_and_code_samples_all_estimate_positive_and_ordered():
    """spec §6 的三类样本：CJK、ASCII、中英混排代码片段都要出正数，且随长度单调。"""
    assert meter.estimate_text("生成用例并写入知识库") > 0
    assert meter.estimate_text("generate cases and write them to the kb") > 0
    assert meter.estimate_text("def f(x):\n    return x * 2  # 双跑") > 0
    assert meter.estimate_text("a" * 300) > meter.estimate_text("a" * 100)
    assert meter.estimate_messages([]) == 0           # 空表是 0，不是「1 条空消息」


def test_estimate_messages_counts_role_content_names_and_tool_calls():
    one = meter.estimate_messages([HumanMessage(content="hello")])
    two = meter.estimate_messages([HumanMessage(content="hello"),
                                   AIMessage(content="hi there")])
    assert two > one                                          # 单调
    called = AIMessage(content="", tool_calls=[
        {"id": "1", "name": "shell", "args": {"command": "dir"}}])
    assert meter.estimate_messages([called]) > meter.estimate_messages(
        [AIMessage(content="")])                              # 工具调用不是免费的
    assert meter.estimate_messages([{"role": "system", "content": "sys"}]) > 0
    assert meter.estimate_messages([ToolMessage(content="t", tool_call_id="1",
                                                name="shell")]) > meter.estimate_messages(
        [ToolMessage(content="t", tool_call_id="1")])         # 工具名也计入


def test_truth_readers_prefer_usage_metadata_then_response_metadata():
    # 本版本 langchain_core 的 UsageMetadata 要求 total_tokens，补齐才构得出消息
    usage = {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
    assert meter.input_tokens_of(AIMessage(content="", usage_metadata=usage)) == 7
    assert meter.output_tokens_of(AIMessage(content="", usage_metadata=usage)) == 2
    assert meter.input_tokens_of(SimpleNamespace(
        usage_metadata=None,
        response_metadata={"token_usage": {"prompt_tokens": 9, "completion_tokens": 3}})) == 9
    assert meter.output_tokens_of(SimpleNamespace(
        usage_metadata=None,
        response_metadata={"token_usage": {"prompt_tokens": 9, "completion_tokens": 3}})) == 3
    assert meter.input_tokens_of(AIMessage(content="no usage")) is None     # 关键：缺真值返 None
    assert meter.output_tokens_of(None) is None
