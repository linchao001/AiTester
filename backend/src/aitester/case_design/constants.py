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

# 断点续跑（第五片，spec 裁定 43）：族属由抛点自标，下游一律不许读 reason 文案猜。
HALT_KINDS: tuple[str, ...] = ("human_wait", "artifact_retry", "transient", "needs_input")
HALT_KIND_CN: dict[str, str] = {
    "human_wait": "等你的一句话",
    "artifact_retry": "制品反复不合法",
    "transient": "环境或内部故障",
    "needs_input": "输入或账本对不上，续跑必再炸",
}

PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2}
DIRECTIONS: tuple[str, ...] = ("正向", "负向", "边界")
ID_RE = re.compile(r"^(ch|st|pt)-\d{4}$")

# 第四层用例编写环（裁定 35/38）：cc- 只活在项目空间 design/cases/，不进 KB、不进 LAYERS/TYPE_PREFIX。
# 扩一位 LAYERS 会连带改 build_universe/run_checks/compose_outline 的三层轴口径，本片明确不做。
CASE_PREFIX = "cc"
CASE_ID_RE = re.compile(r"^cc-\d{4}$")
CASE_BATCH_CAP = 10          # 每批**点数**上限（裁定 39 的「≤10/批」按分母切，条数随认领浮动；
                             # 口径出处：计划 2026-10-07-case-writing-loop.md「实施澄清 B」）
CASES_DIR_NAME = "cases"     # design/cases/ —— 用例正文唯一落点
CASE_DELIVERY_NAME = "case-delivery.md"   # 末门唯一可视交付物

# 规范校验表的启发词（裁定 37：命中只**呈递**，绝不进 hard）。硬断言与「前置不拿环境存量
# 当条件」是内容判断，机器只能给线索；漏测（未认领点）才是本片唯一可硬判的方向。
VAGUE_ASSERTION_MARKS: tuple[str, ...] = ("正常", "正确", "符合预期", "没有问题", "成功即可")
ENV_PRECONDITION_MARKS: tuple[str, ...] = ("已有", "已存在", "存量数据", "库中已有", "环境中已")

CASE_DESIGN_AGENT_ID = "case_design"
CASE_REVIEW_AGENT_ID = "case_review"
CASE_REVIEW_BLIND_AGENT_ID = "case_review_blind"

LEDGER_NAME = "ledger.json"
PLAN_NAME = "plan.json"
OUTLINE_NAME = "outline.md"

# 首触分流：热词 task → 开账全工具面；其余（chat/unsure）→ 只读 ReAct 闲聊后 END。
# face：react_read＝只读/调查工具；空＝全工具面（已开账后）。
FACE_REACT_READ = "react_read"
INTENT_FACES: tuple[str, ...] = ("", FACE_REACT_READ)
# 闲聊只读 ReAct：agent 调用次数上限（含将要发起的这一次；到顶不再调工具，收尾回 driver）。
REACT_READ_MAX_ITERS = 100
# 闲聊 ReAct 禁止的写/改/执行面（1B）；其余工具可 bind（KB 检索、read、搜索、web、task）。
REACT_READ_DENY_TOOLS: frozenset[str] = frozenset({
    "write", "edit", "save_to_knowledge", "prepare_kb_write", "bash", "pwsh",
})
# 历史残留文件名：开账前仍尝试清掉，避免旧 intent.json 干扰现场。
INTENT_NAME = "intent.json"

CASE_DESIGN_KEY = "case_design_env"   # case_env 注入通道（与 GATE_KEY 同款；T9 图装配读写此键）
