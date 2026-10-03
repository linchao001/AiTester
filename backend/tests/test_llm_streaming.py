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
