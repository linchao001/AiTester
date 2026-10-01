from types import SimpleNamespace

from fastapi.testclient import TestClient

from aitester.config import Settings
from aitester.main import create_app


class _FakeKbManager:
    def __init__(self):
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def close_all(self, timeout: float = 30.0):
        self.closed = True


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        kb_id="demo",
        kb_bases_dir=str(tmp_path / "knowledge_bases"),
        kb_embedding_api_key="",
    )


def test_kb_manager_starts_and_closes_with_app(tmp_path):
    fake = _FakeKbManager()
    app = create_app(
        model_config_path=tmp_path / "models.json",
        capability_config_path=tmp_path / "caps.json",
        settings=_settings(tmp_path),
        kb_manager=fake,
    )
    with TestClient(app):
        assert app.state.kb_manager is fake
        assert fake.started
    assert fake.closed


def test_default_settings_kb_fields():
    s = Settings(_env_file=None)
    assert s.kb_enabled is True
    assert s.kb_id == "zhb_kb"
    assert s.kb_embedding_dimensions == 1024
