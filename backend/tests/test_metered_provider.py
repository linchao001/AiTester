# backend/tests/test_metered_provider.py
from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from aitester.adapters.llm.metered import MeteredProvider
from aitester.adapters.llm.mock import MockProvider
from aitester.context import meter
from aitester.context.usage import ContextUsage
from aitester.services.model_config import ModelConfigService

MSG = [HumanMessage(content="hello there this is a prompt")]


def setup_function(_):
    meter.reset_for_tests()


def test_stream_counts_a_round_even_without_truth():
    u = ContextUsage(window=1000)
    p = MeteredProvider(MockProvider(), u)
    out = "".join(str(c.content) for c in p.stream_messages(MSG))
    assert out.startswith("[mock]")
    assert (u.rounds, u.occupancy_source) == (1, "estimated")
    assert u.peak_occupancy == meter.estimate_messages(MSG)


def test_stream_uses_final_chunk_usage_as_truth_and_does_not_drop_chunks():
    class _T:
        def stream_messages(self, messages):
            yield AIMessageChunk(content="he")
            yield AIMessageChunk(content="llo", usage_metadata={
                "input_tokens": 4242, "output_tokens": 11, "total_tokens": 4253})

    u = ContextUsage(window=1000)
    p = MeteredProvider(_T(), u)
    assert "".join(str(c.content) for c in p.stream_messages(MSG)) == "hello"
    assert (u.rounds, u.peak_occupancy, u.occupancy_source) == (1, 4242, "actual")
    assert u.spent_output == 11


def test_abandoned_stream_keeps_the_round_estimated():
    """消费者断开（取消/关页）时不许冒领真值：那一轮保持 estimated。"""
    class _T:
        def stream_messages(self, messages):
            yield AIMessageChunk(content="a")
            yield AIMessageChunk(content="b", usage_metadata={
                "input_tokens": 9, "output_tokens": 1, "total_tokens": 10})

    u = ContextUsage(window=1000)
    p = MeteredProvider(_T(), u)
    gen = p.stream_messages(MSG)
    assert next(gen).content == "a"               # 注意用 .content：str(chunk) 是 repr
    gen.close()
    assert (u.rounds, u.occupancy_source) == (1, "estimated")


def test_invoke_and_complete_both_count():
    u = ContextUsage(window=1000)
    p = MeteredProvider(MockProvider(), u)
    assert isinstance(p.invoke_messages(MSG), AIMessage)
    assert p.complete([{"role": "user", "content": "hi"}]) == "[mock] hi"
    assert u.rounds == 2


def test_bind_tools_shares_one_ledger_and_proxies_identity():
    u = ContextUsage(window=1234)
    p = MeteredProvider(MockProvider(), u)
    bound = p.bind_tools([])
    assert isinstance(bound, MeteredProvider)
    assert bound.usage is u and bound.usage.window == 1234  # 同一个累加件：一回合只一本账
    assert bound.name == p.name and bound.model_ref == p.model_ref
    it = bound.stream_messages(MSG)
    assert u.rounds == 0                            # 没人开始迭代就没发起请求：轮次不许冒领
    assert list(it)
    assert u.rounds == 1                            # 绑定后的调用也记进父回合


def test_window_for_is_side_effect_free_and_zero_when_unknown():
    """只测纯读逻辑：`__new__` 出来不碰仓库、不碰文件（窗口读取必须零副作用）。"""
    svc = ModelConfigService.__new__(ModelConfigService)
    svc._config = {"default_uid": "deepseek/deepseek-chat", "providers": [{
        "id": "deepseek", "name": "DeepSeek", "base_url": "u", "api_key": "k",
        "enabled": True, "models": [
            {"id": "deepseek-chat", "enabled": True, "context": 131072},
            {"id": "weird", "enabled": True, "context": "131072"},   # 字符串不是数
        ]}]}
    assert ModelConfigService.window_for(svc, "deepseek/deepseek-chat") == 131072
    assert ModelConfigService.window_for(svc, "deepseek/weird") == 0   # 脏元数据 ⇒ 未知，不抛
    assert ModelConfigService.window_for(svc, "") == 0
    assert ModelConfigService.window_for(svc, "deepseek/nope") == 0
    assert ModelConfigService.window_for(svc, "nope/x") == 0


def test_openai_compat_passes_stream_usage(monkeypatch):
    from aitester.adapters.llm import openai_compat

    seen: dict[str, object] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChatOpenAI)
    openai_compat.OpenAICompatProvider(name="p", api_key="k", base_url="u",
                                      model="m", stream_usage=True)
    assert seen["stream_usage"] is True
