"""账本：design/ledger.json 是专属 loop 的唯一长期真相（每步重读、每步写回）。

HISTORY_MAX=40 装不下长期状态（spec §6 硬缝），会话只给指针，文件才是状态面。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitester.case_design.constants import LAYERS, LEDGER_NAME, TYPE_PREFIX

STATUSES = ("active", "awaiting_review", "interrupted", "done", "halted", "writeback_failed")
LAYER_STATES = ("pending", "active", "audited", "done", "stale_pending", "skipped")


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
        "counters": {layer: 0 for layer in LAYERS},
        "gate": {"round": 0, "approved_at": ""},
        "writeback": {"done": False, "log": []},
        "history": [],
    }


@dataclass
class Ledger:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, design_dir: Path) -> "Ledger | None":
        path = Path(design_dir) / LEDGER_NAME
        if not path.is_file():
            return None
        return cls(path=path, data=json.loads(path.read_text(encoding="utf-8")))

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
        assert value in STATUSES
        self.data["status"] = value

    @property
    def cursor(self) -> dict[str, Any]:
        return self.data["cursor"]

    def layer(self, layer: str) -> dict[str, Any]:
        return self.data["layers"][layer]

    def next_seq(self, layer: str) -> str:
        seq = int(self.data["counters"][layer]) + 1
        self.data["counters"][layer] = seq
        return f"{TYPE_PREFIX[layer]}-{seq:04d}"
