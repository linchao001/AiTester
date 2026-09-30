from aitester.config import Settings, get_settings


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.deepseek_api_key == ""
    assert s.dashscope_api_key == ""
    assert not hasattr(s, "llm_provider")


def test_get_settings_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)
