from pathlib import Path

import pytest

from aitester.storage import ConfigStorageError, FileJsonConfigRepository


def _repo(tmp_path: Path) -> FileJsonConfigRepository:
    return FileJsonConfigRepository(tmp_path / "model_config.json")


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert _repo(tmp_path).load() is None


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    cfg = {"version": 1, "default_uid": "deepseek/deepseek-flash", "providers": []}
    repo = _repo(tmp_path)
    repo.save(cfg)
    assert repo.load() == cfg


def test_save_leaves_no_tmp_residue(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.save({"version": 1, "default_uid": "", "providers": []})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["model_config.json"]


def test_load_corrupt_json_raises_with_path(tmp_path: Path) -> None:
    target = tmp_path / "model_config.json"
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigStorageError) as exc_info:
        _repo(tmp_path).load()
    assert str(target) in exc_info.value.detail
