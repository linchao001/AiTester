import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import reme

from aitester.config import Settings
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


def test_cross_instance_convergence_without_explicit_reindex(tmp_path):
    """spec 裁定4：读侧各实例索引经后台 watch 循环最终一致（秒级收敛）。

    实例 A（p1/a1）save + 显式 reindex（写方立即可见语义保留）后，
    实例 B（p1/a2）不调 reindex：
    1) B 首启后经 index_update_loop 的 init_changes 全量索引收敛；
    2) B 运行期间 A 再写入新节点，B 经 watch_changes 增量收敛。
    """
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        saved = mgr.run_job_sync(
            "save_to_knowledge",
            title="跨实例节点",
            content="这是跨实例收敛测试节点。",
            bucket="business/wiki",
            project_id="p1",
            agent_id="a1",
        )
        assert saved.success
        mgr.run_job_sync("reindex", project_id="p1", agent_id="a1")

        blob = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            found = mgr.run_job_sync(
                "knowledge_search",
                query="跨实例收敛测试",
                limit=5,
                project_id="p1",
                agent_id="a2",
            )
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "跨实例节点" in blob:
                break
            time.sleep(1)
        assert "跨实例节点" in blob, f"实例 B 首启 30 秒内未经 reindex 未收敛：{blob}"

        saved2 = mgr.run_job_sync(
            "save_to_knowledge",
            title="实时监听节点",
            content="这是实时监听收敛测试节点。",
            bucket="business/wiki",
            project_id="p1",
            agent_id="a1",
        )
        assert saved2.success
        blob2 = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            found2 = mgr.run_job_sync(
                "knowledge_search",
                query="实时监听收敛测试",
                limit=5,
                project_id="p1",
                agent_id="a2",
            )
            blob2 = json.dumps(found2.metadata, ensure_ascii=False) + str(found2.answer)
            if found2.success and "实时监听节点" in blob2:
                break
            time.sleep(1)
        assert "实时监听节点" in blob2, f"实例 B 运行期 watch 增量 30 秒内未收敛：{blob2}"
    finally:
        mgr.close_all()


def test_run_job_async_bridge(tmp_path):
    """Task 5 异步端点消费的 run_job 桥：asyncio.run 驱动真 manager + 真 KB。"""
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        async def main():
            return await mgr.run_job("status", project_id="p1", agent_id="a1")

        resp = asyncio.run(main())
        assert resp.success
    finally:
        mgr.close_all()


def test_failed_construction_is_popped_and_retries(tmp_path, monkeypatch):
    """构造期异常（reme Application.__init__ 真会抛）不得留下 FAILED 任务毒化 key。

    首次构造抛错后：_start_tasks 必须已摘除该 key；第二次调用应重新构造并成功，
    而不是永远重放缓存的旧异常。
    """
    _seed_kb(tmp_path)
    real_application = reme.Application
    attempts = []

    class FlakyApplication(real_application):
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise RuntimeError("模拟构造期失败（ensure_knowledge_mount/wiring）")
            super().__init__(**kwargs)

    monkeypatch.setattr(reme, "Application", FlakyApplication)

    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        # 终审裁定：启动失败以 KbUnavailableError 收敛（供路由层映射 503），
        # 原文案仍随 __cause__ 携带在消息中
        with pytest.raises(KbUnavailableError, match="知识库实例启动失败"):
            mgr.run_job_sync("status", project_id="p1", agent_id="a1", timeout=60.0)
        assert ("p1", "a1") not in mgr._start_tasks, "失败启动的任务滞留缓存，key 被永久污染"
        resp = mgr.run_job_sync("status", project_id="p1", agent_id="a1", timeout=120.0)
        assert resp.success
        assert len(attempts) == 2
        assert ("p1", "a1") in mgr._apps
    finally:
        monkeypatch.setattr(reme, "Application", real_application)
        mgr.close_all()


def test_concurrent_same_key_starts_exactly_one_application(tmp_path, monkeypatch):
    """同一 (project, agent) 并发提交只允许启动一个 Application（单飞）。

    测试侧慢钩子：monkeypatch 出的子类在真实 start() 前 sleep，放大
    check-then-construct 的 await 窗口以暴露竞争——生产代码零改动。
    """
    _seed_kb(tmp_path)
    constructed = []
    real_application = reme.Application

    class SlowApplication(real_application):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            constructed.append(self)

        async def start(self):
            await asyncio.sleep(0.5)  # 仅测试内的慢钩子
            await super().start()

    monkeypatch.setattr(reme, "Application", SlowApplication)

    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: mgr.run_job_sync(
                        "status", project_id="p1", agent_id="a1", timeout=120.0
                    ),
                    range(4),
                )
            )
        assert all(r.success for r in results)
        assert len(constructed) == 1, f"并发下 Application 被重复构造 {len(constructed)} 次"
        assert len(mgr._apps) == 1
        assert mgr._apps[("p1", "a1")] is constructed[0]
    finally:
        mgr.close_all()


def test_kb_root_dir_and_workspace_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("REME_KNOWLEDGE_BASES_DIR", raising=False)
    m = RemeKbManager(
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"), kb_id="zhb_kb"),
        data_dir=tmp_path,
    )
    assert m.kb_root_dir == (tmp_path / "bases" / "zhb_kb").resolve()
    assert m.workspace_dir("default", "kb_assistant") == tmp_path / "workspaces" / "default" / "kb_assistant"
