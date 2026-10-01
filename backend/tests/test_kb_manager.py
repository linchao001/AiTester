import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aitester.services.kb.manager import KbUnavailableError, RemeKbManager


def _settings(tmp_path, **kw):
    base = dict(
        kb_enabled=True,
        kb_id="demo",
        kb_bases_dir=str(tmp_path / "knowledge_bases"),
        kb_create_missing=False,
        kb_embedding_api_key="",
        kb_embedding_base_url="https://example.invalid/v1",
        kb_embedding_model="m",
        kb_embedding_dimensions=1024,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _seed_kb(tmp_path):
    kb_root = tmp_path / "knowledge_bases" / "demo"
    (kb_root / "business" / "wiki").mkdir(parents=True)
    (kb_root / "KB.md").write_text(
        "---\nid: demo\nname: Demo\ndomain: business\nversion: 1\n---\n",
        encoding="utf-8",
    )
    return kb_root


def test_disabled_manager_raises(tmp_path):
    mgr = RemeKbManager(settings=_settings(tmp_path, kb_enabled=False), data_dir=tmp_path)
    mgr.start()
    assert mgr.is_started is False
    with pytest.raises(KbUnavailableError):
        mgr.run_job_sync("status")


def test_save_reindex_search_roundtrip(tmp_path):
    kb_root = _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        saved = mgr.run_job_sync(
            "save_to_knowledge",
            title="测试节点",
            content="这是一个测试知识节点。",
            bucket="business/wiki",
        )
        assert saved.success
        assert (kb_root / "business" / "wiki" / "测试节点.md").is_file()

        bases = mgr.run_job_sync("list_knowledge_bases")
        assert bases.success
        assert "demo" in json.dumps(bases.metadata, ensure_ascii=False) + str(bases.answer)

        mgr.run_job_sync("reindex")
        deadline = time.time() + 20
        blob = ""
        while time.time() < deadline:
            found = mgr.run_job_sync("knowledge_search", query="测试知识节点", limit=5)
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "测试节点" in blob:
                break
            time.sleep(1)
        assert "测试节点" in blob
    finally:
        mgr.close_all()
    assert mgr.is_started is False


def test_two_agents_get_two_workspaces(tmp_path):
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        mgr.run_job_sync("status", project_id="p1", agent_id="a1")
        mgr.run_job_sync("status", project_id="p1", agent_id="a2")
        assert (tmp_path / "data" / "workspaces" / "p1" / "a1").is_dir()
        assert (tmp_path / "data" / "workspaces" / "p1" / "a2").is_dir()
    finally:
        mgr.close_all()
