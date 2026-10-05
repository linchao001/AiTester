"""专属 loop 的单点常量：换产品线只换业务信息入口，本文件零改动。"""

from __future__ import annotations

import re

CHAIN, STORY, POINT = "chain", "story", "point"
LAYERS: tuple[str, ...] = (CHAIN, STORY, POINT)
LAYER_CN: dict[str, str] = {CHAIN: "业务链路", STORY: "用户故事", POINT: "测试点"}
TYPE_PREFIX: dict[str, str] = {CHAIN: "ch", STORY: "st", POINT: "pt"}

# 三层节点桶（P-4）：KV 的桶名与类型标记一一对应，不散写进提示词
LAYER_BUCKET: dict[str, str] = {
    CHAIN: "business/chains",
    STORY: "business/stories",
    POINT: "business/test_points",
}
NODE_BUCKETS: tuple[str, ...] = tuple(LAYER_BUCKET.values())
LAYER_OF_BUCKET: dict[str, str] = {v: k for k, v in LAYER_BUCKET.items()}

# 预算（A4/A5；改这些数字必须同步改 spec 验收节）
ROUND_CAP = 5          # 每块评审-优化环 / 每层全局审环的硬上限
NUDGE_CAP = 3          # plan/gen 制品校验失败的重试上限（超限 halted）
FIX_CAP = 2            # opt/attribute 处置表校验失败的重试上限
WRITEBACK_FIX_CAP = 2  # 回写失败自动重试上限（超限 writeback_failed）
MAX_TRANSITIONS = 80   # 单次运行驱动激活上限（防转场死循环）

PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2}
DIRECTIONS: tuple[str, ...] = ("正向", "负向", "边界")
ID_RE = re.compile(r"^(ch|st|pt)-\d{4}$")

CASE_DESIGN_AGENT_ID = "case_design"
CASE_REVIEW_AGENT_ID = "case_review"
CASE_REVIEW_BLIND_AGENT_ID = "case_review_blind"

LEDGER_NAME = "ledger.json"
PLAN_NAME = "plan.json"
OUTLINE_NAME = "outline.md"
