# 用例设计智能体：可复用原子子图 + Supervisor 动态编排（设计稿）

> 本稿是 [2026-10-11-case-design-subgraph-refactor-design.md](2026-10-11-case-design-subgraph-refactor-design.md)（下称「静态编排稿」）的**演进备选方案**，二者并存。
> 静态编排稿把层序/路由用**静态条件边**钉死；本稿把四大专项拆成**可复用原子子图库**，改由 **Supervisor 中枢逐轮动态路由**组合工作流。
> 方法论依据：`Supervisor 动态子图编排.md`（LLM-as-router + `Command(goto)` + `with_structured_output` 护栏）。
> 本稿当前**只描述编排范式与原子边界**，不含实现细节；方案以用户明确回复【方案通过】为准。

## 1. 决策基线（本稿两条已定口径）

- **落地形态**：新开独立设计文档（本文件），不改动静态编排稿；两稿对照评审后再定取舍/迁移。
- **编排强度**：**混合模式——护栏钉死 + 分支动态**。
  - **钉死（确定性 State 前置条件，不交 LLM 自由裁量）**：层顺序、人审门、回写授权、澄清循环判据、连带重估。
  - **动态（LLM 逐轮路由）**：从哪层起、跳过哪些已完成/无需改的层、走新建还是维护、mixed/纯设计分叉、按需回补故事&测试点。

## 2. 动机：静态编排的三个痛点

1. **逻辑重复**：§6/§7/§9 实为同一套「影响分析 → 逐条分派 → 人审门 → 回写授权」骨架，仅制品粒度不同；静态稿各自成图，维护时多处同改。
2. **组合僵硬**：「可从低层起执行 / mixed / 纯设计直连回写 / 缺故事先补 §7 再进 §9」等分支在静态稿里靠一堆条件边硬编码，新增组合要改拓扑。
3. **不可按实际伸缩**：真实任务常常跨越多个专项边界（如做到一半发现链路要重构），静态稿只能按预设路径路由，无法运行时重组。

Supervisor 模式把「组合权」从中立枢纽的运行时决策给出，原子只负责「干完回中枢」，天然支持任意顺序/次数组合。

## 3. 可复用原子子图清单

把静态稿 §6–§9 反复出现的结构归一成 9 个原子（均为自定义 StateGraph，编译后 `add_node` 挂进父图；仅 `chat` 用 `create_agent`）：

| 原子 | 职责 | 参数（按调用场景注入） | 静态稿出处（被谁复用） |
|---|---|---|---|
| `intent` | 意图判定：新建/维护、目标需求是否交付过 | `scope` | §6/§7/§8/§9 入口（可并入 supervisor 入口逻辑） |
| `clarify` | 需求澄清：背景关联→分析检错→澄清门循环→落盘 `requirement-clarified.md` | `mode=全量/增量` | §8 需求澄清层 |
| `analyze_impact` | 影响集合分析 + 子任务复审（审范围完整 + 审每条动作方向） | `level=链路/故事/测试点/用例` | §6/§7/§8/§9 维护更新首步 |
| `gen_design` | 逐块生成→落大纲（内部调 `review_loop`） | `artifact=链路/故事/测试点` | §6 新建、§7 新建、§8 三层 |
| `gen_cases` | 用例编写（批内两阶段：逐条质检 + 故事级全局质检） | — | §9 新建 |
| `review_loop` | 质检环：评审 ⇄ 优化迭代至合格 | `shape=合并单环/逐块+全局双环/批内两阶段` | `gen_*`、`apply_actions` 内部 |
| `apply_actions` | 逐条按【新增/更新/删除】分派（回调 `gen_*` 增量版） | `level` | §6/§7/§8 维护「逐条分派」 |
| `gate` | 人审门外环：汇编→挂起 `awaiting_review`→批准/提意见回流 | `kind=大纲门/末门` | §6/§7/§8/§9 全部人审门 |
| `authorize_writeback` | 回写授权独立一问→落工作区目录 | `target=test-design/ \| test-case/ \| 两者` | §3/§6/§7/§8/§9 统一回写 |

**辅助原子（延续静态稿既有件）**：`chat`（非任务只读问答）。

**复用边界**：`gen_design` / `analyze_impact` / `apply_actions` 只复用**流程骨架**（分析→逐块生成→质检环→落大纲；逐条按方向分派）；链路/故事/测试点的**层特定质量规则不靠 `artifact` 入参驱动**，而是注入对应的 `DesignPolicy`（见 §3.1）。用例因「编写≠设计」本质不同，独立为 `gen_cases`，不并入 `gen_design` 参数化。

### 3.1 复用边界：骨架 vs 策略（避免层间差异被参数化抹平）

链路/故事/测试点/用例 的设计与评审方法确有结构性差异（非入参差异），若单靠 `artifact` 字符串分支会回归到静态稿质平的弊病。**骨架真复用，判据走策略注入**：

| 维度 | 链路 | 故事 | 测试点 | 用例 |
|---|---|---|---|---|
| **块粒度** | 整棵树（一块） | 按链路成块 | 按故事成块 | 按故事分批 |
| **质检形态** | 合并**单环** | 逐块 + 层全局**双环** | 逐块 + 层全局双环 | 批内**两阶段**（逐条+故事级） |
| **覆盖度判据** | 相关链路清单 vs 影响范围+改动方向**双重一致** | 无独立矩阵 | **覆盖矩阵** | **可多不可少**（不可少硬约束、额外不判违规） |
| **维护复审道数** | **2 道**（集合复审定位 + 业务范围修订后改动内容评审 ≤5；删除无内容审、新增走合并单环） | — | **2 道**（前复审定位 + 后评审改动内容 ≤ 5） | 定位受影响用例**再 1 道** |
| **"更新"动作语义** | **业务范围修订**（`name`·`business_scope`·`excluded` 皆为链路业务身份字段，改 `name` 即视为范围理解变化，随范围一并修订；链路 id/parent/level 不变） | 修订 | 修订 | 修订用例 |
| **本质** | 影响分析 | 设计 | 设计 | **编写≠设计** |

因此每个制品注入一个 `DesignPolicy`，层间差异全收敛在 Policy（而非散进原子内部 if/else）：

```python
DesignPolicy(artifact) = {
   block_rule,          # 块粒度：整树 / 按链路 / 按故事 / 按故事分批
   review_shape,        # 合并单环 / 逐块+全局双环 / 批内两阶段（即 review_loop.shape）
   coverage_criterion,  # 双重一致 / 覆盖矩阵 / 可多不可少
   review_passes,       # 复审道数与上限：1 道 / 2 道(≤5)
   action_semantics,    # 更新=业务范围修订（含改名）vs 修订；是否连带重估下游
   reviewer_brief,      # 该层评审器的领域指令集
}
```

原子执行 = **通用骨架 + 对应 Policy 组合**；既拿到「一套骨架消除四份重复图」的收益，又不丢层特定质量规则（`review_loop.shape` 即此思路的一个雏形，扩成全量 Policy 即可）。

#### 3.1.1 `DesignPolicy` 字段取值明细（实施配置骨架，值取自 §3.1 维度表 / §3.2 / §6 / §9）

| Policy 字段 | 链路 | 故事 | 测试点 | 用例 |
|---|---|---|---|---|
| `block_rule` | 整棵树（一块） | 按链路成块 | 按故事成块 | 逐链路→逐故事分批（一故事=一批） |
| `review_shape` | 合并单环 | 逐块+层全局双环 | 逐块+层全局双环 | 批内两阶段（逐条+故事级） |
| `coverage_criterion` | 双重一致（清单 vs 影响范围+改动方向） | 无独立矩阵 | 覆盖矩阵 | 可多不可少（不可少硬约束、额外不判违规） |
| `review_passes`（维护路径子任务复审道数） | **2** 道（集合复审把关方向 + 业务范围修订改动内容评审 ≤5；删除 0 道、新增走 `gen_design` 合并单环） | **2** 道（前复审定位 + 后内容评审 ≤5） | **2** 道（同故事） | **1** 道（定位受影响用例再复审） |
| `action_semantics`（更新语义） | **业务范围修订**（统一一档）：`name`·`business_scope`·`excluded` 是链路“业务身份”字段，凡改 `name` 即视为业务含义变（**无“仅改名而范围不变”档、无纯纠字例外**），一律随范围一并修订并进第 2 道内容评审，链路 id/parent/level 不变、仍属同一条链路 | 修订 | 修订 | 修订用例 |
| `action_semantics`（连带重估下游） | 置下游 story/tp/cases `stale`（§6） | 置下游 tp/cases `stale`（§6） | 用例侧走 `analyze_impact[用例]` 定位受影响用例（§9，非 stale flag） | 无（叶子） |
| `reviewer_brief` | 该层评审子任务的领域指令集（具体 prompt 待实施，出处：链路 §6 集合复审 / 故事·测试点 §7 逐块+全局双环 / 用例 §9 批内两阶段） | ← 同左 | ← 同左 | ← 同左 |

> 说明：① 本表与§3.1 维度表同根，仅改为**按 Policy 字段名组织**，供实施直接当配置骨架引用；② 用例侧「连带」不走 stale flag（§6 stale 仅作链→故事→测试点的粗粒度标记），而靠 §9「定位受影响用例」的既有↔变更点映射；③ `reviewer_brief` 不在此展开具体文案（属实施阶段的 prompt 设计，需逐层用户确认）；④ 已取消「仅改名（确定性）」独立档与纯纠字例外——`name` 是业务语义字段，改名即业务含义变，一律并入**业务范围修订**（内容产出型、非确定性）；据此链路“更新”补设第 2 道内容评审（对齐故事/测试点 ≤5），`review_passes` 由 1 道改为 2 道（定稿见 §12）。

### 3.2 原子内部工作流细化（细节取自静态稿 §6–§9）

> 下列按「通用骨架 + Policy 注入点」逐个细化每个原子的**内部节点流**；跨层承接、mixed 判定、层顺序、连带重估**一律上交 supervisor**（见 §5/§6/§7），不写进原子内部——这是本稿与静态稿「专项整体成图」的核心结构差异。每个原子只负责「干完回中枢」。静态稿对应出处逐条标注，便于 §8 覆盖性回溯。

**① `intent`（意图判定）** 出处：§10 router②、§6/§7/§8/§9 入口
```
[task 原文 + ledger 挂起态 + scope]
  → 续跑排查：ledger 有 pending 且本消息是其回应 → 出 resume 目标（§10 router①；supervisor 步1 已拦，本原子只做新任务分类）
  → 四信号判定（输入形态/诉求动词/目标对象/范围）→ 专项类别 + 新建/维护 + 目标需求是否交付过
  → 判不准 → 保守归 chat
  → 写 State.intent（新建/维护、全量/迭代）
```
- 参数：`scope`。§3 已注「可并入 supervisor 入口逻辑」——不单列图节点时，上述判据仍作为 supervisor 路由前的确定性预处理生效。
- **按链路写用例的意图判定**：链路已由链路专项（§6）设计好 → `intent` 判为「单链路·全量新建·跳过澄清/链路层」，路由固定走 `gen_design[故事] → gen_design[测试点] → …`，不设「故事/点齐不齐」的缺口判定。

**② `clarify`（需求澄清）** 出处：§8 需求澄清层、§12 澄清门
```
[原始需求 + 知识库 + mode=全量/增量]
  → 背景关联（检索知识库上下游业务/既有链路·故事·测试点作参照基线）
  → 分析检错（四维：错误 / 模糊 / 歧义 / 遗漏）
  → 澄清门循环：有未决问题？
      ├─ 是 → 编号清单 Q-xx（现象+建议澄清方向）→ 挂起 awaiting_clarification（interrupt，跨回合）
      │        → 用户答复并入需求信息 → 回「分析检错」重评（增量支仅对新增/变更部分）
      └─ 否 → 落盘 requirement-clarified.md（原文逐字保留 + 校准处标 【澄清 Q-xx】 + {design}/clarification/ 留痕）
```
- **循环判据钉死**：由用户答复驱动，无固定轮次上限、不套 ROUND_CAP、`iteration` 豁免；不设 AI 自评出口。
- 写 State.`clarified_req`；为空时 supervisor 禁路由任何 `gen_*`（§6「澄清先行」）。
- 挂起后冒泡收口，续跑回「分析检错」（非其它层，靠 `awaiting_clarification` 语义区分）。

**③ `analyze_impact`（影响集合分析 + 复审）** 出处：§6 维护首步、§7 维护定位、§8 第一段每层
```
[clarified_req / 变更源 + 既有 results + level]
  → 整理变更源（business 更新 / 用户新需求 / 上游层变化）
  → 分析受影响集合：逐对象附改动方向【新增/更新/删除】（未受影响者不动）
  → 子任务复审 ⇄ 优化（唯一「范围类」复审点：① 审范围准确完整 ② 审每条动作方向，错判方向=驳回重判）
  → 写 results.<level>_set（合格受影响集合 + 方向）
```
- **level 差异（Policy 注入 `action_semantics`/`review_passes`）**：
  - 链路：块=整树；「更新」=**业务范围修订**（`name`/`business_scope`/`excluded` 随新需求一并改，链路 id/parent/level 不变、仍属同一条链路；凡改 `name` 即视为业务含义变，**不设“仅改名而范围不变”档、无纯纠字例外**）；需求触到既有链路但范围与命名均不必动者，标注「涉及未变」纳入集合、不进链路级优化；仅当链路身份变动（拆分/合并/改挂父/废弃）才在本复审判为「删除＋新增」组合。**复审 2 道**：前集合复审把关方向 + 业务范围修订改动内容评审 ≤5（见 ⑦）；删除为确定性移除无内容审、新增走 `gen_design` 合并单环。
  - 故事/测试点：集合带动作方向；分派后另设第 2 道内容评审（见 ⑦）。
  - 用例：定位受影响用例（既有用例↔变更点映射，大纲给不出），再过 1 道复审。

**④ `gen_design`（逐块生成→落大纲，内部调 `review_loop`）** 出处：§6 新建、§7 新建故事环/测试点环、§8 故事层/测试点层
```
[合格集合/范围锚定 + requirement-clarified.md + DesignPolicy[artifact]]
  → 按 block_rule 切块（链路=整树 / 故事=按链路 / 测试点=按故事）
  → 逐块生成制品 → review_loop(shape/coverage_criterion 由 Policy) → 全合格
  → 落大纲 → 写 results.<artifact>
```
- **artifact 差异**：链路=`review_shape=合并单环`产合格链路树；故事/测试点=`逐块+层全局双环`，测试点另用覆盖矩阵判据。
- 层顺序（链路→故事→测试点）与「改故事连带重估测试点」**不写进本原子**：置 `stale_flags` 交 supervisor。
- 维护增量：由 `apply_actions` 回调本原子增量版（新增=设计 / 更新=修订）。

**⑤ `gen_cases`（用例编写，批内两阶段）** 出处：§9 全量新建支
```
[合格 tp_set（范围=测试大纲测试点）+ 业务信息]
  → 确定性切块：逐链路→逐用户故事，一个故事=一个编写批次（不重判范围）
  → 逐批 review_loop(shape=批内两阶段)：
       ① 逐条编写 + 逐条质检 ⇄ 优化（重点：用例规范，问题当条修复不留批尾）
       ② 批次全过后对该故事全部用例全局质检 ⇄ 优化（重点：与大纲覆盖度，可多不可少=不可少硬约束、额外不判违规）
  → 所有批次完成 → 交 gate(kind=末门)
  → 写 results.cases
```
- 本质「编写≠设计」，独立原子不并入 `gen_design`（§3.1）。维护更新支交 `analyze_impact[用例]→apply_actions`，本原子只管新建支。

**⑥ `review_loop`（质检环，被 `gen_*`/`apply_actions` 内部调用）** 出处：§6 质检环、§7 双环、§9 批内两阶段
- **三形态（shape）**：
  - 合并单环（链路整树）：逐块=全局对象相同，评审 ⇄ 优化迭代至合格，不再分设全局审。
  - 逐块+全局双环（故事/测试点）：逐块评审 ⇄ 优化 → 层全局审 ⇄ 修改重审（有意见改完再审，迭代至通过）。
  - 批内两阶段（用例）：逐条质检 ⇄ 优化 → 故事级全局质检 ⇄ 优化。
- **覆盖判据（coverage_criterion）**：双重一致 / 覆盖矩阵 / 可多不可少。
- **复审道数上限（review_passes）**：链路 2 道（集合复审 + 业务范围修订内容审 ≤5）/ 故事·测试点内容审 ≤5 / 用例定位再审 1 道。
- 只机器判合格、不做用户人审；审不过带意见回优化、**不冒泡**。

**⑦ `apply_actions`（逐条按方向分派，回调 `gen_*` 增量版）** 出处：§6 逐条分派、§7 维护逐条、§8 第二段
```
[合格受影响集合（带方向）+ level + DesignPolicy]
  → 逐条按【新增/更新/删除】分派（方向不再复审，已由 analyze_impact 集合复审把关；改动内容按层设第 2 道评审，见下）：
       新增 → 回调 gen_design/gen_cases 增量（该对象设计）
       更新 → action_semantics：链路=业务范围修订（name/business_scope/excluded 随范围一起改，链路身份不变，无纯纠字例外）；故事/测试点/用例=修订
       删除 → 标记移除 / 废弃
  → 【第 2 道内容评审】链路·故事·测试点分派后、gate 前另设子任务评审本轮改动内容：
       意见回流「逐条分派」，循环 ≤5，触顶携未决意见进 gate（链路仅业务范围修订走此道，删除/新增除外；方向已集合级把关，本道只审改动内容）
  → 交 gate
```
- 连带下游重估（改故事→其测试点/用例）**不写进本原子**：置 `stale_flags` 交 supervisor。

**⑧ `gate`（人审门外环，kind=大纲门/末门）** 出处：§6/§7 大纲门、§9 末门、§12 人审门
```
[当前合格制品 → 汇编成人类可读大纲]
  → 挂起 awaiting_review（interrupt，跨回合）等用户
  ├─ 批准 → 不置 writeback_authorized（§6 护栏），只放行 → 交 supervisor 决定下一跳（authorize_writeback 或续下层）
  └─ 提意见 → 解析为定向修正指令，回流目标由 kind+意见归属交 supervisor 路由：
        大纲门→对应层 gen_design/apply_actions 重跑；末门→相关批次质检环（规范类→逐条、覆盖类→全局）
```
- 构成「机器合格（review_loop）⇄ 人认可（gate）」外层循环，直到批准；批准 ≠ 回写。

**⑨ `authorize_writeback`（回写授权独立一问→落盘）** 出处：§3 统一回写规则、§12 回写授权询问
```
[gate 批准后的制品集 + target=test-design/ | test-case/ | 两者]
  → 独立一问（不附属于门）：授权回写 / 暂不回写（制品留工作区），挂起 awaiting_review 跨回合
  ├─ 授权 → 按 target 落工作区目录（设计产物→test-design/，用例→test-case/）
  └─ 拒绝 → 不算失败，任务就地收尾、制品保留，后续可由用户再发起
```
- 前置钉死：必须在对应 `gate` 批准之后（§6「门先于回写」「回写独立授权」），默认 `writeback_authorized=False`，显式授权才置 True。

**辅助原子**
- **`chat`**（`create_agent`，唯一非 StateGraph）：出处 §11、§10 router① 保守支。只读工具面（浏览/检索/知识库召回/只读命令），禁写/执行类；零副作用（不读写 ledger、不落设计账本）；无门无回写；`recursion_limit` 触顶收尾文案。

## 4. 统一 GraphState（前提中的前提）

原子子图之间**不直连**，数据全靠 supervisor 经 State 中转（schema 桥接是本方案主要工程量）。统一骨架：

```python
class GraphState(TypedDict):
    messages: Annotated[list, add]        # 累积轨迹，supervisor 判「已做过什么」防重复调度
    task: str                             # 原始诉求
    intent: str                           # intent 原子产出：新建/维护、全量/迭代
    clarified_req: str | None             # clarify 产出：requirement-clarified.md 路径（后续唯一权威输入）
    results: dict                         # 各层合格集合/制品：link_set/story_set/tp_set/cases
    stale_flags: set                      # 连带重估标记：改故事→其测试点/用例置 stale
    current_gate: str | None              # 挂起中的人审门标识（回流目标）
    pending: str | None                   # awaiting_review / awaiting_clarification / halt
    writeback_authorized: bool            # 回写授权结果（独立一问，不附属于门）
    next: str                             # supervisor 本轮决策目标（留痕）
    iteration: int                         # 全局循环护栏计数（clarify 豁免）
```

| 字段 | 承载的护栏/职责 |
|---|---|
| `clarified_req` | 钉死「后续各层只认澄清后文档」——为空时 supervisor 禁路由到 `gen_design` |
| `results` + `stale_flags` | 层顺序前置 + 连带重估的判据来源 |
| `pending` / `current_gate` | 跨回合续跑归属（对应静态稿 router §10） |
| `writeback_authorized` | 回写必经独立授权，默认 False |
| `iteration` | 防死循环；`clarify` 循环节点不消费它 |

## 5. Supervisor 编排模型

采用「文档 §4.3 生产写法」：中枢节点 `Command(goto=..., update=...)` 一次性完成跳转+更新+计数+留痕；跳转目标一律来自 `with_structured_output(Literal[...])` 枚举，杜绝幻觉出不存在的原子名。

```python
class RouteDecision(BaseModel):
    next_node: Literal["clarify","gen_design","apply_actions","gate",
                       "authorize_writeback","chat","FINISH"]
    artifact: str | None      # 目标原子的粒度入参（链路/故事/测试点/用例）
    reason: str

def supervisor(state) -> Command:
    # 1) 硬护栏优先（不等 LLM）：pending 未清则原地等，不重路由
    if state["pending"] in ("awaiting_review", "awaiting_clarification"):
        return Command(goto=resume_target(state))          # 续跑回对应原子
    # 2) 顺序/授权前置校验（钉死项违规直接否决 LLM 建议）
    decision = router_llm.invoke(routing_prompt(state))
    if violates_guardrails(state, decision):
        decision = fallback_legal(state)                    # 退回合法下一跳
    if decision.next_node == "FINISH":
        return Command(goto=END, update={"iteration": state["iteration"]+1})
    return Command(goto=decision.next_node,
                   update={"next": decision.next_node,
                           "iteration": state["iteration"]+1,
                           "messages": [f"[supervisor]→{decision.next_node}({decision.artifact}) {decision.reason}"]})
```

**拓扑可见性折中**：`supervisor → 原子` 的候选集用 `add_conditional_edges(..., path_map={...})` 声明（分叉在图上可见、便于校验是否覆盖静态稿全部工作流），原子内部跳转再用 `Command`。

## 6. 护栏规格（钉死项，violates_guardrails 的判据）

| 护栏 | 规格 | 违规处理 |
|---|---|---|
| **澄清先行** | `clarified_req` 为空 → 禁止进入任何 `gen_design`/`gen_cases` | 强制路由 `clarify` |
| **层顺序** | `gen_design[故事]` 需 `results.link_set` 合格；`gen_design[测试点]` 需 `story_set` 合格；`gen_cases` 需 `tp_set` 合格 | 否决并回上一合格层 |
| **连带重估** | 改故事→其 `tp_set`/`cases` 置 stale；改链路→其 `story_set`/`tp_set`/`cases` 置 stale | stale 未清不得 FINISH，回 `gen_*`/`apply_actions` |
| **门先于回写** | `authorize_writeback` 必须在对应 `gate` 批准之后 | 未过门禁路由回写 |
| **回写独立授权** | `gate` 批准**不置** `writeback_authorized`；必须单独一问 | 默认 False，显式授权才 True |
| **澄清无上限** | `clarify` 循环只看「有无未决问题」，不看 `iteration` | 豁免全局上限 |
| **全局迭代上限** | `iteration ≥ N` → `Command(goto=END)` 携未决告警 | 触顶收尾 |

### 6.1 `violates_guardrails` 具体判据表（§6 护栏的代码化，纯确定性布尔、不含 LLM）

> 约定：「合格」= 该层集合已过 `review_loop`（机器判合格）且未被置 stale；`state` 取值对齐账本 `LAYER_STATES`（`pending/active/audited/done/stale_pending/skipped`）。LLM 建议仅在本表全部校验通过后才采纳，任一命中即否决并走 fallback。

| 护栏 | State 判据（命中即违规） | 违规处理（强制下一跳 / 动作） |
|---|---|---|
| 澄清先行 | `next ∈ {gen_design, gen_cases}` ∧ `clarified_req is None` | `goto=clarify`（mode 按 intent） |
| 层顺序·故事 | `next=gen_design[故事]` ∧ `results.link_set` 非合格 | 回 `gen_design[链路]`（无链路集）或 `analyze_impact[链路]`（维护） |
| 层顺序·测试点 | `next=gen_design[测试点]` ∧ `results.story_set` 非合格 | 回故事层对应原子 |
| 层顺序·用例 | `next=gen_cases` ∧ `results.tp_set` 非合格 | 回测试点层对应原子 |
| 连带重估 | `next=FINISH` ∧ `stale_flags ≠ ∅` | 回 **stale 命中的最低层** `gen_*`/`apply_actions`；stale 未清零禁 FINISH |
| 门先于回写 | `next=authorize_writeback` ∧ 对应 `gate` 无批准留痕 | 回 `gate(kind=对应门)` |
| 回写独立授权 | `writeback_authorized is True` ∧ 无独立授权询问留痕 | 置回 `False` 并重发 `authorize_writeback` |
| 澄清无上限 | `next=clarify` | **豁免** `iteration≥N` 否决，不套用全局上限 |
| 全局迭代上限 | `iteration ≥ N` ∧ `next ≠ clarify` ∧ `next ≠ FINISH` | `goto=END` 携未决告警 |

> 与 §5 步 1 的分工：`pending` 未清的续跑属「硬护栏优先、不等 LLM」，不经过本表；本表只校验 **② 新任务路由** 阶段 LLM 给出的 `decision`。

## 7. 动态分支（交 LLM 逐轮决定的部分）

supervisor 的 `routing_prompt` 在满足 §6 护栏的合法集合内，据实际 State 现场选择：

- **从哪层起 / 跳层**：命中链路&故事已存在且无需结构改动 → 跳过对应 `gen_design`，直达测试点/用例（对应静态稿「可从低层起执行」）。
- **新建 vs 维护**：目标需求/链路是否已有合格制品 → 选 `gen_design`（新建）或 `analyze_impact→apply_actions`（维护）。
- **mixed / 纯设计**：`tp_set` 合格且需求含用例 → 续 `gen_cases`；纯设计 → 跳 `gen_cases` 直连 `authorize_writeback(target=test-design/)`。
- **按链路写用例**：链路已由链路专项（§6）交付 → 跳过澄清与链路重设计，直接 `gen_design[故事] → gen_design[测试点] → gate → authorize_writeback[test-design/]` → `gen_cases → gate → authorize_writeback[test-case/]`（两支各自回写，不设缺口检查）。
- **用例进哪支**：对应测试点有无既有用例 → `gen_cases`（新建支）或 `analyze_impact[level=用例]→apply_actions`（维护支）。

### 7.1 按链路写用例（单链路完整流水线，router⑤ 的落点）

> 背景：知识库已有该链路，§6 链路专项已设计好，用户点名某条链路要它的**全量用例**。

- **链路层不重设计**：以已存在链路为只读范围锚（cid），跳过需求澄清层与链路重设计。
- **完整顺序走**：`gen_design[故事] → gen_design[测试点]`（§7 新建支口径）→ `gate → authorize_writeback[test-design/]` → `gen_cases`（§9 全量新建支）→ `gate → authorize_writeback[test-case/]`。
- **与按需求写用例隔离**：复用现成独立子图串接，不动 §8 的澄清层/影响集合/大纲承载/回写后置等质量机制；两支仅在 `gen_cases`（§9）交汇，其「以合格测试点为分母、可多不可少」输入契约不因入口不同而放松。故事&测试点与用例**各自独立人审门与回写授权**（分层回写）。
- **无缺口判定**：每次都完整设计三层，不存在「查上游齐不齐、齐则跳过」的分支，故不设 `capability_gap` 判据。

## 8. 静态稿工作流 = 确定性路由轨迹（可覆盖性对照）

静态稿每条固定流程，本稿退化为 supervisor 在特定 State 下的一条轨迹，逐条可核：

| 静态稿 | 本稿路由轨迹 |
|---|---|
| §6 链路·全量新建 | `gen_design[链路]` → `gate` → `authorize_writeback[test-design/]` |
| §6 链路·维护更新 | `analyze_impact[链路]` → `apply_actions[链路]` → `gate` → `authorize_writeback[test-design/]` |
| §7 故事&测试点·新建 | `gen_design[故事]` → `gen_design[测试点]` → `gate` → `authorize_writeback[test-design/]` |
| §7 故事&测试点·维护 | `analyze_impact[故事/测试点]` → `apply_actions` → `gate` → `authorize_writeback` |
| §8 全量新建 | `clarify[全量]` → `gen_design[链路]` → `gen_design[故事]` → `gen_design[测试点]` → `gate` →（mixed）`gen_cases` → `authorize_writeback[两者]` |
| §8 迭代更新 | `clarify[增量]` → `analyze_impact[三层]` → `apply_actions` → `gate` →（第二段）`analyze_impact[用例]` → `apply_actions[用例]` → `gate` → `authorize_writeback[两者]` |
| §9 按链路写用例 | `gen_design[故事]` → `gen_design[测试点]` → `gate` → `authorize_writeback[test-design/]` → `gen_cases` → `gate` → `authorize_writeback[test-case/]`（链路已由 §6 设计，跳过澄清/链路层，分层各自回写） |

> 覆盖校验建议用 §5 的 `path_map` 声明式枚举，保证 supervisor 候选目标 ⊇ 本表所有原子。

## 9. durable 边界（延续静态稿 §12，不被动态编排破坏）

- **业务进度**（游标 / `pending` / `current_gate` / `writeback_authorized` / 回写节）仍**单一 owner = `design/ledger.json` 的 `tasks/<task_id>`**，跨回合续跑靠它，不靠 checkpointer。
- **checkpointer + thread_id** 只保**同一次 run 内**的 `interrupt()` 挂起/续跑（写/执行审批、人审门暂停），不存跨回合理由。
- supervisor 续跑：新回合先读 ledger 还原归属 task → 灌进 GraphState → `pending` 命中则续跑回对应原子（§5 步 1），否则进入 ② 新任务路由。
- 回写目标目录：设计产物落工作区 `test-design/`、用例落 `test-case/`（与静态稿 §3 一致）。

### 9.1 续跑存储选型：SQLite（混合双库，保留语义分离）

将「文件存储」升级为「SQLite 存储」，但**两个存储各自职责不变**（延续 §9 精神：durable owner 单一、checkpointer 不存跨回合理由），只是介质换成可查询、可并发、重启安全的 SQLite：

| 存储 | 选型 | 职责 | 串联键 |
|---|---|---|---|
| **checkpointer** | `MemorySaver` → **`SqliteSaver`**（langgraph-checkpoint-sqlite） | run 内 `interrupt()`/HITL 挂起恢复；可选解锁跨回合**图位置精确恢复** | `thread_id` |
| **业务账本** | `design/ledger.json` → **SQLite 表**（逻辑 owner 不变） | 跨回合业务游标、`awaiting_*` 语义与授权留痕、id 序列、任务登记表 | `task_id`（外键→`thread_id`） |

账本表建议形态：`tasks(task_id, kind, thread_id, cursor_json, pending, awaiting_reason, writeback_authorized, …)` + `id_seq` + `source_files`。

**续跑流程**：新回合 supervisor **先查账本表**定归属 task 与语义（为何停/谁批了什么）→ 用其 `thread_id` 让 `SqliteSaver` 将图态恢复到对应节点→ 灌进 GraphState 继续。账本供“为什么”，checkpoint 供“在哪”。

**三条约束（防止误用）**：
- **不得用 checkpoint 当业务账本**：checkpoint 存的是图内部序列化 State，与 LangGraph 版本/checkpoint schema 强耦合，升级旧 checkpoint 失效；而「为何停、批准/意见」等跨回合理由语义必留在账本表。
- **大制品不塞 checkpoint**：测试大纲、用例正文仍落工作区 `test-design/`、`test-case/`；State/checkpoint 只存路径 + 游标 + 控制态。
- **两库职责分离不合并**：推荐两张表/两个库文件，避免把业务耐久契约绑到框架实现细节。

### 9.2 统一 GraphState ↔ ledger 字段映射（§4 × §9.1）

> 单一 durable owner 仍在账本：`GraphState` 是**单次 run 内快照**，续跑时由账本→灌进 State（§9.1 续跑流程）；大制品落工作区，State/账本只存**路径 + 游标 + 控制态**（§9.1 约束）。

| GraphState 字段 | ledger / SQLite 归属 | 说明 |
|---|---|---|
| `task` | `tasks.kind` + 任务登记原文 | 原始诉求，账本登记 |
| `intent` | `tasks`（新建/维护、全量/迭代） | intent 原子产出的持久化 |
| `clarified_req` | `tasks` 存路径指针 | 指工作区 `requirement-clarified.md`，正文不入账本 |
| `results`（link/story/tp/cases） | 工作区 `test-design/`、`test-case/` + `cursor_json` 摘要 | 制品正文不塞 State/checkpoint，仅路径+游标 |
| `stale_flags` | `cursor_json` 内 stale 层标记（对齐现 `LAYER_STATES=stale_pending`） | 连带重估判据来源 |
| `current_gate` | `gate` 节 `{round, approved_at}` / `tasks.pending_gate` | 挂起门标识 + 批准留痕（供门先于回写校验） |
| `pending` | `tasks.pending`（`awaiting_review`/`awaiting_clarification`/`halt`）+ `awaiting_reason` | 跨回合续跑归属；「为何停」语义存账本不存 checkpoint |
| `writeback_authorized` | `tasks.writeback_authorized` + `writeback` 节 `{done, log}` | 独立授权结果与留痕，默认 False |
| `next` | 不持久化（run 内留痕进 `messages`） | supervisor 本轮决策目标 |
| `iteration` | `tasks` 计数 / `cursor_json.round` | 全局循环护栏计数（clarify 豁免） |
| `messages` | 可选归档；跨回合理由走 `tasks.awaiting_reason` | checkpoint 不存跨回合理由（§9.1 约束） |

## 10. 可观测与调试

- `stream` + namespace 区分父图/每个原子内部节点、Checkpoint、Interrupt。
- supervisor 每轮 `reason` 写进 `messages`，可复盘「为何这轮调 A 不调 B」。
- 路由轨迹表（§8）作为回归基线：动态编排须能复现静态稿每条流程。

## 11. 风险与代价

1. **State schema 桥接重**：9 原子内部态各异，统一 GraphState + 入/出参映射是主要工程量。
2. **LLM 路由越权**：靠 §6 `violates_guardrails` 硬否决兜底；纯靠 prompt 约束不足，需代码级前置校验。
3. **拓扑可视化下降**：`Command(goto)` 分叉不可见 → §5 用 `path_map` 折中。
4. **续跑正确性**：门/澄清挂起后 supervisor 必须 resume 回原原子而非从头 → 依赖 ledger `pending`/`current_gate` 归属。
5. **护栏与灵活性拉扯**：钉死项过多则退化成静态图；须严守「顺序/门/回写/澄清=钉死，起点/跳层/组合=动态」。

## 12. 取舍口诀与后续

- **要集中管控与可视 → Supervisor（本稿）；子图独立、自然语言传参 → 静态编排稿。**
- 本稿不取代静态稿，作为演进备选；两稿对照评审通过后再定是否迁移。
- ~~待细化：~~ 已补入本稿：`violates_guardrails` 判据表（§6.1）、按链路写用例单链路完整流水线口径（§7.1）、统一 GraphState 与 ledger 字段映射（§9.2）、`DesignPolicy` 字段取值明细表（§3.1.1）。
- 仍待后续（不拆计划）：`reviewer_brief` 逐层评审 prompt 具体文案（实施阶段设计）。
- 定稿（链路“更新”语义，本轮已改 §3.1/§3.1.1/§3.2-③/⑥/⑦）：① 取消“仅改名（确定性）”独立档**及纯纠字例外**——凡改 `name` 即业务含义变，一律并入**业务范围修订**（内容产出型、非确定性）；② 链路“更新”补设第 2 道内容评审（对齐故事/测试点 ≤5），`review_passes` 由 **1 道 → 2 道**（删除为确定性移除无内容审、新增走 `gen_design` 合并单环）。
