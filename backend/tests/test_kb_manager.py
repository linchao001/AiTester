import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import reme

from aitester.config import Settings
from aitester.memory.reme.manager import KbUnavailableError, RemeMemoryManager


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


def _resolver(tmp_path):
    """测试用：project_id → tmp_path/projects/<id>。"""

    def resolve(pid: str) -> Path:
        return tmp_path / "projects" / pid

    return resolve


def _mgr(tmp_path, **kw) -> RemeMemoryManager:
    return RemeMemoryManager(
        settings=_settings(tmp_path, **kw),
        data_dir=tmp_path / "data",
        project_dir_resolver=_resolver(tmp_path),
    )


def test_disabled_manager_raises(tmp_path):
    mgr = RemeMemoryManager(
        settings=_settings(tmp_path, kb_enabled=False),
        data_dir=tmp_path,
        project_dir_resolver=_resolver(tmp_path),
    )
    mgr.start()
    assert mgr.is_started is False
    with pytest.raises(KbUnavailableError):
        mgr.run_job_sync("status")


def test_save_reindex_search_roundtrip(tmp_path):
    kb_root = _seed_kb(tmp_path)
    mgr = _mgr(tmp_path)
    mgr.start()
    try:
        saved = mgr.run_job_sync(
            "save_to_knowledge",
            title="测试节点",
            content="这是一个测试知识节点。",
            bucket="business/wiki",
            project_id="p1",
        )
        assert saved.success
        assert (kb_root / "business" / "wiki" / "测试节点.md").is_file()

        bases = mgr.run_job_sync("list_knowledge_bases", project_id="p1")
        assert bases.success
        assert "demo" in json.dumps(bases.metadata, ensure_ascii=False) + str(bases.answer)

        mgr.run_job_sync("reindex", project_id="p1")
        deadline = time.time() + 20
        blob = ""
        while time.time() < deadline:
            found = mgr.run_job_sync(
                "knowledge_search", query="测试知识节点", limit=5, project_id="p1",
            )
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "测试节点" in blob:
                break
            time.sleep(1)
        assert "测试节点" in blob
    finally:
        mgr.close_all()
    assert mgr.is_started is False


def test_two_agents_share_one_workspace(tmp_path):
    """2A：同 project 不同 agent 共用一个 Reme 实例与同一项目目录 workspace。"""
    _seed_kb(tmp_path)
    mgr = _mgr(tmp_path)
    mgr.start()
    try:
        mgr.run_job_sync("status", project_id="p1", agent_id="a1")
        mgr.run_job_sync("status", project_id="p1", agent_id="a2")
        ws = (tmp_path / "projects" / "p1" / ".AiTester").resolve()
        assert ws.is_dir()
        assert (ws / "knowledge").is_dir()
        assert len(mgr._apps) == 1
        assert mgr.pool_key("p1", "a1") == mgr.pool_key("p1", "a2")
        assert str(ws) in mgr._apps
    finally:
        mgr.close_all()


def test_two_projects_same_dir_share_instance(tmp_path):
    """同绑定目录的两个 project_id 合并为同一池键。"""
    _seed_kb(tmp_path)
    shared = tmp_path / "shared_proj"
    shared.mkdir()

    def resolve(pid: str) -> Path:
        return shared

    mgr = RemeMemoryManager(
        settings=_settings(tmp_path),
        data_dir=tmp_path / "data",
        project_dir_resolver=resolve,
    )
    mgr.start()
    try:
        mgr.run_job_sync("status", project_id="p1", agent_id="a1")
        mgr.run_job_sync("status", project_id="p2", agent_id="a2")
        assert len(mgr._apps) == 1
        key = str((shared / ".AiTester").resolve())
        assert mgr.pool_key("p1") == mgr.pool_key("p2") == key
    finally:
        mgr.close_all()


def test_cross_project_convergence_without_explicit_reindex(tmp_path):
    """两项目目录各挂同一实体：写方 reindex 后读方经 watch 收敛。"""
    _seed_kb(tmp_path)
    mgr = _mgr(tmp_path)
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
                project_id="p2",
                agent_id="a2",
            )
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "跨实例节点" in blob:
                break
            time.sleep(1)
        assert "跨实例节点" in blob, f"项目 B 首启 30 秒内未经 reindex 未收敛：{blob}"

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
                project_id="p2",
                agent_id="a2",
            )
            blob2 = json.dumps(found2.metadata, ensure_ascii=False) + str(found2.answer)
            if found2.success and "实时监听节点" in blob2:
                break
            time.sleep(1)
        assert "实时监听节点" in blob2, f"项目 B 运行期 watch 增量 30 秒内未收敛：{blob2}"
    finally:
        mgr.close_all()


def test_run_job_async_bridge(tmp_path):
    """Task 5 异步端点消费的 run_job 桥：asyncio.run 驱动真 manager + 真 KB。"""
    _seed_kb(tmp_path)
    mgr = _mgr(tmp_path)
    mgr.start()
    try:
        async def main():
            return await mgr.run_job("status", project_id="p1", agent_id="a1")

        resp = asyncio.run(main())
        assert resp.success
    finally:
        mgr.close_all()


def test_failed_construction_is_popped_and_retries(tmp_path, monkeypatch):
    """构造期异常不得留下 FAILED 任务毒化 key。"""
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

    mgr = _mgr(tmp_path)
    mgr.start()
    key = mgr.pool_key("p1", "a1")
    try:
        with pytest.raises(KbUnavailableError, match="知识库实例启动失败"):
            mgr.run_job_sync("status", project_id="p1", agent_id="a1", timeout=60.0)
        assert key not in mgr._start_tasks, "失败启动的任务滞留缓存，key 被永久污染"
        resp = mgr.run_job_sync("status", project_id="p1", agent_id="a1", timeout=120.0)
        assert resp.success
        assert len(attempts) == 2
        assert key in mgr._apps
    finally:
        monkeypatch.setattr(reme, "Application", real_application)
        mgr.close_all()


def test_concurrent_same_key_starts_exactly_one_application(tmp_path, monkeypatch):
    """同一 workspace 路径并发提交只允许启动一个 Application（单飞）。"""
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

    mgr = _mgr(tmp_path)
    mgr.start()
    key = mgr.pool_key("p1", "a1")
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
        assert mgr._apps[key] is constructed[0]
    finally:
        mgr.close_all()


def test_kb_root_dir_and_workspace_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("REME_KNOWLEDGE_BASES_DIR", raising=False)
    m = RemeMemoryManager(
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases"), kb_id="zhb_kb"),
        data_dir=tmp_path,
        project_dir_resolver=lambda pid: tmp_path / "projects" / pid,
    )
    assert m.kb_root_dir == (tmp_path / "bases" / "zhb_kb").resolve()
    with pytest.raises(KbUnavailableError, match="必须绑定项目"):
        m.workspace_dir("", "kb_assistant")
    assert m.workspace_dir("proj_x", "case_design") == (
        tmp_path / "projects" / "proj_x" / ".AiTester"
    ).resolve()
    assert m.workspace_dir("proj_x", "kb_assistant") == (
        tmp_path / "projects" / "proj_x" / ".AiTester"
    ).resolve()


def test_drop_workspace_closes_instance(tmp_path):
    _seed_kb(tmp_path)
    mgr = _mgr(tmp_path)
    mgr.start()
    try:
        mgr.run_job_sync("status", project_id="p1")
        key = mgr.pool_key("p1")
        assert key in mgr._apps
        mgr.drop_workspace(tmp_path / "projects" / "p1" / ".AiTester")
        assert key not in mgr._apps
    finally:
        mgr.close_all()


def test_project_workspace_mount_visible(tmp_path):
    """项目 .AiTester/knowledge/ 为 junction，相对项目根可读为 .AiTester/knowledge/...。"""
    kb_root = _seed_kb(tmp_path)
    (kb_root / "business" / "wiki" / "hello.md").write_text("# hi\n", encoding="utf-8")
    mgr = _mgr(tmp_path)
    mgr.start()
    try:
        mgr.run_job_sync("status", project_id="p1")
        mount = (
            tmp_path / "projects" / "p1" / ".AiTester" / "knowledge"
            / "business" / "wiki" / "hello.md"
        )
        assert mount.is_file()
        assert mount.read_text(encoding="utf-8") == "# hi\n"
        # 项目根保持干净：不再直接出现 knowledge/
        assert not (tmp_path / "projects" / "p1" / "knowledge").exists()
    finally:
        mgr.close_all()
