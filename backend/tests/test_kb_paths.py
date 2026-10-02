"""KB 实体根解析：与 reme store.py:57-70 的三级口径一致（配置 > 环境变量 > 家目录默认）。"""

from pathlib import Path

from aitester.config import Settings
from aitester.services.kb.paths import (
    hidden_segment,
    is_hidden,
    mtime_ms,
    resolve_kb_bases_dir,
    resolve_kb_root,
)


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


def test_default_fallback_is_resolved(tmp_path, monkeypatch):
    # 终审项 1：默认部署分支必须 resolve（注入含「.」段的未规范化 home 验证收敛）
    monkeypatch.delenv("REME_KNOWLEDGE_BASES_DIR", raising=False)
    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home / ".")
    got = resolve_kb_bases_dir(_s())
    assert got == (home / ".reme" / "knowledge_bases").resolve()
    assert "." not in got.parts


def test_is_hidden_case_insensitive():
    # 终审项 9：Windows 真实大小写不敏感
    assert is_hidden(".GIT") and is_hidden("Node_Modules") and is_hidden("__PyCache__")
    assert is_hidden(".hidden") and is_hidden("x.pyc") and is_hidden("Thumbs.DB")
    assert not is_hidden("README.md") and not is_hidden("business") and not is_hidden("_inbox")


def test_hidden_segment_returns_first_hit():
    # 读写两侧共用判据（终审项 2）：段级检查返回首个隐藏段
    assert hidden_segment(("a", ".git", "b.md")) == ".git"
    assert hidden_segment(("__pycache__", "x.md")) == "__pycache__"
    assert hidden_segment(("business", "wiki", "x.md")) is None


def test_mtime_ms_truncates_ns():
    # 终审项 6：ns → 整毫秒的唯一换算口
    class _St:
        st_mtime_ns = 1_234_567_890_123

    assert mtime_ms(_St()) == 1_234_567  # type: ignore[arg-type]
