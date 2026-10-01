"""网页搜索工具测试：provider 协议、402 配额分支、格式化与工具级降级文案。

对齐 QwenPaw ``tests/unit/agents/tools/test_websearch_providers.py`` 与
``test_websearch_anysearch_quota.py`` 的覆盖意图；模型可见文案断言一律英文。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.availability import unavailable_reason
from aitester.adapters.tools.web_tools import web_search as web_search_module
from aitester.adapters.tools.web_tools.web_search import MAX_RESULTS, WebSearchTool
from aitester.adapters.tools.web_tools.websearch import (
    AnySearchProvider,
    SearchProvider,
    TavilyProvider,
    format_search_results,
    get_search_provider,
)
from aitester.adapters.tools.web_tools.websearch import anysearch as anysearch_module
from aitester.adapters.tools.web_tools.websearch import tavily as tavily_module
from aitester.adapters.tools.web_tools.websearch.anysearch import (
    _parse_auto_registered_credentials,
    current_anysearch_key,
)
from aitester.adapters.tools.web_tools.websearch.factory import PROVIDER_ENV

_RESULTS = [
    {"title": "Python 3.13", "url": "https://python.org/3.13", "content": "What's new."},
    {"title": "Docs", "url": "https://docs.python.org", "content": ""},
]

# 402 自动注册消息的典型形状（凭据行末尾带句点，需剥掉）
_AUTO_REGISTER_MESSAGE = (
    "A new account has been automatically generated for you.\n"
    "username=anon_abc123\n"
    "password=Xyz12345\n"
    "api_key=ask-live-001."
)
_ANONYMOUS_QUOTA_MESSAGE = "You have exhausted your anonymous free quota."


class _StubProvider(SearchProvider):
    name = "stub"

    def __init__(self, results: list[dict] | None = None, error: Exception | None = None) -> None:
        self.results = results
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return list(self.results or [])


@pytest.fixture(autouse=True)
def _isolate_anysearch_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """AnySearch Key 是模块级进程内缓存：每个用例都从空开始，结束后自动还原。"""
    monkeypatch.setattr(anysearch_module, "_api_key", "")


def _call(tool, args: dict, call_id: str = "call_1") -> ToolMessage:
    result = tool.invoke(
        {"name": tool.name, "args": args, "id": call_id, "type": "tool_call"}
    )
    assert isinstance(result, ToolMessage)
    return result


def _status_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.anysearch.com/v1/search")
    response = httpx.Response(status_code, json={"message": message}, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


def test_format_search_results_renders_numbered_blocks() -> None:
    text = format_search_results(_RESULTS)
    assert text == (
        "[1] Python 3.13\n"
        "    URL: https://python.org/3.13\n"
        "    What's new.\n"
        "\n"
        "[2] Docs\n"
        "    URL: https://docs.python.org"
    )


def test_format_search_results_empty_list() -> None:
    assert format_search_results([]) == "No results found."


def test_tavily_provider_posts_keyless_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        captured.update(url=url, headers=headers, payload=payload)
        return {"results": _RESULTS}

    monkeypatch.setattr(tavily_module, "_post", fake_post)
    results = TavilyProvider().search("python 3.13", max_results=3)
    assert results == _RESULTS
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["X-Tavily-Access-Mode"] == "keyless"
    assert captured["payload"] == {
        "query": "python 3.13",
        "max_results": 3,
        "search_depth": "basic",
    }


def test_tavily_provider_tolerates_missing_results_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tavily_module, "_post", lambda url, headers, payload: {})
    assert TavilyProvider().search("q") == []


def test_anysearch_provider_anonymous_request_has_no_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        captured.update(url=url, headers=headers, payload=payload)
        return {"data": {"results": _RESULTS}}

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    assert AnySearchProvider().search("q", max_results=2) == _RESULTS
    assert captured["url"] == "https://api.anysearch.com/v1/search"
    assert "Authorization" not in captured["headers"]
    assert captured["payload"] == {"query": "q", "max_results": 2}


def test_anysearch_provider_uses_cached_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anysearch_module, "_api_key", "ask-cached")
    captured: dict[str, Any] = {}

    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        captured["headers"] = headers
        return {"data": {"results": _RESULTS}}

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    assert AnySearchProvider().search("q") == _RESULTS
    assert captured["headers"]["Authorization"] == "Bearer ask-cached"
    assert current_anysearch_key() == "ask-cached"


def test_parse_auto_registered_credentials_strips_trailing_dot() -> None:
    creds = _parse_auto_registered_credentials(_AUTO_REGISTER_MESSAGE)
    assert creds == {
        "username": "anon_abc123",
        "password": "Xyz12345",
        "api_key": "ask-live-001",
    }


def test_anysearch_402_auto_registers_key_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        calls.append(dict(headers))
        if len(calls) == 1:
            raise _status_error(402, _AUTO_REGISTER_MESSAGE)
        return {"data": {"results": _RESULTS}}

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    assert AnySearchProvider().search("q") == _RESULTS
    assert len(calls) == 2
    assert "Authorization" not in calls[0]
    assert calls[1]["Authorization"] == "Bearer ask-live-001"
    assert current_anysearch_key() == "ask-live-001"  # Key 写回进程内缓存


def test_anysearch_402_auto_register_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        raise _status_error(402, "A new account has been automatically generated for you.")

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    with pytest.raises(ValueError, match="missing api_key"):
        AnySearchProvider().search("q")


def test_anysearch_402_anonymous_quota_sleeps_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    class _FakeTime:
        @staticmethod
        def sleep(seconds: float) -> None:
            slept.append(seconds)

    monkeypatch.setattr(anysearch_module, "time", _FakeTime)
    calls: list[int] = []

    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        calls.append(1)
        if len(calls) == 1:
            raise _status_error(402, _ANONYMOUS_QUOTA_MESSAGE)
        return {"data": {"results": _RESULTS}}

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    assert AnySearchProvider().search("q") == _RESULTS
    assert slept == [1]
    assert len(calls) == 2


def test_anysearch_402_anonymous_quota_second_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeTime:
        @staticmethod
        def sleep(seconds: float) -> None:
            pass

    monkeypatch.setattr(anysearch_module, "time", _FakeTime)

    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        raise _status_error(402, _ANONYMOUS_QUOTA_MESSAGE)

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    with pytest.raises(ValueError, match=r"AnySearch quota error \(402\)"):
        AnySearchProvider().search("q")


def test_anysearch_402_other_message_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        raise _status_error(402, "Payment required for the pro tier.")

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    with pytest.raises(ValueError, match="Payment required for the pro tier"):
        AnySearchProvider().search("q")


def test_anysearch_non_402_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, headers: dict, payload: dict) -> dict:
        raise _status_error(500, "server exploded")

    monkeypatch.setattr(anysearch_module, "_post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        AnySearchProvider().search("q")


def test_factory_defaults_to_tavily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    provider = get_search_provider()
    assert isinstance(provider, TavilyProvider) and provider.name == "tavily"


@pytest.mark.parametrize("value", ["tavily", " Tavily "])
def test_factory_accepts_tavily_spellings(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(PROVIDER_ENV, value)
    assert isinstance(get_search_provider(), TavilyProvider)


def test_factory_selects_anysearch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_ENV, "anysearch")
    assert isinstance(get_search_provider(), AnySearchProvider)


def test_factory_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_ENV, "bing")
    with pytest.raises(ValueError, match="Unknown web_search provider"):
        get_search_provider()


def test_web_search_returns_formatted_content_and_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubProvider(results=_RESULTS)
    monkeypatch.setattr(web_search_module, "get_search_provider", lambda: stub)
    msg = _call(WebSearchTool(), {"search_term": "  python 3.13  "})
    assert msg.content == format_search_results(_RESULTS)
    assert msg.artifact["query"] == "python 3.13"  # 前后空白已剥
    assert msg.artifact["provider"] == "stub"
    assert msg.artifact["results"] == _RESULTS
    assert isinstance(msg.artifact["durationMs"], int)
    assert stub.calls == [("python 3.13", MAX_RESULTS)]


def test_web_search_empty_results_render_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search_module, "get_search_provider", lambda: _StubProvider())
    msg = _call(WebSearchTool(), {"search_term": "q"})
    assert msg.content == "No results found."


def test_web_search_blank_term_is_rejected() -> None:
    with pytest.raises(ToolException, match="search_term must be a non-empty string"):
        _call(WebSearchTool(), {"search_term": "   "})


def test_web_search_failure_adds_curl_fallback_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        web_search_module, "get_search_provider", lambda: _StubProvider(error=RuntimeError("boom"))
    )
    with pytest.raises(ToolException) as exc_info:
        _call(WebSearchTool(), {"search_term": "q"})
    message = str(exc_info.value)
    assert "web_search failed: boom" in message
    assert "pwsh or bash tool with curl" in message


def test_web_search_unknown_provider_surfaces_as_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_ENV, "bing")
    with pytest.raises(ToolException, match="Unknown web_search provider"):
        _call(WebSearchTool(), {"search_term": "q"})


def test_registry_includes_web_search() -> None:
    tool = build_default_registry().get("web_search")
    assert tool.name == "web_search"
    assert "Search the web" in tool.description
    assert "curl" in tool.description


def test_web_search_is_available_on_every_platform() -> None:
    assert unavailable_reason("web_search") is None
