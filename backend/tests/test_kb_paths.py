"""KB 实体根解析：与 reme store.py:57-70 的三级口径一致（配置 > 环境变量 > 家目录默认）。"""

from pathlib import Path

from aitester.config import Settings
from aitester.services.kb.paths import resolve_kb_bases_dir, resolve_kb_root


def _s(**kw):
    return Settings(_env_file=None, **kw)


def test_explicit_config_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REME_KNOWLEDGE_BASES_DIR", str(tmp_path / "env"))
    got = resolve_kb_bases_dir(_s(kb_bases_dir=str(tmp_path / "cfg")))
    assert got == (tmp_path / "cfg").resolve()


def test_env_beats_default(tmp_path, monkeypatch):
    monkeypatch.setenv("REME_KNOWLEDGE_BASES_DIR", str(tmp_path / "env"))
    assert resolve_kb_bases_dir(_s()) == (tmp_path / "env").resolve()


def test_default_is_home_reme(tmp_path, monkeypatch):
    monkeypatch.delenv("REME_KNOWLEDGE_BASES_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_kb_bases_dir(_s()) == tmp_path / ".reme" / "knowledge_bases"


def test_root_joins_kb_id(tmp_path, monkeypatch):
    monkeypatch.setenv("REME_KNOWLEDGE_BASES_DIR", str(tmp_path / "env"))
    assert resolve_kb_root(_s(kb_id="zhb_kb")) == (tmp_path / "env" / "zhb_kb").resolve()
