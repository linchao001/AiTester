# 第四层用例编写环 Implementation Plan（第四片）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已闭合的三层测试设计环之后，长出第四层「用例编写环」：按子链路分批生成用例正文、用确定性核对守住「每个测试点都被用例落实」，并在全片最后一道人类门交付。

**Architecture:** 复用既有专属 loop 的全部骨架（账本 `design/ledger.json` 为唯一真相、驱动游标、只读评审子、`Ctx.ask` 重试预算、确定性检查 + 大纲/交付物渲染），新增一个与三层环平行但独立的**编写环**：`case_plan → case_gen（批）→ 批评审/优化/归因 → case_gate（末门）→ case_gate_interpret`。用例正文**只落项目空间 `design/cases/`，零 KB 写入**（裁定 35），点与用例的数量关系灵活、可核对性由每条用例的 `covers: [pt-xxxx]` **显式认领**买回来（裁定 38）。

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / pydantic v2 / pytest（后端-only，前端零改动）。

**Spec:** `docs/superpowers/specs/2026-10-05-case-design-loop-design.md` —— 本片的设计权威是文末**「第四层用例编写环（第四片，2026-10-07 用户菜单裁定 35–41）」整节**（裁定 35–41 + 走查四验收线）。与该节冲突时以该节为准；与更早的三层裁定冲突时，三层裁定继续生效（本片不改设计环的语义）。

## Global Constraints

每一任务的要求都隐含本节；逐条照抄 spec/既有纪律，不许在执行时自行放宽。

1. **只改后端**：`backend/**`。前端（`frontend/**`）**零改动**——本仓纪律，走查靠 HTTP/SSE 读数即可。
2. **不新增任何 SSE/帧类型**：只复用既有 `delta / turn / call / step / wait / draft`；执行节点必须仍命名 `tools`（`stream_graph` 按这个名字折叠 step 帧，`case_design/graph.py:5` 的硬要求）。终局一律 `ctx.end(text)`（内部只发 `delta` + 无 `tool_calls` 的 `turn`）。
3. **用例正文不进知识库**（裁定 35）：本片**不得**调用 `KbClient.upsert_node/delete_node` 写任何 `cc-` 制品，**不得**新增 KB 桶、**不得**把 `CASE` 加进 `LAYERS`/`TYPE_PREFIX`/`LAYER_BUCKET`（`LAYERS` 是三层设计环的引用宇宙轴，扩一位会连带改 `build_universe`/`run_checks`/`compose_outline`/`_scope_for_checks` 全部口径）。用例 id 只在项目空间内有意义。
4. **模型可见标识符英文，UI/提示词/大纲/交付物中文**；账本、提示词、测试夹具、合成业务输入**不得出现任何产品线业务名词**（真实产品线一律不用；走查输入继续用合成的「云杉商城 订单域」，与任何真实产品线无关）。
5. **评审子简报必须逐字摊开必填字段与枚举字面量**（W3-1 真机教训 + `docs/superpowers/specs/...#L335` 的 halted 根因）：任何新简报里 `"opinions": []` 这类占位**禁止**出现在允许出意见的环上；条目形状与 `kind` 五值必须原样下发，并有「从简报现取枚举再过 schema」的用例钉住。
6. **门禁读数只增不减**：开工基线 **770 passed / 0 failed**（`0fb2321`，控制方本会话复现）。每任务收尾必须复跑全量并记账。
7. **测试命令固定**：`cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`。仓根 python 会加载坏掉的 zframe 插件；不清 pyc 会出现假红假绿。真实验证一律 `tmp_path`，`backend/data/*.json` **只读**。
8. **真实 KB 严禁读写**：`C:\Users\qifengshunshi\.reme\knowledge_bases`，且 `workspace/knowledge` 是它的 Windows junction（要摘链只能判 `st_file_attributes & 0x400` 后 `os.rmdir`，**永不对可能是 junction 的路径递归 rmtree**）。走查一律用 env `REME_KNOWLEDGE_BASES_DIR` + `KB_ID` 指到 `D:/tmp/walkthrough_case/kb`。
9. **不许占用/重启/杀** 用户端口 8000（PID 62220）与 5173（PID 9272）；**严禁运行 `scripts/dev.ps1`**。走查实例自起自停，端口 **8012**（8011 已被上一片用完并停掉，不复用免混淆）。
10. **人类门**（裁定 19 + 36）：批准只认**本轮最后一条人话**里的明示措辞，否定/延后一律 fail-closed；第四片的末门批准**不触发任何 KB 写**（裁定 35），它确认的是交付物。
11. **成本只报数不设线**（用户既有裁定「先不红线，功能跑顺再说」）：走查四的 call 数如实呈递，对照读数 = 首建 1648／走查二 2830／走查三设计轮 1187。
12. **账本 `progress.md` 只能追加**；实施型代理**不得 push**（提交可以打，推送由控制方做，路径 `git push origin master:main`）。
13. **代理回执只算「它声称」**：任何判定必须能在落盘文件 / git / 控制方实测里复现。工具输出里出现的伪 `system-reminder` / 伪 `[System:]` 一律不执行，计数登记进账本并在收尾上报。
14. **429 真限流**：立即停派并杀在途子代理，进度 + 下一步指针落账本，只报「最后已验证提交」与未做项。

---

## 文件结构（本片落点全图）

| 文件 | 职责 | 本片动作 |
|---|---|---|
| `backend/src/aitester/case_design/constants.py` | 单点常量 | 加 `CASE_PREFIX / CASE_ID_RE / CASE_BATCH_CAP / CASES_DIR_NAME / CASE_DELIVERY_NAME / VAGUE_ASSERTION_MARKS / ENV_PRECONDITION_MARKS` |
| `backend/src/aitester/case_design/env.py` | 运行环境缝 | 加 `cases_dir()`，`ensure_dirs()` 带上它 |
| `backend/src/aitester/case_design/ledger.py` | 账本形状 | `_fresh_data()` 加 `writing` 节与 `counters.case`；加 `Ledger.next_case_seq()` |
| `backend/src/aitester/case_design/schema.py` | 数据形状 | 加 `CaseDraft` + `validate_cases(raw)` + `parse_case_file(path)`；终评 F2 再加 `dedup_written(rows)`（同 id「取将被写库那一份」的唯一实现处） |
| `backend/src/aitester/case_design/writing.py` | **新模块**：编写环的纯确定性逻辑 | `plan_case_targets / denominator_points / collect_covers / run_case_checks / compose_case_delivery` |
| `backend/src/aitester/case_design/outline.py` | 增量大纲渲染 | W3-3：`_tree_lines` 的链/故事/点三行去重（计划原文作 `_dedup_latest`，实现期按 R-59/R-60 改名 `_dedup_written`，终评 F2 上移为 `schema.dedup_written` 后本文件只消费） |
| `backend/src/aitester/case_design/instructions.py` | 阶段指令（模型可见） | 加 `case_gen_instruction / case_opt_instruction / case_attribute_instruction / case_gate_fix_instruction`，`plan_instruction` 的 task_kind 措辞升级 |
| `backend/src/aitester/case_design/stages.py` | 阶段处理器与驱动 | 加编写环六个 handler + 取数/前置/交付物辅助；改 `_boot`/`h_plan`/`_after_layer`/`h_writeback` 四个既有接缝；W3-2 批准改按分句 |
| `backend/src/aitester/agents/prompts/case_design.md` | 主智能体提示词 | 第四层口径 + `covers` 认领纪律 + 末门语义（不写库） |
| `backend/src/aitester/agents/catalog.py` | 目录描述 | `case_design.desc` 补用例侧一句 |
| `backend/tests/test_case_design_writing.py` | **新测试**：writing.py 单测 + T18 的账本/env 用例 | 建 |
| `backend/tests/test_case_design_writing_stages.py` | **新测试**：编写环阶段与末门（挂具复用 driver 测试文件） | 建 |
| `backend/tests/test_case_design_e2e.py` | 离线端到端 | 加「case_only 全环 + 末门批准 + KB 零写入」用例 |
| `backend/tests/test_case_design_driver.py` | 既有 driver 测试 | 加 W3-2 用例 + 编写环挂具所需的 `_cases_payload` |
| `backend/tests/test_case_design_plan.py` | 既有大纲/计划单测 | 加 W3-3 用例（`test_case_design_plan.py` 才是大纲单测的家） |
| `backend/tests/test_agents.py` | 目录与提示词钉桩 | desc 逐字 + 第四层标记 |

## 任务顺序与依赖

T18（常量/账本/schema）→ T19（writing.py 确定性核对 + 交付物渲染）→ T20（计划侧与转场：`case_plan` + `case_only`/`mixed` 语义升级）→ T21（批生成 + 批评审 + 优化/归因环）→ T22（末门 `case_gate` / `case_gate_interpret` + `_boot` 分流）→ T23（提示词与目录描述）→ T24（W3-2 批准邻域收窄 + W3-3 增量树去重）→ T25（离线端到端 + 门禁收口 + 推送）→ T26（付费走查四 + 读数回填 + 清场）。

T18/T19 是纯函数与形状，先落地可以把 T21/T22 的断言全部钉在稳定接口上；T24 独立可并行于 T20–T23（但按 SDD 纪律仍串行派发，禁止并行开多个实施代理）。

---

## Task 18: 常量、环境缝、账本 `writing` 节与用例 schema

**Files:**
- Modify: `backend/src/aitester/case_design/constants.py`（在 `ID_RE` 之后、`CASE_DESIGN_AGENT_ID` 之前插入）
- Modify: `backend/src/aitester/case_design/env.py:21-39`
- Modify: `backend/src/aitester/case_design/ledger.py:21-35`（`_fresh_data`）、`:88-97`（`next_seq` 之后加方法）
- Modify: `backend/src/aitester/case_design/schema.py:1-26`（导入）、文件末尾追加
- Test: `backend/tests/test_case_design_schema.py`（追加一节）
- Test: `backend/tests/test_case_design_writing.py`（新建，先只放账本/env 用例）

**Interfaces:**
- Consumes: 既有 `ID_RE`/`TYPE_PREFIX`/`Ledger.next_seq`/`parse_json_fence` 不变。
- Produces（后续任务逐字依赖）：
  - `constants.CASE_PREFIX: str = "cc"`、`constants.CASE_ID_RE: re.Pattern`（`^cc-\d{4}$`）、`constants.CASE_BATCH_CAP: int = 10`、`constants.CASES_DIR_NAME: str = "cases"`、`constants.CASE_DELIVERY_NAME: str = "case-delivery.md"`、`constants.VAGUE_ASSERTION_MARKS: tuple[str, ...]`、`constants.ENV_PRECONDITION_MARKS: tuple[str, ...]`
  - `CaseDesignEnv.cases_dir() -> Path`（`design/cases`）
  - 账本：`led.data["writing"] == {"status": "", "targets": [], "batches": [], "opinions": [], "unresolved": [], "gate": {"round": 0, "int_round": 0, "unclear": 0, "approved_at": ""}, "stale_batches": [], "note": ""}`；`led.data["counters"]["case"] == 0`；`Ledger.next_case_seq() -> str`
  - `schema.CaseDraft`（字段：`case_id/title/covers/preconditions/steps/expected/priority/note`）、`schema.validate_cases(raw) -> tuple[list[CaseDraft], list[str]]`、`schema.parse_case_file(path) -> tuple[list[CaseDraft], list[str]]`
  - 用例草稿文件形状（模型必须逐字产出）：`{"chain": "ch-0002", "batch": "ch-0002-b1", "cases": [{"case_id": "", "title": "…", "covers": ["pt-0003"], "preconditions": "…", "steps": ["…"], "expected": ["…"], "priority": "P1", "note": ""}]}`

- [ ] **Step 1: 写失败测试——常量与 id 形状**

在 `backend/tests/test_case_design_writing.py` 新建：

```python
"""第四层编写环的确定性面：常量、账本 writing 节、用例草稿与核对（裁定 35–41）。

不联网、不调真模型；全部 tmp_path，零真实 KB。
"""

from __future__ import annotations

from pathlib import Path

from aitester.case_design.constants import (
    CASE_BATCH_CAP, CASE_DELIVERY_NAME, CASE_ID_RE, CASE_PREFIX, CASES_DIR_NAME,
    ENV_PRECONDITION_MARKS, ID_RE, VAGUE_ASSERTION_MARKS,
)
from aitester.case_design.env import CaseDesignEnv
from aitester.case_design.ledger import Ledger


def test_case_id_constants():
    assert CASE_PREFIX == "cc"
    assert CASE_BATCH_CAP == 10
    assert CASES_DIR_NAME == "cases"
    assert CASE_DELIVERY_NAME == "case-delivery.md"
    assert CASE_ID_RE.match("cc-0001")
    assert not CASE_ID_RE.match("cc-1")
    assert not CASE_ID_RE.match("pt-0001")
    # 三层 id 轴绝不被用例 id 污染（裁定 35：不扩 LAYERS/TYPE_PREFIX）
    assert ID_RE.match("pt-0001") and not ID_RE.match("cc-0001")


def test_marks_are_non_empty_and_have_no_product_line_terms():
    assert VAGUE_ASSERTION_MARKS and ENV_PRECONDITION_MARKS
    joined = "，".join(VAGUE_ASSERTION_MARKS + ENV_PRECONDITION_MARKS)
    for banned in ("智会宝", "zhb", "云杉"):
        assert banned not in joined.lower()


def test_env_cases_dir(tmp_path: Path):
    env = CaseDesignEnv(project_dir=str(tmp_path), kb=None)
    env.ensure_dirs()
    assert env.cases_dir() == tmp_path / "design" / "cases"
    assert env.cases_dir().is_dir()


def test_ledger_fresh_has_writing_section_and_case_counter(tmp_path: Path):
    led = Ledger.fresh(tmp_path / "design")
    writing = led.data["writing"]
    assert writing["status"] == ""
    assert writing["targets"] == []
    assert writing["gate"] == {"round": 0, "int_round": 0, "unclear": 0, "approved_at": ""}
    assert writing["stale_batches"] == []
    assert writing["note"] == ""
    # 意见簿三件套（T21/T22 只换取桶路径、不换字段）必须从开账即在位
    assert writing["batches"] == [] and writing["opinions"] == [] and writing["unresolved"] == []
    assert led.data["counters"]["case"] == 0


def test_next_case_seq_four_digit_and_bounded(tmp_path: Path):
    led = Ledger.fresh(tmp_path / "design")
    assert led.next_case_seq() == "cc-0001"
    assert led.next_case_seq() == "cc-0002"
    led.data["counters"]["case"] = 9999
    try:
        led.next_case_seq()
    except ValueError as exc:
        assert "上限" in str(exc)
    else:
        raise AssertionError("序号到 9999 后必须响亮失败，不许静默产 cc-10000")
```

- [ ] **Step 2: 跑到红**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing.py -q`
Expected: `ModuleNotFoundError` 或 `ImportError: cannot import name 'CASE_PREFIX'`（收集期即红，全部用例未过）。

- [ ] **Step 3: 加常量**

`backend/src/aitester/case_design/constants.py`，在 `ID_RE = re.compile(r"^(ch|st|pt)-\d{4}$")`（第 36 行）之后插入：

```python
# 第四层用例编写环（裁定 35/38）：cc- 只活在项目空间 design/cases/，不进 KB、不进 LAYERS/TYPE_PREFIX。
# 扩一位 LAYERS 会连带改 build_universe/run_checks/compose_outline 的三层轴口径，本片明确不做。
CASE_PREFIX = "cc"
CASE_ID_RE = re.compile(r"^cc-\d{4}$")
CASE_BATCH_CAP = 10          # 每批**点数**上限（裁定 39 的「≤10/批」按分母切，条数随认领浮动；见 spec 实施澄清）
CASES_DIR_NAME = "cases"     # design/cases/ —— 用例正文唯一落点
CASE_DELIVERY_NAME = "case-delivery.md"   # 末门唯一可视交付物

# 规范校验表的启发词（裁定 37：命中只**呈递**，绝不进 hard）。硬断言与「前置不拿环境存量
# 当条件」是内容判断，机器只能给线索；漏测（未认领点）才是本片唯一可硬判的方向。
VAGUE_ASSERTION_MARKS: tuple[str, ...] = ("正常", "正确", "符合预期", "没有问题", "成功即可")
ENV_PRECONDITION_MARKS: tuple[str, ...] = ("已有", "已存在", "存量数据", "库中已有", "环境中已")
```

- [ ] **Step 4: 加 env 缝**

`backend/src/aitester/case_design/env.py`：把 `reviews_dir` 属性之后加一个方法，并把 `ensure_dirs` 的目录元组补上它。

```python
    def cases_dir(self) -> Path:
        return self.design / CASES_DIR_NAME

    @property
    def delivery_path(self) -> Path:
        return self.design / CASE_DELIVERY_NAME
```

`ensure_dirs` 内层元组改为（原第四行 `for layer in LAYERS` 的生成器表达式保留，追加 `self.cases_dir()`）：

```python
    def ensure_dirs(self) -> None:
        for path in (self.design, self.reviews_dir, self.manifests_dir, self.attribution_dir,
                     self.cases_dir(),
                     *(self.drafts_dir(layer) for layer in LAYERS)):
            path.mkdir(parents=True, exist_ok=True)
```

并把该文件第 9 行导入改为：

```python
from aitester.case_design.constants import (
    CASES_DIR_NAME, CASE_DELIVERY_NAME, LAYERS,
)
```

- [ ] **Step 5: 加账本节与用例序号**

`backend/src/aitester/case_design/ledger.py`：`_fresh_data()` 里 `"counters"` 与 `"gate"` 两行改为（`writing` 紧跟 `gate` 之后、`writeback` 之前）：

```python
        "counters": {**{layer: 0 for layer in LAYERS}, "case": 0},
        "gate": {"round": 0, "approved_at": ""},
        "writing": {"status": "", "targets": [], "batches": [], "opinions": [], "unresolved": [],
                    "gate": {"round": 0, "int_round": 0, "unclear": 0, "approved_at": ""},
                    "stale_batches": [], "note": ""},
        "writeback": {"done": False, "log": []},
```

（`opinions`/`unresolved`/`batches` 的形状与 `layers.<layer>` 同名键一致——批评审/末门回溯走的是同一套意见簿纪律，T21/T22 只在取桶处换路径，不换字段。）

在 `next_seq` 之后追加方法（**不**复用 `TYPE_PREFIX`——那会让 `cc-` 被当成一层）：

```python
    def next_case_seq(self) -> str:
        """用例 id：只在项目空间内有意义，故不读 TYPE_PREFIX（裁定 35：cc- 不进 KB、不进三层轴）。
        四位上限与三层同款响亮失败——静默产 cc-10000 会让模型原样回填再被判非法，无从修复。"""
        seq = int(self.data["counters"].get("case", 0)) + 1
        if seq > 9999:
            raise ValueError("用例序号已达四位 id 上限 9999：下一个 cc-10000 超出 CASE_ID_RE 允许的四位")
        self.data["counters"]["case"] = seq
        return f"cc-{seq:04d}"
```

- [ ] **Step 6: 加用例 schema**

`backend/src/aitester/case_design/schema.py` 第 16 行导入补 `CASE_ID_RE`（`from aitester.case_design.constants import ... ` 一行内加），文件末尾追加：

```python
class CaseDraft(BaseModel):
    """一条用例正文（第四层；只活在项目空间 design/cases/，不进 KB——裁定 35）。

    裁定 38：点↔用例数量关系灵活（1:1／一点多条／多点合一条都允许），可核对性全靠 covers
    显式认领：covers 为空 = 这条用例谁都不落实，等于漏测的伪装；认领不存在的点 = 假完整。
    """

    case_id: str = ""                      # 新增留空串，由驱动 next_case_seq 分配并回写
    title: str = ""
    covers: list[str] = Field(default_factory=list)
    preconditions: str = ""
    steps: list[str] = Field(default_factory=list)
    expected: list[str] = Field(default_factory=list)
    priority: str = "P1"
    note: str = ""


def validate_cases(raw: Any) -> tuple[list[CaseDraft], list[str]]:
    """校验一个批次用例草稿文件。错误表非空即重问（批内自检，不进末门修复环）。

    必填缺失在此**拒收**（驱动 nudge 环），因此 `run_case_checks` 不必为它增设 hard code——
    同一条坏输入不该有两个处置出口（裁定 37 的规范侧只留呈递线索）。
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return [], ["用例文件根必须是对象 {chain, batch, cases}"]
    if not isinstance(raw.get("chain"), str) or not raw.get("chain"):
        errors.append("chain 字段缺失（本批归属的链路 id）")
    if not isinstance(raw.get("batch"), str) or not raw.get("batch"):
        errors.append("batch 字段缺失（本批 id，须与文件名一致）")
    items = raw.get("cases")
    if not isinstance(items, list) or not items:
        return [], [*errors, "cases 必须是非空数组（本批一条都没有 = 没做事，不是空批）"]
    cases: list[CaseDraft] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(items):
        where = f"cases[{i}]"
        try:
            case = CaseDraft.model_validate(item)
        except Exception as exc:
            errors.append(f"{where}: {exc}")
            continue
        if case.case_id:
            if not CASE_ID_RE.match(case.case_id):
                errors.append(f"{where}: case_id「{case.case_id}」形状非法（应为 cc-四位数字）")
            elif case.case_id in seen_ids:
                errors.append(f"{where}: case_id「{case.case_id}」在本文件内重复")
            else:
                seen_ids.add(case.case_id)
        if not case.title.strip():
            errors.append(f"{where}: title 不能为空")
        if not case.covers:
            errors.append(f"{where}: covers 不能为空（必须点名这条用例落实哪些测试点 pt-xxxx）")
        bad = [c for c in case.covers if not re.match(r"^pt-\d{4}$", str(c))]
        if bad:
            errors.append(f"{where}: covers 含非测试点 id {bad}（只许 pt-四位数字）")
        if len(set(map(str, case.covers))) != len(case.covers):
            errors.append(f"{where}: covers 内有重复 id")
        if not case.preconditions.strip():
            errors.append(f"{where}: preconditions 不能为空")
        if not [s for s in case.steps if str(s).strip()]:
            errors.append(f"{where}: steps 必须至少一步且非空")
        if not [e for e in case.expected if str(e).strip()]:
            errors.append(f"{where}: expected 必须至少一条硬断言且非空")
        if case.priority not in PRIORITY_RANK:
            errors.append(f"{where}: priority「{case.priority}」非法（P0/P1/P2）")
        cases.append(case)
    return (cases if not errors else []), errors


def parse_case_file(path: Path) -> tuple[list[CaseDraft], list[str]]:
    """读一个批次文件并校验；缺失/坏 JSON 收敛为错误表（不抛，与 parse_draft_file 同形）。"""
    if not path.is_file():
        return [], [f"用例文件不存在：{path.name}"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [], [f"用例文件不可解析：{exc}"]
    return validate_cases(raw)
```

- [ ] **Step 7: 写 schema 失败测试并跑到红**

在 `backend/tests/test_case_design_schema.py` 末尾追加：

```python
# ---- 第四层用例草稿（裁定 35/38）：cc- 形状 + covers 认领纪律 ----

def _case_raw(**over):
    base = {"chain": "ch-0002", "batch": "ch-0002-b1", "cases": [{
        "case_id": "cc-0001", "title": "下单用满足门槛的券可抵扣",
        "covers": ["pt-0003"], "preconditions": "账号内有一张满足门槛的优惠券",
        "steps": ["登录并进入下单页", "选择该券并提交下单"],
        "expected": ["订单金额按券面规则抵扣", "该券状态变为已核销"],
        "priority": "P1", "note": ""}]}
    base.update(over)
    return base


def test_valid_case_file_parses(tmp_path):
    path = tmp_path / "ch-0002-b1.json"
    path.write_text(json.dumps(_case_raw(), ensure_ascii=False), encoding="utf-8")
    cases, errors = parse_case_file(path)
    assert errors == []
    assert cases[0].covers == ["pt-0003"] and cases[0].case_id == "cc-0001"


def test_case_without_covers_is_rejected():
    _, errors = validate_cases(_case_raw(cases=[{
        "case_id": "cc-0001", "title": "谁都不落实", "covers": [],
        "preconditions": "x", "steps": ["y"], "expected": ["z"]}]))
    assert any("covers 不能为空" in e for e in errors)


def test_case_covering_non_point_is_rejected():
    _, errors = validate_cases(_case_raw(cases=[{
        "case_id": "cc-0001", "title": "认领了故事", "covers": ["st-0001"],
        "preconditions": "x", "steps": ["y"], "expected": ["z"]}]))
    assert any("非测试点 id" in e for e in errors)


def test_case_bad_case_id_shape_is_rejected():
    _, errors = validate_cases(_case_raw(cases=[{
        "case_id": "pt-0001", "title": "借用三层 id", "covers": ["pt-0003"],
        "preconditions": "x", "steps": ["y"], "expected": ["z"]}]))
    assert any("cc-四位数字" in e for e in errors)


def test_case_id_duplicated_in_same_file_is_rejected():
    one = {"case_id": "cc-0001", "title": "甲", "covers": ["pt-0003"], "preconditions": "x",
           "steps": ["y"], "expected": ["z"]}
    two = dict(one, title="乙", covers=["pt-0004"])
    _, errors = validate_cases(_case_raw(cases=[one, two]))
    assert any("本文件内重复" in e for e in errors)


def test_empty_cases_array_is_rejected_not_empty_batch():
    _, errors = validate_cases(_case_raw(cases=[]))
    assert any("cases 必须是非空数组" in e for e in errors)


def test_new_case_with_blank_id_is_accepted_for_driver_patch():
    one = {"case_id": "", "title": "待分配", "covers": ["pt-0003"], "preconditions": "x",
           "steps": ["y"], "expected": ["z"]}
    cases, errors = validate_cases(_case_raw(cases=[one]))
    assert errors == [] and cases[0].case_id == ""


def test_missing_steps_or_expected_or_precondition_is_rejected():
    for field in ("preconditions", "steps", "expected"):
        bad = {"case_id": "cc-0001", "title": "缺字段", "covers": ["pt-0003"],
               "preconditions": "x", "steps": ["y"], "expected": ["z"]}
        bad[field] = "" if field == "preconditions" else []
        _, errors = validate_cases(_case_raw(cases=[bad]))
        assert errors, f"{field} 缺失必须拒收"
```

并在该文件导入区补：

```python
from aitester.case_design.schema import (
    CaseDraft, parse_case_file, validate_cases, validate_drafts,
)
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_schema.py tests/test_case_design_writing.py -q`
Expected: 全绿（Step 3–6 已实现；若仍红按报错回改）。

- [ ] **Step 8: 全量门禁复跑**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: `770 + 13 = 783 passed, 0 failed`（只增不减；具体数以实跑为准并在报告里如实写）。

- [ ] **Step 9: 提交**

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/case_design/constants.py backend/src/aitester/case_design/env.py backend/src/aitester/case_design/ledger.py backend/src/aitester/case_design/schema.py backend/tests/test_case_design_schema.py backend/tests/test_case_design_writing.py && git commit -m "feat(case-design): 第四层落点常量与账本 writing 节 + 用例草稿 schema（cc- 不进三层轴）"
```

---

## Task 19: `writing.py` 确定性核对与交付物渲染

**Files:**
- Create: `backend/src/aitester/case_design/writing.py`
- Test: `backend/tests/test_case_design_writing.py`（追加）

**Interfaces:**
- Consumes: `constants.CASE_BATCH_CAP / VAGUE_ASSERTION_MARKS / ENV_PRECONDITION_MARKS`（T18）、`schema.CaseDraft`（T18）、`PRIORITY_RANK`。
- Produces（T20/T21/T22 逐字依赖）：
  - `plan_case_targets(chain_rows: list[dict], story_rows: list[dict], point_rows: list[dict], scope: dict[str, set[str]]) -> list[dict]`
    返回按链路 id 升序的表，每条形如 `{"chain": "ch-0002", "chain_name": "…", "batches": [{"id": "ch-0002-b1", "points": ["pt-0003", "pt-0004"]}]}`；链路必须**升序去重**、批内点升序、每批点数 ≤ `CASE_BATCH_CAP`；一条链路 0 个点 ⇒ 不出批次（该链路在表里 `batches: []`）。
  - `denominator_points(point_rows: list[dict], scope: dict[str, set[str]], chain: str) -> list[dict]`：某链路的分母点（含 `id`/`story`/`name`/`directions`），`scope` 为空 dict 时按全量。
  - `collect_covers(cases: list[CaseDraft]) -> dict[str, list[str]]`：点 id → 认领它的 `cc-` 列表（一链多批合并后调用）。
  - `run_case_checks(points: list[dict], cases: list[CaseDraft]) -> dict` → `{"hard": [{"code","where","detail"}, ...], "report": {...}, "fulfillment": [{"point","name","owners":[...]}]}`，hard code 只有 `uncovered_point` 与 `phantom_cover`（`_HARD_CODES` 常量元组钉死两条，评审可查）。
  - `compose_case_delivery(ledger_data: dict, targets: list[dict], checks_by_chain: dict[str, dict], extras: dict) -> str`
    - `extras` 键（T22 逐字提供）：`uncovered`=**全局去重**后的未落实点 id 列表、`notes`=规范呈递线索、`dispositions`=意见落点、`unresolved`=未消化项（含归因）、`cases_by_chain`=每链的 `CaseDraft` 列表。
    - `checks_by_chain[chain]["fulfillment"]` 必须由**全局一次** `run_case_checks(全部点, 全部用例)` 的表按链路过滤而来（跨批认领的用例仍算 owner），实测计数只认 `extras["uncovered"]`——两处不同源就会出现「表上落实了、计数说没落实」。
- 交付物必须包含的中文段标题（T22 断言逐字匹配）：`## 前置判定`、`## 用例清单`、`## 履约差异表`、`## 规范校验表（呈递项）`、`## 未消化项（含不收敛归因）`、`## 失效待重算批次`、`## 意见落点对照表（每条意见的去向）`。

- [ ] **Step 1: 写失败测试——分批与分母**

在 `backend/tests/test_case_design_writing.py` 追加：

```python
from aitester.case_design.writing import (
    collect_covers, denominator_points, plan_case_targets, run_case_checks,
)


def _pt(pid, story, chain_stories=None):
    return {"id": pid, "story": story, "name": f"点{pid}", "directions": ["正向"]}


def test_plan_case_targets_batches_by_cap_and_sorts_chains():
    chain_rows = [{"id": "ch-0002"}, {"id": "ch-0001"}]
    story_rows = [{"id": "st-0001", "chains": ["ch-0001"]},
                  {"id": "st-0002", "chains": ["ch-0002"]}]
    point_rows = [_pt(f"pt-{i:04d}", "st-0001") for i in range(1, 13)]   # 12 点 → 10+2 两批
    point_rows += [_pt("pt-0101", "st-0002")]
    targets = plan_case_targets(chain_rows, story_rows, point_rows,
                                {"chains": {"ch-0001", "ch-0002"}, "stories": {"st-0001", "st-0002"}})
    assert [t["chain"] for t in targets] == ["ch-0001", "ch-0002"]
    assert [b["id"] for b in targets[0]["batches"]] == ["ch-0001-b1", "ch-0001-b2"]
    assert len(targets[0]["batches"][0]["points"]) == 10
    assert len(targets[0]["batches"][1]["points"]) == 2
    assert targets[1]["batches"][0]["points"] == ["pt-0101"]


def test_chain_without_points_gets_no_batches():
    targets = plan_case_targets([{"id": "ch-0001"}], [{"id": "st-0001", "chains": ["ch-0001"]}],
                                [], {"chains": {"ch-0001"}, "stories": {"st-0001"}})
    assert targets[0]["batches"] == []


def test_denominator_points_scope_empty_means_all():
    rows = [_pt("pt-0001", "st-0001"), _pt("pt-0002", "st-0009")]
    assert denominator_points(rows, {}, "ch-0001") == []      # 链路归属由 story 表反查，见下一条
    got = denominator_points(rows, {"stories": {"st-0001", "st-0009"}}, "ch-0001")
    assert {p["id"] for p in got} == {"pt-0001", "pt-0002"}


def test_plan_case_targets_story_chain_membership_decides_ownership():
    # st-0001 属 ch-0001，st-0002 属 ch-0002；点只算给它所属故事的链路
    targets = plan_case_targets([{"id": "ch-0001"}, {"id": "ch-0002"}],
                                [{"id": "st-0001", "chains": ["ch-0001"]},
                                 {"id": "st-0002", "chains": ["ch-0001", "ch-0002"]}],
                                [_pt("pt-0001", "st-0001"), _pt("pt-0002", "st-0002")],
                                {})
    by_chain = {t["chain"]: [p for b in t["batches"] for p in b["points"]] for t in targets}
    assert by_chain == {"ch-0001": ["pt-0001", "pt-0002"], "ch-0002": ["pt-0002"]}


def test_duplicate_point_rows_without_signals_fall_back_to_last_row():
    """无 存量/草稿 信号可辨时（两行都是裸 upsert 行），mixed 分母退化为同 id 取后见行。

    不去重会把同一点切进两个批次 ⇒ 末门「未落实点」虚增（假漏测）；取错行会拿回写前的旧内容当分母。
    """
    rows = [{"id": "pt-0001", "story": "st-0001", "name": "旧名"},
            {"id": "pt-0001", "story": "st-0001", "name": "新名"}]
    stories = [{"id": "st-0001", "chains": ["ch-0001"]}]
    got = denominator_points(rows, {}, "ch-0001", stories)
    assert [p["id"] for p in got] == ["pt-0001"] and got[0]["name"] == "新名"
    targets = plan_case_targets([{"id": "ch-0001"}], stories, rows, {})
    assert [b["points"] for b in targets[0]["batches"]] == [["pt-0001"]]
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing.py -q -k "targets or denominator"`
Expected: FAIL（`ModuleNotFoundError: aitester.case_design.writing`）。

- [ ] **Step 2: 实现分批与分母**

`backend/src/aitester/case_design/writing.py` 新建：

```python
"""第四层用例编写环的确定性面：分批、分母、履约核对、交付物渲染。

全部不经 LLM（④ 的第四层同型物）：漏测判据必须机器可核，才配当 hard（裁定 37）。
覆盖度没有客观分母这条更早的裁定不被本片推翻——这里 hard 判的是「已知点有没有落实」，
不是「业务穷尽性」。
"""

from __future__ import annotations

from typing import Any

from aitester.case_design.constants import (
    CASE_BATCH_CAP, ENV_PRECONDITION_MARKS, VAGUE_ASSERTION_MARKS,
)
from aitester.case_design.schema import CaseDraft

# 本环唯一两个 hard code（新增必须同步改 spec 验收节，与三层 R-32 同口径）
_HARD_CODES = ("uncovered_point", "phantom_cover")


def _stories_of_chain(chain: str, story_rows: list[dict]) -> set[str]:
    return {str(s.get("id") or "") for s in story_rows
            if chain in {str(c) for c in (s.get("chains") or [])}}


def _in_scope(value: str, scope_set: set[str] | None) -> bool:
    """空 scope（缺省/未给）= 全量任务，一切都在范围内（与 build_universe 的 fail-closed 同源）。"""
    if scope_set is None:
        return True
    return value in scope_set


def denominator_points(point_rows: list[dict], scope: dict[str, set[str]], chain: str,
                       story_rows: list[dict] | None = None) -> list[dict]:
    """某链路的履约分母 = 所属故事挂在它上面的全部测试点（范围空 = 全量）。

    点→故事→链路的关系一律从节点字段确定性反查，不问模型（消费对称性：编写环吃的是 KB/账本
    制品，不是对话记忆）。
    """
    stories = story_rows or []
    owned = _stories_of_chain(chain, stories)
    scope_stories = scope.get("stories") if scope else None
    latest: dict[str, dict] = {}
    for row in point_rows:
        sid = str(row.get("story") or "")
        if not owned and scope_stories is not None:
            # story_rows 缺席（调用方只给了点）时退化为按点所属故事判定范围；否则必须挂在链路上
            if not _in_scope(sid, scope_stories):
                continue
        elif owned:
            if sid not in owned:
                continue
            if not _in_scope(sid, scope_stories):
                continue
        if row.get("op") == "delete":
            continue
        if row.get("id"):
            # 后出现者胜：编写环吃 `_rows_of(POINT)`（KB 存量在前、本 run 草稿在后），
            # mixed 任务里同一 pt- id 会有两行，取旧行 = 拿回写前的内容当分母。
            # 去重还挡住「同一点被切进两个批次」——那是假漏测（分母里重复行）的直接来源。
            latest[str(row["id"])] = dict(row)
    return sorted(latest.values(), key=lambda r: str(r["id"]))


def plan_case_targets(chain_rows: list[dict], story_rows: list[dict], point_rows: list[dict],
                      scope: dict[str, set[str]]) -> list[dict]:
    """编写环计划：链路升序 → 每条链路的分母点 → 按 `CASE_BATCH_CAP` 切批。"""
    chains = sorted({str(r.get("id") or "") for r in chain_rows if r.get("id")})
    scope_chains = scope.get("chains") if scope else None
    targets: list[dict] = []
    for cid in chains:
        if not _in_scope(cid, scope_chains):
            continue
        points = [str(p["id"]) for p in denominator_points(point_rows, scope, cid, story_rows)]
        batches = [{"id": f"{cid}-b{i}", "points": points[i:i + CASE_BATCH_CAP]}
                   for i in range(0, len(points), CASE_BATCH_CAP)]
        # 0 点的链路也要在场：末门要让人看到「这条链路没有点可落实」，而不是静默少一条链路
        targets.append({"chain": cid, "batches": batches})
    return targets


def collect_covers(cases: list[CaseDraft]) -> dict[str, list[str]]:
    """点 id → 认领它的用例 id 列表（多点合一条 = 该条同时认领多个点）。"""
    owners: dict[str, list[str]] = {}
    for case in cases:
        for pid in case.covers:
            key = str(pid)
            if case.case_id not in owners.setdefault(key, []):
                owners[key].append(case.case_id)
    return owners
```

- [ ] **Step 3: 跑到绿（分批/分母）**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing.py -q -k "targets or denominator"`
Expected: PASS。`test_denominator_points_scope_empty_means_all` 的第一句断言依赖「无 story 表 ⇒ 按点所属故事判定」这条退化分支，实现已覆盖；若红，改实现而非改断言。

- [ ] **Step 4: 写失败测试——履约核对（两条 hard）**

追加到 `backend/tests/test_case_design_writing.py`：

```python
def _case(cid, covers, *, expected=None, pre="账号已登录", steps=("提交下单",), title="用例"):
    return CaseDraft(case_id=cid, title=title, covers=list(covers), preconditions=pre,
                     steps=list(steps), expected=list(expected or ["订单金额按券面规则抵扣"]))


def test_run_case_checks_clean_when_every_point_claimed():
    points = [{"id": "pt-0001", "name": "甲"}, {"id": "pt-0002", "name": "乙"}]
    cases = [_case("cc-0001", ["pt-0001", "pt-0002"])]     # 多点合一条：合法（裁定 38）
    out = run_case_checks(points, cases)
    assert out["hard"] == []
    assert {f["point"] for f in out["fulfillment"]} == {"pt-0001", "pt-0002"}
    assert out["fulfillment"][0]["owners"] == ["cc-0001"]


def test_uncovered_point_is_hard_and_names_the_point():
    out = run_case_checks([{"id": "pt-0001", "name": "甲"}, {"id": "pt-0002", "name": "乙"}],
                          [_case("cc-0001", ["pt-0001"])])
    codes = [(f["code"], f["where"]) for f in out["hard"]]
    assert codes == [("uncovered_point", "pt-0002")]


def test_phantom_cover_is_hard_even_when_all_points_claimed():
    out = run_case_checks([{"id": "pt-0001", "name": "甲"}],
                          [_case("cc-0001", ["pt-0001"]), _case("cc-0002", ["pt-9999"])])
    assert ("phantom_cover", "cc-0002") in [(f["code"], f["where"]) for f in out["hard"]]


def test_case_count_is_never_a_criterion():
    """假指标防线：用例数 ≥ 点数不得判通过——未认领点必须仍为 hard。"""
    points = [{"id": "pt-0001", "name": "甲"}]
    cases = [_case(f"cc-000{i}", ["pt-0002"]) for i in (1, 2, 3)]   # 三条用例、全认领不存在的点
    out = run_case_checks(points, cases)
    assert {f["code"] for f in out["hard"]} == {"uncovered_point", "phantom_cover"}


def test_hard_codes_are_exactly_two():
    from aitester.case_design.writing import _HARD_CODES
    assert _HARD_CODES == ("uncovered_point", "phantom_cover")


def test_report_counts_only_advisory_marks_never_hard():
    """含糊断言与环境存量前置：只呈递，不进 hard（裁定 37），且点数与内容对得上。"""
    points = [{"id": "pt-0001", "name": "甲"}]
    cases = [_case("cc-0001", ["pt-0001"], expected=["下单正常"],
                   pre="账号里已有历史订单", steps=("提交下单",)),
             _case("cc-0002", ["pt-0001"], expected=["订单金额按券面规则抵扣"])]
    out = run_case_checks(points, cases)
    assert out["hard"] == []
    rep = out["report"]
    assert rep["vague_assertions"] == 1 and rep["env_preconditions"] == 1
    assert rep["cases_total"] == 2
    assert any("含糊断言" in item["detail"] for item in rep["notes"])
    assert any("环境存量前置" in item["detail"] for item in rep["notes"])
```

- [ ] **Step 5: 实现 `run_case_checks`**

在 `writing.py` 追加：

```python
def _norm(text: Any) -> str:
    return "".join(str(text or "").split())


def run_case_checks(points: list[dict], cases: list[CaseDraft]) -> dict:
    """履约（hard）+ 规范（report 呈递）。

    hard 只有两条，都能被机器证明：`uncovered_point`（分母里的点没人认领 = 漏测）、
    `phantom_cover`（用例认领了分母外的点 = 假完整）。必填缺失已在 schema 层拒收，
    这里不再为它增设 code（同一条坏输入不该有两个处置出口）。
    """
    pid_set = {str(p.get("id") or "") for p in points if p.get("id")}
    owners = collect_covers(cases)
    hard: list[dict] = []

    for pid in sorted(pid_set):
        if not owners.get(pid):
            name = next((str(p.get("name") or "") for p in points if str(p.get("id")) == pid), "")
            hard.append({"code": "uncovered_point", "where": pid,
                         "detail": f"测试点「{pid} {name}」没有任何用例认领（covers），属漏测"})
    for case in cases:
        phantom = [str(c) for c in case.covers if str(c) not in pid_set]
        if phantom:
            hard.append({"code": "phantom_cover", "where": case.case_id,
                         "detail": f"用例认领了不在本链路分母内的点 {phantom}，"
                                   "要么点 id 写错、要么用例越界"})

    notes: list[dict] = []
    vague = env_pre = 0
    for case in cases:
        for line in case.expected:
            hits = [w for w in VAGUE_ASSERTION_MARKS if _norm(w) in _norm(line)]
            if hits:
                vague += 1
                notes.append({"where": case.case_id, "kind": "含糊断言",
                              "detail": f"「{line}」含 {hits}，不可机械判定通过与否"})
        pre_hits = [w for w in ENV_PRECONDITION_MARKS if _norm(w) in _norm(case.preconditions)]
        if pre_hits:
            env_pre += 1
            notes.append({"where": case.case_id, "kind": "环境存量前置",
                          "detail": f"前置「{case.preconditions}」依赖环境既有数据 {pre_hits}，"
                                    "建议改为可造数的前置"})

    fulfillment = [{"point": pid,
                    "name": next((str(p.get("name") or "") for p in points
                                  if str(p.get("id")) == pid), ""),
                    "owners": sorted(owners.get(pid, []))} for pid in sorted(pid_set)]
    return {"hard": hard,
            "report": {"cases_total": len(cases), "points_total": len(pid_set),
                       "vague_assertions": vague, "env_preconditions": env_pre,
                       "notes": notes},
            "fulfillment": fulfillment}
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing.py -q`
Expected: 全绿。

- [ ] **Step 6: 写失败测试——交付物渲染（末门唯一可视对象）**

追加：

```python
from aitester.case_design.writing import compose_case_delivery


def _delivery_fixture():
    points = [{"id": "pt-0001", "name": "甲"}, {"id": "pt-0002", "name": "乙"}]
    cases = [_case("cc-0001", ["pt-0001"], expected=["下单正常"]),
             _case("cc-0002", ["pt-0001"])]
    checks = run_case_checks(points, cases)
    ledger_data = {"writing": {"status": "awaiting_review", "targets": [
        {"chain": "ch-0001", "batches": [{"id": "ch-0001-b1", "points": ["pt-0001", "pt-0002"]}]}],
        "gate": {"round": 0}, "stale_batches": ["ch-0002-b1"], "note": "设计侧回写完成 @t"}}
    extras = {"uncovered": ["pt-0002"], "notes": checks["report"]["notes"],
              "dispositions": [{"ref": "op-01", "chain": "ch-0001", "case": "cc-0001",
                                "kind": "颗粒度", "ask": "拆成两步", "status": "已销账",
                                "note": "已拆"}],
              "unresolved": [{"chain": "ch-0001", "ref": "op-02", "ask": "补负向",
                              "cause": "评审分歧", "note": "评审与本块分歧"}],
              "cases_by_chain": {"ch-0001": cases}}
    return compose_case_delivery(ledger_data, ledger_data["writing"]["targets"],
                                 {"ch-0001": checks}, extras)


def test_delivery_has_all_required_sections_in_order():
    text = _delivery_fixture()
    order = ["## 前置判定", "## 用例清单", "## 履约差异表", "## 规范校验表（呈递项）",
             "## 未消化项（含不收敛归因）", "## 失效待重算批次",
             "## 意见落点对照表（每条意见的去向）"]
    pos = [text.index(h) for h in order]
    assert pos == sorted(pos), "末门可视对象的段落顺序必须固定"


def test_delivery_shows_measured_counts_not_ai_verdict():
    """裁定 36 的落地：门上必须摆实测计数（人在这里看到的「未落实点」是数出来的）。"""
    text = _delivery_fixture()
    assert "实测" in text
    assert "pt-0002" in text                       # 未落实的点必须逐条点名，不许只给数


def test_delivery_marks_stale_batches_and_note():
    text = _delivery_fixture()
    assert "ch-0002-b1" in text and "失效待重算" in text
    assert "设计侧回写完成" in text


def test_delivery_lists_advisory_notes_as_presented_items():
    text = _delivery_fixture()
    assert "含糊断言" in text and "cc-0001" in text


def test_shared_point_counted_once_in_measured_line():
    """一点挂两条链路（裁定 2 的重复归属）：实测计数走全局去重表，不得按链路累加翻倍。"""
    checks = run_case_checks([{"id": "pt-0001", "name": "共享点"}], [])
    targets = [{"chain": "ch-0001", "batches": [{"id": "ch-0001-b1", "points": ["pt-0001"]}]},
               {"chain": "ch-0002", "batches": [{"id": "ch-0002-b1", "points": ["pt-0001"]}]}]
    text = compose_case_delivery({"writing": {"targets": targets, "gate": {},
                                              "stale_batches": [], "note": ""}},
                                 targets, {"ch-0001": checks, "ch-0002": checks},
                                 {"uncovered": ["pt-0001"], "notes": [], "dispositions": [],
                                  "unresolved": [], "cases_by_chain": {}})
    assert "未落实点 1 个" in text and "未落实点 2 个" not in text
    assert text.count("pt-0001") >= 2          # 两条链路各自可见（呈递不缩水），但计数只算一次
```

- [ ] **Step 7: 实现 `compose_case_delivery`**

```python
_SECTION_ORDER = ("前置判定", "用例清单", "履约差异表", "规范校验表（呈递项）",
                  "未消化项（含不收敛归因）", "失效待重算批次",
                  "意见落点对照表（每条意见的去向）")


def compose_case_delivery(ledger_data: dict, targets: list[dict],
                          checks_by_chain: dict[str, dict], extras: dict) -> str:
    """末门唯一可视对象（裁定 36）：实测计数 + 逐点去向 + 呈递线索，全部确定性组装。"""
    writing = ledger_data.get("writing") or {}
    lines = ["# 用例交付清单（本次任务）", "",
             "> 由第四层用例编写环生成；本文件是末门的唯一可视对象。"
             "用例正文只落项目空间 design/cases/，**不写知识库**。", "",
             "## 前置判定"]
    for t in targets:
        batches = t.get("batches") or []
        n_points = sum(len(b.get("points") or []) for b in batches)
        lines.append(f"- 链路 {t.get('chain', '')}：分母测试点 {n_points}，批次 "
                     f"{[b.get('id') for b in batches]}")
    if writing.get("note"):
        lines.append(f"- 设计侧留痕：{writing['note']}")
    lines += ["", "## 用例清单"]
    for t in targets:
        for b in t.get("batches") or []:
            cases = (extras.get("cases_by_chain") or {}).get(t["chain"], [])
            mine = [c for c in cases if c.case_id]
            lines.append(f"- 批次 {b.get('id', '')}（应落实点 {len(b.get('points') or [])} 个）："
                         f"本链路累计用例 {len(mine)} 条，文件 design/cases/{b.get('id', '')}.json")
    lines += ["", "## 履约差异表（逐点去向，实测）"]
    uncovered_ids = {str(u) for u in (extras.get("uncovered") or [])}
    for chain, checks in sorted(checks_by_chain.items()):
        for row in checks.get("fulfillment") or []:
            pid = str(row.get("point", ""))
            owners = row.get("owners") or []
            if pid in uncovered_ids:
                lines.append(f"- [{chain}] {pid} {row.get('name', '')}：**未落实（无用例认领）**")
            else:
                lines.append(f"- [{chain}] {pid} {row.get('name', '')}：落实于 {','.join(owners)}")
    # 计数走全局去重表（uncovered_ids），不逐链路累加：一点挂两链时逐链累加会把同一个漏测报成两个。
    lines.append(f"- 实测：未落实点 {len(uncovered_ids)} 个（hard 判据要求为 0，非 0 不得进门）")
    lines += ["", "## 规范校验表（呈递项）"]
    notes = extras.get("notes") or []
    for n in notes:
        lines.append(f"- {n.get('where', '')}（{n.get('kind', '')}）：{n.get('detail', '')}")
    if not notes:
        lines.append("- 无")
    lines += ["", "## 未消化项（含不收敛归因）"]
    for u in extras.get("unresolved") or []:
        lines.append(f"- [{u.get('chain', '')}/{u.get('ref', '')}] {u.get('ask', '')}"
                     f"（归因：{u.get('cause', '')}——{u.get('note', '')}）")
    if not extras.get("unresolved"):
        lines.append("- 无")
    lines += ["", "## 失效待重算批次"]
    for bid in writing.get("stale_batches") or []:
        lines.append(f"- {bid}（意见回溯所致；批准交付前请重做，或明确保留旧稿）")
    if not writing.get("stale_batches"):
        lines.append("- 无")
    lines += ["", "## 意见落点对照表（每条意见的去向）"]
    for d in extras.get("dispositions") or []:
        note = d.get("note") or ""
        lines.append(f"- [{d.get('chain', '')}] {d.get('ref', '')}"
                     f"（{d.get('kind', '')}·用例 {d.get('case', '')}）："
                     f"{d.get('ask', '')} → {d.get('status', '')}"
                     + (f"——{note}" if note else ""))
    if not extras.get("dispositions"):
        lines.append("- 无")
    return "\n".join(lines) + "\n"
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing.py -q`
Expected: 全绿。

- [ ] **Step 8: 全量门禁 + 提交**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 只增不减、0 failed（把实跑数写进报告）。

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/case_design/writing.py backend/tests/test_case_design_writing.py && git commit -m "feat(case-design): 第四层确定性核对——未落实点/幻影认领两条 hard + 规范呈递与交付物渲染"
```

---

## Task 20: 计划侧接线——`case_plan` 阶段与 `case_only`/`mixed` 语义升级

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（imports、`_writing_enabled`、`h_plan:437-466`、`_first_live`/空窗分支、`_after_layer:1106-1117`、`h_writeback:1552-1617`、`_STAGE_HANDLERS:1620-1624`）
- Modify: `backend/src/aitester/case_design/instructions.py`（`plan_instruction` 的 task_kind 措辞、`case_plan_instruction` 新增）
- Test: `backend/tests/test_case_design_writing_stages.py`（新建）
- Test: `backend/tests/test_case_design_plan.py`（追加 task_kind 语义用例）

**Interfaces:**
- Consumes: T18 的 `writing` 账本结构与 `next_case_seq`；T19 的 `plan_case_targets`；既有 `validate_plan`/`in_scope_targets`/`_rows_of`/`ctx.kb_rows`。
- Produces:
  - `_writing_enabled(ctx) -> bool`：`task_kind in ("case_only", "mixed")`
  - `_case_ready_or_block(ctx) -> str`：返回空串 = 可开工；非空 = 人话拒因（三层未维护 / 有失效待重算 / 分母点数 0）
  - `_materialize_case_batches(ctx, targets) -> None`：把每个批次入账为 `writing["batches"]` 条目 `{"id","chain","state":"todo","round":0}`，并落**分母清单** `design/manifests/case-<batch>.json`（含该批每个点的 scenario/entities/directions 与所属故事的 actor/preconditions/trigger/expected——生成侧一次读拿到写用例所需的全部信息，不靠对话记忆，9b 消费对称性）
  - `_case_batch_points(ctx, batch) -> list[str]`：从账本 targets 读该批应落实的点 id
  - `h_case_plan(ctx) -> Any`：写 `writing["targets"]` + `writing["batches"]` + 批次清单，游标换到 `case_gen`（layer=链路 id、block=批次 id）并**下发首批生成指令**；不满足前置 → `ctx.end(拒因)`（零生成、零写入、不 halted、`writing["note"]` 存拒因）
  - `case_gen_instruction(*, chain, batch, manifest_path, points, batch_no, batch_total) -> str`（`instructions.py`；T21 的批环重试与后续批次复用同一构造器）
  - 游标 stage 名（T21/T22 继续用）：`"case_plan"`, `"case_gen"`, `"case_opt"`, `"case_attribute"`, `"case_gate"`, `"case_gate_interpret"`

- [ ] **Step 1: 写失败测试——`case_only` 三层不进窗口、直进编写环**

新建 `backend/tests/test_case_design_writing_stages.py`：

```python
"""第四层编写环的阶段处理器与转场（挂具复用 test_case_design_driver.py，零联网零真模型）。"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage

from aitester.case_design.constants import CASE_BATCH_CAP
from aitester.case_design.ledger import Ledger
from aitester.case_design.stages import _writing_enabled

from test_case_design_driver import (  # 复用既有挂具，不抄第二份
    ScriptTask, StubKb, _append, _drive, _end_text, _env, _j, _write, drain, simulate,
)


def _kb_with_three_layers(tmp_path: Path) -> StubKb:
    """三层都已维护（点层带完整标记），编写环前置成立。"""
    return StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                   "trigger": "提交下单", "expected": "下单成功", "name": "正常下单",
                   "priority": "P1"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001", "name": "券抵扣",
                   "scenario": "用满足门槛的券下单", "entities": ["订单", "优惠券"],
                   "directions": ["正向"], "priority": "P1"}],
    }, root=tmp_path / "kb")


_CASE_ONLY_PLAN = {"task_kind": "case_only", "entry_layer": "chain", "terminal_layer": "point",
                   "target_subtree": "", "source_files": [], "note": "给下单链路生成用例"}


def _led(env):
    led = Ledger.load(env.design)
    assert led is not None, "账本没落盘"
    return led


def test_writing_enabled_only_for_case_kinds():
    def ctx_of(kind):
        led = Ledger.fresh(Path("unused"))          # 不落盘，只喂一个 task 节
        led.data["task"] = {"descriptor": {"task_kind": kind}}
        return type("_C", (), {"led": led})()

    for kind, expected in (("case_only", True), ("mixed", True), ("design", False)):
        assert _writing_enabled(ctx_of(kind)) is expected      # 只吃账本事实，不猜


def test_case_only_skips_three_layers_and_opens_first_batch(tmp_path):
    """case_only：三层一律 skipped（只读上下文），开账即物化首批并下发第一批生成指令。"""
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    frames: list[dict] = []
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}

    turn = _drive(env, state, ScriptTask(), writer=frames.append)

    assert turn["case"]["route"] == "agent"                   # 下发指令，不空转
    led = _led(env)
    assert {v["state"] for v in led.data["layers"].values()} == {"skipped"}
    assert (led.cursor["stage"], led.cursor["layer"], led.cursor["block"]) \
        == ("case_gen", "ch-0001", "ch-0001-b1")
    writing = led.data["writing"]
    assert writing["status"] == "active"
    assert writing["targets"] == [{"chain": "ch-0001",
                                   "batches": [{"id": "ch-0001-b1", "points": ["pt-0001"]}]}]
    assert writing["batches"] == [{"id": "ch-0001-b1", "chain": "ch-0001",
                                   "state": "todo", "round": 0}]
    manifest = json.loads((env.manifests_dir / "case-ch-0001-b1.json")
                          .read_text(encoding="utf-8"))
    assert manifest["points_cap"] == CASE_BATCH_CAP
    assert manifest["points"][0]["scenario"] == "用满足门槛的券下单"
    assert manifest["points"][0]["trigger"] == "提交下单"       # 故事上下文随清单到位
    assert manifest["points"][0]["actor"] == "客户"
    text = turn["messages"][-1].content
    assert "design/cases/ch-0001-b1.json" in text and "pt-0001" in text
    assert kb.upserts == [] and kb.deletes == []               # 裁定 35：第四层零 KB 写


def test_design_task_never_enters_writing(tmp_path):
    """design 任务：照旧走设计环，编写环节一个字节都不许动（新增缝只对 case 种类生效）。"""
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json",
           {**_CASE_ONLY_PLAN, "task_kind": "design", "entry_layer": "point",
            "terminal_layer": "point"})        # 点层已维护 → update，设计环正常开跑
    state = {"messages": [HumanMessage(content="只做测试设计")], "case": {}}
    _drive(env, state, ScriptTask(), writer=lambda e: None)
    led = _led(env)
    assert led.cursor["stage"] == "gen"                      # 设计侧生成阶段，不是编写环
    assert led.data["writing"]["status"] == ""
    assert led.data["writing"]["targets"] == []
    assert led.layer("chain")["state"] == "skipped" and led.layer("point")["state"] == "active"
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py -q`
Expected: FAIL（`_writing_enabled` 不存在；`ImportError`）。

- [ ] **Step 2: 写失败测试——前置不满足时明示边界**

追加：

```python
def test_case_only_stops_at_boundary_when_layers_missing(tmp_path):
    """三层没维护 ⇒ 明示边界停在设计侧，零生成零写入，不 halted（裁定 40：合法业务状态，不是故障）。"""
    kb = StubKb(layers={"chain": [], "story": [], "point": []}, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)

    state = drain(env, kb, ScriptTask())                 # 边界即终局：route=end，一次收敛
    assert "用例任务未开工" in _end_text(state["frames"])
    led = _led(env)
    assert led.status == "done"
    assert led.data["writing"]["targets"] == [] and led.data["writing"]["batches"] == []
    assert led.data["writing"]["note"]                    # 拒因入账，交付物侧看得见
    assert list(env.cases_dir().glob("*.json")) == []
    assert kb.upserts == [] and kb.deletes == []          # 裁定 35：第四层零 KB 写


def test_case_only_blocks_on_stale_layer(tmp_path):
    """有失效待重算的层 ⇒ 同样明示边界（先重做设计，再写用例）。

    事实源用 `carried_stale`（`_boot` 归档时留下、跨任务持久）而不是 `layers.*.state`：
    后者会被 `init_task` 按 modes 覆写成 skipped，边界判定若读它就永远读不到。
    """
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    led = Ledger.fresh(env.design)
    led.data["carried_stale"] = ["story"]
    led.save()

    frames: list[dict] = []
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    turn = _drive(env, state, ScriptTask(), writer=frames.append)
    assert turn["case"]["route"] == "end"
    assert "失效待重算" in _end_text(frames)
    assert _led(env).data["writing"]["targets"] == []


def test_case_only_blocks_when_no_points_in_scope(tmp_path):
    """三层齐备但范围内点数为 0 ⇒ 分母为空，明示边界（不许产「零用例也算完成」的交付物）。"""
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                  "trigger": "提交下单", "expected": "下单成功", "name": "正常下单",
                  "priority": "P1"}],
        "point": [],
    }, root=tmp_path / "kb")
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    state = drain(env, kb, ScriptTask())
    assert "没有" in _end_text(state["frames"]) and "测试点" in _end_text(state["frames"])
    led = _led(env)
    assert led.status == "done" and led.data["writing"]["batches"] == []
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py -q`
Expected: 仍红（Step 1 的 `ImportError` 未消；边界三条此刻无法执行属正常，实现后一并转绿）。

- [ ] **Step 3: 实现 `_writing_enabled` + 前置判定 + `h_case_plan`**

`stages.py` 导入区（第 42–50 行那两个 import 之间）加：

```python
from aitester.case_design.writing import plan_case_targets
```

在 `h_plan` 之前插入编写环的计划侧（五个模块级函数，`_writing_enabled → _case_targets → _case_ready_or_block → _materialize_case_batches → _case_batch_points`，后三个只被 `h_case_plan` 用）：

```python
# ---- 第四层编写环：计划侧（裁定 40）----

_CASE_WRITING_KINDS = ("case_only", "mixed")


def _writing_enabled(ctx: Ctx) -> bool:
    """编写环是否参与本 run——只吃账本里的 task_kind 事实，不从对话猜。"""
    kind = str(((ctx.led.data.get("task") or {}).get("descriptor") or {}).get("task_kind") or "")
    return kind in _CASE_WRITING_KINDS


def _case_targets(ctx: Ctx) -> list[dict]:
    """编写环的取数单点：活宇宙 = KB 存量 ∪ 本 run 草稿，范围走 `in_scope_targets`（与三层同源）。

    mixed 的同一轮里 `ctx.kb_rows` 缓存的是**回写前**的存量，只用它会漏掉本 run 刚过审的点；
    `_rows_of` 把草稿拼在后面，配合 `denominator_points` 的同 id 后到为准，分母就是回写后的现稿。
    """
    descriptor = (ctx.led.data.get("task") or {}).get("descriptor") or {}
    chains, stories, points = (_rows_of(ctx, CHAIN), _rows_of(ctx, STORY), _rows_of(ctx, POINT))
    scope = in_scope_targets(descriptor, chains, stories)
    return plan_case_targets(chains, stories, points, scope)


def _case_ready_or_block(ctx: Ctx) -> str:
    """编写环前置（裁定 40）：三层已维护 + 无失效待重算 + 分母点数 > 0。

    返回空串 = 可开工；非空 = 给人看的拒因。不满足时**明示边界停在设计侧**，不静默重生成三层
    （那会把已过审的制品按旧口径再烧一遍），也不 halted（这是合法的业务状态，不是程序故障）。
    """
    led = ctx.led.data
    probe = ((led.get("task") or {}).get("probe") or {})
    # 事实源以 carried_stale 为主：layers.*.state 会被 init_task 按 modes 覆写（case_only 全成
    # skipped），只读它就永远看不见上一轮留下的失效层。两处取并集，本 run 内标的失效也认。
    stale = {*(led.get("carried_stale") or [])} | {
        layer for layer in LAYERS if ctx.led.layer(layer)["state"] == "stale_pending"}
    if stale:
        return ("、".join(LAYER_CN[l] for l in LAYERS if l in stale)
                + "层标了失效待重算，请先跑一次测试设计任务把它们重做")
    if not all(probe.get(layer, {}).get("maintained") for layer in LAYERS):
        missing = "、".join(LAYER_CN[l] for l in LAYERS if not probe.get(l, {}).get("maintained"))
        return f"知识库缺少{missing}，用例没有可落实的设计分母，请先跑一次测试设计任务"
    targets = _case_targets(ctx)
    if not any(t["batches"] for t in targets):
        return "本次范围内没有任何测试点可作分母（链路/故事/点齐备但点数为 0），用例任务无从开工"
    return ""


def _materialize_case_batches(ctx: Ctx, targets: list[dict]) -> None:
    """批次入账 + 分母清单落盘：清单自带故事上下文，生成侧不必再翻 KB（消费对称性）。"""
    story_rows = {str(r.get("id") or ""): r for r in _rows_of(ctx, STORY)}
    point_rows = {str(r.get("id") or ""): r for r in _rows_of(ctx, POINT)}
    chain_rows = {str(r.get("id") or ""): r for r in _rows_of(ctx, CHAIN)}
    batches: list[dict] = []
    for target in targets:
        cid = str(target["chain"])
        for batch in target["batches"] or []:
            items = []
            for pid in batch["points"]:
                point = dict(point_rows.get(pid) or {"id": pid})
                story = story_rows.get(str(point.get("story") or "")) or {}
                items.append({
                    "id": pid, "name": str(point.get("name") or ""),
                    "story": str(point.get("story") or ""),
                    "story_name": str(story.get("name") or ""),
                    "scenario": str(point.get("scenario") or ""),
                    "entities": list(point.get("entities") or []),
                    "directions": list(point.get("directions") or []),
                    "priority": str(point.get("priority") or "P1"),
                    "actor": str(story.get("actor") or ""),
                    "preconditions": str(story.get("preconditions") or ""),
                    "trigger": str(story.get("trigger") or ""),
                    "expected": str(story.get("expected") or ""),
                })
            _write_json(ctx.env.manifests_dir / f"case-{batch['id']}.json",
                        {"chain": cid,
                         "chain_name": str((chain_rows.get(cid) or {}).get("name") or ""),
                         "batch": batch["id"], "points_cap": CASE_BATCH_CAP, "points": items})
            batches.append({"id": batch["id"], "chain": cid, "state": "todo", "round": 0})
    ctx.led.data["writing"]["batches"] = batches


def _case_batch_points(ctx: Ctx, batch: str) -> list[str]:
    for target in ctx.led.data["writing"].get("targets") or []:
        for item in target.get("batches") or []:
            if item.get("id") == batch:
                return [str(p) for p in item.get("points") or []]
    return []


def h_case_plan(ctx: Ctx) -> Any:
    """编写环开账：前置满足 → 物化 targets/batches 并下发首批生成指令；不满足 → 明示边界、零写入、正常收尾。"""
    reason = _case_ready_or_block(ctx)
    led = ctx.led
    if reason:
        led.data["writing"]["note"] = reason
        led.status = "done"
        return ctx.end("用例任务未开工：" + reason + "。本轮不生成用例、不写知识库。")
    targets = _case_targets(ctx)
    led.data["writing"]["targets"] = targets
    led.data["writing"]["status"] = "active"
    _materialize_case_batches(ctx, targets)
    batches = led.data["writing"]["batches"]
    first = next((b for b in batches if b["state"] == "todo"), None)
    if first is None:                              # 兜底：_case_ready_or_block 已挡，理论不达
        raise _Halt("编写环计划里没有任何可执行批次")
    _go(ctx, "case_gen", layer=first["chain"], block=first["id"])
    # 首批指令由计划侧下发（一次激活把「准备 + 派活」做完），游标已指向 case_gen，
    # 重入即落批环；后续批次与重试由 h_case_gen 用同一个 case_gen_instruction 发（T21）。
    return ctx.instr(case_gen_instruction(
        chain=first["chain"], batch=first["id"],
        manifest_path=ctx.rel(ctx.env.manifests_dir / f"case-{first['id']}.json"),
        points=_case_batch_points(ctx, first["id"]), batch_no=1, batch_total=len(batches)))
```

`stages.py` 的常量导入行（第 20–27 行那个 `from aitester.case_design.constants import (...)`）补 `CASE_BATCH_CAP`；`writing` 的 import 见上一步；指令 import 行（第 35–38 行）补 `case_gen_instruction`：

```python
from aitester.case_design.instructions import (
    attribute_instruction, case_gen_instruction, gate_fix_instruction, gen_instruction,
    opt_instruction, plan_instruction,
)
```

- [ ] **Step 4: 接四个既有接缝**

1. `h_plan`（`stages.py:462-466`）把「无活层即 halted」改成先给编写环让路：

```python
    entry = _first_live(descriptor["entry_layer"], descriptor["terminal_layer"], modes)
    if entry is None:
        if _writing_enabled(ctx):
            # 裁定 40：case_only 的三层一律 skipped（只读上下文），空窗不是故障而是交接点。
            _go(ctx, "case_plan")
            return None
        raise _Halt("计划窗口内没有任何需要生成的层")
    _enter_layer(ctx, entry)
    return None
```

2. `plan_layers`（`plan.py:92-105`）之后不动签名；在 `h_plan` 里 `modes = plan_layers(...)` 之后、`init_task` 之前插入一条**唯一**的编写环覆盖：

```python
    modes = plan_layers(descriptor, probe, stale)
    if descriptor["task_kind"] == "case_only":
        # case_only：三层全 skipped —— 三层内容只作只读上下文经 kb_rows 使用，本 run 不写它们。
        modes = {layer: "skipped" for layer in LAYERS}
```

3. `_after_layer`（`stages.py:1106-1117`）末尾的 `_go(ctx, "gate")` 之前，mixed 收口不改设计门位置，**不**在此转场（末门只属于编写环；mixed 的设计侧仍走大纲门）。这里不加逻辑，只加一条注释锚定意图：

```python
    # mixed 的设计侧仍走大纲门（三层质量内核不因带用例而缩）；编写环在回写成功后接手，见 h_writeback。
    _go(ctx, "gate")
```

4. `h_writeback`（`stages.py:1614-1617`）把终局改为「编写环排队则不 done、同轮接手」：

```python
    wb["done"] = True
    if _writing_enabled(ctx) and not led.data["writing"].get("targets"):
        # 裁定 40：mixed 在同一任务内续跑编写环。设计侧不回终帧（回写事实写进 writing["note"]，
        # 末门交付物的「前置判定」段呈递）——done 状态会把工作区归档，若在此收尾用例环就没了。
        led.data["writing"]["note"] = f"设计侧回写完成 @{_now()}"
        _go(ctx, "case_plan")
        return None
    led.status = "done"
    tail = f"（{untouched} 个节点内容与库内一致，未重写。）" if untouched else ""
    return ctx.end("回写完成：本次过审节点已写入知识库。" + tail)
```

（原 `led.status = "done"` 与 `wb["done"] = True` 的相对次序保持：`wb["done"] = True` 上移一行，其余照旧。）

5. `_STAGE_HANDLERS` 注册（T20 只注册已实现的）：

```python
    "case_plan": h_case_plan,
```

- [ ] **Step 5: 指令侧——新增 `case_gen_instruction` + task_kind 措辞升级**

`backend/src/aitester/case_design/instructions.py` 末尾新增（模型可见正文用中文，但**文件形状与字段名逐字摊开**——W3-1 真机教训：简报/指令不摊开必填字段与枚举字面量，确定性核对必把付费真机打成 halted）：

```python
def case_gen_instruction(*, chain: str, batch: str, manifest_path: str,
                         points: list[str], batch_no: int, batch_total: int) -> str:
    """第四层批生成指令：一个批次一文件、逐点认领，纪律写死在指令里（裁定 37/38）。"""
    return (
        f"第四层用例编写：第 {batch_no}/{batch_total} 批（链路 {chain}，批次 {batch}）。\n"
        f"1) 先读分母清单 {manifest_path}：每个点自带 scenario/entities/directions 与所属故事的 "
        f"actor/preconditions/trigger/expected，写用例所需信息全在里面，不必再翻知识库。\n"
        f"2) 用例正文落 design/cases/{batch}.json，根对象与每条用例的字段逐字如下（多余的键不要加）：\n"
        '   {"chain": "' + chain + '", "batch": "' + batch + '", "cases": ['
        '{"case_id": "", "title": "…", "covers": ["pt-0000"], "preconditions": "…", '
        '"steps": ["…"], "expected": ["…"], "priority": "P1", "note": ""}]}\n'
        f"3) 认领纪律：本批必须落实的点 = {'、'.join(points)}。每个点至少被一条用例的 covers 点名；"
        "covers 只许填这些 pt-四位数字 id，填界外的点会被判假完整。\n"
        "4) 点与用例数量不固定：一个点可拆多条（换账号/换数据），多个点也可合一条。"
        "禁止为凑数写无用例，也禁止拿「用例数 ≥ 点数」自证完整——核对只看 covers。\n"
        "5) 正文纪律：steps 每步是可执行动作（不写「进行操作」这类空话）；expected 是硬断言"
        "（可核对的具体结果，禁止「正常」「正确」「符合预期」「没有问题」「成功即可」）；"
        "preconditions 只写本用例自己造的前置，禁止拿环境存量当条件"
        "（「已有」「已存在」「存量数据」「库中已有」「环境中已」这类写法一律拒收）；"
        "case_id 留空串，由系统分配，priority 取 P0/P1/P2。\n"
        "6) 写完只回一句确认，不要复述用例正文。"
    )
```

`instructions.py:23-24` 那两行改为。原文（逐字，用于定位）：

```python
        "2) 范围归一化：把指令拆成「入口层 / 目标子树 / 终止层」——task_kind 取 "
        "design（测试设计）/ mixed（设计+用例混杂，本期只做设计侧）/ case_only（纯用例任务，本期只做设计部分）；"
```

新文（模型可见；`case_only`/`mixed` 不再是「本期只做设计部分」）：

```python
        "2) 范围归一化：把指令拆成「入口层 / 目标子树 / 终止层」——task_kind 取 "
        "design（测试设计）/ mixed（设计+用例，先做设计侧、过门回写后同轮续编写环）/ "
        "case_only（纯用例任务：三层只读当分母，本 run 不改三层，只产 design/cases/ 里的用例正文）；"
```

改之前先在 `backend/tests/test_case_design_plan.py` 追加这条钉桩（模型看不见的入口 = 不存在的入口）：

```python
def test_plan_instruction_opens_the_writing_ring_for_case_kinds():
    """task_kind 的两种用例侧语义必须在指令里逐字可见：`plan.py:13 _TASK_KINDS` 早就认
    `case_only`/`mixed`，但旧文案写着「本期只做设计侧／本期只做设计部分」——模型照文案走，
    永远不会把纯用例任务选成编写环入口，第四层的门就形同虚设。本片升级文案，这条用例钉住。"""
    text = plan_instruction()
    assert "本期只做设计" not in text
    assert "同轮续编写环" in text                      # mixed：设计侧之后接手
    assert "design/cases/" in text                    # case_only：产出物点名到目录
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_plan.py -q -k case_kinds`
Expected: FAIL（旧文案含「本期只做设计」且没有后两个标记）→ 改文案后转绿。

- [ ] **Step 6: 跑到绿 + 既有 driver 测试不许红**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py tests/test_case_design_driver.py tests/test_case_design_e2e.py -q`
Expected: 全绿。若既有 `design` 任务用例被转场改动波及，说明第 4 步改错了分支——回改实现，不许动既有断言。

- [ ] **Step 7: 全量门禁 + 提交**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 只增不减、0 failed。

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/case_design/stages.py backend/src/aitester/case_design/instructions.py backend/tests/test_case_design_writing_stages.py backend/tests/test_case_design_plan.py && git commit -m "feat(case-design): 编写环计划侧——case_only 只读三层直进环、mixed 回写后同轮接手、前置不足明示边界"
```

---

## Task 21: 批环——`h_case_gen` / 批评审 / `h_case_opt` / `h_case_attribute`

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（意见簿复用面 `_register_ops`/`_apply_resolutions`、编写环新段 `_cases_file`…`h_case_attribute`、`_STAGE_HANDLERS`、`_ARCHIVE_ITEMS:54`）
- Modify: `backend/src/aitester/case_design/instructions.py`（`case_opt_instruction`、`case_attribute_instruction`）
- Test: `backend/tests/test_case_design_writing_stages.py`（追加）

**Interfaces:**
- Consumes: T18 `parse_case_file` / `CaseDraft` / `CASE_BATCH_CAP`；T19 `run_case_checks`；T20 的游标名与 `case_gen_instruction` / `_case_batch_points` / `writing` 节；既有 `run_reviewer` + `ReviewOut` + `_next_ref` / `_settled` / `_write_in_file` / `_archive_review` / `_errors_block` / `ctx.ask(cap=…)`。
- Produces:
  - `_cases_file(ctx, batch) -> Path`（= `env.cases_dir()/<batch>.json`）、`_case_manifest(ctx, batch) -> dict`
  - `_case_batch_entry(ctx, chain, batch) -> dict`（`writing["batches"]` 条目，缺则补）
  - `_patch_case_ids(ctx, batch) -> None`（空 `case_id` 走 `Ledger.next_case_seq()` 就地改写文件）
  - `_register_ops(ctx, ops, entries, *, source, block) -> None`（`_register` 的簿本无关内核）+ `_w_register(ctx, entries, *, block)` + `_w_open_of(ctx, *, block) -> list[dict]`
  - `_case_gen_text(ctx, chain, batch, *, errors=None) -> str`（`case_gen_instruction` 的取数薄壳：批序、清单路径、点 id 全从账本读）
  - `_case_review_brief(ctx, chain, batch, round_no) -> str`、`_run_case_review(ctx, chain, batch) -> None`
  - `_after_case_batch(ctx) -> None`（下一批 → `case_gen`；批齐 → `_go(ctx, "case_gate")`）
  - `h_case_gen(ctx)` / `h_case_opt(ctx)` / `h_case_attribute(ctx)`；`case_opt_instruction(chain, batch, round_no, *, cases_path, opinions_path, fix_path)`、`case_attribute_instruction(batch, *, opinions_path, out_path)`
  - 批次条目 `state` 取值：`"todo"` → `"drafted"`（草稿已入账，评审/优化在跑）→ `"done"`（本批收口）；`"stale"` 由 T22 的门后回溯写入。
  - 评审 `call_id` 形状：`case-<batch>-r<round>`；意见簿 `source` 固定 `"case_block"`。

- [ ] **Step 1: 写失败测试——批环收草稿、补号、评审、转下一批**

追加到 `backend/tests/test_case_design_writing_stages.py`（文件导入区补 `import re` 与 `from aitester.case_design.schema import ReviewOut`）：

```python
def _kb_with_many_points(tmp_path: Path, n: int = 11) -> StubKb:
    """一条链路 + 一个故事 + n 个测试点：n>CASE_BATCH_CAP ⇒ 天然切成两批。"""
    return StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "level": 1, "parent": "",
                   "name": "下单链路", "business_scope": "下单", "priority": "P1"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"], "actor": "客户",
                   "preconditions": "账号已注册", "trigger": "提交下单", "expected": "下单成功",
                   "name": "正常下单", "priority": "P1"}],
        "point": [{"id": f"pt-{i:04d}", "type": "point", "story": "st-0001",
                   "name": f"点{i:02d}", "scenario": f"场景{i:02d}", "entities": ["订单"],
                   "directions": ["正向"], "priority": "P1"} for i in range(1, n + 1)],
    }, root=tmp_path / "kb")


def _manifest_points(env, batch: str) -> list[str]:
    raw = json.loads((env.manifests_dir / f"case-{batch}.json").read_text(encoding="utf-8"))
    return [str(p["id"]) for p in raw["points"]]


def _cases_payload(chain: str, batch: str, points: list[str]) -> dict:
    """一条点一条用例（1:1 是合法比例之一，裁定 38）；id 留空串交给驱动补号。"""
    return {"chain": chain, "batch": batch, "cases": [
        {"case_id": "", "title": f"用例·{pid}", "covers": [pid],
         "preconditions": "账号已登录且购物车有一件可售商品",
         "steps": ["登录并进入下单页", "提交订单"],
         "expected": ["订单金额按该点场景的规则计算"],
         "priority": "P1", "note": ""} for pid in points]}


def _opinion(value: str, kind: str = "颗粒度", ask: str = "拆成两步再断言") -> dict:
    return {"target": {"type": "node", "value": value}, "kind": kind,
            "ask": ask, "evidence": "步骤合并，失败难定位"}


def test_case_batch_r0_closes_and_dispatches_next_batch(tmp_path):
    kb = _kb_with_many_points(tmp_path, n=11)          # 11 点 ⇒ b1 十点 + b2 一点
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask()                                # 默认判决 REV_CLEAN：无意见
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []

    turn = _drive(env, state, task, writer=frames.append)     # ① 首批指令
    _append(state, turn)
    assert "ch-0001-b1" in turn["messages"][-1].content
    assert task.calls == []                            # 还没读过草稿，评审子一次都不许派

    b1 = _manifest_points(env, "ch-0001-b1")
    assert len(b1) == CASE_BATCH_CAP
    _write(env.cases_dir() / "ch-0001-b1.json", _cases_payload("ch-0001", "ch-0001-b1", b1))

    turn = _drive(env, state, task, writer=frames.append)     # ② 收草稿→r0→下一批
    _append(state, turn)
    assert task.call_ids() == ["case-ch-0001-b1-r0"]
    assert task.calls[0]["agent"] == "case_review"            # 裁定 36②：批内评审必是子智能体
    led = _led(env)
    assert led.data["writing"]["batches"][0]["state"] == "done"
    assert led.data["writing"]["batches"][1]["state"] == "todo"
    assert (led.cursor["stage"], led.cursor["layer"], led.cursor["block"]) \
        == ("case_gen", "ch-0001", "ch-0001-b2")
    assert "第 2/2 批" in turn["messages"][-1].content
    saved = json.loads((env.cases_dir() / "ch-0001-b1.json").read_text(encoding="utf-8"))
    assert [c["case_id"] for c in saved["cases"]] == [f"cc-{i:04d}" for i in range(1, 11)]
    assert led.data["counters"]["case"] == 10                 # 序号只由账本推进
    assert kb.upserts == [] and kb.deletes == []              # 裁定 35：第四层零 KB 写


def test_uncovered_batch_is_reasked_before_paying_for_review(tmp_path):
    """批内自检（确定性）在评审之前：漏点当场重问，一次评审子调用都不烧（裁定 39 的省钱面）。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _append(state, _drive(env, state, task, writer=frames.append))

    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0001", "ch-0001-b1", b1[: len(b1) - 1]))   # 少写最后一条
    turn = _drive(env, state, task, writer=frames.append)
    assert task.calls == []                            # ← 硬防线：坏批不进付费评审
    text = turn["messages"][-1].content
    assert "没有任何用例认领" in text and b1[-1] in text
    assert _led(env).cursor["block"] == "ch-0001-b1"   # 原地重问，不转场
```

- [ ] **Step 2: 写失败测试——意见环（开环 / 处置销账 / unresolved / 归因 / 简报形状纪律）**

追加（同一文件；`_to_case_opt` 是三条用例共用现场，避免各抄一份三轮转场）：

```python
def _to_case_opt(tmp_path: Path):
    """把一条 11 点链路推到「b1 出意见、驱动正在等处置表」的现场。"""
    kb = _kb_with_many_points(tmp_path, n=11)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask(script={"case-*-r0": _j({"opinions": [_opinion("cc-0001")],
                                               "resolutions": []})})
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _append(state, _drive(env, state, task, writer=frames.append))       # 首批指令
    b1 = _manifest_points(env, "ch-0001-b1")
    _write(env.cases_dir() / "ch-0001-b1.json", _cases_payload("ch-0001", "ch-0001-b1", b1))
    turn = _drive(env, state, task, writer=frames.append)                # r0 出意见 → 优化指令
    return env, task, state, frames, turn


def test_batch_opinion_opens_opt_ring(tmp_path):
    env, task, state, frames, turn = _to_case_opt(tmp_path)
    led = _led(env)
    assert (led.cursor["stage"], int(led.cursor["round"]), led.cursor["source"]) \
        == ("case_opt", 0, "case_block")
    assert led.data["writing"]["batches"][0]["state"] == "drafted"       # 没收口
    op = led.data["writing"]["opinions"][0]
    assert (op["ref"], op["source"], op["block"], op["resolved"], op["disposition"]) \
        == ("op-01", "case_block", "ch-0001-b1", False, "")
    text = turn["messages"][-1].content
    assert "case-ch-0001-b1-in-r0.json" in text and "case-ch-0001-b1-fix-r0.json" in text
    assert "fixed|covered|unresolved" in text                            # 枚举逐字下发


def test_disposition_then_clean_re_review_closes_batch(tmp_path):
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-01", "status": "fixed", "note": "已拆两步并补断言"}]})
    _drive(env, state, task, writer=frames.append)
    led = _led(env)
    assert task.call_ids() == ["case-ch-0001-b1-r0", "case-ch-0001-b1-r1"]
    op = led.data["writing"]["opinions"][0]
    assert op["resolved"] is True and op["disposition"] == "fixed"
    assert "已拆两步并补断言" in op["note"]
    assert led.data["writing"]["batches"][0]["state"] == "done"
    assert led.cursor["block"] == "ch-0001-b2"          # 串行：下一批（第二批刻意留着，见 Step 8 注意）


def test_unresolved_disposition_books_writing_unresolved(tmp_path):
    """主智能体判定消化不了 → 带账离开在途集（escalated），未消化项进 `writing["unresolved"]`。"""
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-01", "status": "unresolved", "note": "本轮造不出数据"}]})
    _drive(env, state, task, writer=frames.append)
    led = _led(env)
    assert led.data["writing"]["opinions"][0]["escalated"] is True
    assert led.data["writing"]["unresolved"] == [
        {"block": "ch-0001-b1", "refs": ["op-01"], "cause": "评审分歧", "note": "本轮造不出数据"}]
    assert led.data["writing"]["batches"][0]["state"] == "done"


def test_bad_disposition_is_reasked_not_booked(tmp_path):
    """处置表形状错（ref 不在簿 / status 越枚举）⇒ 重问，绝不计入轮次、绝不销账。"""
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    _write(env.reviews_dir / "case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-99", "status": "fixed", "note": "x"},
                             {"ref": "op-01", "status": "done", "note": "y"}]})
    turn = _drive(env, state, task, writer=frames.append)
    text = turn["messages"][-1].content
    assert "ref 不在意见簿中" in text and "status 须为 fixed|covered|unresolved" in text
    assert task.call_ids() == ["case-ch-0001-b1-r0"]    # 一次也没复审
    assert _led(env).data["writing"]["opinions"][0]["resolved"] is False


def test_case_attribute_books_cause_and_closes(tmp_path):
    env, task, state, frames, _ = _to_case_opt(tmp_path)
    led = _led(env)
    led.cursor.update({"stage": "case_attribute", "round": 5})   # 复现「轮次用尽」现场
    led.save()
    turn = _drive(env, state, task, writer=frames.append)        # 首派归因
    assert "attr-case-ch-0001-b1-r5.json" in turn["messages"][-1].content
    assert (env.attribution_dir / "open-case-ch-0001-b1-r5.json").is_file()

    _write(env.attribution_dir / "attr-case-ch-0001-b1-r5.json",
           {"cause": "成本超限", "note": "评审与生成反复不一致，再跑只烧钱"})
    _drive(env, state, task, writer=frames.append)
    led = _led(env)
    assert led.data["writing"]["unresolved"][0]["cause"] == "成本超限"
    assert led.data["writing"]["unresolved"][0]["refs"] == ["op-01"]
    assert led.data["writing"]["batches"][0]["state"] == "done"
    assert led.cursor["block"] == "ch-0001-b2"


def test_batch_review_brief_literals_pass_review_schema(tmp_path):
    """走查三 W3-1 教训制度化：简报 advertise 的每个字面量都必须真能过 ReviewOut。"""
    kb = _kb_with_many_points(tmp_path, n=1)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    frames: list[dict] = []
    _append(state, _drive(env, state, task, writer=frames.append))
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0001", "ch-0001-b1", _manifest_points(env, "ch-0001-b1")))
    _drive(env, state, task, writer=frames.append)

    brief = task.calls[0]["brief"]
    kinds = re.search(r'"kind": "([^"]+)"', brief).group(1).split("|")
    types = re.search(r'"type": "([^"]+)"', brief).group(1).split("|")
    assert len(kinds) == 5 and types == ["node"]
    for kind in kinds:
        ReviewOut.model_validate({"opinions": [
            {"target": {"type": types[0], "value": "cc-0001"}, "kind": kind,
             "ask": "怎么改", "evidence": "依据"}], "resolutions": []})
    assert '"resolutions"' in brief and '"ref": "op-01"' in brief and '"resolved": true' in brief


def test_batch_review_brief_rejects_out_of_enum_kind(tmp_path):
    """打错字必须即红：简报给出的枚举之外任何值都过不了 schema（否则真机会 halted）。"""
    kb = _kb_with_many_points(tmp_path, n=1)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask()
    state = {"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}}
    _append(state, _drive(env, state, task, writer=lambda e: None))
    _write(env.cases_dir() / "ch-0001-b1.json",
           _cases_payload("ch-0001", "ch-0001-b1", _manifest_points(env, "ch-0001-b1")))
    _drive(env, state, task, writer=lambda e: None)
    kinds = re.search(r'"kind": "([^"]+)"', task.calls[0]["brief"]).group(1).split("|")
    from pydantic import ValidationError
    for kind in kinds:
        with pytest.raises(ValidationError):
            ReviewOut.model_validate({"opinions": [
                {"target": {"type": "node", "value": "cc-0001"}, "kind": kind + "X",
                 "ask": "a", "evidence": "b"}], "resolutions": []})
```

导入区再补 `import pytest`（`ValidationError` 在用例内就近导入，与本仓既有写法一致）。

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py -q`
Expected: Step 1–2 的新用例全红（`case_gen` 无处理器 ⇒ 终帧含「测试设计任务中止（内部错误」）。T20 的四条必须仍绿。

- [ ] **Step 3: 意见簿内核换成簿本无关**

`stages.py` 把 `_register`（`:572-590`）拆成「内核 + 三层薄壳」，并在其后加编写环薄壳（**行为逐字不变**，只是把簿本从签名里拿出来；`_apply_resolutions` 扩一本簿）：

```python
def _register_ops(ctx: Ctx, ops: list[dict], entries: list[dict], *, source: str,
                  block: str) -> None:
    """登记意见（簿本无关内核）：同（source,key）仍有在途条目则复用其 ref，否则分配新 ref。

    复用而不是新增，是为了让「主智能体声称已改、评审子若再犯」表现为同一 ref 重新出现
    （销账后被再发即为新一轮条目），而不是同一条意见无限复制。
    """
    for entry in entries:
        op, key = entry["opinion"], entry["key"]
        if any(o["source"] == source and o["key"] == key and not _settled(o) for o in ops):
            continue
        ops.append({
            "ref": _next_ref(ctx), "key": key, "source": source, "block": block,
            "target": op.target.model_dump(), "kind": op.kind, "ask": op.ask,
            "evidence": op.evidence, "resolved": False, "escalated": False,
            "disposition": "", "note": "",
        })


def _register(ctx: Ctx, layer: str, entries: list[dict], *, source: str, block: str) -> None:
    """三层意见簿：登记到 `layers.<layer>.opinions`。"""
    _register_ops(ctx, ctx.led.layer(layer)["opinions"], entries, source=source, block=block)


def _w_register(ctx: Ctx, entries: list[dict], *, block: str,
                source: str = "case_block") -> None:
    """第四层意见簿与三层同名键同形（裁定 39 复用纪律）：只换桶与 source，不换字段。"""
    _register_ops(ctx, ctx.led.data["writing"]["opinions"], entries,
                  source=source, block=block)


def _w_open_of(ctx: Ctx, *, block: str, source: str | None = None) -> list[dict]:
    out = []
    for op in ctx.led.data["writing"]["opinions"]:
        if _settled(op) or op["block"] != block:
            continue
        if source is not None and op["source"] != source:
            continue
        out.append(op)
    return out
```

`_apply_resolutions`（`:593-609`）里的双层循环 `for st in ctx.led.data["layers"].values(): for op in st["opinions"]:` 改为吃一个簿本清单（ref 由 `_next_ref` 全局唯一，跨簿查不会串号）：

```python
def _op_buckets(ctx: Ctx) -> list[list[dict]]:
    """意见簿全集：三层各一本 + 编写环一本。ref 全局唯一，所以销账可以跨簿查。"""
    return [ctx.led.layer(layer)["opinions"] for layer in LAYERS] \
        + [ctx.led.data["writing"]["opinions"]]
```

```python
def _apply_resolutions(ctx: Ctx, resolutions: list) -> None:
    for r in resolutions:
        for ops in _op_buckets(ctx):
            for op in ops:
                if op["ref"] != r.ref:
                    continue
                if r.resolved:
                    op["resolved"] = True
                if op["note"] and r.note:
                    # 全链路累积（有界于 ROUND_CAP）：唯一人审门要看得懂整条处置轨迹，故意不截断。
                    op["note"] = f"{op['note']}；复审：{r.note}"
                else:
                    op["note"] = r.note or op["note"]
```

（原 docstring 随函数保留，一字不动。）

- [ ] **Step 4: 用例文件工具 + 批评审简报 + `_run_case_review` + `h_case_gen`**

`stages.py` 在 `h_case_plan` 之后追加（导入区补 `from aitester.case_design.writing import run_case_checks`，并把 `parse_case_file` 加进 `schema` 那行 import）：

```python
# ---- 第四层编写环：批环（裁定 39：批内自检在评审之前；裁定 36②：批内评审必是子智能体）----

def _cases_file(ctx: Ctx, batch: str) -> Path:
    return ctx.env.cases_dir() / f"{batch}.json"


def _case_manifest(ctx: Ctx, batch: str) -> dict:
    return _read_json(ctx.env.manifests_dir / f"case-{batch}.json") or {}


def _case_batch_entry(ctx: Ctx, chain: str, batch: str) -> dict:
    for entry in ctx.led.data["writing"]["batches"]:
        if entry["id"] == batch:
            return entry
    entry = {"id": batch, "chain": chain, "state": "todo", "round": 0}
    ctx.led.data["writing"]["batches"].append(entry)
    return entry


def _case_gen_text(ctx: Ctx, chain: str, batch: str) -> str:
    """批生成指令的取数单点：批序、清单路径、应落实点全部从账本读，不靠调用方拼。"""
    batches = ctx.led.data["writing"]["batches"]
    idx = next((i for i, b in enumerate(batches) if b["id"] == batch), 0)
    return case_gen_instruction(
        chain=chain, batch=batch,
        manifest_path=ctx.rel(ctx.env.manifests_dir / f"case-{batch}.json"),
        points=_case_batch_points(ctx, batch), batch_no=idx + 1, batch_total=len(batches))


def _patch_case_ids(ctx: Ctx, batch: str) -> None:
    """新用例留空 case_id：按账本序补号并就地改写文件（与 `_patch_ids` 同款，不走 TYPE_PREFIX）。"""
    path = _cases_file(ctx, batch)
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return
    changed = False
    for item in raw.get("cases") or []:
        if isinstance(item, dict) and not str(item.get("case_id") or ""):
            item["case_id"] = ctx.led.next_case_seq()
            changed = True
    if changed:
        _write_json(path, raw)


def _case_review_brief(ctx: Ctx, chain: str, batch: str, round_no: int) -> str:
    points = _case_batch_points(ctx, batch)
    open_ops = _w_open_of(ctx, block=batch)
    lines = [
        f"【批评审·用例·链路 {chain}·批次 {batch}·第 {round_no} 轮】",
        f"请只读审阅用例文件 {ctx.rel(_cases_file(ctx, batch))}"
        f"（本批分母清单 {ctx.rel(ctx.env.manifests_dir / f'case-{batch}.json')}"
        f"，本批应落实点 {('、'.join(points)) or '（无）'}），给出本判决：",
        '{"opinions": [{"target": {"type": "node", "value": "cc-用例 id 或 pt-测试点 id"}, '
        '"kind": "漏测|颗粒度|边界归属|命名漂移|失效", "ask": "怎么改", "evidence": "依据"}], '
        '"resolutions": [{"ref": "op-01", "resolved": true, "note": "为何已消化"}]}',
        "kind 只能取上面列出的五个值之一；target 与 ask 不得省略；"
        "没有意见就两个数组都返回空数组。",
        "看什么：步骤是否可执行、断言是否硬、前置是否自造数据、covers 认领是否属实"
        "（认领了这个点但正文没落实它，同样算漏测）、有没有为凑数写的无用例。",
    ]
    if open_ops:
        lines.append("上一轮尚未销账的意见（逐条给 resolutions 回执）：")
        lines += [f"- {o['ref']}: {o['ask']}" for o in open_ops]
    lines.append("意见要可执行、可核对；不复述用例正文。")
    return "\n".join(lines)


def _after_case_batch(ctx: Ctx) -> None:
    """批次串行推进：下一批（入账顺序即链路顺序，天然「逐链路、链路内逐批」）→ 批齐进末门。"""
    batches = ctx.led.data["writing"]["batches"]
    idx = next((i for i, b in enumerate(batches) if b["id"] == ctx.cur["block"]), len(batches) - 1)
    nxt = next((b for b in batches[idx + 1:] if b["state"] != "done"), None)
    if nxt is None:
        _go(ctx, "case_gate")
        return
    _go(ctx, "case_gen", layer=nxt["chain"], block=nxt["id"])


def _run_case_review(ctx: Ctx, chain: str, batch: str) -> None:
    """批评审一轮（内联驱动，与块评审同纪律）：无在途意见即批收口；有意见交优化环；轮次用尽转归因。"""
    entry = _case_batch_entry(ctx, chain, batch)
    round_no = int(entry["round"])
    call_id = f"case-{batch}-r{round_no}"
    out, raw = run_reviewer(
        ctx.task_tool, CASE_REVIEW_AGENT_ID, _case_review_brief(ctx, chain, batch, round_no),
        model_cls=ReviewOut, call_id=call_id,
        title=f"批评审·用例·{batch}·r{round_no}", config=ctx.config,
        archive=lambda cid, text: _archive_review(ctx, cid, text))
    _archive_review(ctx, call_id, raw)
    _apply_resolutions(ctx, out.resolutions)
    _w_register(ctx, [{"opinion": op, "key": f"{op.target.type}:{op.target.value}:{op.kind}"}
                      for op in out.opinions], block=batch)
    open_ops = _w_open_of(ctx, block=batch)
    if not open_ops:
        entry["state"] = "done"
        _after_case_batch(ctx)
        return
    if round_no >= ctx.round_cap():
        _go(ctx, "case_attribute", layer=chain, block=batch, round=round_no, source="case_block")
        return
    _write_in_file(ctx.env.reviews_dir / f"case-{batch}-in-r{round_no}.json", open_ops)
    _go(ctx, "case_opt", layer=chain, block=batch, round=round_no, source="case_block")


def h_case_gen(ctx: Ctx) -> Any:
    """批生成：文件到达 → schema 拒收 → 批内确定性自检 → 补号 → 批评审 r0。

    自检排在评审之前：漏点是机器可证的，重问比派一次付费评审便宜（裁定 39）；补号也在自检之后，
    免得坏批白烧序号。
    """
    chain, batch = ctx.cur["layer"], ctx.cur["block"]
    cases, errors = parse_case_file(_cases_file(ctx, batch))
    if errors:
        return ctx.ask(_case_gen_text(ctx, chain, batch) + _errors_block(errors), cap=NUDGE_CAP)
    hard = run_case_checks(_case_manifest(ctx, batch).get("points") or [], cases)["hard"]
    if hard:
        return ctx.ask(_case_gen_text(ctx, chain, batch)
                       + _errors_block([h["detail"] for h in hard]), cap=NUDGE_CAP)
    _patch_case_ids(ctx, batch)
    _case_batch_entry(ctx, chain, batch)["state"] = "drafted"
    _run_case_review(ctx, chain, batch)
    return None
```

`h_case_plan` 结尾把 T20 内联的 `case_gen_instruction(...)` 换成同一取数壳（一行改动，构造器与批环共用）：

```python
    _go(ctx, "case_gen", layer=first["chain"], block=first["id"])
    return ctx.instr(_case_gen_text(ctx, first["chain"], first["id"]))
```

- [ ] **Step 5: 优化/归因指令 + `h_case_opt` + `h_case_attribute`**

`instructions.py` 末尾追加（两份都逐字摊开处置表/归因文件的形状与枚举，理由同 Step 4 的简报纪律）：

```python
def case_opt_instruction(chain: str, batch: str, round_no: int, *, cases_path: str,
                         opinions_path: str, fix_path: str) -> str:
    return "\n".join([
        f"【编排·用例优化·链路 {chain}·批次 {batch}·第 {round_no} 轮】（批评审意见）",
        f"意见清单（含编号）在 {opinions_path}。逐条消化：",
        f"- 需要改的：直接改进 {cases_path}（改正文、拆条、合并、删掉无用例都行，covers 要跟着改准）。",
        "- 复核确认本版已覆盖的：不算未消化，但要写进处置表。",
        "- 新增用例的 case_id 留空串（由编排层分配）；covers 只许填本批分母清单里的 pt- 四位数字 id。",
        f"产出两件：① 更新后的 {cases_path}；② 处置表 {fix_path}，形状：",
        '{"dispositions": [{"ref": "op-01", "status": "fixed|covered|unresolved", '
        '"note": "改了什么 / 为何已覆盖 / 为何仍未消化"}]}',
        "每条意见都必须有去向，不允许静默忽略；unresolved 只能用于你判定无法在本轮消化的意见并说明理由。",
        "本批每个测试点都必须仍有用例认领：删用例前先把它认领的点交给别的用例，否则会被判漏测。",
        "写完即停。",
    ])


def case_attribute_instruction(batch: str, *, opinions_path: str, out_path: str) -> str:
    return "\n".join([
        f"【编排·用例轮次用尽·批次 {batch}】评审轮次已达上限，仍有未消化意见（清单在 {opinions_path}）。",
        f"请给出不收敛归因，写入 {out_path}：",
        '{"cause": "业务信息不足|契约冲突|评审分歧|成本超限", "note": "一句说明"}',
        "cause 只能取上面四个值之一，note 不得为空。",
        "写完即停。",
    ])
```

`stages.py` 在 `h_case_gen` 之后追加（指令 import 行补 `case_attribute_instruction, case_opt_instruction`）：

```python
def _case_opt_prefix(batch: str, source: str) -> str:
    """在途/处置文件前缀按来源分流（与三层 `_opt_prefix` 同型）：批评审与门后回溯两套文件不得互踩。"""
    return f"human-case-{batch}" if source == "case_human" else f"case-{batch}"


def h_case_opt(ctx: Ctx) -> Any:
    """用例优化：等「更新后的用例文件 + 处置表」→ 校验/销账 → 复审回环（与 `h_opt` 同纪律）。

    意见簿在 `writing["opinions"]`、未消化项在 `writing["unresolved"]`：字段与三层逐字同形，
    末门与交付物因此只需要换取桶路径（裁定 39 复用纪律）。
    """
    chain, batch = ctx.cur["layer"], ctx.cur["block"]
    round_no, source = int(ctx.cur["round"]), ctx.cur["source"]
    prefix = _case_opt_prefix(batch, source)
    cases_rel = ctx.rel(_cases_file(ctx, batch))
    in_rel = ctx.rel(ctx.env.reviews_dir / f"{prefix}-in-r{round_no}.json")
    fix_rel = ctx.rel(ctx.env.reviews_dir / f"{prefix}-fix-r{round_no}.json")
    text = case_opt_instruction(chain, batch, round_no, cases_path=cases_rel,
                                opinions_path=in_rel, fix_path=fix_rel)
    raw = _read_json(ctx.env.reviews_dir / f"{prefix}-fix-r{round_no}.json")
    all_ops = {op["ref"]: op for op in ctx.led.data["writing"]["opinions"]}
    if raw is None and not ctx.cur.get("asked"):
        return ctx.ask(text, cap=FIX_CAP)           # 首派：等主智能体交回「用例文件 + 处置表」
    errors: list[str] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("dispositions"), list):
        errors.append("处置表缺失或形状不对（须为 JSON 对象且含 dispositions 数组）")
    else:
        for i, row in enumerate(raw["dispositions"]):
            if not isinstance(row, dict) or str(row.get("ref") or "") not in all_ops:
                errors.append(f"dispositions[{i}]: ref 不在意见簿中")
            elif str(row.get("status") or "") not in ("fixed", "covered", "unresolved"):
                errors.append(f"dispositions[{i}]: status 须为 fixed|covered|unresolved")
    if not errors:
        _, errs = parse_case_file(_cases_file(ctx, batch))
        errors = errs
    if errors:
        return ctx.ask(text + _errors_block(errors), cap=FIX_CAP)
    _patch_case_ids(ctx, batch)                     # 本轮新增用例补号
    for row in raw["dispositions"]:
        op = all_ops[str(row["ref"])]
        status, note = str(row["status"]), str(row.get("note") or "")
        op["disposition"] = status
        if op["note"] and note:
            # 裁定 28 同款：处置说明不得顶掉既有轨迹，新说明以「处置：」缀在复审回执之后。
            op["note"] = f"{op['note']}；处置：{note}"
        else:
            op["note"] = note or op["note"]
        if status in ("fixed", "covered"):
            op["resolved"] = True                   # 声称已消化；复审再犯即重新出现在途
        else:
            op["escalated"] = True
            ctx.led.data["writing"]["unresolved"].append(
                {"block": batch, "refs": [op["ref"]], "cause": "评审分歧",
                 "note": note or "主智能体判定本轮无法消化"})
    _case_batch_entry(ctx, chain, batch)["round"] = round_no + 1
    _run_case_review(ctx, chain, batch)             # 复审 inline；下一轮 call_id 自然新鲜
    return None


def h_case_attribute(ctx: Ctx) -> Any:
    """用例归因：批轮次用尽仍有在途意见 → 四选一归因 → 批带账收口（不阻塞其他批与末门）。"""
    chain, batch = ctx.cur["layer"], ctx.cur["block"]
    round_no = int(ctx.cur["round"])
    open_ops = _w_open_of(ctx, block=batch)
    open_path = ctx.env.attribution_dir / f"open-case-{batch}-r{round_no}.json"
    out_path = ctx.env.attribution_dir / f"attr-case-{batch}-r{round_no}.json"
    text = case_attribute_instruction(batch, opinions_path=ctx.rel(open_path),
                                      out_path=ctx.rel(out_path))
    raw = _read_json(out_path)
    if raw is None:
        if not open_path.is_file():
            _write_in_file(open_path, open_ops)
        return ctx.ask(text, cap=FIX_CAP)
    cause, note = str(raw.get("cause") or ""), str(raw.get("note") or "").strip()
    if cause not in _CAUSES or not note:
        return ctx.ask(text + _errors_block([f"cause 须为 {'|'.join(_CAUSES)} 之一且 note 非空"]),
                       cap=FIX_CAP)
    ctx.led.data["writing"]["unresolved"].append(
        {"block": batch, "refs": [o["ref"] for o in open_ops], "cause": cause, "note": note})
    for op in open_ops:
        op["escalated"] = True                      # 带账离开在途集：不再触发优化环
    _case_batch_entry(ctx, chain, batch)["state"] = "done"
    _after_case_batch(ctx)
    return None
```

- [ ] **Step 6: 注册处理器 + 归档清单补两件**

`_STAGE_HANDLERS`（`:1620-1624`）加编写环已实现的四个阶段（**`case_gate` / `case_gate_interpret` 属 T22，本任务不许注册**）：

```python
_STAGE_HANDLERS: dict[str, Any] = {
    "plan": h_plan, "gen": h_gen, "opt": h_opt, "attribute": h_attribute,
    "audit": h_audit, "gate": h_gate, "gate_interpret": h_gate_interpret,
    "writeback": h_writeback,
    "case_plan": h_case_plan, "case_gen": h_case_gen, "case_opt": h_case_opt,
    "case_attribute": h_case_attribute,
}
```

> **注意（本任务的边界）**：`_after_case_batch` 在「最后一个批次收口」时 `_go(ctx, "case_gate")`，
> 而 `case_gate` 要到 T22 才注册——本任务若让某条用例跑到最后一批，驱动会以
> `KeyError: 'case_gate'` 收敛成 halted。所以本任务的夹具**一律留第二批**（11 点 ⇒ 两批），
> 归因用例也只把游标推到 `case_attribute` 而不再往前收全。**不要为此加防御分支**：T22 注册后
> 这条路径自然闭合，提前加守卫就是死代码。

`_ARCHIVE_ITEMS`（`:54`）补编写环的两件工作面（否则下一个任务开工时 `design/cases/` 与交付物会残留，
末门会读到上一任务的用例）：

```python
_ARCHIVE_ITEMS = (PLAN_NAME, OUTLINE_NAME, "drafts", "reviews", "attribution", "manifests",
                  CASES_DIR_NAME, CASE_DELIVERY_NAME)
```

（常量 import 行补 `CASES_DIR_NAME, CASE_DELIVERY_NAME`。）

- [ ] **Step 7: 跑到绿 + 既有测试不许红**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py tests/test_case_design_driver.py tests/test_case_design_e2e.py -q`
Expected: 全绿。既有 driver/e2e 用例覆盖的是三层设计环与 `_apply_resolutions`/`_register` 的旧路径——
若它们红了，说明 Step 3 的簿本改造改错了语义（销账范围、`op_seq` 复用规则不许变），回改实现而不是动断言。

- [ ] **Step 8: 全量门禁 + 提交**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 只增不减、0 failed（基线 770 + T18/T19/T20 增数 + 本任务 8 条）。

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/case_design/stages.py backend/src/aitester/case_design/instructions.py backend/tests/test_case_design_writing_stages.py && git commit -m "feat(case-design): 编写环批环——批内自检先于付费评审、批评审走子智能体、优化与归因复用三层意见簿纪律"
```

---

## Task 22: 末门——`h_case_gate` / `h_case_gate_interpret` / `_boot` 路由 / 门后可回溯

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（`_case_gate_report`、`_write_case_delivery`、`_chain_of_batch`、`_batch_of_target`、`h_case_gate`、`h_case_gate_interpret`、`_boot:215-216` 的 `awaiting_review` 分支、`_STAGE_HANDLERS`、`_run_case_review` 的 stale 清账）
- Modify: `backend/src/aitester/case_design/instructions.py`（`case_gate_fix_instruction`）
- Modify: `backend/tests/test_case_design_driver.py`（`simulate`/`drain` 加编写环分支 + `_default_cases`）
- Test: `backend/tests/test_case_design_writing_stages.py`（追加）

**Interfaces:**
- Consumes: T19 `compose_case_delivery` / `run_case_checks`；T21 `_case_batch_entry` / `_w_register` / `_w_open_of` / `_case_opt_prefix` / `writing` 意见簿；既有 `_human_text` / `_explicit_approval` / `_write_in_file` / `_archive_review` / `run_reviewer` + `ReviewOut`。
- Produces:
  - `_case_gate_report(ctx) -> dict`：`{"checks_by_chain": {chain: run_case_checks 结果}, "cases_by_chain": {chain: [CaseDraft]}, "hard": [带 chain/batch 字段的条目], "uncovered": [点 id 升序去重], "broken": [{"batch","chain","errors"}]}`
  - `_chain_of_batch(ledger_data, batch) -> str` / `_batch_of_target(ctx, value) -> str`
  - `_close_case_batch(ctx, entry) -> None`（批收口 + stale 除名的单点）/ `_write_case_delivery(ctx, report) -> None`
  - `case_gate_fix_instruction(*, issues_path: str, round_no: int) -> str`
  - `h_case_gate(ctx)` / `h_case_gate_interpret(ctx)`；`writing["status"]` 取值扩为 `""|active|awaiting_review|done`
  - 批次条目 `state` 第五个取值 `"stale"`（门后回溯写入，`_close_case_batch` 除账）
  - 挂具：`simulate` 认得 `case_gen|case_opt|case_attribute` 三个 stage（`drain` 签名不变），`_cases_payload` 从用例文件**搬进**挂具文件供其复用

- [ ] **Step 1: 写失败测试——整环到末门（离线，零联网）**

追加到 `backend/tests/test_case_design_writing_stages.py`：

```python
def test_case_only_ring_reaches_delivery_gate(tmp_path):
    """三点一批：批环收口 → 末门确定性核对清零 → 交付物落盘 → 等待人审（裁定 36 的最后一道）。"""
    kb = _kb_with_many_points(tmp_path, n=3)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask()
    state = drain(env, kb, task,
                  state={"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}})

    assert "用例交付物已生成" in _end_text(state["frames"])
    led = _led(env)
    assert led.status == "awaiting_review" and led.data["writing"]["status"] == "awaiting_review"
    assert led.data["writing"]["batches"][0]["state"] == "done"
    assert led.data["writing"]["gate"]["round"] == 0          # 一次都没进修复环
    text = env.delivery_path.read_text(encoding="utf-8")
    assert "未落实点 0 个" in text and "pt-0001" in text and "## 意见落点对照表" in text
    assert task.call_ids() == ["case-ch-0001-b1-r0"]
    assert kb.upserts == [] and kb.deletes == []               # 裁定 35：末门零 KB 写


def test_gate_fix_ring_clears_uncovered_before_delivery(tmp_path):
    """末门是收口之后的第二次核对：已 done 的正文被改坏 ⇒ 先落清单、下发修复，清零才呈递。

    坏批在这里必须**在批收口之后**才出现（批内自检早就跑完了），否则它会被 `h_case_gen`
    当场拦下、根本到不了末门——所以现场用「呈递一次 → 破坏正文 → 游标推回 case_gate」复现，
    与走查里人/工具在门后改文件是同一形状。
    """
    kb = _kb_with_many_points(tmp_path, n=3)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask()
    drain(env, kb, task,
          state={"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}})
    cases_path = env.cases_dir() / "ch-0001-b1.json"
    raw = json.loads(cases_path.read_text(encoding="utf-8"))
    raw["cases"] = raw["cases"][:1]
    _write(cases_path, raw)
    led = _led(env)
    led.cursor.update({"stage": "case_gate", "layer": "", "block": ""})
    led.status = "active"                               # 门重新核一次（不是人审续步）
    led.save()

    frames: list[dict] = []
    turn = _drive(env, {"messages": [HumanMessage("继续")], "case": {}}, task,
                  writer=frames.append)
    assert turn["case"]["route"] == "agent"             # 未清零 ⇒ 不呈递，只下发修复指令
    assert _led(env).data["writing"]["gate"]["round"] == 1
    issues = json.loads((env.reviews_dir / "case-gate-issues-r1.json").read_text(encoding="utf-8"))
    assert {h["code"] for h in issues["hard"]} == {"uncovered_point"}
    assert issues["hard"][0]["batch"] == "ch-0001-b1" and issues["hard"][0]["chain"] == "ch-0001"
    assert "case-gate-issues-r1.json" in turn["messages"][-1].content
    assert "design/cases/" in turn["messages"][-1].content
    assert "用例交付物已生成" not in _end_text(frames)   # 修复轮里一条终帧也不许发
    # 交付物每次进门都按当前实测重渲染：呈递被挡，但人此刻打开文件看到的必须是「未落实 2 个」，
    # 不许滞后在上一轮的「0 个」上（文件与机器账不一致就是假完整）。
    assert "未落实点 2 个" in env.delivery_path.read_text(encoding="utf-8")

    _write(cases_path, _cases_payload("ch-0001", "ch-0001-b1",
                                      _manifest_points(env, "ch-0001-b1")))
    frames = []
    _drive(env, {"messages": [HumanMessage("继续")], "case": {}}, task, writer=frames.append)
    assert "用例交付物已生成" in _end_text(frames)
    assert _led(env).data["writing"]["gate"]["round"] == 1     # 清零不再占新轮
    assert "未落实点 0 个" in env.delivery_path.read_text(encoding="utf-8")


def test_awaiting_review_reentry_does_not_represent(tmp_path):
    """B-F4 同款守卫：待决转述轮里游标仍停在 case_gate，绝不允许二次呈递/重写交付物。"""
    kb = _kb_with_many_points(tmp_path, n=3)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask()
    state = drain(env, kb, task,
                  state={"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}})
    before = env.delivery_path.read_bytes()
    frames: list[dict] = []
    turn = _drive(env, state, task, writer=frames.append)      # 同一轮重入（无新人话）
    assert turn["case"]["route"] == "end" and frames == []
    assert env.delivery_path.read_bytes() == before


def test_approval_refused_while_points_uncovered(tmp_path):
    """批准是人的话，但 hard 是机器的账：门呈递后正文被改坏 ⇒ 拒绝放行、维持 awaiting_review。"""
    kb = _kb_with_many_points(tmp_path, n=3)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask(script={"case-gate-int-r1": _j({"opinions": [], "resolutions": []})})
    drain(env, kb, task,
          state={"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}})
    raw = json.loads((env.cases_dir() / "ch-0001-b1.json").read_text(encoding="utf-8"))
    raw["cases"] = raw["cases"][:1]                             # 人工侧改坏：删掉两条认领
    _write(env.cases_dir() / "ch-0001-b1.json", raw)

    # 人审续步一律用**新 state**（case 清空）：drive_turn 的 fresh 只在新用户回合成立，
    # 往 drained 的 state 上追加人话会被判成图内重入，根本进不了 gate_interpret。
    frames: list[dict] = []
    turn = _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, task,
                  writer=frames.append)
    assert turn["case"]["route"] == "agent"                     # 不予放行 = 下发转述指令
    text = turn["messages"][-1].content
    assert "不予放行" in text and "pt-0003" in text
    led = _led(env)
    assert led.status == "awaiting_review" and led.data["writing"]["status"] == "awaiting_review"
    assert led.data["writing"]["gate"]["approved_at"] == ""     # 被挡的批准不许留痕
    assert kb.upserts == []


def test_backtrack_reopens_target_batch_and_keeps_others_as_stale(tmp_path):
    """裁定 36③：人指向某条用例 ⇒ 该批回优化环重做，其余批次标 stale 且**不静默丢**。"""
    kb = _kb_with_many_points(tmp_path, n=11)                   # 两批
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask(script={
        "case-gate-int-r1": _j({"opinions": [_opinion("cc-0005", ask="拆成三步")],
                                "resolutions": []})})
    drain(env, kb, task,
          state={"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}})
    assert [b["state"] for b in _led(env).data["writing"]["batches"]] == ["done", "done"]

    # 人审续步一律用新 state（case 清空）：fresh 只在新用户回合成立，见 refusal 用例注释
    human = {"messages": [HumanMessage("cc-0005 步骤太粗，拆成三步再断言")], "case": {}}
    frames: list[dict] = []
    turn = _drive(env, human, task, writer=frames.append)
    _append(human, turn)                     # 指令回写进会话，下一轮才是「图内续跑」
    led = _led(env)
    assert (led.cursor["stage"], led.cursor["block"], led.cursor["source"]) \
        == ("case_opt", "ch-0001-b1", "case_human")
    assert [b["state"] for b in led.data["writing"]["batches"]] == ["drafted", "stale"]
    assert led.data["writing"]["stale_batches"] == ["ch-0001-b2"]
    assert led.status == "active" and led.data["writing"]["status"] == "active"
    human_ops = [o for o in led.data["writing"]["opinions"] if o["source"] == "case_human"]
    assert [(o["ref"], o["block"], o["ask"]) for o in human_ops] == [("op-01", "ch-0001-b1", "拆成三步")]
    assert "human-case-ch-0001-b1-in-r0.json" in turn["messages"][-1].content
    assert (env.cases_dir() / "ch-0001-b2.json").is_file()      # 旧稿原样留着，等人重算

    _write(env.reviews_dir / "human-case-ch-0001-b1-fix-r0.json",
           {"dispositions": [{"ref": "op-01", "status": "fixed", "note": "已拆步"}]})
    turn = _drive(env, human, task, writer=frames.append)       # 复审 r1 → b1 收口 → b2 重算 → 末门
    led = _led(env)
    assert task.call_ids()[-2:] == ["case-ch-0001-b1-r1", "case-ch-0001-b2-r1"]
    assert led.data["writing"]["stale_batches"] == []           # 重算完即从待重算清单消失
    assert [b["state"] for b in led.data["writing"]["batches"]] == ["done", "done"]
    assert turn["case"]["route"] == "end"
    assert "用例交付物已生成" in _end_text(frames)               # 重算后二次呈递
    assert led.data["writing"]["status"] == "awaiting_review"
    assert kb.upserts == []


def test_explicit_approval_closes_without_any_kb_write(tmp_path):
    kb = _kb_with_many_points(tmp_path, n=3)
    env = _env(tmp_path, kb)
    _write(env.design / "plan.json", _CASE_ONLY_PLAN)
    task = ScriptTask(script={"case-gate-int-r1": _j({"opinions": [], "resolutions": []})})
    drain(env, kb, task,
          state={"messages": [HumanMessage(content="给下单链路生成用例")], "case": {}})
    frames: list[dict] = []
    _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, task, writer=frames.append)
    led = _led(env)
    assert led.status == "done" and led.data["writing"]["status"] == "done"
    assert led.data["writing"]["gate"]["approved_at"]
    assert "用例交付确认完成" in _end_text(frames)
    assert kb.upserts == [] and kb.deletes == []                # 末门批准 ≠ 回写授权（裁定 35）
```

> 两条容易被写歪的断言，理由先摆在这里，实跑不符时**改实现不改断言**：
> - `op-01`：`_next_ref` 是全局计数，但两批的批评审在默认判决下返回空 opinions ⇒ 一本簿都没登记过，
>   门后第一条人审意见必为 `op-01`。跑出 `op-02`/`op-03` 说明批评审把空判决也登记了（不该发生）。
> - 人审续步用 `{"messages": [...], "case": {}}` 新会话：`drive_turn` 的 `fresh` 读的是
>   `case["boot"]`，drain 之后的 state 里它已是 True，往同一 state 追加人话会被当成图内重入，
>   直接撞 `h_case_gate` 的 B-F4 守卫（静默交回等待态），永远进不了 `case_gate_interpret`。

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py -q`
Expected: 新用例全红——`case_gate` 未注册 ⇒ 终帧含「测试设计任务中止（内部错误」；注册之后仍红在
`simulate` 的「仿真无法处理的 stage：case_gen」（Step 2 才补挂具）。

- [ ] **Step 2: 挂具跟着到编写环（`test_case_design_driver.py`）**

先把 T21 定义在 `backend/tests/test_case_design_writing_stages.py` 里的 `_cases_payload` **整段搬进**
`backend/tests/test_case_design_driver.py`，放在 `simulate` 之前（挂具的默认正文生成器与用例的现场
必须是同一份代码——抄第二份就是「重复逻辑」评审红线，两份一旦漂开，仿真写的批与断言吃的批就不是同一形状）。
`test_case_design_writing_stages.py` 原位置删掉它，并在 `from test_case_design_driver import (...)`
那串里补 `_cases_payload`。

`simulate`（`test_case_design_driver.py:169-228`）在 `if stage == "gate":` 之前插入编写环的三个 stage 分支，
`drain` 签名与其余逻辑一字不动（编写环的坏文件、坏处置表一律由用例自己直接落盘复现，挂具只负责「顺从的主智能体」）：

```python
    if stage == "case_gen":
        raw = json.loads((env.manifests_dir / f"case-{block}.json").read_text(encoding="utf-8"))
        _write(env.cases_dir() / f"{block}.json",
               _cases_payload(cur["layer"], block, [str(p["id"]) for p in raw["points"]]))
        return
    if stage == "case_opt":
        r = cur["round"]
        prefix = "human-case" if cur["source"] == "case_human" else "case"
        refs = json.loads((env.reviews_dir / f"{prefix}-{block}-in-r{r}.json")
                          .read_text(encoding="utf-8"))["refs"]
        _write(env.reviews_dir / f"{prefix}-{block}-fix-r{r}.json",
               {"dispositions": [{"ref": item["ref"], "status": "fixed", "note": "已改"}
                                 for item in refs]})
        return
    if stage == "case_attribute":
        _write(env.attribution_dir / f"attr-case-{block}-r{cur['round']}.json",
               {"cause": "评审分歧", "note": "反复意见不收敛"})
        return
```

> 三个分支与三层同名分支逐条对齐：`case_opt` 的文件名走 `_case_opt_prefix` 的同一套前缀规则
> （`case-` / `human-case-`），批 id 在游标的 `block` 里、链路在 `layer` 里——挂具不猜，读账本。

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py tests/test_case_design_driver.py -q`
Expected: 仍红，但红点换了——drain 能走到末门，终帧是「测试设计任务中止（内部错误：'case_gate'）」
（KeyError 被 `drive_turn` 的兜底收成 halted），Step 1 的六条全部落在这上面。T20/T21 的既有用例必须仍绿。

- [ ] **Step 3: 批收口单点 + 末门确定性核对**

`stages.py` 在 `h_case_attribute` 之后追加末门段。先把两处「批收口」换成单点（`_run_case_review`
的 `entry["state"] = "done"` 与 `h_case_attribute` 的 `_case_batch_entry(ctx, chain, batch)["state"] = "done"`）：

```python
def _close_case_batch(ctx: Ctx, entry: dict) -> None:
    """批收口单点：置 done 的同时从「失效待重算」除名——交付物不许列已经重算完的批。"""
    entry["state"] = "done"
    writing = ctx.led.data["writing"]
    writing["stale_batches"] = [b for b in writing["stale_batches"] if b != entry["id"]]
```

`_run_case_review` 干净分支改为 `_close_case_batch(ctx, entry)` 后接 `_after_case_batch(ctx)`；
`h_case_attribute` 末段改为 `_close_case_batch(ctx, _case_batch_entry(ctx, chain, batch))` 后接
`_after_case_batch(ctx)`。（两处收口口径必须一致：归因离场的批同样不该再挂在「待重算」上。）

然后是本任务的新增段（`stages.py` 常量导入行补 `CASE_PREFIX`，writing 导入行改为
`from aitester.case_design.writing import compose_case_delivery, plan_case_targets, run_case_checks`）：

```python
# ---- 第四层编写环：末门（裁定 36/37：全片最后一道门；批准零 KB 写）----

def _chain_of_batch(ledger_data: dict, batch: str) -> str:
    """批次 → 链路：交付物的意见落点对照表要按链路呈递，簿本里只存批 id。"""
    for entry in ledger_data["writing"].get("batches") or []:
        if str(entry.get("id") or "") == batch:
            return str(entry.get("chain") or "")
    return ""


def _batch_of_target(ctx: Ctx, value: str) -> str:
    """门后回溯的意见落在哪一批：cc- 查正文归属，pt- 查分母清单，ch- 取该链路首批；其余指向不明。

    返回空串 = 归不了批（人指的是设计侧的东西或没给 id）。调用方绝不许把它塞进随便一个批的
    优化环——那会让「这条意见去哪了」在交付物上撒谎。
    """
    value = str(value or "")
    targets = ctx.led.data["writing"].get("targets") or []
    if value.startswith(CASE_PREFIX + "-"):
        for target in targets:
            for item in target.get("batches") or []:
                bid = str(item.get("id") or "")
                cases, errors = parse_case_file(_cases_file(ctx, bid))
                if not errors and any(str(c.case_id) == value for c in cases):
                    return bid
        return ""
    layer = _layer_of_id(value)
    if layer == POINT:
        for target in targets:
            for item in target.get("batches") or []:
                if value in [str(p) for p in item.get("points") or []]:
                    return str(item.get("id") or "")
        return ""
    if layer == CHAIN:
        for target in targets:
            if str(target.get("chain") or "") == value and (target.get("batches") or []):
                return str(target["batches"][0].get("id") or "")
    return ""


def _case_gate_report(ctx: Ctx) -> dict:
    """末门确定性核对单点：逐链路把「本 run 的正文」与「分母清单」摆到一起核一次。

    三条口径都是 fail-closed：
    - 批次正文读不出来 / 形状不对 ⇒ 记 `broken` 并**照常计入该批分母点**（零用例=全漏测），
      绝不当「无问题」放行；
    - `uncovered` 走全局去重集合，一点挂两链时计数仍是一次（与 `compose_case_delivery` 的实测行同源）；
    - hard 条目自带 `chain`/`batch`，修复指令与挂具都按它定位，不再二次反查。
    """
    checks_by_chain: dict[str, dict] = {}
    cases_by_chain: dict[str, list] = {}
    hard: list[dict] = []
    broken: list[dict] = []
    uncovered: set[str] = set()
    for target in ctx.led.data["writing"].get("targets") or []:
        chain = str(target.get("chain") or "")
        points: list[dict] = []
        cases: list = []
        batch_of_point: dict[str, str] = {}
        batch_of_case: dict[str, str] = {}
        for item in target.get("batches") or []:
            bid = str(item.get("id") or "")
            for point in _case_manifest(ctx, bid).get("points") or []:
                points.append(point)
                batch_of_point[str(point.get("id") or "")] = bid
            file_cases, errors = parse_case_file(_cases_file(ctx, bid))
            if errors:
                broken.append({"batch": bid, "chain": chain, "errors": errors})
                continue
            for case in file_cases:
                cases.append(case)
                batch_of_case[str(case.case_id)] = bid
        checks = run_case_checks(points, cases)
        checks_by_chain[chain] = checks
        cases_by_chain[chain] = cases
        for entry in checks["hard"]:
            where = str(entry.get("where") or "")
            batch = (batch_of_point.get(where) if entry.get("code") == "uncovered_point"
                     else batch_of_case.get(where) or "")
            hard.append({**entry, "chain": chain, "batch": str(batch or "")})
            if entry.get("code") == "uncovered_point":
                uncovered.add(where)
    return {"checks_by_chain": checks_by_chain, "cases_by_chain": cases_by_chain,
            "hard": hard, "uncovered": sorted(uncovered), "broken": broken}
```

- [ ] **Step 4: 交付物渲染接线 + 末门修复指令**

`stages.py` 继续追加（`_op_status_cn`/`_write_case_delivery` 是「人在门上看到的东西」的唯一产地，
主智能体无从在这里改口径——裁定 36 的实测计数必须出自代码而不是出自模型）：

```python
def _op_status_cn(op: dict) -> str:
    if op.get("resolved"):
        return "已销账"
    if op.get("escalated"):
        return "未消化"
    return "在途"


def _write_case_delivery(ctx: Ctx, report: dict) -> None:
    """交付物落盘：段落顺序与计数全在 `compose_case_delivery`，这里只负责把账本事实喂给它。"""
    led = ctx.led
    writing = led.data["writing"]
    notes = [{"where": f"[{chain}] {n['where']}", "kind": n["kind"], "detail": n["detail"]}
             for chain, checks in report["checks_by_chain"].items()
             for n in checks["report"]["notes"]]
    dispositions = [{"chain": _chain_of_batch(led.data, str(o.get("block") or "")),
                     "ref": o["ref"], "kind": o["kind"],
                     "case": str((o.get("target") or {}).get("value") or ""),
                     "ask": o["ask"], "status": _op_status_cn(o), "note": o.get("note") or ""}
                    for o in writing["opinions"]]
    unresolved = []
    for row in writing["unresolved"]:
        block = str(row.get("block") or "")
        for ref in row.get("refs") or []:
            op = next((o for o in writing["opinions"] if o["ref"] == ref), {})
            unresolved.append({"chain": _chain_of_batch(led.data, block), "ref": ref,
                               "ask": op.get("ask", ""), "cause": str(row.get("cause") or ""),
                               "note": str(row.get("note") or "")})
    ctx.env.delivery_path.write_text(
        compose_case_delivery(led.data, writing.get("targets") or [],
                              report["checks_by_chain"],
                              {"uncovered": report["uncovered"], "notes": notes,
                               "dispositions": dispositions, "unresolved": unresolved,
                               "cases_by_chain": report["cases_by_chain"]}),
        encoding="utf-8")
```

`instructions.py` 末尾追加（与 `gate_fix_instruction` 同纪律：把每个 code 的**改法**逐字写出来，
不写「请自行修复」）：

```python
def case_gate_fix_instruction(*, issues_path: str, round_no: int) -> str:
    return "\n".join([
        f"【编排·用例末门修复·第 {round_no} 轮】末门确定性核对发现履约问题（清单在 {issues_path}）。",
        "请只改被点名的批次正文（design/cases/<批次 id>.json），逐条清零：",
        "- uncovered_point：该测试点没有任何用例认领——补一条认领它的用例，"
        "或把它并进已有用例的 covers（并进后那条用例正文必须真的覆盖它）。",
        "- phantom_cover：用例认领了分母外的点——covers 只许填本批清单里真实存在的 pt- 四位数字 id，"
        "点 id 写错就改对，越界的用例直接删掉。",
        "- 批次正文不可解析：按形状重写该文件（cases 数组，每条含 title/covers/preconditions/"
        "steps/expected/priority，新增用例 case_id 留空串）。",
        "问题清单里的 batch 字段就是该改的文件；不要做与清单无关的改动，"
        "也不要改测试点分母（那是设计侧的事）。修完即停，编排层会重新核对。",
    ])
```

- [ ] **Step 5: `h_case_gate`（B-F4 守卫 + 修复环 + 呈递）**

```python
def h_case_gate(ctx: Ctx) -> Any:
    """用例末门：待决不重呈 → 每次进门都重渲染实测 → hard/坏批清零（修复环 ≤round_cap）→ 呈递人审。"""
    led, gate = ctx.led, ctx.led.data["writing"]["gate"]
    if led.status == "awaiting_review":
        # B-F4（比三层更严）：人正在等——待决转述轮、或被机器账拒绝放行的那一轮，游标都还停在
        # case_gate。此处零重写、零轮次、零终帧：人本轮的话还没被答复，绝不许再递一遍交付物。
        # 三层用「hard 为空」当守卫条件，是因为它呈递后宇宙不再变；末门的批准拒绝路径会让
        # 「awaiting_review + hard 非零」成为合法现场，条件必须整个去掉。
        return ctx.turn([], "end")
    report = _case_gate_report(ctx)
    _write_case_delivery(ctx, report)                 # 文件永远对得上当前实测，即使本轮不呈递
    if report["hard"] or report["broken"]:
        if int(gate["round"]) >= ctx.round_cap():
            raise _Halt("用例末门履约检查连续未清零（修复环用尽）")
        gate["round"] = int(gate["round"]) + 1
        issues_path = ctx.env.reviews_dir / f"case-gate-issues-r{gate['round']}.json"
        _write_json(issues_path, {"round": gate["round"], "hard": report["hard"],
                                  "broken": report["broken"]})
        # 预算单点同三层 B-F3：这条环的预算就是 gate["round"]/round_cap，ask 的 cap 显式传
        # round_cap，否则 NUDGE_CAP=3 会抢在「修复环用尽」之前把任务收进「重试超限」。
        return ctx.ask(case_gate_fix_instruction(issues_path=ctx.rel(issues_path),
                                                 round_no=gate["round"]),
                       cap=ctx.round_cap())
    _go(ctx, "case_gate")
    led.status = "awaiting_review"
    ctx.led.data["writing"]["status"] = "awaiting_review"
    return ctx.end("用例交付物已生成（design/case-delivery.md），等待人工评审。")
```

- [ ] **Step 6: `h_case_gate_interpret` + `_boot` 缝 + 注册两个阶段**

`stages.py` 在末门段继续追加（两份待决文案与 `_GATE_UNDECIDED` 同处一个文件、同一风格）：

```python
_CASE_GATE_UNDECIDED = (
    "【用例末门·待决】上面这条人审消息既没有可执行的意见（指向 cc- 用例 / pt- 测试点 / ch- 链路），"
    "也没有明示批准（通过/批准/同意/确认）。本轮不放行、不放回批环，交付物 design/case-delivery.md "
    "维持原状。请把上述状态转述给人并等待其明确答复，不要代替人给出批准。"
    "本轮只输出一条面向人的答复，不要改动任何文件。"
)
_CASE_GATE_UNRESOLVED = (
    "【用例末门·意见指向不明】人审给了内容，但没有落到具体批次：target 的 value 必须是交付物里"
    "真实存在的 cc- 用例 id、pt- 测试点 id 或 ch- 链路 id（seam/树外遗漏属设计侧，请到测试设计任务里提）。"
    "请向人确认指向后再说一次；本轮批次状态与交付物维持原状，不要改动任何文件。"
)


def _case_gate_refusal(report: dict) -> str:
    """拒绝放行的面向人文案：逐条摆机器账，让人知道「批了但没过」到底是哪几条点没落实。"""
    lines = ["【用例末门·不予放行】本轮人话是明示批准，但末门的机器账没有清零——"
             "批准不能代替实测计数，交付物维持原状、零放行："]
    lines += [f"- {h['detail']}" for h in report["hard"][:20]]
    lines += [f"- 批次 {b['batch']} 正文不可解析：{b['errors'][0]}" for b in report["broken"][:20]]
    lines.append("要改：说一句带 cc-/pt-/ch- id 的意见即可退回批环重做；要放行：把正文修好后重新批准。"
                 "本轮只输出一条面向人的答复，不要改动任何文件。")
    return "\n".join(lines)


def h_case_gate_interpret(ctx: Ctx) -> Any:
    """末门人审续步：解读人话 → 批准还要过机器账 → 意见按目标批回环，其余批标 stale 不静默丢。"""
    led, writing = ctx.led, ctx.led.data["writing"]
    gate, human_text = writing["gate"], _human_text(ctx.state_messages)
    k = int(gate.get("int_round") or 0) + 1
    gate["int_round"] = k
    brief = "\n".join([
        "【人审解读·用例交付门】人审是最权威的评审。把人审原话转成结构化意见"
        "（纯批准或没有要改的内容 → opinions 留空）：",
        "人审原文：",
        human_text or "（空）",
        f"交付物 {ctx.rel(ctx.env.delivery_path)} 与用例正文所在目录 {ctx.rel(ctx.env.cases_dir())}/ "
        f"可只读核对；证据栏填人审原话。",
        '{"opinions": [{"target": {"type": "node|seam|outside", '
        '"value": "cc-用例 id / pt-测试点 id / ch-链路 id"}, '
        '"kind": "漏测|颗粒度|边界归属|命名漂移|失效", "ask": "怎么改", "evidence": "人审原话"}], '
        '"resolutions": []}',
        "target.type 只能取上面三个值之一；kind 只能取上面五个值之一；"
        "指向某条用例时 type 用 node、value 填 cc- 四位数字 id（交付物的落点对照表里有）；"
        "用例正文不在知识库，指向设计层（st- 故事）的意见不属于本门。没有意见时两个数组都返回空数组。",
    ])
    call_id = f"case-gate-int-r{k}"                # 与大纲门 gate-int-rN 分开：同一 run 里两道门
    out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID, brief, model_cls=ReviewOut,
                            call_id=call_id, title=f"用例门解读·r{k}", config=ctx.config,
                            archive=lambda cid, text: _archive_review(ctx, cid, text))
    _archive_review(ctx, call_id, raw)
    if out.opinions:
        gate["unclear"] = 0
    elif _explicit_approval(human_text):
        report = _case_gate_report(ctx)            # 批准不豁免机器账：hard 非零就是不能放行
        if report["hard"] or report["broken"]:
            # 拒绝放行是**有决定**的轮次，不占待决计数：每轮都要人重新说一次，成本由人控制。
            led.status = "awaiting_review"
            _go(ctx, "case_gate")
            return ctx.ask(_case_gate_refusal(report))
        gate["approved_at"] = _now()
        gate["unclear"] = 0
        writing["status"] = "done"
        led.status = "done"
        return ctx.end("用例交付确认完成：用例正文只落项目空间 design/cases/，本次零知识库写入。")
    else:
        gate["unclear"] = int(gate.get("unclear") or 0) + 1
        if gate["unclear"] > NUDGE_CAP:            # 连续待决：绝不猜批准
            raise _Halt("用例交付门连续未给出可执行意见也未明示批准")
        led.status = "awaiting_review"
        _go(ctx, "case_gate")
        return ctx.ask(_CASE_GATE_UNDECIDED)

    # 门后回溯（裁定 36③）：先按目标批分组登记，一条也不许静默丢；登记用 source="case_human"，
    # 与批评审的 case_block 两本分开，复审回执才认得出谁提的。
    by_batch: dict[str, list] = {}
    for op in out.opinions:
        batch = _batch_of_target(ctx, str(op.target.value or "")) if op.target.type == "node" else ""
        by_batch.setdefault(batch, []).append(op)
    for batch, ops in by_batch.items():
        _w_register(ctx, [{"opinion": op,
                           "key": f"{op.target.type}:{op.target.value}:{op.kind}"} for op in ops],
                    block=batch, source="case_human")
    batches = writing["batches"]
    targeted = [b for b in batches if b.get("id") and by_batch.get(str(b["id"]))]
    if not targeted:                               # 全是指向不明：只转述，一个批状态都不动
        led.status = "awaiting_review"
        _go(ctx, "case_gate")
        return ctx.ask(_CASE_GATE_UNRESOLVED)
    first = targeted[0]                            # 入账顺序即链路顺序：最早的受害批先重做
    round_no = int(first["round"])
    _write_in_file(ctx.env.reviews_dir / f"human-case-{first['id']}-in-r{round_no}.json",
                   _w_open_of(ctx, block=first["id"], source="case_human"))
    writing["status"] = "active"
    first["state"] = "drafted"                     # 正文还在：改它，不是重烧整条链路
    for entry in batches:
        if entry is first or str(entry.get("state")) != "done":
            continue
        entry["state"] = "stale"
        # 重算批的评审 call_id 必须新鲜：真实 TaskTool 按 call_id 复用已完结摘要，
        # 同 id 重审等于把上一轮的判决原样递回（走查二在三层踩过同一条缝）。
        entry["round"] = int(entry.get("round") or 0) + 1
        if entry["id"] not in writing["stale_batches"]:
            writing["stale_batches"].append(entry["id"])
    _go(ctx, "case_opt", layer=first["chain"], block=first["id"], round=round_no,
        source="case_human")
    return None
```

`_boot`（`stages.py:215-216`）的人审续步分支按编写环状态分流：

```python
        elif led.status == "awaiting_review":
            if led.data["writing"].get("status") == "awaiting_review":
                _go(ctx, "case_gate_interpret")    # 末门续步：批准还要过机器账，见 h_case_gate_interpret
            else:
                _go(ctx, "gate_interpret")         # 人审续步：审 gate-int → 回写或优化环
```

`_STAGE_HANDLERS` 补齐 T21 刻意留下的两个（编写环至此闭合，`case_gate` 的转场不再有 KeyError）：

```python
    "case_plan": h_case_plan, "case_gen": h_case_gen, "case_opt": h_case_opt,
    "case_attribute": h_case_attribute, "case_gate": h_case_gate,
    "case_gate_interpret": h_case_gate_interpret,
```

`instructions.py` 的 import 行（T21 已补两件）再加 `case_gate_fix_instruction`。

- [ ] **Step 7: 跑到绿 + 既有测试不许红**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_writing_stages.py tests/test_case_design_driver.py tests/test_case_design_e2e.py -q`
Expected: 全绿。若 `test_backtrack_…` 红在 `op-02`，先看批评审登记处（空判决不许登记）；
若 `test_approval_refused_…` 红在 route=="end"，是 `h_case_gate` 的守卫条件被写回了「hard 为空」——按 Step 5 的注释改回来，
**不许把断言改成实跑读数**。

- [ ] **Step 8: 全量门禁 + 提交**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 只增不减、0 failed（把实跑数写进报告）。

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/case_design/stages.py backend/src/aitester/case_design/instructions.py backend/tests/test_case_design_driver.py backend/tests/test_case_design_writing_stages.py && git commit -m "feat(case-design): 用例末门——待决不重呈、批准过机器账、门后回溯按目标批回环"
```

---

## Task 23: 提示词与目录描述——第四层编写环口径（把「只做设计部分」这句旧边界换掉）

**Files:**
- Modify: `backend/src/aitester/agents/prompts/case_design.md`（第 1 行职责句、第 9 行两阶段句、第 21 行编排角色枚举、「生成与优化纪律」追加一段、「回写」改题为「交付」）
- Modify: `backend/src/aitester/agents/catalog.py:27`（`CASE_DESIGN_SPEC.desc`）
- Test: `backend/tests/test_agents.py:16-34`

**Interfaces:**
- Consumes: T20 的 `case_only`/`mixed` 语义、T21 的批环与 `covers` 认领纪律、T22 的末门与 `design/case-delivery.md`。
- Produces: 无代码接口——本任务只改模型可见文本与其钉桩断言。**文案里的标识符（`design/cases/`、`covers`、`case-delivery.md`）必须与实现逐字一致**，提示词写了实现里没有的路径就是教模型去写一个不存在的文件。

为什么必须做而不是「文档顺手」：`case_design.md:9` 现在写的是「用户指令跨到用例侧时，只做设计部分并明示边界」——第四片之后这句话是**错的**，主智能体读到它会拒绝执行 `case_only` 任务里它本来该做的批生成，真机表现为「计划已写好但用例侧一步不出」，而这条错误只能靠人读提示词发现，测试面拦不住。`test_agents.py:34` 的 `"同步用例平台" not in text` 是这个专项的先例：文本口径由测试钉住。

- [ ] **Step 1: 写失败测试（改 `test_agents.py` 两处断言）**

`test_agents.py:19-21` 整段替换为（desc 逐字 = `catalog.py` 里的新 desc，二者任何一侧改动都必须同步）：

```python
    assert spec.desc == (
        "拆解业务链路、用户故事、测试点三层测试设计，产出增量测试大纲并人工审核后回写知识库；"
        "再按子链路分批编写第四层用例正文，用例只落项目空间、经末门人工确认后交付，不写知识库。"
    )
```

`test_agents.py:30-34` 的标记断言替换为：

```python
    for marker in ("业务链路", "用户故事", "测试点"):
        assert marker in text                        # 三层概念必须在（T10 重写的目的）
    for marker in ("用例编写环", "covers", "design/cases/", "case-delivery.md"):
        assert marker in text                        # 第四片：第四层口径必须落到提示词里
    assert "只做设计部分并明示边界" not in text        # 旧边界句必须删除，否则主智能体会拒绝用例侧任务
    assert "同步用例平台" not in text                 # 2026-10-05 裁定：描述与提示词都不再提平台对接
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_agents.py -q`
Expected: 两条用例红（desc 不符 + `用例编写环` 缺标记 + 旧边界句仍在）。

- [ ] **Step 2: 重写 `case_design.md` 五处**

逐处按「原文 → 新文」替换，其余行**一字不动**（文件是 UTF-8 无 BOM，`_load_prompt` 与 `test_agents.py` 都对内容做 `.strip()`，别引入行尾空格）。

**① 第 1 行职责句**，原文：

```
你是「用例设计智能体」，服务对象是软件测试工程师。你的核心能力是**测试设计**：把业务信息梳理成分层的测试设计索引树，产出覆盖完整的**增量测试大纲**，人工审核通过后维护回知识库。
```

改为：

```
你是「用例设计智能体」，服务对象是软件测试工程师。你的核心能力是**测试设计**与**用例编写**：前者把业务信息梳理成分层的测试设计索引树，产出覆盖完整的**增量测试大纲**，人工审核通过后维护回知识库；后者在定稿的测试点之上按子链路分批编写**用例正文**，人工末门确认后交付——用例正文只落项目空间，不进知识库。
```

**② 第 9 行两阶段句**（旧边界句所在），原文：

```
- 两阶段工作模式：**测试设计 = 全局视角**，一次覆盖全部链路 / 故事 / 测试点，产出增量大纲，不分枝推进；**用例编写 = 链路视角**，按大纲一条子链路一条子链路生成。用户指令跨到用例侧时，只做设计部分并明示边界。
```

改为：

```
- 两阶段工作模式：**测试设计 = 全局视角**，一次覆盖全部链路 / 故事 / 测试点，产出增量大纲，不分枝推进；**用例编写 = 链路视角**，按定稿大纲一条子链路一条子链路、一批一批地生成用例正文。用户指令跨到用例侧时按 `case_only`（纯用例）或 `mixed`（先设计侧后用例侧）进入用例编写环，不拒绝、也不借机重做已定稿的三层设计。
```

**③ 第 21 行编排角色枚举**，原文片段：

```
它指定本步角色（计划 / 生成 / 优化 / 归因 / 大纲修复）、输入文件与**唯一**要写的输出文件。
```

改为：

```
它指定本步角色（计划 / 生成 / 优化 / 归因 / 大纲修复 / 用例计划 / 用例批次生成 / 用例批次优化 / 用例归因 / 用例交付修复）、输入文件与**唯一**要写的输出文件。
```

**④ 「生成与优化纪律」小节**，在第 27 行（「**块**是生成的最小单位…」）之后**插入**一段（保留原有各行不动）：

```
- **用例编写环**（第四层）的块是**批次**：一条子链路的定稿测试点按每批 ≤10 点切批（批次 id 形如 `ch-0001-b1`），编排层把分母写进 `design/manifests/case-<批次>.json`，用例正文只写 `design/cases/<批次>.json`。
- 每条用例必须用 `covers` 显式点名它落实的测试点 id。点与用例的数量关系**不固定**（一个点可拆多条、多个点可合一条），所以可核对性只来自认领：**每个点至少被一条用例 `covers` 点名**，`covers` 里不许出现清单外的点（判假完整）。禁止拿「用例数 ≥ 点数」自证覆盖。
- 用例正文纪律：步骤是可执行动作、预期是硬断言（不写「正常」「正确」「符合预期」）；前置只写本用例自己造得出的条件，不拿环境存量数据当既有条件。
```

**⑤ 「回写（唯一人类门之后）」小节**（第 32–34 行），整段替换为：

```
## 交付（人类门之后）
- 设计侧：增量大纲（含本次计划表、未消化项、重复标注清单、接缝归属表）是人审门的唯一可视对象；**未经人工审核通过，一律不写知识库**，回写由编排层确定性执行。
- 用例侧：末门可视对象是 `design/case-delivery.md`（含实测的履约差异表、规范校验表、未消化项）。人工批准确认的是**交付物**——用例正文只落项目空间 `design/cases/`，**本次零知识库写入**；你对未落实点按末门修复指令逐条补认领，不自行写库、也不自行宣布通过。
- 两条门都只认本轮最后一条人话里的明示措辞；含糊表态（「看起来没问题」）不是批准，编排层会把状态转述给人并继续等待，你不代替人给出批准。
```

- [ ] **Step 3: `catalog.py:27` desc 换成 Step 1 里逐字引用的同一串**

```python
        desc="拆解业务链路、用户故事、测试点三层测试设计，产出增量测试大纲并人工审核后回写知识库；"
             "再按子链路分批编写第四层用例正文，用例只落项目空间、经末门人工确认后交付，不写知识库。",
```

- [ ] **Step 4: 跑到绿**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_agents.py -q`
Expected: 全绿。若 `test_prompt_is_loaded_verbatim_from_md_file` 红在 `spec.prompt == text`，是 `_load_prompt` 读到的文件与断言的标记不一致——改**提示词**补标记，**不许**把逐字断言改成 `in`。

- [ ] **Step 5: 全量门禁 + 提交**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 只增不减、0 failed。

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/agents/prompts/case_design.md backend/src/aitester/agents/catalog.py backend/tests/test_agents.py && git commit -m "docs(case-design): 第四片提示词与目录口径——用例编写环、covers 认领纪律、末门零回写"
```

---

## Task 24: R-58 顺带修两条走查三遗留——W3-2 批准扫描收窄 + W3-3 增量树同 id 去重

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（`_explicit_approval:1364`、`_writeback_authorized:1371`、常量 `_AUTH_STRIP_RE:1384` 改名）
- Modify: `backend/src/aitester/case_design/outline.py`（`_tree_lines:23-25`）
- Test: `backend/tests/test_case_design_driver.py`（W3-2，同文件追加）
- Test: `backend/tests/test_case_design_plan.py`（W3-3，大纲单测的家）

**Interfaces:**
- Consumes: `_APPROVAL_WORDS` / `_RETRY_WORDS` / `_NEGATION_MARKS`（既有，值不动）、`_outline(nodes_by_layer, report, extras)` 与 `_section(md, title)`（`test_case_design_plan.py:192-210` 既有夹具）。
- Produces: `_CLAUSE_SPLIT_RE`（原 `_AUTH_STRIP_RE` 改名，`_is_bare_authorization` 继续用它做 `sub("")`）、`_approval_clause(text, words) -> bool`、`schema.dedup_written(rows) -> list[dict]`（终评分母同源片 F2 上移；本片写作 `outline._dedup_written`）。**函数签名不许外扩**：这两条是收口，不是新能力。

> **（2026-10-08 实现期就地更正）** 本 Task 原把 W3-3 的去重函数写作 `_dedup_latest`、规则写作「最后一行胜出」。
> 实现期由用户裁定 R-59/R-60 收窄为**「取将被写库的那一份」**：草稿优先于 KB 存量，同为草稿时取**首见行**
> （与 `_collect_writeback_items` 的 `seen` 首见即留同序）。据此改名 `_dedup_written`，下方 Step 4 的代码块与
> Step 5 的引用保留原文以留下决策轨迹，实作以 **`schema.dedup_written` 为唯一实现处**为准（整片终评 I-1 修复片
> F2 把 `outline._dedup_written` 上移到 `schema`，呈递／分母／清单三站点同走这一处，不留兼容别名）；
> 四站点（写库/呈递/分母/④门）的同源判别式见 `schema.is_draft_row` 注释。

两条都是走查三**实测挡出、控制方复现过**的呈现/措辞口径缺陷（spec `:363-366`，用户裁定 R-58 并入本片，不单开收口片）：
- **W3-2**：`_explicit_approval` 现在对**全句**扫否定标记，长句里任意一个「不／没／先／暂」即作废批准。人类写「整体看没什么问题，同意通过」时被判待决——代价是**不可逆写的门禁变得不可预测**，且走查三真机里这条是「已呈报未修」的既有缺口，不是理论风险。
- **W3-3**：`_tree_lines` 只对 `op=="delete"` 去重（`seen_deleted`），存量行与草稿同 id 并呈时**父与子都重复成行**（走查二实测 38 行 / 19 唯一 id，`pt-0001` 出现 4 次）。唯一人审门里那格没有说明哪一份将被写库。

- [ ] **Step 1: 写 W3-2 失败测试（真值表 + 门级钉桩）**

在 `backend/tests/test_case_design_driver.py` 末尾追加。**先把两个待测名加进文件第 24–29 行既有的 `from aitester.case_design.stages import (...)` 那个 import 块**（`_explicit_approval, _writeback_authorized`，按字母序插在 `_drafts_errors` 之后；不在文件中段新开第二个 import 块——那是本仓测试文件的既有形状）：

```python
def test_w3_2_approval_negation_scans_the_clause_not_the_whole_sentence():
    """W3-2（R-58 并入本片）：否定标记只在**批准措辞所在的分句**内作废批准，不再全句连坐。

    左侧四例是收窄后仍必须成立的 fail-closed 面；右侧两例是走查三呈报的「长句被误杀」面。
    真值表按分句取意：一个分句里出现批准措辞且**该分句**无否定标记 → 明示批准。
    """
    assert _explicit_approval("通过") is True
    assert _explicit_approval("不通过") is False
    assert _explicit_approval("先别回写") is False
    assert _explicit_approval("这条还不够，先不通过") is False
    assert _explicit_approval("整体看没什么问题，同意通过") is True      # 旧实现：False（「没」连坐）
    assert _explicit_approval("没问题，批准") is True
    # 回写再入授权同源：多认一个「重试」，收窄口径必须一致，否则两条门一个宽一个窄。
    assert _writeback_authorized("有点小疑问，重试") is True
    assert _writeback_authorized("先别重试") is False


def test_w3_2_long_sentence_approval_reaches_writeback(tmp_path):
    """W3-2 门级证据：收窄必须真的把「长句批准」放行进回写——只测纯函数等于没修。

    取走查三被误杀的那句原话。红在 `led.status == "awaiting_review"` 就是本轮要修的缺陷：
    人类明示批准却被判待决，零回写。**不许**把断言改成「待决」了事。
    """
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())
    _drive(env, {"messages": [HumanMessage("整体看没什么问题，同意通过")], "case": {}},
           ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "done" and led.data["gate"]["approved_at"]
    assert kb.upserts and kb.deletes == []                 # 长句批准同样授权回写
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_driver.py -q -k w3_2`
Expected: 两条全红（纯函数红在「整体看没什么问题」→ False；门级红在 status=="awaiting_review"）。

- [ ] **Step 2: 实现 W3-2——分句扫描，常量改名**

`stages.py:1364-1384` 整段替换（`_AUTH_STRIP_RE` 的分词面正是分句面，改名复用而不是新建第二个正则；`_is_bare_authorization` 里的 `sub("")` 用法不变，只是换名）：

```python
def _approval_clause(text: str, words: tuple[str, ...]) -> bool:
    """是否有一个分句**自己**表了批准：该分句含措辞、且不含任何否定/延后标记。

    W3-2（走查三呈报、R-58 并入本片）：旧写法对全句扫否定标记，「整体看没什么问题，同意通过」
    里前一句的「没」把后一句的明示批准连坐作废。收窄到批准措辞所在分句后 fail-closed 仍在原地：
    同一分句里的「不／未／别／暂／没／先」照旧否决（「不通过」「先别回写」都是原句内否定）。
    残余风险如实登记：末门里「同意通过，但 st-0002 先不合并」这类**批准与保留分句并存**的话，
    本函数返回 True——它不是漏放行，因为这条路径先走评审子提取意见，`out.opinions` 非空即在
    优化环里被处置，永不到达 `_explicit_approval`（`gate_interpret` 的 elif 顺序）。
    """
    for clause in _CLAUSE_SPLIT_RE.split(text or ""):
        if not clause or not any(word in clause for word in words):
            continue
        if not any(mark in clause for mark in _NEGATION_MARKS):
            return True
    return False


def _explicit_approval(text: str) -> bool:
    """本轮人话是否是明示批准：某个分句表了批准，且那个分句里没有否定/延后标记。"""
    return _approval_clause(text, _APPROVAL_WORDS)


# 授权/重试措辞之外的「杂质」剥离面：标点/空白（判裸授权用），同面兼作 W3-2 的分句面。
_CLAUSE_SPLIT_RE = re.compile(r"[\s，。、！!？?；;：:,.~〜「」『』\"'`（）()]+")
```

`_writeback_authorized`（第 1371–1380 行）整段替换（docstring 里「与 `_explicit_approval` 的差别只多认一个『重试』」那段原样保留，只改判定体与首行说明）：

```python
def _writeback_authorized(text: str) -> bool:
    """回写再入授权（R-27）：某个分句表了批准或给了「重试」，且那个分句没有否定/延后标记。

    与 `_explicit_approval` 的差别只多认一个「重试」：那条路是**首次**批准（大纲门），
    措辞必须是批准词；这条路是裁定 19 的续跑支路——人已经在更早的回合过了一次门，
    本轮只需要一个不带否定的重试信号即可把不可逆写接着做完。否定句仍然一律不算授权。
    """
    return _approval_clause(text, _APPROVAL_WORDS + _RETRY_WORDS)
```

`_is_bare_authorization` 内 `rest = _AUTH_STRIP_RE.sub("", text)` 改为 `rest = _CLAUSE_SPLIT_RE.sub("", text)`。全仓 `grep -n "_AUTH_STRIP_RE" backend/` 必须**只剩零处**（改名不留兼容别名，本仓纪律）。

- [ ] **Step 3: 写 W3-3 失败测试（大纲单测）**

在 `backend/tests/test_case_design_plan.py` 追加。夹具形状取自既有 `test_outline_deletes_appear_once_per_layer`（同 id 两条 upsert 行、`state` 一存一更），断言只呈最新那行且**子树不重影**：

```python
def test_outline_dedups_same_id_upsert_keeping_latest_row():
    """W3-3（R-58 并入本片）：增量树里同 id 的「存量／更新」并呈时子节点去重。

    `_outline_nodes()`（stages.py）把 KB 存量行（state=存量）与本 run 草稿行（state=更新）**先存后草
    直接相加**，`_tree_lines` 旧写法只对 delete 去重——走查三实测同一 id 出双行、其子树整体重影
    （38 行 / 19 唯一 id，pt-0001 出现 4 次）。人审门里这一格必须说清「将被写库的是哪一份」：
    最后一行（草稿）胜出，父与子各只呈一行。
    """
    nodes_by_layer = {
        "chain": [{"id": "ch-0001", "name": "旧名的链路", "op": "noop", "parent": "",
                   "state": "存量", "priority": "P2"},
                  {"id": "ch-0001", "name": "新名的链路", "op": "upsert", "parent": "",
                   "state": "更新", "priority": "P0"}],
        "story": [{"id": "st-0001", "name": "旧故事", "op": "noop", "chains": ["ch-0001"],
                   "state": "存量"},
                  {"id": "st-0001", "name": "新故事", "op": "upsert", "chains": ["ch-0001"],
                   "state": "更新"}],
        "point": [{"id": "pt-0001", "name": "旧点", "op": "noop", "story": "st-0001",
                   "directions": ["正向"], "entities": ["旧实体"]},
                  {"id": "pt-0001", "name": "新点", "op": "upsert", "story": "st-0001",
                   "directions": ["正向", "负向"], "entities": ["新实体"]}],
    }
    md = _outline(nodes_by_layer, {"hard": [], "report": {}},
                  {"claims": [], "matrix_notes": [], "unresolved": [], "duplicates": []})
    tree = _section(md, "增量树")
    assert [line for line in tree if "ch-0001" in line] == ["- ch-0001 新名的链路（更新，P0）"]
    assert [line for line in tree if "st-0001" in line] == [
        "  - st-0001 新故事（更新，chains: ch-0001）"]
    assert [line for line in tree if "pt-0001" in line] == [
        "    - pt-0001 新点（方向: 正向/负向；实体: 新实体）"]
    assert "旧名的链路" not in md and "旧故事" not in md and "旧实体" not in md
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_plan.py -q -k w3_3`
Expected: 红——每层拿到 2 行（父行重复两次，因两行同 id 都是根；子行 ×2 挂在两个父下）。

- [ ] **Step 4: 实现 W3-3——最后一行胜出**

`outline.py` 在 `_tree_lines` 之前新增（口径与 `writing.denominator_points` 的 last-row-wins 同源，注释里点名，防止后来者两处漂开）：

```python
def _dedup_latest(rows: list[dict]) -> list[dict]:
    """同 id 只留**最后一行**：`_outline_nodes()` 的行序是「先 KB 存量、后本 run 草稿」，
    草稿才是将被写库的那一份（W3-3）。与 `writing.denominator_points` 的 last-row-wins 同源——
    分母与呈递必须认同一个节点，否则履约表按新版算、大纲按旧版呈。

    出现位置取该 id 的**首次**位置（树形顺序不因去重而漂移），内容取**最后一行**。
    无 id 的行（异常草稿残留）不参与去重、原样呈递：呈递侧宁可多一行，不可静默少一行。
    """
    out: list[dict] = []
    index: dict[str, int] = {}
    for row in rows:
        key = str(row.get("id") or "")
        if key and key in index:
            out[index[key]] = row
            continue
        if key:
            index[key] = len(out)
        out.append(row)
    return out
```

`_tree_lines:23-25` 三行改为：

```python
    chains = _dedup_latest([n for n in nodes_by_layer.get(CHAIN, []) if n.get("op") != "delete"])
    stories = _dedup_latest([n for n in nodes_by_layer.get(STORY, []) if n.get("op") != "delete"])
    points = _dedup_latest([n for n in nodes_by_layer.get(POINT, []) if n.get("op") != "delete"])
```

**只这三行**。delete 清单（`seen_deleted`，A-M3 已有专门用例）不动；`walked` 环保护不动。

- [ ] **Step 5: 两条既有大纲用例不许被顺带改坏**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_plan.py tests/test_case_design_driver.py -q`
Expected: 全绿。重点复核 `test_outline_deletes_appear_once_per_layer` 与 `test_outline_dedups_same_id_delete_across_blocks`（A-M3）：它们的输入每层同 id 只有一条存活行，`_dedup_latest` 必须**零影响**——若这两条红了，是 `_dedup_latest` 把顺序或行内容改了，回 Step 4 修实现，**不许**改这两条既有断言。

- [ ] **Step 6: 全量门禁 + 提交**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 只增不减、0 failed。

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/case_design/stages.py backend/src/aitester/case_design/outline.py backend/tests/test_case_design_driver.py backend/tests/test_case_design_plan.py && git commit -m "fix(case-design): R-58 顺带修走查三遗留 W3-2/W3-3——批准按分句判定、增量树同 id 草稿胜出"
```

---

## Task 25: 离线端到端（case_only 全环 + 末门批准零写入）+ mixed 同轮续跑 + 门禁收口与推送

**Files:**
- Test: `backend/tests/test_case_design_e2e.py`（追加 `_case_only_script` + 两个端到端用例）
- Test: `backend/tests/test_case_design_writing_stages.py`（追加 mixed 同轮续跑的 drain 级用例）

**Interfaces:**
- Consumes: T20–T22 全部已注册 handler（`case_plan/case_gen/case_opt/case_attribute/case_gate/case_gate_interpret`）、`_boot` 的 `writing["status"]` 分流、既有 e2e 挂具（`StubKb`、`_env`、`ScriptedProvider`、`_ai`/`_tc`/`stream_graph`、`_first_build_script`/`_update_script` 的先例形状）。
- Produces: 无新代码接口——本任务是**整片的机器证据**：证明六个阶段接起来真的能从「一句人话」走到「末门呈递」再走到「done 且零 KB 写入」，并把门禁计数收口写进 spec。

为什么要 drain 级 + e2e 级两层：drain 仿真（T18–T22）证明状态机每步对，但它挂的是 `ScriptTask`（call_id 命中的假评审子）；e2e 走真图（`stream_graph` + `ScriptedProvider`），才能钉住**帧序、provider 调用数、以及「批准终帧文案逐字」**这三类只有整图才暴露的缺陷（走查一/二/三每一片的真机挡出物都在这一层，前例见 `_first_build_script` 的帧注释）。

- [ ] **Step 1: 写 e2e 失败用例——case_only 全环**

在 `backend/tests/test_case_design_e2e.py` 追加（脚本形状对齐 `_update_script` 的既有先例：**一次工具调用一条 AIMessage，工具后跟一句人话**；provider 调用数是断言对象，多一句少一句都会红）：

```python
def _case_only_script() -> list[AIMessage]:
    """case_only：计划 → 批用例正文 → 停末门呈递。三层一律 skipped，零设计侧块。"""
    plan = {"task_kind": "case_only", "entry_layer": "point", "terminal_layer": "point",
            "target_subtree": "ch-0001", "source_files": [], "note": "给下单链路写用例"}
    cases = {"chain": "ch-0001", "batch": "ch-0001-b1", "cases": [
        {"case_id": "", "title": "下单正向：库存充足时提交订单", "covers": ["pt-0001"],
         "preconditions": "买家已登录，购物车内有一件在售商品",
         "steps": ["以买家身份提交订单", "读取订单状态与库存扣减记录"],
         "expected": ["订单状态为已创建", "库存数量比提交前减少 1"],
         "priority": "P0", "note": ""}]}
    return [
        _ai(_tc("w1", "write", "design/plan.json", plan)),                    # 1
        _ai("计划已写好，本次是纯用例任务。"),                                  # 2
        _ai(_tc("w2", "write", "design/cases/ch-0001-b1.json", cases)),        # 3
        _ai("第一批用例正文已交。"),                                           # 4
    ]


def test_case_only_end_to_end_delivery_and_approval(tmp_path: Path) -> None:
    """第四片整图：case_only 从一句人话走到末门呈递，批准后 done 且**零知识库写入**（裁定 35/36）。"""
    kb = StubKb(layers={
        "chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1,
                   "name": "下单链路", "business_scope": "下单主流程", "priority": "P0"}],
        "story": [{"id": "st-0001", "type": "story", "chains": ["ch-0001"],
                   "name": "下单成功", "priority": "P0"}],
        "point": [{"id": "pt-0001", "type": "point", "story": "st-0001",
                   "name": "下单正向", "entities": ["订单"], "directions": ["正向"],
                   "priority": "P0"}],
    }, root=str(tmp_path / "kb_root"))               # 三层 maintained 存量：case_only 的放行前提
    env = _env(tmp_path, kb)
    task = ScriptTask({
        "case-ch-0001-b1-r0": _j({"opinions": [], "resolutions": []}),
        "case-gate-int-r1": _j({"opinions": [], "resolutions": []}),
    })
    tools = _tools_with_task(tmp_path, task)
    provider = ScriptedProvider(_case_only_script())

    frames = list(stream_graph(build_case_design_graph, provider, tools,
                               [HumanMessage(content="给下单链路写用例")], case_env=env))
    assert frames[-1]["reply"] == "用例交付物已生成（design/case-delivery.md），等待人工评审。"
    assert len(provider.calls) == 4                 # 两次工具回合 + 两句人话，不多不少
    assert [e["tool"] for e in frames if e["type"] == "call"] == ["write", "write"]
    assert task.call_ids() == ["case-ch-0001-b1-r0", "case-gate-int-r1"]
    # 设计侧零动：三层全 skipped ⇒ 一块草稿都没落，①②③ 一次没派
    assert [Ledger.load(env.design).layer(x)["mode"] for x in ("chain", "story", "point")] == \
        ["skipped"] * 3
    assert not any(i.startswith(("blk-", "enum-", "claims-", "matrix-"))
                   for i in task.call_ids())
    delivery = (env.design / "case-delivery.md").read_text(encoding="utf-8")
    assert "实测：未落实点 0 个" in delivery
    assert "cc-0001" in delivery                     # 用例 id 由系统补齐，只活在这份交付物里
    assert kb.upserts == [] and kb.deletes == []     # 呈递阶段零写库

    provider2 = ScriptedProvider([])                 # 批准轮：零工具调用，只回人话
    frames2 = list(stream_graph(build_case_design_graph, provider2, tools,
                                [HumanMessage(content="通过")], case_env=env))
    assert frames2[-1]["reply"] == "用例交付确认完成：用例正文只落项目空间 design/cases/，本次零知识库写入。"
    assert kb.upserts == [] and kb.deletes == []     # 裁定 35：末门批准 ≠ 回写授权
    led = Ledger.load(env.design)
    assert led.status == "done" and led.data["writing"]["gate"]["approved_at"]
    assert (env.cases_dir() / "ch-0001-b1.json").exists()
    body = json.loads((env.cases_dir() / "ch-0001-b1.json").read_text(encoding="utf-8"))
    assert body["cases"][0]["case_id"] == "cc-0001"  # 补号写回文件本体，交付物与制品逐字一致
```

`stream_graph` 的实参顺序照本文件既有用例（`stream_graph(build_case_design_graph, provider, tools, messages, case_env=env)`）逐字抄，**不要**按别处的记忆写。三层存量夹具**逐字取自 `test_update_branch_end_to_end`**（同一文件里已有），要点只有一个：`pt-0001` 挂在 `st-0001`→`ch-0001` 上且三层都是 `maintained`，否则 `case_only` 会被 T20 的 `_case_ready_or_block` 挡在「三层未过审」的明示边界上——那是另一条路径，本用例要的是放行路径。

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_e2e.py -q -k case_only`
Expected: 红在帧数或 `provider.calls`（末门未注册时的 generic halted），实现完成后绿。

- [ ] **Step 2: 写 mixed 续跑用例（drain 级，两次人话）**

在 `backend/tests/test_case_design_writing_stages.py` 追加（`_kb_with_three_layers` 是 T20 已在本文件建好的三层存量夹具，直接用；`ScriptTask()` 的 `_default` 已按前缀给 `enum/claims/matrix/case-*` 结构化干净判决，不需要显式 script）。

这条钉的是裁定 40 的 `mixed`：设计侧的**唯一人类门**照旧要人批准，批准后 `h_writeback` 尾部同轮接 `case_plan`，一路跑到用例末门才再发终帧——中途**不许**有任何「请你再下一条指令」的空转：

```python
_MIXED_PLAN = {"task_kind": "mixed", "entry_layer": "chain", "terminal_layer": "point",
               "target_subtree": "", "source_files": [], "note": "按新业务信息做增量并写用例"}


def test_mixed_continues_into_writing_ring_right_after_writeback(tmp_path):
    """裁定 40：mixed 的设计侧批准后**同轮**续跑编写环，直到用例末门才发终帧。

    设计侧取「三层存量 + 逐块 no_change」的最小增量（回写零节点），编写环只出 1 批。
    两次人话分别是两道门：第一次「通过」= 大纲门（设计侧回写），第二次「通过」= 用例末门。
    若实现把续跑写成「等用户再指令」，第一条 drain 就会停在大纲门后不再有编写环事实——
    下面的 `writing` 断言即红。**不许**把断言改成「三次人话」迁就实跑。
    """
    kb = _kb_with_three_layers(tmp_path)
    env = _env(tmp_path, kb)
    task = ScriptTask()
    nc = lambda layer, block, mode: {"nodes": [], "note": "no_change"}   # noqa: E731

    first = drain(env, kb, task, plan=_MIXED_PLAN, gen_nodes=nc)
    led = _led(env)
    assert led.status == "awaiting_review" and led.data["gate"]["approved_at"] == ""
    assert _end_text(first["frames"]) == "大纲已生成（design/outline.md），等待人工评审。"
    assert led.data["writing"]["status"] == ""                  # 批准前编写环一步不许动

    second = drain(env, kb, task, state={"messages": [HumanMessage("通过")], "case": {}},
                   plan=_MIXED_PLAN, gen_nodes=nc)
    led = _led(env)
    assert led.data["gate"]["approved_at"]                      # 设计侧门已过（批准那一轮）
    assert led.status == "awaiting_review"                      # 现在等的是**用例末门**
    assert led.data["writing"]["status"] == "awaiting_review"
    assert led.data["writing"]["note"].startswith("设计侧回写完成 @")
    assert (env.cases_dir() / "ch-0001-b1.json").exists()       # 编写环确实跑了批，不是空转
    assert _end_text(second["frames"]) == "用例交付物已生成（design/case-delivery.md），等待人工评审。"

    frames3: list[dict] = []
    turn = _drive(env, {"messages": [HumanMessage("通过")], "case": {}}, task,
                  writer=frames3.append)
    assert turn["case"]["route"] == "end"
    assert "零知识库写入" in _end_text(frames3)
    assert Ledger.load(env.design).status == "done"
    assert kb.upserts == [] and kb.deletes == []        # 全 no_change ⇒ 设计侧本轮零节点回写；
    # 若实跑出现非空 upserts，说明 no_change 块被重发（T13 语义破了），照报不改断言。
```

**形状说明（写用例前先认清，别照记忆写）**：
- `drain` 停在 `route == "end"`，所以设计侧大纲门那句 `ctx.end` 就是第一次 drain 的终帧——这正是 mixed 的正确行为（两道门两次人话），本用例断言的是**第一次批准之后到末门呈递之间不再需要第三次人话**。
- 第二次 `drain` 必须传**新 state**（`{"messages": [HumanMessage("通过")], "case": {}}`），不能复用 `first`：`drain` 留下的 `case["boot"]` 为真会让 `_boot` 不重入、`fresh` 判假直接走 B-F4 的静默交回。这是本片已确立的挂具纪律（T22 里三条人审续步用例同理）。
- `_end_text(state["frames"])` 是 T22 Step 2 已加好的取数壳；`drain` 把终帧列表存在 `state["frames"]`。

- [ ] **Step 3: 跑到绿——整片六阶段第一次接成真图**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_e2e.py tests/test_case_design_writing_stages.py tests/test_case_design_writing.py -q`
Expected: 全绿。分三种红法分开处置：
- mixed 用例红在「第一次批准后 `_end_text(first["frames"])` 不是大纲门那句」或 `writing["status"]` 仍是空串 ⇒ 是 `h_writeback` 尾部接缝没生效 / `h_case_plan` 把 mixed 拒了（只认 `case_only`），按 T20 Step 3/4 的接缝顺序查实现。
- mixed 用例红在**设计侧**（drain 未收敛、账本 `halted`、或大纲门 hard 非零）⇒ **报 BLOCKED**，把这轮的 hard 与账本读数原样贴进报告；**不许**为了让 mixed 跑绿而把 `gen_nodes` 改成出真节点、也不许改 `terminal_layer`/砍断言迁就实跑——那是设计侧既有语义的问题，不归本片。
- e2e 用例红在 `provider.calls` 或末门未注册时的 generic halted ⇒ 按 T22 已注册的六个 handler 对照 `_STAGE_HANDLERS`，实现侧修，**不许**改断言迁就实跑。

- [ ] **Step 4: 全量门禁 + 计数收口**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 0 failed；把实跑 passed 数（基线 770 + 本片新增）报给控制方，由控制方回填 spec 的第四片记录。

```bash
cd /d/code/github/AiTester && git add backend/tests/test_case_design_e2e.py backend/tests/test_case_design_writing_stages.py && git commit -m "test(case-design): 第四片整图端到端——case_only 全环到末门、mixed 同轮续编写环、批准零知识库写入"
```

- [ ] **Step 5: 推送（控制方执行，实施代理不得 push）**

`git push origin master:main` —— 由控制方在本任务评审通过后执行（Global Constraints 12）。

---

## Task 26: 付费走查四——真机端到端 + 五条判据读数回填 + R-56 等字节证据 + 清场

**Files:**
- 无代码改动（除判据落空需要修复时另起提交，走查纪律）。
- Modify: `docs/superpowers/specs/2026-10-05-case-design-loop-design.md`（第四片节末回填实测读数、两条实施澄清、R-58 收口状态）

**Interfaces:**
- Consumes: T18–T25 全部已推送的实现（HEAD 必须 == origin/main 且门禁全绿）；走查三用过的同一 KB 根（三层已 maintained、已回写过一轮）；既有 HTTP 面（`/api/chat/stream`）。
- Produces: spec 里的**走查四记录**（判据 ①–⑤ 逐条实测读数 + call 数 + 清场 md5），与本片是否收口的判定。

**授权线（务必先读）**：本任务的前两步是**免费**的（起实例 + 探针 + md5 基线，零 LLM 调用）；第三步起是真金付费（预计一次 case_only 走查 ~200–400 call，含修复轮可能更多）。按用户既有走查纪律（「走查与整份验收由我端到端做完，含付费」）**不必逐项再问**，但控制方仍要在开跑前把「免费部分读数 + 预计付费」一并呈报一句，开了就跑到底，不中途停。

- [ ] **Step 1: 隔离实例自起（端口 8012，零向量调用）**

```bash
cd /d/code/github/AiTester/backend
mkdir -p /d/tmp/walkthrough_case/kb
PYTHONDONTWRITEBYTECODE=1 REME_KNOWLEDGE_BASES_DIR=D:/tmp/walkthrough_case/kb KB_ID=case_probe \
  env -u DASHSCOPE_API_KEY KB_EMBEDDING_API_KEY= \
  .venv/Scripts/python -m uvicorn aitester.main:app --host 127.0.0.1 --port 8012 &
```

- 端口只用 **8012**：8000（PID 62220）与 5173（PID 9272）是用户的，**不重启、不杀、不占用**；8010/8011 是前几片用过的，不复用免混淆。**严禁运行 `scripts/dev.ps1`**。
- **复用走查三回写后的 KB 根**（三层已 maintained、第四层不落库）——这是裁定 40 的 `case_only` 放行前提，也是判据 ④ 的对照面。开跑前 `md5sum` 全量存基线（在 `D:/tmp/walkthrough_case/kb` 根里跑，别在 junction 里跑）。
- 实例健康检查用**零副作用** GET（不打 `/stream`，避免白烧一次调用）：`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8012/api/health`。

- [ ] **Step 2: 探针会话与项目（先建、后删，名字留痕）**

建一个合成项目「云杉商城 订单域」（**合成，与任何真实产品线无关**），里面放两份业务文档：`requirements.md`（已有三层内容的复述，供设计侧不触发）与 `notes.md`（一句话：「请把下单链路的测试点落成可执行用例」）。建探针会话并把 `agent_id=case_design`、`project_id` 指过去，**记下 session id 与 project id**（清场要删、spec 要写）。

- [ ] **Step 3: 真机 case_only 一轮（付费）**

发一句人话（合成措辞，不引任何真实产品名词）：「给云杉商城订单域的下单链路编写用例」。用 `curl -N` 打 `/api/chat/stream` 抄帧，全程存 `walkthrough4.sse`：
- 数成本：`grep -c '^event: call' walkthrough4.sse`（既有口径）。
- 抄 run_id：`curl` 侧 `tee` 或从 `data:` 帧里读（走查三手法）。
- 期望走到末门呈递终帧「用例交付物已生成（design/case-delivery.md），等待人工评审。」。**若走到 halted**：按走查纪律如实报 halted 文案与账本 `cursor`，先归因（多半是简报字段形状——W3-1 的教训），再修，不许粉饰。

- [ ] **Step 4: 判据 ①–⑤ 逐条实测（每条都要能复现的读数）**

| 判据 | 取数方法 | 通过线 |
|---|---|---|
| ① 未认领点 = 0（hard，实测） | 读项目空间 `design/case-delivery.md` 的「实测：未落实点 N 个」；再读 `design/ledger.json` 的 `writing.targets[].batches[].points` 与各 `design/cases/<批次>.json` 的 `covers` 并集**自数一遍**（两个数必须一致——交付物说 0、自己数是 3 就是假绿） | N=0 且两数一致 |
| ② 规范校验表在场并逐条呈递 | `## 规范校验表（呈递项）` 段存在，且逐行含「空泛断言／环境存量前置」两类线索；无问题时该段写「无」而**不是缺段** | 段在场 + 线索可复核 |
| ③ 末门后可回溯 + 其余批次标 `stale` 可见 | 末门呈递后发一句「把 st-0001 那个点的用例拆成两条」（人话，非批准）→ 应走回溯：目标批 `writing.batches[].state` 回 `drafted` 重跑、其余 done 批次变 `stale` 且 `writing.stale_batches` 在场、交付物「失效待重算批次」段列出 | 状态迁移可见、无静默 |
| ④ 真实 KB 与探针 KB **零写入** | `md5sum -c` 对照 Step 1 基线：`D:/tmp/walkthrough_case/kb` 全量 + 真实 KB `C:\Users\qifengshunshi\.reme\knowledge_bases`（1803 文件，判「今日 0 改动」）；两者都必须零变化 | 零变化 |
| ⑤ 用例制品落 `design/cases/` 且字节可复核 | `ls design/cases/`、每条用例含 `cc-` id、`wc -c` 与台账对得上；`design/case-delivery.md` 里的 `cc-` 与文件内**逐字一致** | 落点 + 字节一致 |

- [ ] **Step 5: 批准轮（付费，一次）**

末门发一句**明示批准**（建议就用 W3-2 修过的那句长句「整体看没什么问题，同意通过」——顺带在真机上给 W3-2 留一条证据）。期望终帧逐字「用例交付确认完成：用例正文只落项目空间 design/cases/，本次零知识库写入。」，且 `kb` 侧 md5 仍零变化（裁定 35 的真机确认：批准**不**触发回写）。

- [ ] **Step 6: R-56 补证——「等字节跳过」分支的真机证据**

走查三照报了 `untouched=0`：R-56/T14 的等字节跳过分支只有离线证据。本轮补法（**设计侧**，不是用例侧）：在同一个探针项目里再发一句纯设计增量（合成新文档 `notes2.md`：只补一条与既有链路无关的新二级链路），跑完回写后读 `design/ledger.json` 的 `writeback` 计数与尾句——**13 个未动链节点整文件 md5 与基线逐字节一致** + 尾句「（其中 N 个节点内容未变，已跳过重写）」在场，即该分支首次真机触发。若尾句仍无（本轮没有任何下发项逐字等于存量），照报「未触发」，不伪造输入去凑。

- [ ] **Step 7: spec 回填 + 两条实施澄清 + 提交**

在 spec 第四片节末追加「走查四记录（2026-10-XX 实测）」，含：五条判据逐条读数（过了/没过，落空照报）、call 数对照（首建 1648／走查二 2830／走查三设计轮 1187）、W3-2/W3-3 收口证据、R-56 是否补上、清场读数。并落两条**实施澄清**（裁定原文与实现有差、必须写回 spec 免得后来者按原文读不出实现）：
- **实施澄清 A（裁定 35）**：裁定原文「不新增 `tc-` 前缀」被实现为**新增 `cc-` 前缀但只活在大项目空间**（`design/cases/` + 交付物 + `counters.case`），KB 侧零写入。理由：用例必须有可被 `covers` 引用的稳定 id，否则认领关系无从核对；裁定真正禁止的是**进知识库**，不是禁 id。
- **实施澄清 B（裁定 39）**：「≤10 条用例/批」实现为 **≤10 个测试点/批**（`CASE_BATCH_CAP=10`）。理由：裁定 38 明定「点↔用例数量关系不固定」，按用例条数封顶会让一批的**分母**不确定（同 10 点可出 3 条也可出 40 条），末门核对必须按点算；点数封顶才让「一批 = 一份可核对的分母」成立。
- **实施澄清 C（裁定 37）**：规范校验表原文列五项（模板字段齐全／步骤可执行／断言为硬断言／前置不拿环境存量／无产品线业务名词）。本片把**可确定性判定的三项**落进 `run_case_checks` 的 `report`（字段齐全 = schema 校验，硬断言 = `VAGUE_ASSERTION_MARKS`，前置不拿存量 = `ENV_PRECONDITION_MARKS`）；**另两项是语义判断**（「这一步到底可不可执行」「这个词是不是产品线专有名词」没有可枚举的字面标记），交批评审子的意见出，同表呈递、同样不卡关。交付物里两者混排但线索来源标注清楚，人不被误导为「机器全查过了」。

```bash
cd /d/code/github/AiTester && git add docs/superpowers/specs/2026-10-05-case-design-loop-design.md && git commit -m "docs(case-design): 走查四读数回填——五条判据实测、W3-2/W3-3 收口证据、R-56 等字节补证与两条实施澄清"
```

- [ ] **Step 8: 清场（实测，逐项复现）**

- 停掉 8012 实例（只停自己起的这个 PID，**不碰 62220/9272**）。
- 删探针会话与探针项目（删前记项目数，用户项目 `debug1` 不许动）；`backend/data/projects.json` md5 回基线 `9116cdf85e…`、`data/sessions/index.json` 回 `bd78c88524…`。
- `backend/data/workspaces/` 无探针项目子目录残留。
- 真实 KB 零改动复述一遍（1803 文件今日 0 改动）；`D:/tmp/walkthrough_case/` 归控制方自留，**不递归删**（里面有 junction 风险的教训：摘链要判 `st_file_attributes & 0x400` 后 `os.rmdir`）。
- `git status --porcelain` 与开工基线一致（只有那 4 件他专项未跟踪文件）；账本 `progress.md` 追加 T26 complete + 走查四收口状态。

---

## 收尾（本片之后）

- 四条待办口径留给下一片：① `case_only` 的**明示边界**路径（三层未过审时不静默重生成）只测了放行侧，拒绝侧走查未见；② 末门修复环的 round_cap 用尽文案在真机未触发；③ 跨链路认领（一条用例 `covers` 别的链路的点）目前判 `phantom_cover`，是否需要放行是产品问题；④ 走查三的「halted 后无断点续跑受控入口」产品缺口仍在。
- 本片闭合定义 = T18–T26 全部有 complete 记录、门禁只增不减、走查四五条判据读数在 spec 落字（落空项照报）。

---

## 附：2026-10-08 整片终评后的计划文本对齐（不改决策轨迹，只把「以哪份为准」写清）

终评（0 Critical / 2 Important / 13 Minor，账本 R-63）落了一个修复片 F1–F5，其中三处**计划文本与落地代码
不一致**，就地按落地代码对齐并在此留痕，免得后来者照计划原文找不到实现处：

- **键名 `points_cap`（Minor 8）**：T20 的清单代码块与测试原写 `cases_cap`，落地为 `points_cap`
  （`stages.py:541`、`test_case_design_writing_stages.py:99`）。理由与实施澄清 B 同源——封顶的是**点数**（分母），
  不是用例条数。两处正文已按落地改。
- **去重函数落点（Minor 6/7 + I-1 修复 F2）**：`_dedup_written` 不再住在 `outline.py`，上移为
  **`schema.dedup_written` 唯一实现处**，呈递（`outline._tree_lines`）／分母（`writing.denominator_points`）／
  清单（`stages._rows_of`）三站点同走它；文件表与 T24「Produces」行已改，终评 I-1 那个「同 id 多行取哪一份」的四侧分叉从此只有一处定义。
- **`CASE_BATCH_CAP` 引用**：注释里的出处从「裁定 39 泛说」落到可定位的本文「实施澄清 B」。



