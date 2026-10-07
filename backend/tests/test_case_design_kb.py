import subprocess
import sys

import pytest

from aitester.case_design.kb import KbClient, KbClientError


class _Resp:
    def __init__(self, success=True, answer="ok", metadata=None):
        self.success = success
        self.answer = answer
        self.metadata = metadata if metadata is not None else {}


class _StubKb:
    """吃 run_job_sync(name, *, project_id, agent_id, timeout, **kwargs) 的替身。"""

    def __init__(self, *, root=None, resp=None, exc=None):
        self.calls = []
        self._resp = resp or _Resp()
        self._exc = exc
        self.kb_root_dir = root

    def run_job_sync(self, name, *, project_id="default", agent_id="console",
                     timeout=60.0, **kwargs):
        self.calls.append({"name": name, "agent_id": agent_id, "timeout": timeout,
                          "kwargs": kwargs})
        if self._exc is not None:
            raise self._exc
        return self._resp


def test_list_layer_forwards_job_and_returns_rows():
    kb = _StubKb(resp=_Resp(metadata={"layer": "chain", "count": 1,
                                      "nodes": [{"id": "ch-0001", "type": "chain"}]}))
    rows = KbClient(kb).list_layer("chain")
    assert rows == [{"id": "ch-0001", "type": "chain"}]
    assert kb.calls[0]["name"] == "case_nodes_list"
    assert kb.calls[0]["kwargs"] == {"layer": "chain"}
    assert kb.calls[0]["agent_id"] == "case_design"
    assert kb.calls[0]["timeout"] == 120.0


def test_upsert_and_delete_forward_shapes():
    kb = _StubKb(resp=_Resp(metadata={
        "layer": "story", "id": "st-0001", "unchanged": False,
        "path": "D:/kb/demo/business/stories/st-0001.md"}))
    meta = KbClient(kb).upsert_node("story", {"type": "story", "name": "S"})
    # 返回整份 metadata（不再只是 path 字符串）：unchanged 是「这个节点根本没被触碰」的凭据，
    # 驱动必须把它呈递给人——等值判定的口径在 step 单点，这里只许原样透出。
    assert meta["path"].endswith("st-0001.md") and meta["unchanged"] is False
    assert kb.calls[0]["name"] == "case_node_upsert"
    assert kb.calls[0]["kwargs"] == {"layer": "story", "node": {"type": "story", "name": "S"}}

    kb2 = _StubKb(resp=_Resp(metadata={"layer": "point", "id": "pt-0001", "deleted": True}))
    assert KbClient(kb2).delete_node("point", "pt-0001") is True
    assert kb2.calls[0]["kwargs"] == {"layer": "point", "id": "pt-0001"}

    kb3 = _StubKb(resp=_Resp(metadata={"layer": "point", "id": "pt-0001", "deleted": False}))
    assert KbClient(kb3).delete_node("point", "pt-0001") is False   # 重复删除幂等，回写重试不炸（A7）


def test_job_failure_and_timeout_map_to_kb_client_error():
    bad = _StubKb(resp=_Resp(success=False, answer="unknown layer: nope"))
    with pytest.raises(KbClientError, match="unknown layer"):
        KbClient(bad).list_layer("nope")
    late = _StubKb(exc=TimeoutError("too slow"))
    with pytest.raises(KbClientError, match="timed out"):
        KbClient(late).list_layer("chain")
    assert late.calls[0]["name"] == "case_nodes_list"


def test_list_business_files_walk_and_exclusions(tmp_path):
    root = tmp_path / "kb"
    for rel in ("business/wiki/a.md", "business/wiki/nested/b.md",
                "business/chains/ch-0001.md", "business/stories/st-0001.md",
                "business/test_points/pt-0001.md",
                "business/wiki/.hidden.md", "business/wiki/__pycache__/x.md",
                "test/test_design/x.md", "_inbox/note.md"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    (root / "KB.md").write_text("---\nid: demo\n---\n", encoding="utf-8")
    assert KbClient(_StubKb(root=root)).list_business_files() == [
        "business/wiki/a.md", "business/wiki/nested/b.md"]


def test_list_business_files_missing_root_returns_empty(tmp_path):
    assert KbClient(_StubKb(root=tmp_path / "nope")).list_business_files() == []


def test_kb_module_import_does_not_pull_agent_runtime():
    """启动期事故守卫：本模块在启动导入链上（graph_registry → case_design）。

    顶层 `import aitester.services...` 会走 services/__init__ → chat → agent_runtime
    （chat 顶层还 import orchestration.graph_registry，反向成环半初始化）。同进程内其它
    测试早已把 services 拉进 sys.modules，查进程内状态会假绿，故用干净子进程实测导入面。
    """
    code = ("import sys; import aitester.case_design.kb; "
            "assert 'aitester.services.agent_runtime' not in sys.modules")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert proc.returncode == 0, proc.stderr
