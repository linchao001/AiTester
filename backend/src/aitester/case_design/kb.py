"""KbClient：测试设计域对知识库的唯一访问面（枚举 / 节点增改删 / 业务源清单）。

薄封装的意义有二：① 域内代码只吃 `run_job_sync` 一个方法面，测试替身零成本；
② 失败与超时收敛成单一 KbClientError，驱动层只留一条 halted 收敛路径（A3）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aitester.case_design.constants import CASE_DESIGN_AGENT_ID, NODE_BUCKETS


class KbClientError(RuntimeError):
    """KB job 失败 / 超时（驱动层据此落 halted，不再细分）。"""


class KbClient:
    def __init__(self, kb: Any, *, agent_id: str = CASE_DESIGN_AGENT_ID,
                 timeout: float = 120.0) -> None:
        self._kb = kb
        self._agent_id = agent_id
        self._timeout = timeout

    def _job(self, name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._kb.run_job_sync(name, agent_id=self._agent_id,
                                         timeout=self._timeout, **kwargs)
        except TimeoutError as exc:
            raise KbClientError(f"KB job {name} timed out ({self._timeout}s)") from exc
        if not resp.success:
            raise KbClientError(f"KB job {name} failed: {resp.answer}")
        return dict(resp.metadata or {})

    def list_layer(self, layer: str) -> list[dict[str, Any]]:
        return list(self._job("case_nodes_list", layer=layer).get("nodes") or [])

    def upsert_node(self, layer: str, node: dict[str, Any]) -> dict[str, Any]:
        """回传 job metadata（含 path 与 unchanged）：unchanged 是「这个节点根本没被触碰」的凭据，
        驱动必须把它呈递给人（裁定 18 的写侧同型）。"""
        return self._job("case_node_upsert", layer=layer, node=node)

    def delete_node(self, layer: str, node_id: str) -> bool:
        return bool(self._job("case_node_delete", layer=layer, id=node_id).get("deleted"))

    def list_business_files(self) -> list[str]:
        """业务信息来源白名单（① 盲枚举的分母）：`business/` 下除三层节点桶外的文件。

        返回 KB 根相对 posix 路径（如 "business/wiki/a.md"）。三层节点桶、非业务域
        （test/ 产物）、KB 根级文件（KB.md/_inbox）与隐藏名在构造期即排除——枚举器
        （面仅 read、无 glob/grep）拿到的就是这份清单里的文件，看不到树（A2 的结构盲）。
        """
        # 延迟导入（与 kb_tools.PrepareKbWriteTool 同因）：services 包 __init__ 会经
        # chat→agent_runtime 反向成环，而本模块在启动导入链上（graph_registry→case_design）
        from aitester.services.kb.paths import hidden_segment

        root = Path(self._kb.kb_root_dir)
        base = root / "business"
        if not base.is_dir():
            return []
        out: list[str] = []
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            parts = rel.split("/")
            if hidden_segment(tuple(parts)):
                continue
            if "/".join(parts[:2]) in NODE_BUCKETS:
                continue
            out.append(rel)
        return sorted(out)
