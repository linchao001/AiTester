"""运行环境缝：驱动与阶段处理器只认这一个环境对象（构造零副作用）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitester.case_design.constants import (
    CASES_DIR_NAME, CASE_DELIVERY_NAME, LAYERS,
)
from aitester.project_runtime import design_root


@dataclass(frozen=True)
class CaseDesignEnv:
    project_dir: str
    kb: Any                       # RemeMemoryManager（或其同签名替身）；未启用时为 None 的调用方不给 env
    project_id: str = ""          # Reme 池键解析用；空则走 default/_platform

    @property
    def design(self) -> Path:
        return design_root(self.project_dir)

    def drafts_dir(self, layer: str) -> Path:
        return self.design / "drafts" / layer

    @property
    def reviews_dir(self) -> Path:
        return self.design / "reviews"

    def cases_dir(self) -> Path:
        return self.design / CASES_DIR_NAME

    @property
    def delivery_path(self) -> Path:
        return self.design / CASE_DELIVERY_NAME

    @property
    def manifests_dir(self) -> Path:
        return self.design / "manifests"

    @property
    def attribution_dir(self) -> Path:
        return self.design / "attribution"

    def ensure_dirs(self) -> None:
        for path in (self.design, self.reviews_dir, self.manifests_dir, self.attribution_dir,
                     self.cases_dir(),
                     *(self.drafts_dir(layer) for layer in LAYERS)):
            path.mkdir(parents=True, exist_ok=True)
