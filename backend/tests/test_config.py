from aitester.config import Settings, get_settings


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.llm_provider == "mock"


def test_get_settings_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)
