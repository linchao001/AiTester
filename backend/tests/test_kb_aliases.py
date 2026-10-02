from pathlib import Path

import pytest

from aitester.config import Settings
from aitester.services.kb.aliases import (
    PROJECT_KB_DEFAULT,
    UnknownKbAlias,
    is_registered,
    registered_aliases,
    resolve_kb_id,
    resolve_kb_root_for,
)


def test_default_alias_is_kb_and_only_registered_alias() -> None:
    assert PROJECT_KB_DEFAULT == "kb"
    assert registered_aliases() == ["kb"]
    assert is_registered("kb") is True
    assert is_registered("") is False
    assert is_registered("KB") is False  # 别名大小写敏感，配置不猜用户意图


def test_resolve_kb_id_maps_alias_to_settings_kb_id(tmp_path: Path) -> None:
    s = Settings(_env_file=None, kb_id="zhb_kb", kb_bases_dir=str(tmp_path / "bases"))
    assert resolve_kb_id("kb", s) == "zhb_kb"


def test_resolve_unknown_alias_raises_chinese_detail() -> None:
    s = Settings(_env_file=None)
    with pytest.raises(UnknownKbAlias) as exc:
        resolve_kb_id("nope", s)
    assert "未知知识库" in str(exc.value.detail)


def test_resolve_kb_root_for_uses_bases_dir(tmp_path: Path) -> None:
    s = Settings(_env_file=None, kb_id="zhb_kb", kb_bases_dir=str(tmp_path / "bases"))
    assert resolve_kb_root_for("kb", s) == (tmp_path / "bases" / "zhb_kb").resolve()
