> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

# 断点续跑片（第五片）Implementation Plan

**Goal:** `halted` 之后用户再发一句话，不再把整条工作面归档重烧，而是**默认从断点续跑**；只有本轮人话**明示重开**才销毁现场。

**Architecture:** 三件事，各自单点。① halt 现场落盘：`_Halt` 携带族属（抛点自标），驱动两处 `except` 经 `_book_halt` 写账本 `data["halt"]`；② `_boot` 把 `done` 与 `halted` 分家：`halted` 走 `_resume_halted`，按四族分流（复位待决计数 / 重开重试预算并注入本轮人话 / 拒绝推进零调用）；③ 重开判据 `_wants_restart` **复用** `_approval_clause`（分句＋否定标记撤销），全仓不写第二份措辞规则。

**Tech Stack:** Python 3.11 + LangGraph（专属 loop 驱动是纯状态机，测试模块级直调 `drive_turn`）、pytest（`backend/tests`）、账本 = `design/ledger.json`。

**Spec:** `docs/superpowers/specs/2026-10-05-case-design-loop-design.md` —「断点续跑片（第五片…）」**裁定 42–47** ＋「走查五验收线」①–⑤。

**Baseline:** 代码 `8b23d9a`（docs 到 `3c700b3` = origin/main），门禁 **868 passed / 0 failed**。本片只动 `backend/src/aitester/case_design/{constants,ledger,stages}.py` 与 `backend/tests/test_case_design_{writing,driver,graph,e2e}.py`。

---

## Global Constraints

- **裁定 42**：`halted` 的新回合**默认续跑**（不归档、不 `Ledger.fresh`）；`done` 的新回合＝新任务**保持原样**（W4-2 已裁，不许顺手改）。
- **裁定 43**：halt 族属**只由抛点声明**（`_Halt(msg, kind)`，构造必传、非法值 `ValueError`）；下游**禁读 `reason` 文案猜族**。`_book_halt` 是 `data["halt"]` 的唯一写入处。
- **裁定 43 纠正**：「块环／层审**轮次用尽**」**不是** halt——`h_opt` 到 `round_cap` 转 `h_attribute` 收口前进。本片不给它任何分支。
- **裁定 44**：续跑复用 `_go`（`nudge/asked` 自动复位），**`round` 原样保留**——断一次不许白送 5 轮。`human_wait` 只复位**游标那道门**的 `unclear`。`needs_input` **零转场、零子调用**、状态保持 `halted`。
- **裁定 45**：注入点只有 `Ctx.ask` 一处，附「【人工补充】…」并**用后即清**；`resume_note` 每个新回合进 `_boot` 先清一次（只活一回合）。
- **裁定 46**：`_wants_restart` 必须是 `_approval_clause(text, _RESTART_WORDS)` 的一行调用。**不许**新建第二套分句/否定扫描。
- **裁定 47**：不启用 `interrupted` 状态、不动 `_ARCHIVE_ITEMS`、不动两道门的 hard=0 门禁。
- **文案基句逐字不许动**：`测试设计任务中止：{reason}` 是既有逐字断言对象（`test_case_design_graph.py:146/182`），尾巴**另起一句**追加；改那两处断言时基句用 `startswith` 钉住。
- **门禁只增不减**：基线 868，只许 ≥868。读数一律以**提交态/纯净树**为准（裁定 23），脏树读数只作线索。
- **测试执行只用** `backend/.venv/Scripts/python -m pytest -q`（系统 python312 会在 collection 前撞 `--env` argparse 冲突）。
- **Windows/Git Bash 纪律**：控制台中文乱码 ⇒ 证据写 UTF-8 文件再读；`md5sum -c` 必须在基线自己的根里跑；python 路径用 `D:/...`。
- **走查五环境**：隔离实例端口 **8013**（8010/8011/8012 已用完并停，不复用），探针 KB 根 `D:/tmp/walkthrough_case/kb`（`KB_ID=case_probe`）；**真实 KB（1803 文件）零改动**；用户 8000/5173 与 `scripts/dev.ps1` 全程不许碰；成本**只报数不设线**。
- **不许为凑判据伪造输入**：真机未触发的分支照实报「未触发」并给机制归因（R-56 同一条纪律）。

---

## 现场预检表（控制方 2026-10-08 读码得出，派发时逐字带上；行号以 `8b23d9a` 为准）

| 事实 | 出处 |
|---|---|
| `done`/`halted` 合用归档支路（本片要分家的就是它） | `stages.py:212-217` |
| `ctx.cur` **就是** `led.data["cursor"]` ⇒ 断点位置早就在盘上，续跑无须新增游标 | `stages.py:88-90`、`ledger.py:94-96` |
| `_go` 转场会 `clear()` 整张游标 ⇒ 用它续跑即自动复位 `nudge/asked`，`round` 须由调用方原样传回 | `stages.py:193-199` |
| halt 时只写 `status="halted"`，**原因与游标快照都没落盘** | `stages.py:2449-2457` |
| `_Halt` 抛点全集（**11 处** raise/return）：`124`（ask 重试超限）、`390/392`（`_phantom_subtree_halt`，定义在 `:383`）、`522`、`572`、`904`、`1030`、`1102`、`1183`、`2073`、`2229`、`2446` | `grep -n "_Halt(" stages.py` 实测 |
| 族属归属（裁定 43）：`124`→`artifact_retry`；`1102/2229`→`human_wait`；`2446`＋泛异常→`transient`；`390/392/522/572/904/1030/1183/2073`→`needs_input` | — |
| 轮次用尽**不抛** `_Halt`：转 `attribute` 后 `_after_block`／`_layer_audited` 前进 | `stages.py:1430-1432`、`1549-1553` |
| `human_wait` 抛点处游标是 `gate_interpret`／`case_gate_interinterpret`… **实测：`gate_interpret`／`case_gate_interpret`**（raise 发生在 `_go(...,"gate")` **之前**） | `stages.py:2229`（在 `:2233 _go` 之前）、`:1102`（在 `:1104` 之前） |
| 两道门的待决计数分居 `gate["unclear"]` 与 `writing["gate"]["unclear"]` | `stages.py:2227`、`:1100` |
| `_approval_clause`／`_APPROVAL_WORDS`／`_NEGATION_MARKS`／`_CLAUSE_SPLIT_RE` 全在 `stages.py:2096-2140`（`_RESTART_WORDS` 加在这一族旁边，词表判重开即调它） | 见左 |
| `h_plan` 续跑后第一件事必然经 `ctx.ask`（`plan.json` 在盘但非法→带错重问；不在盘→首问） | `stages.py:1149-1157` |
| 幻影子树 halt 发生在 `init_task` 之后 ⇒ `needs_input` 拒绝路径**不重烧任何评审** | `stages.py:1168-1174` |
| `_ARCHIVE_ITEMS` 不含 `ledger.json`；`Ledger.load` 的损坏自愈会 `os.replace` 留证 | `stages.py:56-57`、`ledger.py:62-70` |
| 窄背填先例（F1）：`_backfill_fourth_slice` 只补 `writing` 与 `counters.case`，**明确不做深合并**；它的测试在 `test_case_design_writing.py:128` 附近 | `ledger.py:41-47` |
| halt 终帧的**全等**断言只有 2 处：`test_case_design_graph.py:146`、`:182` | 见左 |
| 挂具：`_env/_drive/_append/simulate/drain/_end_text/StubKb/ScriptTask/_j` 全在 `test_case_design_driver.py:34-284`；`e2e` 从该文件 import | 见左 |
| 待办④ 的代价读数（本片要修的账）：走查四 **572 call**、走查二 **~1000 call** | spec「走查记录」段、第四片终评记录「断点续跑的人证与边界」 |

## 任务依赖

`T27 → T28 → T29 → T30 → T31 → T32 → T33 → T34`（严格串行：T30 依赖 T28 的账本节与 T29 的词表；T31/T32 依赖 T30 的分流；T33/T34 依赖全部）。

---

### Task T27: 族属常量与账本 `halt` 节（含窄背填）

**Files:**
- Modify: `backend/src/aitester/case_design/constants.py`（`MAX_TRANSITIONS` 之后）
- Modify: `backend/src/aitester/case_design/ledger.py:21-47`
- Test: `backend/tests/test_case_design_writing.py`（追加到既有背填测试之后）

**Interfaces:**
- Produces：`HALT_KINDS: tuple[str, ...]`、`HALT_KIND_CN: dict[str, str]`、账本 `data["halt"]` 的**形状**（T28 写它、T30 读它、T31/T32 读它的子字段）：
  `{"kind": str, "reason": str, "stage": str, "layer": str, "block": str, "round": int, "at": str, "count": int, "resume_note": str}`

- [ ] **Step 1: 写失败测试**（追加到 `test_case_design_writing.py`，紧邻 `_backfill_fourth_slice` 那条测试）

```python
def test_halt_node_fresh_shape_and_narrow_backfill():
    """裁定 43：续跑的依据必须成节存在；背填仍走 F1 的窄口径——只补 halt，别把坏账本洗白。"""
    from aitester.case_design.constants import HALT_KINDS
    from aitester.case_design.ledger import _backfill_ledger_slices, _fresh_data

    halt = _fresh_data()["halt"]
    assert tuple(halt) == ("kind", "reason", "stage", "layer", "block",
                           "round", "at", "count", "resume_note")
    assert halt["kind"] == "" and halt["count"] == 0 and halt["round"] == 0
    assert set(HALT_KINDS) == {"human_wait", "artifact_retry", "transient", "needs_input"}

    old = _fresh_data()
    del old["halt"]                                   # 本片之前落盘的账本
    _backfill_ledger_slices(old)
    assert old["halt"] == _fresh_data()["halt"]       # 缺什么补什么，补完即健康

    broken = _fresh_data()
    del broken["halt"]
    broken["gate"] = None                             # 真坏了：不许顺手「修好」
    _backfill_ledger_slices(broken)
    assert broken["gate"] is None                     # 深合并会把真损坏洗成健康账本
```

- [ ] **Step 2: 跑红** — `backend/.venv/Scripts/python -m pytest -q backend/tests/test_case_design_writing.py -k halt_node`，预期 `ImportError: HALT_KINDS`。
- [ ] **Step 3: 落常量**（`constants.py`，`MAX_TRANSITIONS` 行之后）

```python
# 断点续跑（第五片，spec 裁定 43）：族属由抛点自标，下游一律不许读 reason 文案猜。
HALT_KINDS: tuple[str, ...] = ("human_wait", "artifact_retry", "transient", "needs_input")
HALT_KIND_CN: dict[str, str] = {
    "human_wait": "等你的一句话",
    "artifact_retry": "制品反复不合法",
    "transient": "环境或内部故障",
    "needs_input": "输入或账本对不上，续跑必再炸",
}
```

- [ ] **Step 4: 落账本节与背填**（`ledger.py`）

```python
def _fresh_halt() -> dict[str, Any]:
    """halt 节形状（裁定 43）：续跑的唯一依据。count 记「停在同一处的第几次」，供终帧说实话。"""
    return {"kind": "", "reason": "", "stage": "", "layer": "", "block": "",
            "round": 0, "at": "", "count": 0, "resume_note": ""}
```
`_fresh_data()` 里 `"writeback"` 之前插入 `"halt": _fresh_halt(),`；把 `_backfill_fourth_slice` **改名**为 `_backfill_ledger_slices`（它现在补的是两个片的缺节，旧名对新来者说谎），并加一行 `if "halt" not in data: data["halt"] = _fresh_halt()`；`Ledger.load` 的调用点同步改名。**不做深合并**（F1 口径原样保留，注释也在）。改名面实测：全仓只有 `test_case_design_writing.py:128` 的 **docstring** 提到旧名（无 import），一并改成 `_backfill_ledger_slices`——旧名留在测试说明里就是说谎。

- [ ] **Step 5: 跑绿 + 全量门禁** — `pytest -q` 相关文件后跑 `-q` 全量，读数只增不减。
- [ ] **Step 6: 提交**

```bash
git add backend/src/aitester/case_design/constants.py backend/src/aitester/case_design/ledger.py backend/tests/test_case_design_writing.py
git commit -m "feat(case-design): halt 现场成节落账——HALT_KINDS 与账本 halt 节（裁定 43，窄背填）"
```

---

### Task T28: `_Halt` 带族属 + 十一处抛点自标 + `_book_halt` 单点记账

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（`_Halt` 类、`Ctx.ask`、`_phantom_subtree_halt`、`:522/:572/:904/:1030/:1102/:1183/:2073/:2229/:2446` 各 raise、两处 `except`）
- Test: `backend/tests/test_case_design_driver.py`（追加）；`backend/tests/test_case_design_graph.py:154` 补一条子断言

**Interfaces:**
- Consumes: T27 的 `HALT_KINDS`、`data["halt"]` 形状。
- Produces: `_Halt(message, kind)`（`.kind`）、`_book_halt(ctx, kind, reason)`（唯一写入处）、`ctx.led.data["halt"]` 在 halted 时**必然**带 `kind/stage/layer/block/round/at/count/reason`。

- [ ] **Step 1: 写失败测试**

```python
def test_halt_books_cursor_snapshot_and_kind(tmp_path):
    """裁定 43：停在哪儿、为什么停、第几次停——不落盘就没有「续跑的依据」。族属只由抛点声明。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):                                   # 首问 + 3 次携错重问（NUDGE_CAP=3）
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))     # 第 5 次：nudge 用尽 → halted
    led = Ledger.load(env.design)
    halt = led.data["halt"]
    assert led.status == "halted" and halt["kind"] == "artifact_retry"
    assert halt["stage"] == "plan" and halt["count"] == 1 and halt["round"] == 0
    assert halt["reason"].startswith("plan/-/- 重试超限") and halt["at"]


def test_halt_kind_validation_fails_loud():
    """新抛点漏标族属必须响亮失败：默认值会把未知族属洗成某一族的续跑策略（比不分类更坏）。"""
    from aitester.case_design.stages import _Halt
    import pytest
    with pytest.raises(ValueError):
        _Halt("某句原因", "not_a_kind")
    assert _Halt("某句原因", "transient").kind == "transient"
```

> 计划缺陷更正（控制方拆计划期自查，裁定 24 同族）：本任务原本还带一条 `test_halt_count_tracks_same_spot`，
> 它断言的是「续跑保住账本 ⇒ 同一处第二次 halt 时 count==2」，而那要到 T30 的分流才成立——留在 T28 就是
> 「提交时全量必红」，与门禁只增不减直接冲突。该测试**移入 T30 Step 1**，红证归属随之写清。

- [ ] **Step 2: 跑红**（`-k halt_kind or halt_books or halt_count`）。
- [ ] **Step 3: `_Halt` 带 kind**（替换 `stages.py:60-61`）

```python
class _Halt(RuntimeError):
    """驱动内的确定性中止。kind 必须是 HALT_KINDS 之一且**由抛点自标**（裁定 43）：
    续跑策略按族分流，靠 reason 文案猜族等于把产品语义押在字符串上。"""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        if kind not in HALT_KINDS:
            raise ValueError(f"非法 halt 族属「{kind}」，允许 {HALT_KINDS}")
        self.kind = kind
```

- [ ] **Step 4: 十一处抛点逐点标族**（只加第二个实参，**文案一字不动**——`artifact_retry` 那句 reason 被 graph 测试逐字吃着）
  - `:124` `Ctx.ask` → `"artifact_retry"`
  - `_phantom_subtree_halt` 两条 `return _Halt(...)` → `"needs_input"`
  - `:522`、`:572`、`:904`、`:1183` → `"needs_input"`
  - `:1030`、`:2073`（两道门修复环用尽）→ `"needs_input"`（裁定 47②：不开 hard 进门的口子）
  - `:1102`、`:2229`（两道门连续待决）→ `"human_wait"`
  - `:2446`（转场上限）→ `"transient"`
- [ ] **Step 5: 落 `_book_halt`**（放在 `_now()` 之后，紧跟 `_archive`）

```python
def _book_halt(ctx: Ctx, kind: str, reason: str) -> None:
    """halt 现场落盘的唯一实现处（裁定 43）：游标快照＋停留计数。整节重写，resume_note 随之一清。"""
    halt, cur = ctx.led.data["halt"], ctx.cur
    same = (halt.get("kind") == kind and halt.get("stage") == cur["stage"]
            and halt.get("layer") == cur["layer"] and halt.get("block") == cur["block"])
    halt.update({
        "kind": kind, "reason": reason, "stage": cur["stage"], "layer": cur["layer"],
        "block": cur["block"], "round": int(cur["round"] or 0), "at": _now(),
        "count": (int(halt.get("count") or 0) + 1) if same else 1, "resume_note": ""})
```

- [ ] **Step 6: 驱动两处 `except` 记账**（`stages.py:2449-2457`）

```python
    except _Halt as exc:
        if ctx.led is not None:
            ctx.led.status = "halted"
            _book_halt(ctx, exc.kind, str(exc))
        return ctx.end(f"测试设计任务中止：{exc}")
    except Exception as exc:                       # A3：其余异常收敛 halted，不炸图
        logger.exception("case_design 驱动失败")
        if ctx.led is not None:
            ctx.led.status = "halted"
            _book_halt(ctx, "transient", f"内部错误：{exc}")
        return ctx.end(f"测试设计任务中止（内部错误：{exc}）")
```

- [ ] **Step 7: graph 测试补一条子断言**（`test_case_design_graph.py:154` 同一行扩写，别动 146/147）

```python
    assert (led is not None and led.status == "halted" and led.cursor["nudge"] == 3
            and led.data["halt"]["kind"] == "artifact_retry" and led.data["halt"]["stage"] == "plan")
```

- [ ] **Step 8: 跑绿 + 全量门禁 + 提交**

```bash
git commit -m "feat(case-design): _Halt 带族属、十个抛点自标、_book_halt 单点落 halt 现场（裁定 43）"
```

---

### Task T29: `_wants_restart` 词表判重开

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（`_RETRY_WORDS` 附近新增词表与函数）
- Test: `backend/tests/test_case_design_driver.py`

**Interfaces:**
- Consumes: `_approval_clause`（`:2119`，唯一实现处）。
- Produces: `_wants_restart(text: str) -> bool`、`_RESTART_WORDS: tuple[str, ...]`。

- [ ] **Step 1: 写失败测试**

```python
def test_wants_restart_truth_table():
    """裁定 46：重开＝销毁现场，判据只许偏「少销毁」。分句规则复用批准那一族，不写第二份。"""
    from aitester.case_design.stages import _wants_restart
    for text, expected in (
        ("重开任务", True),
        ("作废这次，重新按新需求来", True),
        ("从头开始跑一遍", True),
        ("换新任务，按 notes.md 来", True),
        ("放弃本次", True),
        ("别重开", False), ("先不重开", False), ("未重开的意思", False),
        ("整体看没什么问题，重开就不必了", False),        # 后半分句自带「不」→ 整条否决
        ("继续", False), ("同意通过", False), ("", False),
        ("这条链路重开了新市场", True),                   # 已登记的误判面（裁定 46）：钉住现状
    ):
        assert _wants_restart(text) is expected, text


def test_wants_restart_negation_only_kills_the_matching_clause():
    """W3-2 的教训反向复用：前一分句的「没」不许连坐后一分句的明示重开。"""
    from aitester.case_design.stages import _wants_restart
    assert _wants_restart("没什么问题，重开任务吧") is True
```

- [ ] **Step 2: 跑红** → **Step 3: 实现**（放在 `_writeback_authorized` 之后、`_is_bare_authorization` 之前）

```python
# 重开措辞（裁定 46）：销毁现场是不可逆动作，判据与批准同一条形状——某分句含措辞且该分句无否定/延后标记。
_RESTART_WORDS: tuple[str, ...] = ("重开", "重新开始", "从头开始", "从零开始", "作废",
                                   "放弃这次", "放弃本次", "开新任务", "换新任务")


def _wants_restart(text: str) -> bool:
    """本轮人话是否明示「作废这次工作、开新任务」。未命中一律续跑——方向偏「少销毁」。

    与批准共用 `_approval_clause`（裁定 19／R-27／W3-2 同族）：一条措辞规则只有一个实现处。
    误判面如实登记：「重开」在无关语境里出现会被判成重开（真值表钉住），走查五实测第 ④ 判据。
    """
    return _approval_clause(text, _RESTART_WORDS)
```

- [ ] **Step 4: 跑绿 + 提交** — `git commit -m "feat(case-design): _wants_restart 复用分句授权规则判明示重开（裁定 46）"`

---

### Task T30: `_boot` 分流——`done`/`halted` 分家 + 四族续法

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（`Ctx` 加字段、`_boot`、新增 `_restart`/`_resume_halted`/`_reset_gate_unclear`/`_needs_input_text`、驱动 `_boot` 调用点）
- Test: `backend/tests/test_case_design_driver.py`、`backend/tests/test_case_design_writing_stages.py`（`carried_stale` 与归档相关既有断言的归属核对）

**Interfaces:**
- Consumes: T28 的 `data["halt"]["kind"]`、T29 的 `_wants_restart`。
- Produces: `Ctx.halt_refusal: str`（非空＝本轮一步都不许走，驱动据此直接终局）、`_restart(ctx, old) -> Ledger`、`_resume_halted(ctx) -> bool`。

- [ ] **Step 1: 写失败测试（四条，覆盖四族＋重开）**

```python
def test_halted_new_turn_resumes_without_archiving(tmp_path):
    """裁定 42 的正身：中断后再发一句不销毁现场。走查四为这条赔了 572 call。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))                 # → halted
    assert Ledger.load(env.design).data["counters"] == {"chain": 0, "story": 0, "point": 0, "case": 0}

    turn = _drive(env, {"messages": [HumanMessage("换个说法再试")], "case": {}}, ScriptTask())
    assert turn["case"]["route"] == "agent"                          # 续跑：重新下发，不是重开账本
    assert not (env.design / "archive").exists()                     # 现场一个文件都没搬走
    assert (env.design / "plan.json").is_file()
    led = Ledger.load(env.design)
    assert led.status == "active" and led.cursor["stage"] == "plan"
    assert led.cursor["nudge"] == 0 and led.cursor["asked"] is False  # 该重开的预算重开
    assert any(h.startswith("resumed-from-halted@") for h in led.data["history"])


def test_halted_new_turn_with_explicit_restart_archives(tmp_path):
    """明示重开＝今天那条路：归档＋fresh＋carried_stale 携带，一并保住既有语义。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    _append(state, _drive(env, state, ScriptTask()))
    simulate(env, plan={"task_kind": "design", "entry_layer": "chain", "terminal_layer": "chain",
                        "target_subtree": "", "source_files": [], "note": "只链层"})
    _append(state, _drive(env, state, ScriptTask()))                 # gen 阶段推进
    led = Ledger.load(env.design)
    led.status = "halted"
    led.data["halt"].update({"kind": "artifact_retry", "stage": led.cursor["stage"],
                             "layer": led.cursor["layer"], "block": led.cursor["block"],
                             "round": int(led.cursor["round"] or 0), "reason": "手搭", "count": 1})
    led.layer("story")["state"] = "stale_pending"
    led.save()

    _drive(env, {"messages": [HumanMessage("作废这次，重开任务")], "case": {}}, ScriptTask())
    assert (env.design / "archive").is_dir()
    fresh = Ledger.load(env.design)
    assert fresh.data["task"] == {} and fresh.data["carried_stale"] == ["story"]


def test_human_wait_halt_resume_goes_back_to_the_gate(tmp_path):
    """门连续待决而 halted 后，一句「同意通过」必须从 gate_interpret 接上放行，不是重烧设计环。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    drain(env, kb, ScriptTask())                                     # 跑到 awaiting_review
    for _ in range(4):                                               # NUDGE_CAP=3 → 第 4 次待决 halted
        _drive(env, {"messages": [HumanMessage("我再想想")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "halted" and led.data["halt"]["kind"] == "human_wait"
    assert led.cursor["stage"] == "gate_interpret" and led.data["gate"]["unclear"] == 4
    assert kb.upserts == []

    turn = _drive(env, {"messages": [HumanMessage("同意通过")], "case": {}}, ScriptTask())
    assert turn["case"]["route"] == "end"
    assert not (env.design / "archive").exists()
    led = Ledger.load(env.design)
    assert led.status == "done" and kb.upserts                        # 解读 1 call → 回写，零重烧
    assert led.data["gate"]["unclear"] == 0


def test_needs_input_halt_refuses_and_costs_nothing(tmp_path):
    """裁定 44 的反面：同游标重放必再炸的那族，续跑一步都不许走——零转场、零子调用、现场不动。"""
    kb = StubKb(layers={"chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1}],
                        "story": [], "point": []})
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage("只更新 ch-9999")], "case": {}}
    _append(state, _drive(env, state, task))
    simulate(env, plan={"task_kind": "design", "entry_layer": "story", "terminal_layer": "point",
                        "target_subtree": "ch-9999", "source_files": [], "note": "窄任务"})
    frames: list[dict] = []
    _append(state, _drive(env, state, task, writer=frames.append))
    led = Ledger.load(env.design)
    assert led.status == "halted" and led.data["halt"]["kind"] == "needs_input"
    assert "在链路树里不存在" in _end_text(frames) and "重开任务" in _end_text(frames)

    calls, upserts = len(task.calls), len(kb.upserts)
    frames2: list[dict] = []
    _drive(env, {"messages": [HumanMessage("继续")], "case": {}}, task, writer=frames2.append)
    assert len(task.calls) == calls and len(kb.upserts) == upserts     # 判据 ① 的离线同型
    led2 = Ledger.load(env.design)
    assert led2.status == "halted" and led2.data["halt"]["count"] == 2  # 拒绝不改状态、只说实话
    assert not (env.design / "archive").exists()


def test_halt_count_tracks_same_spot(tmp_path):
    """同一处停两次，count 必须说真话——终帧那句「第 N 次」是给人的成本读数（裁定 18 可见性同族）。
    本条从 T28 移来：只有续跑真的保住账本（T30），第二次 halt 才会落在同一处（红证归属见 T28 注）。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(2):                                   # 两整轮「重问到超限」
        for _ in range(4):
            _append(state, _drive(env, state, ScriptTask()))
            simulate(env, plan=bad)
        _append(state, _drive(env, state, ScriptTask()))    # → halted
        state = {"messages": [HumanMessage("再试一次")], "case": {}}   # 新回合＝续跑（本任务前是归档）
    halt = Ledger.load(env.design).data["halt"]
    assert halt["kind"] == "artifact_retry" and halt["count"] == 2
```

- [ ] **Step 2: 跑红**（五条同时红；记录每条的红证形态进报告）。
- [ ] **Step 3: `Ctx` 加字段**（`stages.py:84` `transitions: int = 0` 之后）

```python
    halt_refusal: str = ""       # 非空＝本轮一步都不许走（needs_input，裁定 44），驱动直接终局
```

- [ ] **Step 4: 抽出 `_restart` 并写续跑分流**（放在 `_boot` 之前）

```python
def _restart(ctx: Ctx, old: Ledger) -> Ledger:
    """销毁现场、开新账（裁定 42 里唯一该归档的那条路）：done 的新回合，或 halted 被明示重开。"""
    carried = [l for l in LAYERS if old.layer(l)["state"] == "stale_pending"]
    _archive(ctx.env)
    led = Ledger.fresh(ctx.env.design)
    led.data["carried_stale"] = carried
    ctx.led = led
    return led


def _reset_gate_unclear(led: Ledger, stage: str) -> None:
    """续跑复位待决计数（裁定 44）：只复位游标那道门的那一个，另一道门的账不许顺手洗白。"""
    if stage == "gate_interpret":
        led.data["gate"]["unclear"] = 0
    elif stage == "case_gate_interpret":
        led.data["writing"]["gate"]["unclear"] = 0


def _resume_halted(ctx: Ctx) -> bool:
    """halted 的新回合（裁定 42/44）：默认从断点续跑。返回 False＝本轮一步都不该走。

    续跑复用 `_go` 是因为它清整张游标：nudge/asked 归零（该重开的重开），
    而 round 由调用方**原样传回**——断一次白送 5 轮就是换个姿势重烧（裁定 44）。
    """
    led = ctx.led
    halt = led.data["halt"]
    if _wants_restart(_human_text(ctx.state_messages)):
        new = _restart(ctx, led)
        new.data["history"].append(f"restarted-from-halted@{_now()}")
        return True
    kind = str(halt.get("kind") or "transient")
    if kind == "needs_input":
        ctx.halt_refusal = _needs_input_text(halt)
        return False
    cur = dict(led.cursor)
    _go(ctx, cur["stage"], layer=cur["layer"], block=cur["block"],
        round=int(cur["round"] or 0), source=str(cur.get("source") or "block"))
    if kind == "human_wait":
        _reset_gate_unclear(led, cur["stage"])
    elif kind in ("artifact_retry", "transient"):
        led.data["halt"]["resume_note"] = _human_text(ctx.state_messages)   # 裁定 45，T31 消费
    led.data["history"].append(f"resumed-from-halted@{_now()} kind={kind}")
    return True
```

- [ ] **Step 5: 重写 `_boot` 的 fresh 块**——**关键纪律：`fresh` 块内一律用 `ctx.led`，不许再用局部 `led`**（`_restart` 会换绑账本，局部变量会把旧字典盖回新账本——这条是本片最容易踩的坑，测试 `test_halted_new_turn_with_explicit_restart_archives` 钉它）。

```python
    if fresh:
        ctx.led.data["halt"]["resume_note"] = ""       # 补充语只活一个回合（裁定 45）
        if ctx.led.status == "done":
            _restart(ctx, ctx.led)
        elif ctx.led.status == "halted":
            if not _resume_halted(ctx):
                ctx.led.save()                          # needs_input：状态原样 halted
                return
        elif ctx.led.status == "awaiting_review":
            if ctx.led.data["writing"].get("status") == "awaiting_review":
                _go(ctx, "case_gate_interpret")
            else:
                _go(ctx, "gate_interpret")
        elif ctx.led.status == "writeback_failed":
            # （B-F1 注释与两条分支原样保留，只把 led 换成 ctx.led）
            ...
        elif ctx.led.status == "active" and loaded:
            # （B-F2/R-31 注释与 history 追加原样保留）
            ...
        ctx.led.status = "active"
    ctx.led.save()
```

- [ ] **Step 6: 驱动调用点加拒绝出口**（`stages.py:2436` 之后）

```python
        _boot(ctx, fresh)
        if ctx.halt_refusal:
            return ctx.end(ctx.halt_refusal)            # 零转场、零子调用（裁定 44）
```

- [ ] **Step 7: `_needs_input_text` 先给最小可用版**（T32 再补 halt 帧尾巴；此函数放 `_resume_halted` 之后）

```python
def _needs_input_text(halt: dict) -> str:
    """停在「续跑也无解」那一族：终帧说实话——停在哪、为什么、要人先做什么、本轮什么都没花。"""
    where = f"{halt.get('stage') or '-'}/{halt.get('layer') or '-'}/{halt.get('block') or '-'}"
    return "\n".join([
        f"测试设计任务停在 {where}（第 {int(halt.get('round') or 0)} 轮）：{halt.get('reason') or ''}",
        f"这是第 {int(halt.get('count') or 1)} 次停在同一处。这类停顿续跑也无解——"
        "请先修正上面的业务信息或目标范围，然后明说「重开任务」开启新任务。"
        "本轮未做任何生成、未写入知识库、未归档现场。",
    ])
```

- [ ] **Step 8: 跑绿 + 全量门禁**（`test_halt_count_tracks_same_spot` 到此处必须转绿——若仍红，说明续跑没保住账本，回到 Step 5 查局部变量残留）。既有断言核对：`test_case_design_driver.py:670`（`carried_stale`）与 `test_case_design_writing_stages.py:147/299/337`（归档）都走 `done` 或显式 `_archive`，**不应改动**；若某条因分家而红，报出来由控制方裁，不许改断言迁就。
- [ ] **Step 9: 提交** — `git commit -m "feat(case-design): _boot 把 halted 与 done 分家——默认断点续跑、四族分流（裁定 42/44）"`

---

### Task T31: `Ctx.ask` 续跑注入单点（【人工补充】用后即清）

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py:119-127`
- Test: `backend/tests/test_case_design_driver.py`

**Interfaces:**
- Consumes: `halt["resume_note"]`（T30 写入）。
- Produces: 续跑后第一条下发的文本末尾含 `【人工补充】<本轮人话>`，且下发后 `halt["resume_note"] == ""`。

- [ ] **Step 1: 写失败测试**

```python
def test_resume_note_attaches_to_first_ask_then_clears(tmp_path):
    """裁定 45：_gen_text 一类指令纯确定性拼装、根本不读人话——不带这句就是同一指令的确定性重放。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))                    # halted

    note = "链路按下单／售后拆成两条再试"
    turn = _drive(env, {"messages": [HumanMessage(note)], "case": {}}, ScriptTask())
    assert f"【人工补充】{note}" in turn["messages"][0].content
    assert Ledger.load(env.design).data["halt"]["resume_note"] == ""    # 用后即清
    _append(state, turn)
    turn2 = _drive(env, state, ScriptTask())                            # 同一回合的后续重问
    assert "【人工补充】" not in turn2["messages"][0].content


def test_resume_note_does_not_cross_turns(tmp_path):
    """补充语只活一个回合：下一回合即便还停在同一处，也不许把上一轮的人话再附一遍。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask()))
    _append(state, _drive(env, {"messages": [HumanMessage("换个说法")], "case": {}}, ScriptTask()))
    led = Ledger.load(env.design)
    assert led.data["halt"]["resume_note"] == ""                        # 首问已消费
    turn = _drive(env, {"messages": [HumanMessage("再想想")], "case": {}}, ScriptTask())
    assert "【人工补充】" not in turn["messages"][0].content
    assert len([m for m in turn["messages"] if "换个说法" in str(m.content)]) == 0
```

- [ ] **Step 2: 跑红** → **Step 3: 实现**（`ask` 头部两行，其余不动）

```python
    def ask(self, text: str, cap: int = NUDGE_CAP) -> HumanMessage:
        """带重试上限的下发：首问只置 asked，重问加 nudge；用尽即中止（plan/gen 3、opt/attr 2）。"""
        cur = self.cur
        assert self.led is not None
        note = str(self.led.data["halt"].get("resume_note") or "")
        if note:                                         # 续跑注入的唯一落点（裁定 45）：用后即清
            self.led.data["halt"]["resume_note"] = ""
            text = f"{text}\n【人工补充】{note}"
        if cur.get("asked"):
            ...
```

- [ ] **Step 4: 跑绿 + 提交** — `git commit -m "feat(case-design): Ctx.ask 单点注入续跑补充语，用后即清（裁定 45）"`

---

### Task T32: 终帧说实话——halt 帧尾巴 + 族属中文

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py`（新增 `_halt_frame`，两处 `ctx.end` 用它）
- Test: `backend/tests/test_case_design_driver.py`、`backend/tests/test_case_design_graph.py:146/182`

**Interfaces:**
- Consumes: `data["halt"]`（T28）、`HALT_KIND_CN`（T27）。
- Produces: 中止终帧 = 基句 `测试设计任务中止：{reason}` ＋换行尾巴（停在哪儿、第几次、续跑/重开各是什么后果）。

- [ ] **Step 1: 写失败测试**

```python
def test_halt_frame_tells_where_it_stopped_and_what_next(tmp_path):
    """W4-2 的同族要求：终帧是用户唯一读到的话，不许只说「中止」而不说下一步的代价。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = {"messages": [HumanMessage("生成测试设计")], "case": {}}
    bad = {"task_kind": "x", "entry_layer": "chain", "terminal_layer": "point",
           "target_subtree": "", "source_files": [], "note": ""}
    frames: list[dict] = []
    for _ in range(4):
        _append(state, _drive(env, state, ScriptTask()))
        simulate(env, plan=bad)
    _append(state, _drive(env, state, ScriptTask(), writer=frames.append))
    text = _end_text(frames)
    assert text.startswith("测试设计任务中止：plan/-/- 重试超限")        # 基句逐字不动
    assert "现场已保留" in text and "制品反复不合法" in text
    assert "再发一句将从这里续跑" in text and "重开任务" in text
```

- [ ] **Step 2: 跑红** → **Step 3: 实现**（`_needs_input_text` 之后）

```python
def _halt_frame(ctx: Ctx, base: str) -> str:
    """中止终帧：基句一字不动（既有逐字断言），尾巴说实话＋给下一步（裁定 42/47、W4-2）。"""
    halt = ctx.led.data["halt"] if ctx.led is not None else {}
    if not halt.get("kind"):
        return base
    where = f"{halt['stage']}/{halt['layer'] or '-'}/{halt['block'] or '-'}"
    return (f"{base}\n现场已保留（停在 {where} 第 {halt['round']} 轮，"
            f"第 {halt['count']} 次停在这一处；{HALT_KIND_CN.get(halt['kind'], halt['kind'])}）。"
            "再发一句将从这里续跑；要放弃这次工作请明说「重开任务」。")
```
两处 `ctx.end(f"测试设计任务中止…")` 改为 `ctx.end(_halt_frame(ctx, f"测试设计任务中止：{exc}"))` / `...（内部错误：{exc}）`。`_Halt` 的 `HALT_KIND_CN` 需要 import（`constants` 那行加）。

- [ ] **Step 4: 更新两处全等断言**（`test_case_design_graph.py:146` 与 `:182`）——基句改 `startswith` 钉住，新增尾句存在性断言；**除此之外不许动那两个测试的其他行**。

```python
    assert turns[-1]["text"].startswith("测试设计任务中止：plan/-/- 重试超限")
    assert "现场已保留" in turns[-1]["text"]
```

- [ ] **Step 5: 跑绿 + 全量门禁 + 提交** — `git commit -m "feat(case-design): 中止终帧说实话——基句不动、补「停在哪儿＋续跑还是重开」（裁定 47）"`

---

### Task T33: 离线端到端 + 全量门禁 + 推送

**Files:**
- Test: `backend/tests/test_case_design_e2e.py`（追加两条）
- Modify: `docs/superpowers/specs/…design.md` 的「走查记录」不动；计划自身勾选

**Interfaces:**
- Consumes: T27–T32 全部。
- Produces: 一条「首建跑到门 → 门待决 halted → 明示批准续跑闭合」的整链证据，一条「needs_input 拒绝 → 明示重开 → 新账本跑通」的整链证据。

- [ ] **Step 1: 追加 e2e 两条**（drain 级为主；图级两回合只有在同 `thread_id` 能取回上一回合 state 时才写，否则在报告里说明「本仓 checkpointer 形态不支持」并以 drain 级为准——**不许为此改生产码或伪造断言**）

```python
def test_e2e_gate_halt_then_resume_closes_without_reburn(tmp_path):
    """第五片整链：门待决用尽 → halted → 「同意通过」续跑 → 回写 → done，现场全程不归档。"""
    kb = StubKb()
    env = _env(tmp_path, kb)
    state = drain(env, kb, ScriptTask())                    # 到 awaiting_review
    for _ in range(4):
        _drive(env, {"messages": [HumanMessage("我再想想")], "case": {}}, ScriptTask())
    assert Ledger.load(env.design).status == "halted"
    outline_before = (env.design / "outline.md").read_bytes()
    _drive(env, {"messages": [HumanMessage("同意通过")], "case": {}}, ScriptTask())
    led = Ledger.load(env.design)
    assert led.status == "done" and kb.upserts
    assert (env.design / "outline.md").read_bytes() == outline_before   # 大纲没被重生成
    assert not (env.design / "archive").exists()


def test_e2e_needs_input_refuse_then_restart_replans(tmp_path):
    """拒绝轮零调用，重开轮才重新计划——两句话的区别必须由账本说话，不是靠文案（判据 ①④ 离线同型）。"""
    kb = StubKb(layers={"chain": [{"id": "ch-0001", "type": "chain", "parent": "", "level": 1}],
                        "story": [], "point": []})
    env = _env(tmp_path, kb)
    task = ScriptTask()
    state = {"messages": [HumanMessage("只更新 ch-9999")], "case": {}}
    _append(state, _drive(env, state, task))
    simulate(env, plan={"task_kind": "design", "entry_layer": "story", "terminal_layer": "point",
                        "target_subtree": "ch-9999", "source_files": [], "note": "窄任务"})
    frames: list[dict] = []
    _append(state, _drive(env, state, task, writer=frames.append))
    led = Ledger.load(env.design)
    assert led.status == "halted" and led.data["halt"]["kind"] == "needs_input"
    calls = len(task.calls)

    _drive(env, {"messages": [HumanMessage("继续")], "case": {}}, task)             # 拒绝轮
    assert len(task.calls) == calls and kb.upserts == []                            # 零子调用、零写入
    assert not (env.design / "archive").exists()                                    # 现场一个文件没搬
    assert Ledger.load(env.design).status == "halted"

    _drive(env, {"messages": [HumanMessage("重开任务，按链路树全量来")], "case": {}}, task)
    assert (env.design / "archive").is_dir()                                        # 只有这句才销毁现场
    assert Ledger.load(env.design).data["task"] == {}                               # 新账本等待 h_plan
```
- [ ] **Step 2: 全量门禁** — `backend/.venv/Scripts/python -m pytest -q`，记录读数（预期 868 + 本片新增 ≈ 15–18 条）；纯净树复现一次（裁定 23）。
- [ ] **Step 3: 计划文本回填**：本文件所有已完成 step 勾 `[x]`，头部补「执行状态」一行（提交号／门禁读数）。
- [ ] **Step 4: 提交并推送** — `git push origin master:main`（本仓提交与推送常授权，`project-repo-remote`）。

---

### Task T34: 付费走查五（真机断点续跑）

**Files:**
- Create: `D:/tmp/walkthrough_case/w5_*.py|json|md`（探针工作区，**不进仓库**）
- Modify: `docs/superpowers/specs/2026-10-05-case-design-loop-design.md`（「走查五」小节，读数照实回填）

**Interfaces:**
- Consumes: T33 后的 origin/main。
- Produces: 五条判据的实测读数 + 清场回基线证据。

- [ ] **Step 1: 起隔离实例（端口 8013）前先跑免费预检**：`REME_KNOWLEDGE_BASES_DIR=D:/tmp/walkthrough_case/kb`、`KB_ID=case_probe`、`env -u DASHSCOPE_API_KEY KB_EMBEDDING_API_KEY=`（零向量调用）；**付费 key 在 `backend/data/model_config.json`，撤 env 不省钱**（走查四实测，别再报一遍省钱）。探针项目新建（不复用已删的 `proj_dfc5374e`），用户项目 `debug1` 不动。KB 基线：先按 `baseline/kb-before-walk5.md5` 落前置基线（沿用走查四的 26 文件根，判定用 `md5sum -c` 在基线根里跑）。
- [ ] **Step 2: 判据① `needs_input` 零成本拒绝**：发一句 `target_subtree` 指向不存在链路的窄任务 → 必 `halted`；核对账本 `halt.kind=="needs_input"`、`count==1`、SSE `^event: call` 计数增量；再发「继续」→ 核对终帧文案、`design/` 未归档、**call 计数增量为 0**、`count==2`。
- [ ] **Step 3: 判据② `human_wait` 断点续跑**：跑一次窄设计任务到大纲门（成本照实记），连发 4 句无意见无批准 → `halted`；再发走查四那句长句「整体看没什么问题，同意通过」→ 核对：游标从 `gate_interpret` 接上、`gate.unclear` 归 0、回写发生、`status=done`、**归档目录不存在**、本轮 call 增量 ≈ 解读 1＋回写 0（与走查四批准轮的 1 call 对照）。
- [ ] **Step 4: 判据③ `artifact_retry`**：真机自然出现重试超限才测「续跑首轮下发带【人工补充】」；未出现 ⇒ **照实报「真机未触发」**＋机制归因（离线 `test_resume_note_*` 为准），**不许伪造坏草稿凑触发**（R-56 纪律）。
- [ ] **Step 5: 判据④ 重开词表**：发「作废这次任务，重新按新需求来」→ 归档＋新账本＋`carried_stale` 核对；另发一句歧义句（「这条链路重开了新市场」）实测方向，读数登记（裁定 46 的误判面）。
- [ ] **Step 6: 判据⑤ 真实 KB 与清场**：真实 KB 1803 文件当日 0 改动（mtime 口径）＋探针 KB 根 `md5sum -c` 逐文件对基线；停 8013（命令行核对后再 `taskkill //PID <pid> //F`）、删探针项目并核对 `projects.json`/`sessions/index.json` 回基线 md5、用户 8000/5173 未动。
- [ ] **Step 7: 读数回填与推送**：spec 新增「### 走查五（第五片，2026-10-08 付费真机）」段（五条判据逐条实测／未触发照实），计划勾选执行状态；`git push origin master:main`。

**走查纪律（照抄走查四的三条教训）**：① 起流只用一条通道，`nohup curl &` 看不到输出文件 ≠ 没发出去，**补发会双发**（走查四为此白烧 38 call）；② 会话落盘 `backend/data/sessions/<id>.jsonl` 是终帧与调用数的第二证据源；③ 付费轮之前所有可免费的核对（路由/账本形态/文件落点）先在离线证据里做完。

---

## Self-Review（控制方 2026-10-08 拆计划期自查）

- **裁定覆盖**：42→T30（分家＋默认续跑）；43→T27（节形状）＋T28（族属与落盘，含「轮次用尽不属 halt」的预检纠正）；44→T30（四族分流、`round` 保留、`unclear` 只复位对应门、`needs_input` 零调用）；45→T30 写入＋T31 消费＋生命周期两条测试；46→T29；47→T30（不启用 interrupted／不动 `_ARCHIVE_ITEMS`）＋T32（不动 hard 门禁）。走查五 ①–⑤→T34 Step 2–6。**无未落任务的裁定**。
- **占位符扫描**：仅 T34 Step 6 的 `<pid>`（真机 PID 只能在运行时取）与 T33 第二条 e2e 的 `...`（该条与 T30 `test_needs_input_halt_refuses_and_costs_nothing` 同形，实施时按那条的断言集扩写并**逐条列出**，不许只抄不写——已在步骤里写明）。其余步骤均有可执行代码。
- **类型/命名一致性**：`_Halt(message, kind)` 二参（T28 定义、T28 全抛点使用）；`data["halt"]` 九个键名在 T27 定、T28/T30/T31/T32 读；`_backfill_ledger_slices` 由 T27 改名，`Ledger.load` 同步；`_restart`/`_resume_halted`/`_reset_gate_unclear`/`_needs_input_text`/`_halt_frame`/`Ctx.halt_refusal`/`_wants_restart` 的签名在各自任务「Interfaces」里唯一命名，无第二种写法散落。
- **已知风险三条**（派发时必须带上，别当未知）：① `_boot` 的 `fresh` 块残留局部 `led` 会把旧账本盖回新账本（T30 Step 5 明令改用 `ctx.led`，测试 `..._explicit_restart_archives` 钉住）；② `human_wait` 复位必须按游标 stage 取对应那道门，写死 `gate` 会让末门续跑永远再 halted（T30 Step 4 注释＋`_reset_gate_unclear` 两支）；③ 改终帧文案会撞两处全等断言（T32 Step 4 已点名，别在别的测试里扩大改动面）。
