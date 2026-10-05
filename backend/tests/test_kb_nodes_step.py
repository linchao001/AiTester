"""T5：三层节点桶 job 通道（list/upsert/delete）+ 插件 entry point + watch_dirs 落地面。"""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aitester.services.kb.config import KbConfig, build_reme_config
from aitester.services.kb.manager import RemeKbManager, _ensure_node_buckets
from aitester.services.kb.steps import parse_node_markdown, render_node_markdown

CHAIN_NODE = {"id": "ch-0001", "type": "chain", "name": "下单链路", "level": 1,
              "parent": "", "business_scope": "下单主流程", "excluded": ""}
STORY_NODE = {"id": "st-0001", "type": "story", "name": "提交订单", "chains": ["ch-0001"],
              "actor": "已登录用户", "preconditions": "库存充足", "trigger": "点击提交",
              "expected": "订单创建成功", "assumptions": ["优惠券可用"]}
POINT_NODE = {"id": "pt-0001", "type": "point", "name": "库存不足时提交", "story": "st-0001",
              "scenario": "库存为零时提交订单", "entities": ["订单", "库存"],
              "directions": ["负向"], "priority": "P1"}


def _settings(tmp_path, **kw):
    base = dict(kb_enabled=True, kb_id="demo", kb_bases_dir=str(tmp_path / "knowledge_bases"),
                kb_create_missing=False, kb_embedding_api_key="",
                kb_embedding_base_url="https://example.invalid/v1", kb_embedding_model="m",
                kb_embedding_dimensions=1024)
    base.update(kw)
    return SimpleNamespace(**base)


def _seed_kb(tmp_path):
    kb_root = tmp_path / "knowledge_bases" / "demo"
    (kb_root / "business" / "wiki").mkdir(parents=True)
    (kb_root / "KB.md").write_text("---\nid: demo\nname: Demo\ndomain: business\nversion: 1\n---\n",
                                   encoding="utf-8")
    return kb_root


def test_render_parse_roundtrip_three_layers():
    for layer, node in (("chain", CHAIN_NODE), ("story", STORY_NODE), ("point", POINT_NODE)):
        text = render_node_markdown(layer, node)
        row = parse_node_markdown(text, layer)
        assert row["id"] == node["id"] and row["name"] == node["name"]
        assert "updated_at" in row
        for key, value in node.items():
            if key in ("id", "type", "name"):
                continue
            assert row[key] == value, (layer, key)
    # chain 顶层空 parent 必须是「键在、值为空串」：P-3 判维护性靠 "parent" in row
    row = parse_node_markdown(render_node_markdown("chain", CHAIN_NODE), "chain")
    assert "parent" in row and row["parent"] == ""
    # 正文含人读标签与节点内容（reindex 后的检索内容面）
    body = render_node_markdown("story", STORY_NODE)
    assert "提交订单" in body and "主角" in body and "优惠券可用" in body


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_node_markdown("没有 frontmatter", "chain")
    with pytest.raises(ValueError):
        parse_node_markdown("---\nid: st-0001\ntype: story\n---\n", "chain")   # 类型/前缀不符
    with pytest.raises(ValueError):
        parse_node_markdown("---\nid: 乱\n type: chain\n---\n", "chain")      # id 形状非法


def test_config_declares_plugin_buckets_and_jobs(tmp_path):
    ws = tmp_path / "ws"
    cfg = build_reme_config(KbConfig(workspace_dir=str(ws), kb_id="demo"))
    assert cfg["plugins"] == ["aitester"]
    watch = cfg["jobs"]["index_update_loop"]["watch_dirs"]
    for bucket in ("business/chains", "business/stories", "business/test_points"):
        assert str(ws / "knowledge" / bucket) in watch
    assert cfg["jobs"]["case_nodes_list"]["steps"][0] == {"backend": "aitester_kb_nodes_step", "op": "list"}
    assert cfg["jobs"]["case_node_upsert"]["steps"][0]["op"] == "upsert"
    assert cfg["jobs"]["case_node_delete"]["steps"][0]["op"] == "delete"


def test_ensure_node_buckets_three_branches(tmp_path):
    # 1) KB 根存在 → 直接建三桶
    root = tmp_path / "bases" / "demo"
    (root / "business" / "wiki").mkdir(parents=True)
    (root / "KB.md").write_text("---\nid: demo\n---\n", encoding="utf-8")
    _ensure_node_buckets(KbConfig(workspace_dir=str(tmp_path / "ws"), kb_id="demo",
                                  kb_bases_dir=str(tmp_path / "bases")))
    for bucket in ("business/chains", "business/stories", "business/test_points"):
        assert (root / bucket).is_dir()
    # 2) 根缺失 + create_missing → 先补 KB 骨架（KB.md）再建桶
    root2 = tmp_path / "bases2" / "demo2"
    _ensure_node_buckets(KbConfig(workspace_dir=str(tmp_path / "ws2"), kb_id="demo2",
                                  kb_bases_dir=str(tmp_path / "bases2"), create_missing=True))
    assert (root2 / "KB.md").is_file() and (root2 / "business" / "chains").is_dir()
    # 3) 根缺失 + 不允许自建 → 无声跳过（mount 照旧响亮失败收敛为 KbUnavailableError）
    _ensure_node_buckets(KbConfig(workspace_dir=str(tmp_path / "ws3"), kb_id="demo3",
                                  kb_bases_dir=str(tmp_path / "bases3"), create_missing=False))
    assert not (tmp_path / "bases3" / "demo3").exists()


def test_plugin_entry_point_installed():
    from reme.entry_point import find_entry_points
    entries = find_entry_points("reme.plugins", "aitester")
    assert [e.value for e in entries] == ["aitester.kb_plugin"]


def test_node_roundtrip_via_manager(tmp_path):
    kb_root = _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        up = mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE)
        assert up.success, up.answer
        path = kb_root / "business" / "chains" / "ch-0001.md"
        assert path.is_file()
        # junction 单挂整个 KB 根：实体侧写入在 workspace/knowledge 下立即可见
        mount = mgr.workspace_dir("default", "console") / "knowledge" / "business" / "chains" / "ch-0001.md"
        assert mount.is_file()

        lst = mgr.run_job_sync("case_nodes_list", layer="chain")
        assert lst.success and lst.metadata["count"] == 1
        row = lst.metadata["nodes"][0]
        assert row["id"] == "ch-0001" and row["type"] == "chain"
        assert row["parent"] == "" and row["level"] == 1

        d1 = mgr.run_job_sync("case_node_delete", layer="chain", id="ch-0001")
        assert d1.success and d1.metadata["deleted"] is True
        assert not path.is_file()
        d2 = mgr.run_job_sync("case_node_delete", layer="chain", id="ch-0001")
        assert d2.success and d2.metadata["deleted"] is False  # 幂等：回写重试不炸（A7）
        lst2 = mgr.run_job_sync("case_nodes_list", layer="chain")
        assert lst2.metadata["count"] == 0 and lst2.metadata["nodes"] == []
    finally:
        mgr.close_all()


def test_step_rejects_bad_input(tmp_path):
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        bad_layer = mgr.run_job_sync("case_nodes_list", layer="bogus")
        assert bad_layer.success is False and "bogus" in bad_layer.answer
        bad_id = mgr.run_job_sync("case_node_upsert", layer="chain",
                                  node={**CHAIN_NODE, "id": "st-0001"})
        assert bad_id.success is False and "st-0001" in bad_id.answer
        no_node = mgr.run_job_sync("case_node_upsert", layer="chain")
        assert no_node.success is False and "node" in no_node.answer
        chain_dir = tmp_path / "knowledge_bases" / "demo" / "business" / "chains"
        assert list(chain_dir.glob("*.md")) == []              # 失败路径零落盘
    finally:
        mgr.close_all()


def test_node_bucket_joins_index(tmp_path):
    """P-4 正式断言：新桶经 reindex 后对 knowledge_search 可见（T1 探针结论落成常驻测试）。"""
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        up = mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE)
        assert up.success
        mgr.run_job_sync("reindex")
        blob = ""
        deadline = time.time() + 20
        while time.time() < deadline:
            found = mgr.run_job_sync("knowledge_search", query="下单链路", limit=5)
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "下单链路" in blob:
                break
            time.sleep(1)
        assert "下单链路" in blob
    finally:
        mgr.close_all()


# ---- 补充实测（简报 8 项之外）：T1 探针两条时序约束 CF-1a / CF-1b 的常驻证据 ----


def test_cf1_buckets_exist_and_are_in_watch_dirs_before_application_ctor(tmp_path, monkeypatch):
    """CF-1a+CF-1b 前提实测：新桶不在 reme PUBLISHED_BUCKETS（augment 不会替我们加），
    三桶 junction 路径由 build_reme_config 显式注入；且实体侧桶在
    Application 构造时刻已存在（watch_changes 只对启动时已存在的路径建监听）。"""
    import reme
    from reme.knowledge.store import knowledge_watch_dirs
    from aitester.case_design.constants import NODE_BUCKETS

    # CF-1a 事实面：reme 的发布桶与 augment 产物都不含新桶
    from reme.knowledge.store import PUBLISHED_BUCKETS
    assert not (set(NODE_BUCKETS) & set(PUBLISHED_BUCKETS))
    ws = tmp_path / "nowhere"
    assert not any(bucket in p.replace("\\", "/")
                   for p in knowledge_watch_dirs(ws, "knowledge")
                   for bucket in NODE_BUCKETS)

    _seed_kb(tmp_path)
    real_application = reme.Application
    snapshots = []

    class SpyApplication(real_application):
        def __init__(self, **kwargs):
            root = tmp_path / "knowledge_bases" / "demo"
            watch = kwargs["jobs"]["index_update_loop"]["watch_dirs"]
            snapshots.append({
                "at_ctor": [(root / b).is_dir() for b in NODE_BUCKETS],
                "watched": [str(Path(kwargs["workspace_dir"]) / "knowledge" / b) in watch
                            for b in NODE_BUCKETS],
            })
            super().__init__(**kwargs)

    monkeypatch.setattr(reme, "Application", SpyApplication)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        assert mgr.run_job_sync("status").success
    finally:
        monkeypatch.setattr(reme, "Application", real_application)
        mgr.close_all()

    assert snapshots, "Application 未被构造"
    assert all(all(s["at_ctor"]) and all(s["watched"]) for s in snapshots), \
        f"桶未在构造前落位或 watch_dirs 缺桶路径：{snapshots}"


def test_cf2_watch_loop_indexes_new_node_without_reindex(tmp_path):
    """CF-1a/CF-1b 接线后的行为面实测：无任何 reindex，运行期 upsert 的节点
    经增量 watch 秒级收敛进索引（P-4 收敛语义常驻证据）。

    实测备注（见 task-5-report）：reme 0.4.1.8 的 watch 是 knowledge 根递归轮询，
    晚建的桶也会被根规则兜住——因此本测试验「链路通」，结构性前提（显式 watch_dirs
    + 桶先于 Application 构造存在）由 CF1 测试单独钉住，防 watch 配置漂移。"""
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        up = mgr.run_job_sync("case_node_upsert", layer="story", node=STORY_NODE)
        assert up.success, up.answer
        blob = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            found = mgr.run_job_sync("knowledge_search", query="提交订单", limit=5)
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "提交订单" in blob:
                break
            time.sleep(1)
        assert "提交订单" in blob, f"30 秒内 watch 增量未收敛（CF-1a/CF-1b 被破坏？）：{blob}"
    finally:
        mgr.close_all()
