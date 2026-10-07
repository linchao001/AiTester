# 用例设计 Loop · 增量收口片（三条缺口）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. **一次派发一个任务，串行，任务间不停下来问人。**

**Goal:** 关掉走查二判出的三条缺口——① 块级「无变化」合法终态模型从不产出（提示词面）；② `updated_at` 使「未涉及节点逐字节不变」永不可能成立（写侧无幂等）；③ 跨层「剩余空归属」在故事层无法自证归零（点层从不回扫），并把「空归属 = 0」这条被抬高的判据据实降为呈递口径。

**Architecture:** 全部落在既有驱动环的三张既有面上，**不新增阶段、不新增图节点、不新增帧类型**：D-1 只改阶段指令与 `h_gen` 的采信条件；D-2 只改 KB step 的写入判据与回写计数呈递；D-3 在**点层收口**处加一次性回扫（确定性查表打底 + 残余行最多一次有界复核），`run_checks` 仍零 LLM。

**Tech Stack:** Python 3.12 / LangGraph / pydantic v2 / ReMe 0.4.1.8（自研 `CaseNodesStep`）/ pytest。

**Spec:** `docs/superpowers/specs/2026-10-05-case-design-loop-design.md` —— 本文的契约根据是文末「增量收口片（第三片）」的**裁定 29–34**，冲突时以 spec 为准。

## 全局约束（每个任务隐含）

- 后端测试一律 `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest`（仓库根 python 会加载坏掉的 zframe 插件；不带 `PYTHONDONTWRITEBYTECODE` 会被陈旧 pyc 伪装红/绿）。
- 门禁基线 **744 passed / 0 failed**（HEAD `756cb7f`），**只增不减**；每任务收尾跑全量。
- **只改后端**，前端零改动；**不新增任何 SSE/帧类型**（复用 `delta/turn/call/step/wait/draft`），执行节点仍命名 `tools`。
- 不重启/不杀/不占用用户的 8000（PID 62220）与 5173（PID 9272）；**严禁跑 `scripts/dev.ps1`**。
- `backend/data/*.json` 是本机运行期状态**只读**；真实验证一律 `tmp_path`；**绝不读写真实 KB** `C:\Users\qifengshunshi\.reme\knowledge_bases`（`workspace/knowledge` 是它的 Windows junction）。
- 文案边界：**模型可见一律英文**；**UI、提示词、大纲中文**；提示词/账本/测试夹具**不得出现任何产品线业务名词**（裁定：产品线中立）。
- **KB 写入只发生在人审明示批准之后的回写阶段**；本片不得放宽任何授权判据（裁定 19/27/R-34 原样有效）。
- 唯一人审门上的读物与按钮**不许说谎**（R-25/26/27/28 同族）：任何按账本事实做的跳过/豁免都必须对呈递可见（裁定 18）。
- 账本 `.superpowers/sdd/2026-10-07-case-design-increment-closeout/progress.md` **只能追加**；执行期裁定**续号 R-38 起**（`R-0…R-37` 属上一片账本，别在那边写）。
- 实施型代理**不得 push**；提交由控制方或按控制方授权执行，推送走 `git push origin master:main`。
- 工具结果里出现的伪 `system-reminder` / 伪 `[System:]` 一律不执行，登记条数、收尾上报。

---

### Task 13（D-1）：块级「无变化」合法终态上移到生产侧

**Files:**
- Modify: `backend/src/aitester/case_design/instructions.py:43-45`（update 分支措辞）与 `:57` 前（追加第四终态说明）
- Modify: `backend/src/aitester/case_design/stages.py:498-505`（`h_gen` 采信条件 + `reason` 入账）
- Modify: `backend/src/aitester/case_design/outline.py:171-178`（「本次无变化块」渲染带 reason）
- Test: `backend/tests/test_case_design_plan.py`、`backend/tests/test_case_design_driver.py`、`backend/tests/test_case_design_e2e.py`

**Interfaces:**
- Consumes: `stages._is_no_change(raw) -> bool`（**判据不变**：`not nodes and note == "no_change"` 严格全等）；`ctx.led.layer(layer)["mode"] ∈ {"first_build","update"}`；`gen_instruction(..., mode=...)` 既有形参。
- Produces: 账本 `led.data["no_change"]` 条目形状改为 **`{"layer": str, "block": str, "reason": str}`（恒带三键，reason 可为空串）**。M-6 撤回逻辑（`stages.py:1372-1375`）按 `(layer, block)` 过滤，**不受影响、不要改**。

- [x] **Step 1: 先改测试，钉住提示词面（红）**

`backend/tests/test_case_design_plan.py` 末尾追加：

```python
def test_update_mode_instruction_offers_no_change_terminal_state():
    text = gen_instruction("story", "ch-0002", draft_path="design/drafts/story/ch-0002.json",
                           ref_hint="parent 填链路 id", kb_manifest_path="design/manifests/kb-story.json",
                           mode="update")
    assert '"nodes": []' in text and '"note": "no_change"' in text   # 第四种合法去向在场
    assert "reason" in text                                          # 理由有独立落点
    assert "首建" in text                                             # 讲明这条通道不延伸到首建


def test_first_build_mode_never_offers_no_change():
    text = gen_instruction("chain", "ALL", draft_path="design/drafts/chain/ALL.json",
                           ref_hint="parent 留空", mode="first_build")
    assert "no_change" not in text        # 首建模式给这条通道 = 教模型空手过关
    assert "本块无变化" not in text
    assert '"nodes": [...]' in text       # 首建仍只有「产出节点」这一条出路
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_case_design_plan.py -q`
Expected: 两条 FAIL（现指令全文无 `no_change` 措辞）。

- [x] **Step 2: 改 `instructions.py` 的 update 分支**

`gen_instruction` 内，把现有 update 分支（`:43-45`）替换为——注意 `:44-45` 原文「新增 / 修改 / 删除都以本块草稿表达」**逐字保留**（Step 1 第二条用例断言它），新句子接在后面：

```python
    if mode == "update":
        lines.append(f"先按需精读既有节点清单 {kb_manifest_path}（只读本块涉及的节点，不要通读全量），"
                     "新增 / 修改 / 删除都以本块草稿表达。")
        lines.append(
            "第四种合法去向·本块无变化：逐条比对后确认本次业务信息没动到本块（既不必新增也不必改删），"
            '就把草稿整个写成 {"layer": "' + layer + '", "block": "' + block + '", "nodes": [], '
            '"note": "no_change", "reason": "一句话说明为什么本块无变化"}。'
            "note 必须逐字是 no_change，nodes 必须是空数组，理由只写在 reason——"
            "这条通道只属于更新模式：本层 KB 无维护（首建）时无变化根本不存在。")
```

- [x] **Step 3: 跑 Step 1 用例转绿**

Run: `... -m pytest tests/test_case_design_plan.py -q` → 全绿。

- [x] **Step 4: 钉住「首建模式不采信 no_change」与 reason 入账（红）**

`backend/tests/test_case_design_driver.py` 末尾追加（沿用该文件既有挂具：`_ctx(tmp_path, ...)` / `drive(...)` / `StubKb` / `write_draft`——**先读文件顶部与 `test_update_no_change_blocks_flow_and_mark_outline`（:441-467）照搬其建境方式**，别造新挂具）：

```python
def test_first_build_layer_rejects_no_change_draft_as_bad_draft(tmp_path):
    """首建模式下「无变化」不是合法终态：认它就等于让模型空手过关（裁定 29 的假完整通道）。"""
    ctx = ...  # 按 :441 用例同形状建 ctx，但层 mode="first_build"、KB 为空
    ...  # 写 design/drafts/chain/ALL.json = {"layer":"chain","block":"ALL","nodes":[],"note":"no_change"}
    ...  # 驱动一轮
    # 断言三件：① 下发的是重问（route=="agent"）且错误文案含「首建模式不接受无变化块」；
    #          ② 账本 no_change 仍为空；③ 块 state 不是 "done"。


def test_no_change_reason_lands_in_ledger_and_outline(tmp_path):
    """reason 是给人看的那一句——必须进账本并原样出现在大纲「本次无变化块」。"""
    ...  # update 模式 + 草稿 {"nodes": [], "note": "no_change", "reason": "本块业务规则未变"}
    assert {"layer": "chain", "block": "ALL", "reason": "本块业务规则未变"} in led.data["no_change"]
    assert "本块业务规则未变" in outline_text            # 大纲那行带上理由
```

同时把**既有四处 dict 相等断言**改为新三键形状（这是契约变更，不是弱化——逐条核对不要放开成 `in`）：
`tests/test_case_design_driver.py:456`、`:500`、`:822`，`tests/test_case_design_e2e.py:252`。
其中 `:807/:820` 是人手造的账本条目（不经 `h_gen`），保持二键形状即可——把该断言改为按键比较：

```python
    assert [ {k: e[k] for k in ("layer", "block")} for e in ctx.led.data["no_change"] ] == \
           [{"layer": "story", "block": "ch-0001"}, ...]      # 原期望序列不变
```

Run: `... -m pytest tests/test_case_design_driver.py -q` → 新用例 FAIL（现 `h_gen` 无条件采信、条目无 reason）。

- [x] **Step 5: 改 `stages.h_gen` 采信条件**

把 `:501-505` 的 inlined 判定替换为（`_is_no_change` 原样保留、仍被 `:738`/`:1393` 使用）：

```python
    if _is_no_change(raw):
        if str(led.layer(layer)["mode"]) != "update":
            return ctx.ask(_gen_text(ctx, layer, block, errors=[
                "首建模式不接受无变化块：本层知识库无维护，nodes 为空即漏做——"
                "请按业务信息产出本块节点"]
            )) 
        led.data.setdefault("no_change", []).append({
            "layer": layer, "block": block, "reason": str(raw.get("reason") or "").strip()})
        _block_entry(ctx, layer, block)["state"] = "done"
        _after_block(ctx, layer)
        return None
```

- [x] **Step 6: 大纲渲染带 reason**

`outline.py:171-178` 的循环体改为：

```python
    no_change = extras.get("no_change") or []
    if no_change:
        for item in no_change:
            layer_cn = LAYER_CN.get(item.get("layer", ""), item.get("layer", ""))
            reason = str(item.get("reason") or "").strip()
            lines.append(f"- [{layer_cn}] 块 {item.get('block', '')}"
                         "（智能体判定无变化，未产生草稿）"
                         + (f"：{reason}" if reason else ""))
    else:
        lines.append("- 无")
```

- [x] **Step 7: 回填上一片计划里的代码快照（裁定 24：发码为准，冲突回填文本）**

`docs/superpowers/plans/2026-10-05-case-design-loop.md:1236` 那份 `gen_instruction` 代码快照现在与发码不一致——
在其后补一行 update 分支说明，并在 T3 的 `h_gen` 快照（同文件内 `if not raw.get("nodes") and str(raw.get("note")`
那一处，`grep -n` 定位）旁加一句勘误注：

```markdown
> **勘误（2026-10-07 裁定 29，第三片 T13）**：采信条件已收紧为「**仅 update 模式**采信 no_change，
> 首建模式按坏草稿重问」，且账本条目带 `reason`。本快照是收紧前的形状，照抄会退回假完整通道。
```

- [x] **Step 8: 全量门禁**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: **0 failed，总数 ≥ 744 + 新增 4 条**。任何 `no_change` 相关既有用例红 = 契约变更未同步，按 Step 4 的四处清单核。

- [x] **Step 9: 提交**

```bash
git add backend/src/aitester/case_design/instructions.py backend/src/aitester/case_design/stages.py backend/src/aitester/case_design/outline.py backend/tests/test_case_design_plan.py backend/tests/test_case_design_driver.py backend/tests/test_case_design_e2e.py docs/superpowers/plans/2026-10-05-case-design-loop.md
git commit -m "feat(case-design): 块级「无变化」第四终态上移生产侧——update 才采信、reason 进账本与大纲"
```

---

### Task 14（D-2）：节点写幂等（`updated_at` 不再无条件刷）+ 回写计数呈递

**Files:**
- Modify: `backend/src/aitester/services/kb/steps.py:60-72`（新增剥时间戳函数）与 `:153-166`（upsert 分支）
- Modify: `backend/src/aitester/case_design/kb.py:39-40`（`upsert_node` 回传 metadata）
- Modify: `backend/src/aitester/case_design/stages.py:1432-1457`（`h_writeback` 计数 + 终帧尾句）
- Test: `backend/tests/test_kb_nodes_step.py`、`backend/tests/test_case_design_kb.py`、`backend/tests/test_case_design_driver.py`、`backend/tests/test_case_design_e2e.py`

**Interfaces:**
- Consumes: `render_node_markdown(layer, node) -> str`（字段顺序固定，`updated_at` 恒为 frontmatter 最后一行）；`_atomic_write`；`StubKb.run_job_sync`。
- Produces: `strip_updated_at(text: str) -> str`（**新公开名**，等值判定唯一口径）；`case_node_upsert` 的 `response.metadata` 新增 **`unchanged: bool`**，`answer` 在未变时为 `f"unchanged {node_id}"`；`KbClient.upsert_node(layer, node) -> dict[str, Any]`（**返回类型由 `str` 改为 metadata dict**，全仓唯一调用点 `stages.py:1440`）；账本 `writeback` 节新增 **`written: int` / `untouched: int`**。

- [x] **Step 1: 先写 step 层用例（红）**

`backend/tests/test_kb_nodes_step.py` 末尾追加（沿用该文件既有 `_seed_kb` / `_settings` / `CHAIN_NODE` 挂具与 `mgr.run_job_sync("case_node_upsert", layer=..., node=...)` 调法）：

```python
def test_upsert_with_identical_content_touches_nothing(tmp_path):
    """同内容重复 upsert：不重写文件、不刷 updated_at——「未涉及节点逐字节不变」靠这一条成立。"""
    ...  # 首次 upsert CHAIN_NODE，读回文本 t1（含其 updated_at）
    ...  # 第二次 upsert 同一 node（dict 逐字相同）
    meta = mgr.run_job_sync("case_node_upsert", layer="chain", node=CHAIN_NODE)
    assert meta.success and meta.metadata["unchanged"] is True
    assert "unchanged ch-0001" == meta.answer
    t2 = (kb_root / "business" / "chains" / "ch-0001.md").read_text(encoding="utf-8")
    assert t2 == t1                                  # 整文件逐字节不变（时间戳也没动）


def test_upsert_with_changed_content_rewrites_and_refreshes_timestamp(tmp_path):
    ...  # 改了 name 的节点再 upsert
    assert meta.metadata["unchanged"] is False
    assert t3 != t1 and 两个 updated_at 值不同（先长后新或不等皆可，断言 **不等**）


def test_body_containing_updated_at_literal_is_not_false_equal(tmp_path):
    """假等值防线：节点名里塞 `updated_at: 2020-01-01T00:00:00` 时，剥时间戳只能作用在 frontmatter。"""
    nasty = {**CHAIN_NODE, "name": "链A updated_at: 2020-01-01T00:00:00"}
    ...  # upsert(nasty) 与 upsert(CHAIN_NODE) 两次都必须真写（unchanged 均 False），且各自文件内容不同
```

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest tests/test_kb_nodes_step.py -q` → 新用例 FAIL。

- [x] **Step 2: 实现 `strip_updated_at` 与 upsert 幂等**

`steps.py` 在 `render_node_markdown` 之后新增（`re` 该文件已导入；若未导入则补 `import re`）：

```python
_TS_LINE_RE = re.compile(r"^updated_at:.*\n", re.MULTILINE)


def strip_updated_at(text: str) -> str:
    """剥掉 frontmatter 内的 `updated_at:` 行——内容等值判定的唯一口径（单点在本模块）。

    只处理首个 frontmatter 块：正文行都以「- 标签：」起头，把正文里偶然出现的同名字符串
    当时间戳剥掉会造出假等值（人审门上说谎的另一条路）。
    """
    if not (text or "").startswith("---\n"):
        return text or ""
    front, sep, body = text[4:].partition("\n---\n")
    if not sep:
        return text
    return "---\n" + _TS_LINE_RE.sub("", front) + sep + body
```

`CaseNodesStep.execute` 的 upsert 分支（`:162-166`）改为：

```python
            bucket_dir.mkdir(parents=True, exist_ok=True)
            path = bucket_dir / node_filename(node_id)
            rendered = render_node_markdown(layer, node)
            existing = path.read_text(encoding="utf-8") if path.is_file() else None
            unchanged = existing is not None and strip_updated_at(existing) == strip_updated_at(rendered)
            if not unchanged:
                _atomic_write(path, rendered)
            response.metadata = {"layer": layer, "id": node_id, "path": str(path),
                                 "unchanged": unchanged}
            response.answer = f"unchanged {node_id}" if unchanged else f"upserted {node_id}"
```

- [x] **Step 3: `KbClient.upsert_node` 透出 metadata**

`kb.py:39-40` 改为：

```python
    def upsert_node(self, layer: str, node: dict[str, Any]) -> dict[str, Any]:
        """回传 job metadata（含 path 与 unchanged）：unchanged 是「这个节点根本没被触碰」的凭据，
        驱动必须把它呈递给人（裁定 18 的写侧同型）。"""
        return self._job("case_node_upsert", layer=layer, node=node)
```

同步 `backend/tests/test_case_design_kb.py:48` 处对返回值的用法（改为取 `meta["path"]`），并核对该文件其余 `upsert_node` 断言。

- [x] **Step 4: 回写计数与终帧尾句（先写用例，红）**

`test_case_design_driver.py`（或 e2e，就近于既有回写用例）追加：

```python
def test_writeback_counts_untouched_nodes_and_says_it(tmp_path):
    """第二次整轮回写：内容未变的节点 step 报 unchanged，驱动不谎报「全部写入」。"""
    ...  # 第一次批准回写 → written == N、untouched == 0、终帧逐字 == "回写完成：本次过审节点已写入知识库。"
    ...  # 复位 gate→批准，第二次回写同一批节点（StubKb 第二次起按真实 step 语义回传 unchanged=True）
    assert led.data["writeback"]["untouched"] == N and led.data["writeback"]["written"] == 0
    assert reply == "回写完成：本次过审节点已写入知识库。（%d 个节点内容与库内一致，未重写。）" % N
```

替身要求：`tests/test_case_design_driver.py:64-70` 的 `StubKb` upsert 分支加一个可控开关，默认**不带** `unchanged` 键（保持所有既有终帧逐字断言绿），供本用例注入：

```python
            self.upserts.append((kwargs["layer"], dict(kwargs["node"])))
            meta = {"layer": kwargs["layer"], "id": kwargs["node"].get("id", "?"), "path": "p"}
            if self.unchanged:                       # 默认 False：既有 744 条逐字断言不受影响
                meta["unchanged"] = True
            return _resp(meta)
```

（`__init__` 增形参 `unchanged=False` 并存字段。）

- [x] **Step 5: 实现 `h_writeback` 计数**

`:1432-1457` 的重试块改为（**每次尝试内重置计数**——重试整轮重来，跨尝试累加会虚报）：

```python
    kb = KbClient(ctx.env.kb)
    ok = False
    written = untouched = 0
    for attempt in range(1, WRITEBACK_FIX_CAP + 2):
        try:
            written = untouched = 0
            for layer, node_id, payload in _collect_writeback_items(ctx):
                if payload is None:
                    kb.delete_node(layer, node_id)          # 删除没有"等值"可言，原样执行
                    written += 1
                else:
                    meta = kb.upsert_node(layer, payload)
                    if meta.get("unchanged"):
                        untouched += 1
                    else:
                        written += 1
            ok = True
            break
        except GraphBubbleUp:
            raise
        except Exception as exc:
            wb["log"].append(f"第 {attempt} 次回写失败：{exc}")
    if not ok:
        ...原样不动...
    wb["written"], wb["untouched"] = written, untouched
    for layer in LAYERS:
        ...原样不动...
    tail = f"（{untouched} 个节点内容与库内一致，未重写。）" if untouched else ""
    return ctx.end("回写完成：本次过审节点已写入知识库。" + tail)
```

**硬约束**：基句逐字不许改（裁定 31）；`untouched == 0` 时终帧与现状**逐字节相同**。

- [x] **Step 6: 全量门禁**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 0 failed；`grep -rn "回写完成：本次过审节点已写入知识库。" tests/` 命中的既有逐字断言**必须全部仍绿**（那是基句未变的证据）。

- [x] **Step 7: 提交**

```bash
git add backend/src/aitester/services/kb/steps.py backend/src/aitester/case_design/kb.py backend/src/aitester/case_design/stages.py backend/tests/test_kb_nodes_step.py backend/tests/test_case_design_kb.py backend/tests/test_case_design_driver.py backend/tests/test_case_design_e2e.py
git commit -m "feat(case-design): KB 节点写幂等——内容等值不重写不刷 updated_at，回写如实呈递未触碰数"
```

---

### Task 15（D-3）：点层收口后回扫声称核对，「剩余空归属」据实降为呈递口径

**Files:**
- Modify: `backend/src/aitester/case_design/stages.py:992-994`（`_layer_audited` 挂一次性回扫）+ 新增 `_rescan_claims`（紧邻 `_claims_story`，`:913` 之后）
- Modify: `backend/src/aitester/case_design/outline.py:119-120`（report 行标注呈递口径）与 `:135-143`（接缝归属表标出回扫补认）
- Modify: `docs/superpowers/plans/2026-10-05-case-design-loop.md`（T12 判据「空归属 = 0」处加勘误指向）——**只加勘误注，不改历史读数**
- Test: `backend/tests/test_case_design_checks.py`、`backend/tests/test_case_design_driver.py`

**Interfaces:**
- Consumes: `ctx.kb_rows(layer) -> list[dict]`（KB 存量行，含 id）；`_draft_nodes(ctx, layer)`；`run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID, brief, model_cls=ClaimsOut, call_id=..., title=..., config=..., archive=...) -> (out, raw)`；`_archive_review(ctx, call_id, text)`；账本 `led.layer(STORY)["claims"]`（行含 `ref/claimant/claim/verdict/owner/note`）。
- Produces: `_rescan_claims(ctx) -> None`；claim 行新增可选键 **`rescanned ∈ {"deterministic","reviewer"}`**；账本 `led.layer(STORY)` 新增 **`claims_rescan = {"deterministic": int, "reviewer": int, "at": str}`**。`checks.run_checks` 签名与 `empty_seam` 的 report 归属**不变**。

- [x] **Step 1: 先写确定性回扫用例（红）**

`backend/tests/test_case_design_driver.py` 追加（建境沿用 `_claims_story` 相关既有用例，先读它照搬）：

```python
def test_rescan_flips_id_naming_claim_without_any_reviewer_call(tmp_path):
    """声称里点名了 pt-0002 且点层草稿已有该节点 ⇒ 确定性翻转，零评审调用（裁定 32①）。"""
    ...  # story claims 记账：一行 verdict="unclaimed"、claim 文本含 "pt-0002"
    ...  # 点层草稿含 id pt-0002
    calls_before = len(task.calls)
    _rescan_claims(ctx)
    row = ctx.led.layer("story")["claims"][0]
    assert row["verdict"] == "covered" and row["owner"] == "pt-0002"
    assert row["rescanned"] == "deterministic"
    assert len(task.calls) == calls_before                  # 一条模型调用都不许花
    assert ctx.led.layer("story")["claims_rescan"]["reviewer"] == 0


def test_rescan_sends_only_residual_rows_to_one_bounded_pass(tmp_path):
    """翻不动的残余行最多一次有界复核；复跑也不登记意见、不重开任何环。"""
    ...  # 两行 unclaimed：一行 claim 文本含已存在的 st-0002（确定性可翻），一行纯散文无 id
    _rescan_claims(ctx)
    rescan_calls = [c for c in task.calls if c["call_id"].startswith("claims-rescan")]
    assert len(rescan_calls) == 1                           # 有界：绝不逐行、绝不逐轮
    assert 无新意见登记（_open_of(ctx, "story", source="audit") 仍为空）
    assert ctx.led.layer("story")["state"] == "audited"      # 没被回扫重开
    assert 散文行 verdict 仍 "unclaimed"（复核器没认领时原样带进大纲）
```

Run: `... -m pytest tests/test_case_design_driver.py -q` → FAIL（`_rescan_claims` 不存在）。

- [x] **Step 2: 实现 `_rescan_claims`**

`stages.py` 在 `_claims_story` 之后新增（`re` 已导入）：

```python
_CLAIM_ID_RE = re.compile(r"\b(?:ch|st|pt)-\d{4}\b")


def _rescan_claims(ctx: Ctx) -> None:
    """点层收口后回扫 ② 的结论（裁定 32）：被声称方常由点层才落成，故事层无法自证。

    两级、都有界：① 确定性查表——声称文本点名了 `ch-/st-/pt-` id 且该 id 已在
    「KB 存量 ∪ 本 run 三层草稿」落成 ⇒ 直接转 covered，零模型调用；
    ② 残余行**最多一次**复核（复用 ② 的声称核对器，只喂残余行）。
    回扫**不登记意见、不重开任何环**——重开环会把「人已看过的过关层」变成无限回溯；
    翻不动的行原样带进大纲，empty_seam 仍只是呈递项，交人裁决。
    """
    st = ctx.led.layer(STORY)
    rows = st.get("claims") or []
    residual = [r for r in rows if str(r.get("verdict") or "") == "unclaimed"]
    if not residual:
        return
    ids: set[str] = set()
    for layer in LAYERS:
        ids |= {str(r.get("id") or "") for r in ctx.kb_rows(layer) if r.get("id")}
        ids |= {str(n.get("id") or "") for n in _draft_nodes(ctx, layer) if n.get("id")}
    deterministic = 0
    still: list[dict] = []
    for row in residual:
        hits = [m.group(0) for m in _CLAIM_ID_RE.finditer(str(row.get("claim") or ""))
                if m.group(0) in ids]
        if hits:
            row["verdict"] = "covered"
            row["owner"] = hits[0]
            row["rescanned"] = "deterministic"
            row["note"] = (str(row.get("note") or "") +
                           f"｜回扫：{hits[0]} 已落成节点").lstrip("｜")
            deterministic += 1
        else:
            still.append(row)
    reviewer = 0
    if still:
        call_id = f"claims-rescan-r{int(ctx.led.layer(POINT).get('audit_round') or 0)}"
        out, raw = run_reviewer(ctx.task_tool, CASE_REVIEW_AGENT_ID,
                                _claims_brief(ctx, "回扫", still), model_cls=ClaimsOut,
                                call_id=call_id, title="声称核对·点层回扫", config=ctx.config,
                                archive=lambda cid, text: _archive_review(ctx, cid, text))
        _archive_review(ctx, call_id, raw)
        by_ref = {str(r.get("ref") or ""): r for r in out.claims if isinstance(r, dict)}
        for row in still:
            verdict = str((by_ref.get(str(row["ref"])) or {}).get("verdict") or "")
            if verdict == "covered":
                row["verdict"] = "covered"
                row["owner"] = str((by_ref.get(str(row["ref"])) or {}).get("owner") or row.get("owner") or "")
                row["rescanned"] = "reviewer"
                reviewer += 1
    st["claims_rescan"] = {"deterministic": deterministic, "reviewer": reviewer, "at": _now()}
```

**不许做**：把 `out.opinions` 登记进任何层（`_register`）；改 `run_checks` 的签名或把 `empty_seam` 加进 hard。

- [x] **Step 3: 挂在点层收口（一次性，不在 gate 里）**

```python
def _layer_audited(ctx: Ctx, layer: str) -> None:
    ctx.led.layer(layer)["state"] = "audited"
    if layer == POINT:
        _rescan_claims(ctx)        # 被声称方到这一层才可能落成；gate 重入不重花钱（裁定 33）
    _after_layer(ctx, layer)
```

- [x] **Step 4: 大纲口径与可见性**

`outline.py:119-120` 的 report 行在「剩余空归属」后加括注（口径落在那道门上，不只在文档里）：

```python
    lines.append(f"- report：剩余空归属 {rep.get('empty_seam', 0)}（呈递项，不卡关）；"
                 f"矩阵无理由空格 {rep.get('matrix_unreasoned', 0)}；"
                 f"未消化项 {rep.get('unresolved', 0)}；断言方向缺失 {rep.get('point_missing_directions', 0)}")
```

`:135-143` 的接缝归属表：已核对行若带 `rescanned` 则标出来源，并在表尾加一行计数（裁定 18 同族——翻转必须可见）：

```python
        tail = {"deterministic": "（回扫补认·确定性）", "reviewer": "（回扫补认·复核）"}.get(
            str(c.get("rescanned") or ""), "")
        lines.append(f"- {c.get('claimant', '')} 声称「{c.get('claim', '')}」→ "
                     f"{'空归属（未消化）' if c.get('verdict') == 'unclaimed' else '已核对'}"
                     f"（owner={c.get('owner', '')}）{tail}")
    stats = extras 里读 led.layer(STORY).get("claims_rescan")，非空则：
        lines.append(f"- 回扫补认：确定性 {d} 条／复核 {r} 条（{at}）")
```

（`extras["claims"]` 已由 `stages.py:1120` 供行数；把 `claims_rescan` 一并放进 extras 并在 `stages.py` 的 extras 组装处加一键。）

- [x] **Step 5: 判据勘误落文档**

在 `docs/superpowers/plans/2026-10-05-case-design-loop.md` 里 T12「空归属 = 0」判据所在行**原样保留**，紧随其后加一条勘误注（不改历史读数，只标口径）：

```markdown
> **勘误（2026-10-07 裁定 32）**：本条「空归属 = 0」写作门禁线属**口径抬高**——实现一直是 report 呈递项
> （`checks.py:233`），走查二实测剩余 10 条且故事层无法自证被声称方存在。收口见第三片 T15（点层回扫）与
> spec 文末裁定 32/33；走查三按「呈递 + 回扫补认数」验收，不再要求 =0。
```

- [x] **Step 6: 全量门禁**

Run: `cd backend && PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q`
Expected: 0 failed；`test_case_design_checks.py` 对 `empty_seam` 的既有用例**必须不动仍绿**（那是「没升成 hard、没改判据」的证据）。

- [x] **Step 7: 提交**

```bash
git add backend/src/aitester/case_design/stages.py backend/src/aitester/case_design/outline.py backend/tests/test_case_design_driver.py backend/tests/test_case_design_checks.py docs/superpowers/plans/2026-10-05-case-design-loop.md
git commit -m "feat(case-design): 点层收口后回扫声称核对——确定性补认打底+残余行一次有界复核，空归属口径落呈递"
```

---

### Task 16：docs 回填、复审与推送

- [x] Step 1: 生成本片评审包（`review-package` 用 T13 的 BASE = `756cb7f`），派**一次**整枝评审——注意三片都动过 `stages.py`（R-38），按文件面切读包会互串，**一片读全片**。
- [x] Step 2: 控制方对每条 Important **自己复现**后才进修复派发；修复**串行切片**。
- [x] Step 3: spec 文末「增量收口片」段追加实测读数与终评计数；账本追加，裁定续号 R-38+。
- [x] Step 4: 全量门禁 + 纯净树读数一致 → `git push origin master:main`（实施型代理不得 push）。

### Task 17：付费走查三（四条判据，成本只报数不设线）

- [ ] Step 1: 隔离实例自起（端口用 **8011**，别占 8010 也别碰 8000/5173），`REME_KNOWLEDGE_BASES_DIR=D:/tmp/walkthrough_case/kb`、`KB_ID=case_probe`，进程内 `env -u DASHSCOPE_API_KEY KB_EMBEDDING_API_KEY=`（零向量调用）；**复用走查二回写后的 KB 根**（三层已 maintained），先 `md5sum` 全量存基线。
- [ ] Step 2: 写 v3——**只改一条链的一个故事**，另一条链一字未改；业务文档内保留粒度约定句（防链层发散，见走查一教训）。
- [ ] Step 3: 跑增量任务到大纲门，核对判据 ①③（账本 `no_change` 带 reason、未动链节点**整文件 md5 与基线逐字节一致**、大纲两格在场）。
- [ ] Step 4: 明示批准回写，核对判据 ④（批准前 KB 零写入；终帧带未重写尾句；`writeback.written/untouched` 与 md5 实测互相印证）。
- [ ] Step 5: 判据 ② 报数：本次 call 帧数 vs 首建 1648 / 走查二 2830。**超了也照报**，不许改提示词或判据把它跑过。
- [ ] Step 6: 清场按走查纪律（探针会话与项目删净、`projects.json`/`sessions/index.json` md5 回基线、真实 KB 未被触碰），读数回填 spec「走查记录」。
