"""账本：design/ledger.json 是专属 loop 的唯一长期真相（每步重读、每步写回）。

HISTORY_MAX=40 装不下长期状态（spec §6 硬缝），会话只给指针，文件才是状态面。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from aitester.case_design.constants import LAYERS, LEDGER_NAME, TYPE_PREFIX

STATUSES = ("active", "awaiting_review", "interrupted", "done", "halted", "writeback_failed")
LAYER_STATES = ("pending", "active", "audited", "done", "stale_pending", "skipped")


def _fresh_halt() -> dict[str, Any]:
    """halt 节形状（裁定 43）：续跑的唯一依据。count 记「停在同一处的第几次」，供终帧说实话。"""
    return {"kind": "", "reason": "", "stage": "", "layer": "", "block": "",
            "round": 0, "at": "", "count": 0, "resume_note": ""}


def _fresh_data() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "active",
        "task": {},
        "cursor": {"stage": "plan", "layer": "", "block": "", "round": 0,
                   "source": "block", "nudge": 0},
        "layers": {layer: {"state": "pending", "mode": "", "blocks": [],
                           "audit_round": 0, "unresolved": [], "opinions": []}
                   for layer in LAYERS},
        "counters": {**{layer: 0 for layer in LAYERS}, "case": 0},
        "gate": {"round": 0, "approved_at": ""},
        "writing": {"status": "", "targets": [], "batches": [], "opinions": [], "unresolved": [],
                    "gate": {"round": 0, "int_round": 0, "unclear": 0, "approved_at": ""},
                    "stale_batches": [], "note": ""},
        "halt": _fresh_halt(),
        "writeback": {"done": False, "log": []},
        "history": [],
    }


def _backfill_ledger_slices(data: dict[str, Any]) -> None:
    """旧账本（各片之前写的）可能缺 writing 节、counters.case 与 halt 节：只补这几处，
    缺别的键说明文件真坏了，不做深合并把腐坏洗成健康账本。"""
    if "writing" not in data:
        data["writing"] = _fresh_data()["writing"]
    counters = data.get("counters")
    if isinstance(counters, dict) and "case" not in counters:
        counters["case"] = 0
    if "halt" not in data:
        data["halt"] = _fresh_halt()


@dataclass
class Ledger:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, design_dir: Path) -> "Ledger | None":
        path = Path(design_dir) / LEDGER_NAME
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # B-F6 损坏自愈：旧写法裸 loads 把语法错抛穿 _boot，每一轮都收口成
            # 「测试设计任务中止（内部错误：Expecting …）」死循环，坏文件原地不动、
            # 唯一出路是手工删——而 _ARCHIVE_ITEMS 不含 ledger.json，删即全丢。
            # 改名为 ledger.corrupt-<ts>.json 留证（原字节一字不改、不删），按无账本走。
            corrupt = path.with_name(
                f"ledger.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json")
            os.replace(path, corrupt)
            return None
        _backfill_ledger_slices(data)
        return cls(path=path, data=data)

    @classmethod
    def fresh(cls, design_dir: Path) -> "Ledger":
        return cls(path=Path(design_dir) / LEDGER_NAME, data=_fresh_data())

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def status(self) -> str:
        return str(self.data.get("status") or "active")

    @status.setter
    def status(self, value: str) -> None:
        # 状态枚举校验必须响亮失败：`assert` 在 `-O` 下会被剥离、非法状态静默落盘。
        if value not in STATUSES:
            raise ValueError(f"非法账本状态「{value}」，允许 {STATUSES}")
        self.data["status"] = value

    @property
    def cursor(self) -> dict[str, Any]:
        return self.data["cursor"]

    def layer(self, layer: str) -> dict[str, Any]:
        return self.data["layers"][layer]

    def next_seq(self, layer: str) -> str:
        seq = int(self.data["counters"][layer]) + 1
        # 四位 id 上限（ID_RE 只认 ch-/st-/pt- + 四位）：越界若静默产 ch-10000，模型原样回填必被判非法、
        # nudge 烧尽后 halted 且无法合法修复。到上限即响亮失败，不推进计数、不留脏状态。
        if seq > 9999:
            raise ValueError(
                f"{layer} 层序号已达四位 id 上限 9999：下一个 {TYPE_PREFIX[layer]}-{seq} 超出 ID_RE 允许的四位，"
                "需要扩位（改 ID_RE）或清账本后重试")
        self.data["counters"][layer] = seq
        return f"{TYPE_PREFIX[layer]}-{seq:04d}"

    def next_case_seq(self) -> str:
        """用例 id：只在项目空间内有意义，故不读 TYPE_PREFIX（裁定 35：cc- 不进 KB、不进三层轴）。
        四位上限与三层同款响亮失败——静默产 cc-10000 会让模型原样回填再被判非法，无从修复。"""
        seq = int(self.data["counters"].get("case", 0)) + 1
        if seq > 9999:
            raise ValueError("用例序号已达四位 id 上限 9999：下一个 cc-10000 超出 CASE_ID_RE 允许的四位")
        self.data["counters"]["case"] = seq
        return f"cc-{seq:04d}"
