"""T5：三层节点桶 job 通道（list/upsert/delete）+ 插件 entry point + watch_dirs 落地面。

评审修复版（R-16 + Minor 1/3/4/5/6/7/8）：
- 夹具一律换成**全字段 DraftNode dump**（剥 op/reason 即 T8 回写 payload 形状，priority 保留），
  chain/story 的 priority 与空列表字段都在夹具里——旧夹具省略跨层共用字段，正好看不见 R-16 缺陷。
- 字段保真断言**走 job 通道**（run_job_sync upsert → case_nodes_list 读回逐字段等值），
  三层 list/upsert/delete 全覆盖（Minor 5），不只测纯函数。
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aitester.case_design.schema import DraftNode
from aitester.services.kb.config import KbConfig, build_reme_config
from aitester.services.kb.manager import RemeKbManager, _ensure_node_buckets
from aitester.services.kb.steps import (
    _NODE_FIELDS,
    _atomic_write,
    parse_node_markdown,
    render_node_markdown,
)

# 账本/夹具零产品线业务名词（全局约束）：一律用「实体甲/实体乙」这类中性名。


def _payload(layer: str, **fields: Any) -> dict:
    """全字段 DraftNode dump → 回写 payload 形状（剥 op/reason；priority 保留，R-16）。"""
    payload = DraftNode(type=layer, **fields).model_dump()
    payload.pop("op")
    payload.pop("reason")
    return payload


# chain/story 用 P0：R-16 的致祸形态正是「P0 链路回写丢 priority → 下一轮按 P1 兜底 →
# 其下合法 P0 故事被假报 hard priority_violation 卡死人工审门」。
CHAIN_NODE = _payload("chain", id="ch-0001", name="实体甲链路", level=1, parent="",
                      business_scope="实体甲总入口", excluded="", priority="P0")
STORY_NODE = _payload("story", id="st-0001", name="操作实体甲",
                      chains=["ch-0001", "ch-0002"], actor="测试账号",
                      preconditions="实体甲已就绪", trigger="发起操作",
                      expected="实体甲状态变更", assumptions=["环境已就绪"], priority="P0")
POINT_NODE = _payload("point", id="pt-0001", name="实体甲缺失时发起", story="st-0001",
                      scenario="实体甲为空时发起操作", entities=["实体甲"],
                      directions=["负向"], priority="P1")


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


def _expected_front_keys(layer: str) -> list[str]:
    """frontmatter 键序契约：公共键 id/type/name 在前，层字段按 _NODE_FIELDS 表序，updated_at 最后。"""
    return ["id", "type", "name", *_NODE_FIELDS[layer], "updated_at"]


def test_fixtures_are_full_draft_node_dumps():
    """夹具钉桩：每份都是全字段 dump（含 chain/story 的 priority 与其它层的空列表字段）。"""
    expected = set(DraftNode.model_fields) - {"op", "reason"}
    for node in (CHAIN_NODE, STORY_NODE, POINT_NODE):
        assert set(node) == expected
    assert CHAIN_NODE["priority"] == "P0" and CHAIN_NODE["chains"] == []
    assert STORY_NODE["priority"] == "P0" and STORY_NODE["entities"] == []


def test_render_parse_roundtrip_three_layers():
    for layer, node in (("chain", CHAIN_NODE), ("story", STORY_NODE), ("point", POINT_NODE)):
        text = render_node_markdown(layer, node)
        row = parse_node_markdown(text, layer)
        assert row["id"] == node["id"] and row["name"] == node["name"]
        assert "updated_at" in row
        assert list(row.keys()) == _expected_front_keys(layer)  # 键序=表序，priority 紧跟 name
        for key, value in node.items():
            if key in _NODE_FIELDS[layer]:
                assert row[key] == value, (layer, key)
            elif key not in ("id", "type", "name"):
                assert key not in row  # 其它层字段不泄漏进本层 frontmatter
    # chain 顶层空 parent 必须是「键在、值为空串」：P-3 判维护性靠 "parent" in row
    row = parse_node_markdown(render_node_markdown("chain", CHAIN_NODE), "chain")
    assert "parent" in row and row["parent"] == ""
    # 正文含人读标签与节点内容（reindex 后的检索内容面）；chain/story 正文也带「优先级」（R-16）
    body = render_node_markdown("story", STORY_NODE)
    assert "操作实体甲" in body and "主角" in body and "环境已就绪" in body
    assert "- 优先级：P0" in body


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


def test_field_fidelity_all_layers_via_job_channel(tmp_path):
    """R-16 + Minor 5：三层全字段 dump 走 job 通道逐字段等值；三层 list/upsert/delete 全覆盖。

    chain/story 的 priority 必须原样读回——④ 的优先级沿树检查直接读宇宙里的 chain/story 行，
    回写丢字段会让下一轮把 P0 存量当成 P1，合法 P0 故事被假报 hard priority_violation。
    多父故事 chains=["ch-0001","ch-0002"]（「重复比遗漏好」语义）也走 job 通道验证。
    """
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        for layer, node in (("chain", CHAIN_NODE), ("story", STORY_NODE), ("point", POINT_NODE)):
            up = mgr.run_job_sync("case_node_upsert", layer=layer, node=node)
            assert up.success, up.answer
            lst = mgr.run_job_sync("case_nodes_list", layer=layer)
            assert lst.success and lst.metadata["count"] == 1, lst.metadata
            row = lst.metadata["nodes"][0]
            assert list(row.keys()) == _expected_front_keys(layer), (layer, list(row.keys()))
            carried = {"id", "type", "name"} | set(_NODE_FIELDS[layer])
            for key, value in node.items():
                if key in carried:
                    assert row[key] == value, (layer, key, row.get(key), value)
            # R-16 显式钉桩：三层（含 chain/story）priority 键在且保值
            assert row["priority"] == node["priority"], layer
            # 多父故事：chains 列表原样读回（Minor 5，旧版只在纯函数层验过）
            if layer == "story":
                assert row["chains"] == ["ch-0001", "ch-0002"]
            d1 = mgr.run_job_sync("case_node_delete", layer=layer, id=node["id"])
            assert d1.success and d1.metadata["deleted"] is True
            d2 = mgr.run_job_sync("case_node_delete", layer=layer, id=node["id"])
            assert d2.success and d2.metadata["deleted"] is False
            lst2 = mgr.run_job_sync("case_nodes_list", layer=layer)
            assert lst2.metadata["count"] == 0 and lst2.metadata["nodes"] == []
    finally:
        mgr.close_all()


def test_empty_carried_fields_survive_job_channel(tmp_path):
    """共用字段缺省（priority 未给 → DraftNode 默认 P1）与层内空列表（assumptions=[]）
    走 job 通道后必须「键在、值原样」回来——宇宙读回不许出现缺键。"""
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        st2 = _payload("story", id="st-0002", name="操作实体乙", chains=["ch-0001"],
                       actor="测试账号", trigger="再发起", expected="变更留痕")
        assert st2["priority"] == "P1" and st2["assumptions"] == [] and st2["preconditions"] == ""
        up = mgr.run_job_sync("case_node_upsert", layer="story", node=st2)
        assert up.success, up.answer
        lst = mgr.run_job_sync("case_nodes_list", layer="story")
        assert lst.success and lst.metadata["count"] == 1
        row = lst.metadata["nodes"][0]
        assert list(row.keys()) == _expected_front_keys("story")
        assert row["assumptions"] == [] and row["priority"] == "P1"
        assert row["preconditions"] == ""
    finally:
        mgr.close_all()


def test_step_rejects_id_with_trailing_newline(tmp_path):
    """Minor 3：`.match`+`^…$` 会放走 "ch-0001\\n" → 文件名 ch-0001\\n.md（POSIX 垃圾）。
    step 是 FS 边界，_validate_id 必须 fullmatch 响亮拒绝，且失败路径零落盘。"""
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        bad = mgr.run_job_sync("case_node_upsert", layer="chain",
                               node={**CHAIN_NODE, "id": "ch-0001\n"})
        assert bad.success is False
        # 只断言 success 在 Windows 上会被冒充：`match` 放行后 `_atomic_write` 写
        # "ch-0001\n.md" 由 OS 拒绝（Errno 22），照样 success is False、目录照样空。
        # 钉住校验路径的专属文案，任何平台都必须走 _validate_id 才收敛。
        assert "invalid node id" in bad.answer, bad.answer
        chain_dir = tmp_path / "knowledge_bases" / "demo" / "business" / "chains"
        assert list(chain_dir.glob("*")) == []               # 连隐藏垃圾名都不许出现
    finally:
        mgr.close_all()


def test_list_yields_marker_row_for_corrupt_file(tmp_path):
    """Minor 4：坏文件 marker 分支原零用例——桶里一个坏节点文件不许炸 list，
    必须以 {"id":"", "file", "error"} 标记行进列表（P-3 判「未维护」依赖此形状）。"""
    _seed_kb(tmp_path)
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        assert mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE).success
        corrupt = tmp_path / "knowledge_bases" / "demo" / "business" / "chains" / "bad.md"
        corrupt.write_text("没有任何 frontmatter 的坏文件", encoding="utf-8")
        lst = mgr.run_job_sync("case_nodes_list", layer="chain")
        assert lst.success, lst.answer
        assert lst.metadata["count"] == 2
        marker = [r for r in lst.metadata["nodes"] if r.get("file") == "bad.md"]
        assert len(marker) == 1 and marker[0]["id"] == "" and marker[0]["error"]
        good = [r for r in lst.metadata["nodes"] if r.get("id") == "ch-0001"]
        assert len(good) == 1 and good[0]["priority"] == "P0"
    finally:
        mgr.close_all()


def test_atomic_write_removes_tmp_on_replace_failure(tmp_path):
    """Minor 1：os.replace 失败时 .tmp 不许永久留在桶里。用真实失败路径（目标是目录，
    replace 必抛 OSError），不 mock——清理也必须失败时才掩盖原异常，原异常照常冒泡（Minor 2 驳回口径）。"""
    target = tmp_path / "ch-0001.md"
    target.mkdir()
    with pytest.raises(OSError):
        _atomic_write(target, "节点内容")
    assert not (tmp_path / "ch-0001.md.tmp").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["ch-0001.md"]  # 只剩那个目录，零垃圾


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
            found = mgr.run_job_sync("knowledge_search", query="实体甲链路", limit=5)
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "实体甲链路" in blob:
                break
            time.sleep(1)
        assert "实体甲链路" in blob
    finally:
        mgr.close_all()


# ---- 补充实测（简报 8 项之外）：T1 探针两条时序约束 CF-1a / CF-1b 的常驻证据 ----


def test_cf1_buckets_exist_and_are_in_watch_dirs_before_application_ctor(tmp_path, monkeypatch):
    """CF-1a+CF-1b 前提实测：新桶不在 reme PUBLISHED_BUCKETS（augment 不会替我们加），
    三桶 junction 路径由 build_reme_config 显式注入；且实体侧桶在
    Application 构造时刻已存在（结构性约定：桶先于构造落位——这是 watch 形态漂移保险，
    非运行期硬保证；当前根递归轮询下后建桶也会被兜住，见 test_cf2 备注）。"""
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
            found = mgr.run_job_sync("knowledge_search", query="操作实体甲", limit=5)
            blob = json.dumps(found.metadata, ensure_ascii=False) + str(found.answer)
            if found.success and "操作实体甲" in blob:
                break
            time.sleep(1)
        assert "操作实体甲" in blob, f"30 秒内 watch 增量未收敛（CF-1a/CF-1b 被破坏？）：{blob}"
    finally:
        mgr.close_all()


def test_upsert_creates_missing_bucket_without_ensure_step(tmp_path, monkeypatch):
    """评审 C Minor-2：`_ensure_node_buckets` 自称「漂移保险、非既成保证」，真正兜住
    「桶缺失仍能写」的是 upsert 里 step 自己的 `bucket_dir.mkdir`。把前者置成 no-op，
    断言仍建桶、落文件、list 读回——删掉 steps.py 那行 mkdir 本用例必须变红。"""
    import aitester.services.kb.manager as kb_manager

    kb_root = _seed_kb(tmp_path)
    chains_dir = kb_root / "business" / "chains"
    monkeypatch.setattr(kb_manager, "_ensure_node_buckets", lambda cfg: None)
    assert not chains_dir.exists()
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        up = mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE)
        assert up.success, up.answer
        assert (chains_dir / "ch-0001.md").is_file()
        lst = mgr.run_job_sync("case_nodes_list", layer="chain")
        assert lst.success and lst.metadata["count"] == 1
        assert lst.metadata["nodes"][0]["id"] == "ch-0001"
    finally:
        mgr.close_all()


# ---- D-2 节点写幂等：同内容不重写、不刷 updated_at（等值判定的单点口径） ----


def test_upsert_with_identical_content_touches_nothing(tmp_path):
    """同内容重复 upsert：不重写文件、不刷 updated_at——「未涉及节点逐字节不变」靠这一条成立。"""
    kb_root = _seed_kb(tmp_path)
    path = kb_root / "business" / "chains" / "ch-0001.md"
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        first = mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE)
        assert first.success, first.answer
        assert first.metadata["unchanged"] is False           # 首写（库里没这个文件）必是真写
        t1 = path.read_text(encoding="utf-8")
        mtime1 = path.stat().st_mtime_ns
        # updated_at 是秒级时间戳：隔一秒再写才验得出「时间戳没被刷」而不是「恰好同一秒」
        time.sleep(1.1)
        meta = mgr.run_job_sync("case_node_upsert", layer="chain", node=dict(CHAIN_NODE))
        assert meta.success and meta.metadata["unchanged"] is True
        assert "unchanged ch-0001" == meta.answer
        t2 = path.read_text(encoding="utf-8")
        assert t2 == t1                                  # 整文件逐字节不变（时间戳也没动）
        assert path.stat().st_mtime_ns == mtime1          # 连文件都没碰——原子替换必改 mtime
    finally:
        mgr.close_all()


def test_upsert_with_changed_content_rewrites_and_refreshes_timestamp(tmp_path):
    """内容变了就必须重写并刷时间戳：幂等只免「没改还写」，不免「改了不写」。"""
    kb_root = _seed_kb(tmp_path)
    path = kb_root / "business" / "chains" / "ch-0001.md"
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        assert mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE).success
        t1 = path.read_text(encoding="utf-8")
        time.sleep(1.1)
        changed = {**CHAIN_NODE, "name": "实体甲链路（改名）"}
        meta = mgr.run_job_sync("case_node_upsert", layer="chain", node=changed)
        assert meta.success and meta.metadata["unchanged"] is False
        assert meta.answer == f"upserted {changed['id']}"
        t3 = path.read_text(encoding="utf-8")
        ts1 = parse_node_markdown(t1, "chain")["updated_at"]
        ts3 = parse_node_markdown(t3, "chain")["updated_at"]
        assert t3 != t1 and ts1 != ts3                    # 只断言不等（秒级时钟不保证严格递增）
        assert "实体甲链路（改名）" in t3
    finally:
        mgr.close_all()


def test_body_containing_updated_at_literal_is_not_false_equal(tmp_path):
    """假等值防线：节点名里塞 `updated_at: 2020-01-01T00:00:00` 时，剥时间戳只能作用在 frontmatter。"""
    kb_root = _seed_kb(tmp_path)
    chains_dir = kb_root / "business" / "chains"
    mgr = RemeKbManager(settings=_settings(tmp_path), data_dir=tmp_path / "data")
    mgr.start()
    try:
        nasty = {**CHAIN_NODE, "name": "链A updated_at: 2020-01-01T00:00:00"}
        # 同一路径先落 nasty 再落干净节点：两次都必须是真写（内容确有不同）
        up1 = mgr.run_job_sync("case_node_upsert", layer="chain", node=nasty)
        assert up1.success and up1.metadata["unchanged"] is False, up1.metadata
        t1 = (chains_dir / "ch-0001.md").read_text(encoding="utf-8")
        up2 = mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE)
        assert up2.success and up2.metadata["unchanged"] is False, up2.metadata
        t2 = (chains_dir / "ch-0001.md").read_text(encoding="utf-8")
        assert t1 != t2                                    # 两个节点各落各的内容，没被假等值吞掉
        assert "2020-01-01T00:00:00" in t1 and "2020-01-01T00:00:00" not in t2
    finally:
        mgr.close_all()


def test_strip_updated_at_scope_is_the_first_frontmatter_block_only():
    """等值口径单点：只剥首个 frontmatter 块里的 `updated_at:` 行；正文里同名字符串必须原样留着。

    正文行都以「- 标签：」起头，但库内文件可被人/其它工具改过——正文出现顶格 `updated_at:` 时
    若整文件盲替换就会造出假等值（唯一那道门上说谎的另一条路）。
    """
    from aitester.services.kb.steps import strip_updated_at

    text = render_node_markdown("chain", CHAIN_NODE)
    front, sep, body = text.partition("\n---\n")
    stripped = strip_updated_at(text)
    assert "updated_at" in front                                   # 夹具确有时间戳行可剥
    assert "updated_at" not in stripped.partition("\n---\n")[0]     # frontmatter 内已无该键
    assert stripped.endswith(sep + body)                             # 正文一个字符没动
    assert strip_updated_at(text) == strip_updated_at(             # 等值判定成立的根据
        render_node_markdown("chain", dict(CHAIN_NODE)))
    assert strip_updated_at(text) != text                          # 确实剥掉了一行

    nasty = ("---\nid: ch-0001\ntype: chain\nupdated_at: 2026-01-01T00:00:00\n---\n\n"
             "# 链A\nupdated_at: 2020-01-01T00:00:00\n")
    out = strip_updated_at(nasty)
    assert "updated_at: 2026-01-01T00:00:00" not in out      # frontmatter 那行剥掉
    assert "updated_at: 2020-01-01T00:00:00" in out          # 正文那行原样保留（不盲替换）
    assert strip_updated_at("没有 frontmatter") == "没有 frontmatter"
    assert strip_updated_at("") == ""
