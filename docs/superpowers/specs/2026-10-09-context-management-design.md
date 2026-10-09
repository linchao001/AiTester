# 上下文管理专项 · 第一片「计量与闸门」设计

**状态**：待用户批准（批准后由 writing-plans 拆实施计划；本文批准前不动代码）
**日期**：2026-10-09 ｜ **代码基线**：HEAD = `9010294`（origin/main 同点，门禁 888 passed）
**一句话**：给这个智能体装**一根尺**、**一道只砍单条工具产物的闸**、**一张会说真话的读数表**。

本文所有 `file:line` 于 2026-10-09 只读实测核对，见文末坐标表。

---

## 1. 问题本体（实测，不是推测）

| 事实 | 落点 |
| --- | --- |
| 每轮把**全量** `state["messages"]` 喂模型，含不断追加的 ToolMessage | `orchestration/agent_graph.py:87-122`（`_stream_round`）、`:149` |
| 唯一的上限是**条数**，不是量；磁盘仍留全量 | `services/chat.py:43 HISTORY_MAX = 40`，生效点 `:112`、`:209` |
| 后端**零计量**：provider 三条出口没有一条读 usage | `adapters/llm/openai_compat.py:30 complete`、`:48 invoke_messages`、`:58 stream_messages` |
| 真值在流式路径上会被**二次丢弃** | `orchestration/agent_graph.py:119-123`：`_stream_round` 收尾显式重建 `AIMessage`，只带 `content`/`tool_calls`/`additional_kwargs` |
| 流式调用压根没请求 usage | `services/model_config.py:288-293 build_provider` 从不传 `stream_usage`；实测 `langchain_openai` 1.6.6 的 `ChatOpenAI.model_fields` **含 `stream_usage`** |
| 窗口元数据是装饰品 | 同上：模型 `context` 值（1048576 / 131072 / 1000000）从不进 provider |
| 最大的一颗在**单个回合内**：命令输出 stdout 与 stderr **各自** 1MB ⇒ 单条工具产物可达 ~2MB | `adapters/tools/command_tools/runner.py:26 OUTPUT_MAX_BYTES` |
| 其余字节地板 | `file_tools/read.py:25-26`（单行 2000 / 单次 50KB）、`file_tools/search.py:48-52`（200 命中 / 2MB 文件 / 5 万字符） |
| 子智能体回收正文**无长度上限** | `orchestration/subagent.py:140-173`（`summary` = 子最后一句正文） |
| 前端那条进度条是**算出来的假数** | `frontend/src/pages/chat/utils.ts:6-10`（CJK 1 字 = 1 token）、`:14` 手抄 `HISTORY_MAX`、`pages/chat/Composer.tsx:37-40,70` |
| 窗口 cap 前端已有、后端没用 | `frontend/src/pages/ChatPage.tsx:92,95`（`hit.ctx` 来自模型元数据） |

**三条结论**：① 溢出风险主要在回合内，`HISTORY_MAX` 那把刀**完全管不到**；② 现在连「还剩多少」都没人知道，前端那条还在谎报；③ 真值就在响应里躺着，但流式路径**两道都在丢**（provider 不请求、图侧重建时丢）——所以「装个尺」的第一步是把真值接回来，而不是发明新估算。

---

## 2. 裁定集（本专项独立编号 CM-*，别与 case-design 的 18–47 混）

- **CM-1（用户 2026-10-09 菜单）**：第一片要解决的失败形态 = **不炸**——立计量与闸门。压缩/摘要、事实外置都不在第一片。
- **CM-2（用户）**：算出占用之后，**到线只呈递、不拦**；**闸门只硬管单条工具产物**。历史累积、system prompt 全量、子智能体委派开销均只报数。
- **CM-3（用户）**：读数落点 = **把前端现有那条假估算条换成真值**，并让**截断留痕**在人眼前可见。不新开面板。
- **CM-4（控制方代定，用户可否决）**：估算口径 = `tiktoken`（已装）近似 × 安全垫；**不为此新增依赖**。事前估算、事后真值；`tiktoken` 与 DeepSeek 分词器不同，故估算只用于「离线远不远」的相对判断，**报数以真值优先**。
- **CM-5（控制方代定，用户可否决）**：计量单点 = **provider 装饰器**，装在 `build_provider` 返回处；工具产物 cap 单点 = **`AiTooler` 工具基类**。
  这条修正了我先前口头说的「装在 `openai_compat.py:48 invoke_messages`」——**主链路根本不走它**（`agent_graph.py:98` 走 `stream_messages`）。装饰器同时覆盖 `complete` / `invoke_messages` / `stream_messages` 三条出口与 `bind_tools`，因此仍是**一个实现处**；而 react 图与 case_design 图各建了一个 `ToolNode`（`case_design/graph.py:52`），写在任何一张图里就必然分叉。
- **CM-6（口径）**：**占用与花费是两个读数，不许混**。一回合内每次调用都重发全量历史，所以「本轮请求了几次」与「最后一次塞进窗口多少」是两件事。读数表同时给：`occupancy`（单次调用输入的最大值，决定离线多远）与 `spent`（累加，决定花了多少）。前端那条按 `occupancy / window` 画。
- **CM-7（顺带收口）**：cap 装在工具基类 ⇒ 子智能体回收正文（它是 `TaskTool` 的返回值）**天然被同一道闸管住**，不需要第二处实现。故 CM-2 的「不管子智能体」只指**委派本身的开销**（子的多轮消耗），不再指回收正文无上限。
- **CM-8（本片不做）**：压缩/摘要、工具结果落盘 + 按需回喂、`HISTORY_MAX` 改 token 口径、system prompt 精简、prompt caching、超线自动拦。

---

## 3. 设计

### 3.1 数据流（新增部分加粗）

```
用户句 → prepare（HISTORY_MAX=40 不变）→ PassthroughContextBuilder
      → **MeteredProvider(window, usage)** 包住 build_provider 的产物
      → 图：每轮 _stream_round 前 → **usage.on_request(estimate)**
             流末 chunk 带 usage → **usage.on_response(real)**（不改 _stream_round）
      → 工具：AiTooler.run/arun 返回 → **cap_content()** → ToolMessage（超线即截并留痕）
      → done 帧带 `context` 读数 → 会话文件 `context` 节 → 前端条换成真值
```

### 3.2 `context/meter.py` —— 唯一的一把尺

```python
SAFETY_MARGIN = 1.15          # 估算安全垫，可单测、不散落
ENCODING_NAME = "o200k_base"

def estimate_text(text: str) -> int: ...            # tiktoken；不可用时降级 len//3 + 登记
def estimate_messages(messages: list[Any]) -> int:  # 含 role/工具名/参数，不只 content
def input_tokens_of(response: Any) -> int | None:   # usage_metadata.input_tokens → response_metadata.token_usage
def output_tokens_of(response: Any) -> int | None:  # 同上取 output
```

全仓「这条占多少」只准问这个模块：前端不算、图不算、case_design 不算。

### 3.3 `context/usage.py` —— 一回合一个累加件

```python
@dataclass
class Truncation:
    tool: str; original: int; kept: int; dropped: int

@dataclass
class ContextUsage:
    window: int
    rounds: int = 0
    peak_occupancy: int = 0          # 单次调用输入最大值（真值优先，无真值即估算）
    occupancy_source: Literal["actual", "estimated"] = "estimated"
    spent_input: int = 0
    spent_output: int = 0
    truncations: list[Truncation] = field(default_factory=list)
    error: str | None = None         # 计量自身失败时说实话，不静默
```

同一回合跨 wait/resume 必须连续：`resume_stream` 复用 `entry.prepared`（`services/chat.py:382`），prepared 携带同一 provider 实例，故累加件天然连续——**实现必须保住这条链路，并有测试钉住**（续跑后 `rounds` 递增而非归零）。

### 3.4 `adapters/llm/metered.py` —— 计量缝

```python
class MeteredProvider:
    def __init__(self, inner: LlmProvider, window: int, usage: ContextUsage): ...
    def bind_tools(self, tools):     # 返回 MeteredProvider(inner.bind_tools(tools), window, usage) —— 共享同一累加件
    def complete(self, messages):    # 记估算；无 usage ⇒ 照实不写真值
    def invoke_messages(self, messages):
    def stream_messages(self, messages):   # 透传每个 chunk；收尾读带 usage 的末 chunk
    # model_ref / name 原样代理
```

`_stream_round`（`agent_graph.py:87-123`）**一行不改**：它重建 `AIMessage` 时丢 usage 是既有事实，读数走累加件这条独立通道，不挂在消息字段上。

### 3.5 `services/model_config.py:288` —— 窗口与 stream_usage

`build_provider` 构造 `OpenAICompatProvider` 时带上该模型的 `context` 值（现在纯装饰）与 `stream_usage=True`，返回 `MeteredProvider(...)`。`stream_usage` 是新增请求字段：**若某兼容端点不支持导致报错或末 chunk 无 usage，退估算路径**（见 §5 第 2 条），不硬撑。窗口值缺失或为 0（`ChatPage.tsx:95` 现在就会 `setCap(0)`）时，`window = 0` 就是「未知」，前端**不画百分比、显示「—」**——拿 0 当窗口算出 100% 是本片最容易犯的谎。

### 3.6 `context/budget.py` + `adapters/tools/base.py` —— 唯一那道闸

```python
TOOL_OUTPUT_TOKEN_CAP = 12_000     # 单条工具产物进上下文的上限；env AITESTER_TOOL_OUTPUT_TOKEN_CAP 覆盖（同 AITESTER_SHELL_MAX_OUTPUT_BYTES 的先例）
                                   # 取默认窗口 131072 的一成——单条产物不该吃掉一屏以上的预算
HEAD_RATIO = 0.6                    # 保留头 60%、尾 40%，省略中段

def cap_content(text: str, *, tool: str, usage: ContextUsage | None) -> str:
    """超 TOOL_OUTPUT_TOKEN_CAP 即截断，前插一行标记并把 Truncation 记进 usage。"""
```

`AiTooler` 覆写 `run()` 与 `arun()`，两条都调**同一个** `cap_content`（规则一个实现处、两个调用点，必须有差分测试钉两条都生效——本仓 SDD 纪律）。形状约定：

- 只处理 **content**：文件工具是 `content_and_artifact`（`file_tools/read.py:105`、`edit.py:51` 等），返回 `(str, artifact)` ⇒ **只截 str，`artifact` 一个字节都不动**（它是前端 diff 面板的数据，截了就是打坏 UI）。
- 标记文案（逐字，进断言）：`[工具输出已截断：原约 {original} tokens，保留 {kept}，省略中段 {dropped}。以下内容由编排层截断，非工具自身报错。]`
- 既有**字节地板全部保留不动**（`runner.py:26`、`read.py:25-26`、`search.py:48-52`）——它们是地板，token cap 是压在上面的天花板。本片不改任何工具内部逻辑。
- cap 自身抛异常 ⇒ 原样返回未截断文本，`usage.error` 记一笔。**新增的是读数，不是新的失败面。**

### 3.7 落盘

`services/session_store.py:67-79` 的 `ChatMessage` 必填字段**一个不动**，assistant 行新增**可选** `context` 节（`ContextUsage` 的 dict 形；照 `stopped` 的先例：老 jsonl 行没这个键 ⇒ `from_dict` 读缺省，零迁移）。HTTP 读侧同步：`interaction/schemas.py:296-301` 的 `ChatMessageInfo` 加同名可选字段（该处注释定的规矩就是「读侧字段只增不删」）。`services/chat.py:_persist`（`:231-239`）透传；`steps`（`_STEP_KEYS`）里带截断痕迹，让人回看时知道哪一步被砍过。

### 3.8 前端

- 删 `frontend/src/pages/chat/utils.ts:6-10` 的 CJK 启发式与 `:14` 手抄的 `HISTORY_MAX`——**有测试钉它们不存在**（改名测试断言字节不动的那类反向钉）。
- `Composer.tsx` 的百分比条改吃 done 帧 / 会话行的 `context`：`occupancy / window`，`occupancy_source == "estimated"` 时换一种样式并写「估算」；缺 `context` 节时显示「—」，**不显示 0%**。
- 截断在步序行可见（原长/保留/省略三个数）。

> **实施澄清（第一片 C6，2026-10-09）**：「三个数可见」本片交付在 **composer 的上下文 tooltip** 里，逐条列到工具名（`工具名 原 X→留 Y（省 Z）`）；**不在步序行**。机制归因三条，逐条核过：① 步序行的 `detail` 是**参数摘要**（`orchestration/subagent.py:36` `detail_of` = `json.dumps(args)[:DETAIL_MAX]`）；② step 帧按契约**不外泄工具正文**（`orchestration/agent_graph.py:281`——`result` 只进 `tool_traces` 落盘、不进 UI 事件），而截断标记只前插进 ToolMessage 正文（`context/budget.py` 的 `TRUNCATION_MARK`）；③ `Truncation`（`context/usage.py:14-23`）**没有回合归属**，不带 `round`/`call_id` ⇒ 现有协议下三个数**无法按步配对**（同一工具第 1 次没超、第 2 次超，按执行顺序硬配必错配）。要在步序行内联展示，得给 step 帧新增契约字段并动 `_STEP_KEYS`，还要给 `Truncation` 加回合号并由 `cap_result` 调用点传入 ⇒ 越过本片「一把尺 + 一道闸」的范围，与 §7 的「工具结果落盘 + 文件指针回喂」同批留第二片。§6 判据② 的「可见」据此读作：**界面上（tooltip）看得见三数 + 落盘 `context.truncated` 逐条留痕**，反向钉在 `test_frontend_context_display.py`。

---

## 4. 硬规则

- **R-C1**：估算值**永远不许写进真值字段**。真值缺失就是缺失（`occupancy_source = "estimated"`），UI 区分两种样式。
- **R-C2**：一条规则一个实现处。尺只在 `meter.py`，闸只在 `budget.py` + `AiTooler` 两个调用点，读数只在 `ContextUsage`。任何「第二处也算一遍」都在评审里挡掉。
- **R-C3**：截断必留痕，且痕迹必含**原长 / 保留 / 省略**三个数；标记文案本身是断言对象（「唯一人审门上的那句话不许说谎」的同一口径）。
- **R-C4**：到线只呈递不拦（CM-2）。唯一会改变送给模型的内容的动作是 §3.6 那条闸。
- **R-C5**：计量/落盘/cap 的一切异常一律收敛成「读数缺失」，不许升级成「测试设计任务中止」或回合失败。与第五片 A3 同族。
- **R-C6**：走查提示里**只写业务事实，判据一个字不进提示**；未触发的分支照实报并给机制归因，绝不伪造输入凑判据。

---

## 5. 错误处理与兼容

1. **老会话文件没 `context` 节**：读侧做**窄背填**（只补认识的缺节，**明确不做深合并**——深合并会把真损坏洗成健康账本）。缺节 ⇒ 读数缺失，UI 显示「—」。现有 `data/sessions/index.json` 仅 39 B（本地无历史），兼容以「旧文件照常加载」为测试判据。
2. **provider 不返 usage**：`input_tokens_of` 返 `None` ⇒ `occupancy_source = "estimated"`，`peak_occupancy` 用估算。既有 `MockProvider`（`adapters/llm/mock.py`，完全不产 usage）**天然就是这条路径的测试件**，验收判据④ 直接用它，不造新桩。
3. **`tiktoken` 不可用 / 编码缺失**：降级为字符数粗算并置 `usage.error`，不抛。
4. **结构化产物被砍坏**（`task` 摘要、search 的类 JSON 正文）：本片**不修、不猜补全**，只靠标记头声明「可能不完整」，让模型别把半截当全量。真解法是工具结果落盘 + 给模型文件指针按需回喂（`case_design/stages.py:764-780` 的简报已是路径指针形状）——留第二片。
5. **`stream_usage=True` 兼容性**：若某端点因此报错，回退该参数并登记为「本模型只能估算」，**不许让整条聊天挂掉**。
6. **case_design 专属图**：共用同一个 bound provider（`case_design/graph.py:53`）⇒ 自动被计量与 cap；它的账本 `history` 里另记读数摘要，供长循环自查（本片只保证「能读到」，账本记账格式由实施计划定）。

---

## 6. 测试与验收线

**离线门禁**：`cd backend && .venv/Scripts/python -m pytest -q`，基线 **888**，只增不减；每条硬规则都要在**撤掉它的变异下变红**，否则只是装饰。

- `meter`：估算单调性；CJK / ASCII / 代码三类混合；真值优先于估算；`tiktoken` 不可用时降级路径。
- `budget.cap_content`：正好上限 / 上限 +1 / 多字节边界三类；「保留头尾、省略中段」逐字节可复现；标记文案逐字断言；`(str, artifact)` 形状下 **artifact 逐字节不变** 的差分测试。
- **单点差分测试**（CM-5 的证）：从 `MeteredProvider` 抓计数点，断言主会话（`agent_graph.py:98`）与 case_design 图（`case_design/graph.py:53`、`:55-58`）**确实经过同一个累加件**；把缝挪回任一张图、或把 cap 挪进任一 `ToolNode`，测试必须红。`run()` 与 `arun()` **两条入口各一条**生效证明。
- `usage` 跨 wait/resume 连续：续跑后 `rounds` 递增不归零。
- `session_store` / `file_memory`：无 `context` 节的老行 ⇒ 加载不炸 + 读数为空；追加写不丢已有节。
- 前端：钉 `utils.ts` 的启发式与手抄 `HISTORY_MAX` **不存在**；条按 `occupancy/window` 画且估算/真值两样式。

**付费走查（隔离实例，新端口 8014；8000/5173 与 `scripts/dev.ps1` 绝不动）**四条判据：

1. **真值到位**：一回合后会话 `context.peak_occupancy` 的来源是 `actual`，且与 done 帧读数一致。
2. **闸门真管**：让模型跑一条**故意**把 stdout 打爆的命令（业务话术，判据不入提示），实测 `truncations` 留痕、**原长/保留/省略三个数在界面可见**（本片在上下文 tooltip，口径见 §3.8 实施澄清）、且模型不假称拿到了全文。
3. **呈递诚实**：历史累积时读数上升但**不被拦**（这正是 CM-2 的语义边界）；同一条会话在改造前后读数明显不同（假估算 vs 真值）。
4. **零假绿**：走一遍不返 usage 的档位（MockProvider 或关掉 `stream_usage`），照实标「估算」。

成本沿用 case-design 专项既有裁定「先不设红线，功能跑顺再说」——**只报数**。

---

## 7. 第二片边界（登记，不在本片开工）

压缩/摘要（Claude Code auto-compact / Codex `/compact` 那一族）、工具结果落盘 + 按需回喂、`HISTORY_MAX` 从条数改 token 口径、system prompt 与工具描述的精简、prompt caching 的追加式排布。第一片的读数是第二片的输入——**尺先立准，砍什么才有依据**。

---

## 8. 风险与已知代价

- **估算不等于真值**：DeepSeek 分词器与 `o200k_base` 不同，估算会有偏差，故安全垫 + 真值优先（CM-4）。风险落在「离线还远时误判很近」，代价是多报数，不是误拦（本片不拦）。
- **`TOOL_OUTPUT_TOKEN_CAP = 12_000` 是拍的**：需要真机读数校准。走查判据② 会给出第一条实测分布，若明显不合适由控制方在片内当场改常量（改数值不改形状）。
- **窗口值可信度**：`context` 元数据由用户配置，可能与真实窗口不符 ⇒ 读数按配置值算，UI 标「按配置的窗口」，不假装知道服务端真相。
- **只计量不拦 ≠ 不炸**：超线时 provider 仍会 4xx。本片把「为什么会超」变成可见数字与可见截断，把「超线怎么办」留给第二片——这是 CM-1 选择的代价，如实登记。

---

## 9. 坐标表（实测于 `9010294`）

`orchestration/agent_graph.py:87-123`（`_stream_round`，`:98` 走 `stream_messages`、`:119-123` 重建 AIMessage 丢 usage）｜ `:149`（`bind_tools`）
`case_design/graph.py:52`（自建 `ToolNode`）、`:53`（共用同一个 bound provider）、`:55-58`（agent 节点走同一条 `_stream_round`）
`adapters/llm/openai_compat.py:30/:48/:58`（三条出口，均不读 usage）｜ `adapters/llm/mock.py:24/:32`（完全不产 usage）
`services/model_config.py:271-293`（`build_provider`，`context` 值与 `stream_usage` 都没传）
`services/chat.py:43`（`HISTORY_MAX`）、`:112`、`:185`（`agent_runtime.build` 每回合现建 provider）、`:209`、`:231-239`（`_persist`）、`:263`、`:315-322`（done 帧）、`:382`（`prepared = entry.prepared`，续跑复用同一 provider）
`services/pending.py:48-62`（`PendingEntry` 携带 `prepared`）
`services/session_store.py:67-79`（`ChatMessage`，`stopped` 是可选字段先例）、`:175-178`、`:263-286`
`interaction/schemas.py:296-301`（`ChatMessageInfo`，读侧字段只增不删）
`adapters/tools/base.py:6`（`AiTooler`）｜ `command_tools/runner.py:26`（1MB，stdout 与 stderr 各自应用）｜ `file_tools/read.py:25-26,105`、`search.py:48-52`、`edit.py:51`（`content_and_artifact`）
`orchestration/subagent.py:140-173`（回收正文无上限）｜ `adapters/tools/subagent_tools/task.py:64,84`
`frontend/src/pages/chat/utils.ts:6-10,14,17-27`、`Composer.tsx:37-40,70`、`ChatPage.tsx:92,95`

---

## 实施澄清（第一片，C7 登记）

本节只登记**第一片实际交付与本 spec 原文有差**的条目。§3.8 里那条由控制方所写的「实施澄清（第一片 C6）」（截断三数交付在上下文 tooltip、不在步序行）**以那一节为准，此处不重抄、不改写**；§6 走查判据② 的「可见」口径同样只受那一节收窄。

- **CM-澄清-1（收窄 §5 第 6 条后半句）**：case_design 的**账本 `ledger.json` 里不另抄每回合读数摘要**。
  - **收窄对象逐字**：§5 第 6 条「它的账本 `history` 里另记读数摘要，供长循环自查（本片只保证「能读到」，账本记账格式由实施计划定）」——**「能读到」已交付**，**「账本里另记摘要」不在第一片**，格式因此也无需定。
  - **「能读到」靠什么保证（机制，不是措辞）**：case_design 的 provider 与工具面都由装配根 `AgentRuntime.build` 造的**同一个** `ContextUsage` 贯穿——`services/agent_runtime.py:93` `_metered_pair` 包出 `MeteredProvider(inner, usage)`，`:107` `build_default_registry(usage=usage)` 把同一本注进每个 `AiTooler`；专属图与 react 图都从 `stream_graph(build, provider, tools, messages)` 那一个入口进去，所以缝在图**之外**（CM-5）。读数出账两处可见：done 帧与会话行共用 `_persist` 算出的那一份快照（`services/chat.py:247`、`:337`）。
  - **离线证据（第一片，不花钱）**：`backend/tests/test_context_e2e.py` 的 `test_case_design_graph_and_main_loop_share_the_ruler` 把两张图接在同一本账上（实测 `rounds` 2→4，逐字钉死）；`test_gate_and_meter_work_in_one_turn` 钉闸与尺同回合、且 `done["context"] == 磁盘行的 context`。
  - **为什么不在本片做**：账本是**断点续跑的真相源**，往里加一节就要动 `Ledger` 的窄背填与版本判定——正是第五片（T27–T34）刚收口的那族风险；收益只有长循环自查方便。⇒ 与 §5 第 4 条、§7 的第二片边界同批做。
  - **代价（照实，不许粉）**：长循环跨多次驱动激活时，**账本自身答不出「上一次激活时占用多少」**；只能从该会话行的 `context` 反查，而那是人审面、不是模型自查面。第二片若要给驱动自查用，应与「工具结果落盘 + 按需回喂」一起设计（那才是长循环真正缺的东西），别单点往账本加节。
  - **归属**：第二片。

---

## 走查一（第一片）

**环境与前置**（2026-10-09）：隔离实例 8014（`uvicorn aitester.main:app --port 8014`，独立日志），前端另起 `vite --port 5199` + `VITE_PROXY_TARGET=http://127.0.0.1:8014`；用户端口 8000/5173 与 `scripts/dev.ps1` 全程未动（实测两者全程拒连）。免费预检四条全过（`pytest -q` 949 passed、`git status` 只剩 4 个约定未跟踪、8014 空闲、导入不炸）；`AITESTER_TOKEN_WARM` 未关（真机截断读数 `original=20829` 即词表口径的旁证）。走查提示只写业务事实，判据一词未进提示。

### 判据① 真值到位——**达成**（干净回合）；另测出「被中止回合恒报估算」的机制

- **干净回合（1 轮、无工具）**：`occupancy_source = "actual"`，`window 1048576 / rounds 1 / peak 1675 / spent_input 1675 / spent_output 83 / truncated [] / error null`。`peak` 与 `spent_input` 即 DeepSeek 回传的 `usage.prompt_tokens`——本链路 `meter.input_tokens_of` 只从 `usage_metadata` 取数，没有第二个来源。
- **含工具回合（2 轮）**：`actual`，`peak 9881`（第二轮真实 prompt tokens）。
- **端点确实回 usage（直连实测）**：`openai` 客户端**不带 `stream_options`** 流式请求，87 块、末块带 `usage`（prompt 32 / completion 86）⇒ DeepSeek 不请求也回，`stream_usage` 那面旗不是必要条件。
- **真图三处同源**：done 帧快照与会话 `.jsonl` 那行逐字相同（离线侧 `test_context_e2e.py` 两条钉死；真机侧同一快照对象经 `_persist` 双出口）。
- **被中止回合为什么恒报 `estimated`（离线复现 + 真机同构）**：同一本账跑 4 轮、第 4 轮消费到第 1 块即停（照 `_stream_round` 的取消口径）⇒ `peak 1873 / source estimated / spent_out 60`：`spent_out>0` 证明真值确实到过账，但**被中止那一轮**依 R-C1 不许写估算（`metered.py` 的 `finally` 只在流跑完时 `note_response(real…)`）；一回合内消息只增不减 ⇒ **末轮估算必经是 peak**，整回合遂报 `estimated`。真机同构：判据① 首跑（事故见下）`rounds 83 / peak 129220(估) / spent_input 4181939 / spent_output 60004 / truncated []`——6.0 万输出真值的存在本身就证明流式 usage 在真机通路上是通的。（帧 `round` 号到 84、账 `rounds=83`：两者口径不同，`_round_no` = 1 + ToolMessage 条数，账 = provider 请求次数，差 1 非缺陷。）
- **并发子智能体共账本时样本会错位（离线复现，登记呈报）**：`note_response` 只替换 `[-1]`，两个并发回合交错时后到的真值会盖住别人的样本（实测：父 900 估算存活、子 5000 估算被父真值 1500 顶掉）。读数仍在、归属已错。⇒ 呈报项。

### 判据② 闸门真管——**达成**

- 业务话术让 `read` 读约 130 KB 的 KB 文件：产物经字节地板后 `original 20829`（估算）> `cap 12000` ⇒ 截断一次：`truncated [{"tool":"read","original":20829,"kept":9556,"dropped":11273}]`，恒等式 `9556+11273=20829` 成立、`dropped>0`。
- **模型没有假称拿到全文**：它自报「本次读取约 2 万 token，已截断…后半部分还有更多问答未展示」。
- `detail` 未被截坏：`steps[0].detail = {"file_path": "knowledge/_gold_qa/bz_qa.json"}`（参数摘要；产物正文从不进步序帧，§3.8 澄清）。
- **界面三个数**：口径在 composer tooltip（§3.8 澄清）；真机 DOM 实测 tooltip 逐字 `上下文占用 129220 / 1M tokens（估算·本回合 83 次调用·累计输入 4181939、输出 60004）`（该会话无截断；截断串渲染由 `test_frontend_context_display.py` 反向钉）。
- **`TOOL_OUTPUT_TOKEN_CAP` 默认 12000 的实测分布（P-5 校准依据）**：① 真机截断发生在 20829→9556（省 54%），截断后那一轮真值 prompt = 9881；② 被中止的长循环 268 个 `call` 帧、`truncated` 为空——常规 `read`/`grep` 产物够不到 12000。**读数口径**：12000 是「131072 窗口一成」的固定常数，而本机配置窗 1048576 ⇒ 相对窗口偏紧（约 1.1%），对「别把上下文吃爆」仍成立。本片不动常数（CM-2 只动工具产物），窗口相对化归第二片。

### 判据③ 呈递诚实（到线只报不拦）——**达成**

- 同会话连发 5 轮（含工具轮）：**5/5 全 `actual`**，`rounds` 1–2、`peak` 1675 → 9881 → 3163 → 2627 → 3035，**没有任何一轮被拦下**（无 `error`、无拒绝文案、`stopped` 全 false）。每回合一本账，peak 随该回合的轮次上升；跨回合不累计是 CM-6 的口径（长循环内的上升由被中止那次 `rounds 83 / peak 129220` 另证）。界面在 12% 占用时照常可发——本片除工具产物闸外**不存在拦回合的代码路径**，与 CM-2 一致。
- **前端读数与实际来源相符（真机 DOM）**：estimated 会话渲染 `class="ctx-meter est"`、`≈12%`、`bar width:12%`，tooltip 逐字含「估算」；新会话无读数显示「—」；actual 分支由 C6 反向钉逐字节钉死（真机未再驱动浏览器看 actual 分支，登记为限制）。
- **CM-3 换真值的收益（同一回合三种口径对比）**：改造前前端假尺（`estTokens` = CJK 数 + 非 CJK/4，只数可见文本、看不见工具产物）算出 **447**；后端词表估算（系统提示 + 用户话）**460**；同回合 DeepSeek 真值 **1754**（第一轮）与 **9881**（第二轮，含 20829→9556 的截断产物）。⇒ 差距不在分词（447 vs 460），在**数不到的东西**：工具产物旧口径贡献 0，真值里它是大头。

### 判据④ 零假绿——**达成**（原定手法造不出这一档，改用等价档位）

- 原定「临时关 `stream_usage`」**造不出不返 usage 的档**：实测（直连、无 `stream_options`）DeepSeek 仍回 usage；真机把 `model_config.py` 暂改 `stream_usage=False` 重启后跑一轮也仍是 `actual`（peak 1675 / out 47）⇒ 立即还原（`git diff` 0 字节）。
- 改用**真正不返 usage 的档位**：本机 OpenAI 兼容流式端点（逐块无 `usage`），被检对象全是生产件（真 `ChatOpenAI`、真 HTTP、真 `MeteredProvider`）。① 直连 provider：`source=estimated`、`spent_output=0`、`error=None`；② 再过真图（`build_agent_graph` + 真 `read` 工具，2 轮）：`source=estimated`、`rounds=2`、`truncated=0`、末帧 `finish`。**零假绿成立**：没有真值就停在估算，不冒领。
- 「落盘行不得出现 actual」：平台智能体回合不落盘（裁定 7），真机侧没有该行可查；等价保证由离线钉给出——帧与行是**同一份快照**（`test_sse_done_frame_carries_the_snapshot` 先钉非空再钉等值；`test_gate_and_meter_work_in_one_turn` 钉行 == 帧），帧为 `estimated` 则行不可能是 `actual`。

### 成本读数（按裁定只报数不设线）

- 判据①②③④ 正向部分：**6 个 `call` 帧**、6 次 LLM 请求，输入 29 938 tokens、输出 2 631 tokens（j1 1 轮 / j2 2 轮 / j3 三期各 2 轮 / j4 1 轮）。
- **判据① 事故（照实登记，计划外支出）**：首跑把提示发给了 `case_design` 智能体 ⇒ 第五片的断点续跑语义启动长循环（账 83 轮、268 个 `call` 帧、输入 4 181 939、输出 60 004），被 `/chat/stop` 中止；`git status` 干净、产出只落在探针项目目录内。此事故同时产出了上文中「被中止回合恒报估算」的真机读数。
- 直连探针两次（usage 在不在、不请求是否也回）：输入合计 67、输出 107。

### 清场与基线

- 只杀自己起的实例：8014 两代 PID 25720/33080 与 vite 5199 的 35292 逐个核对后杀；用户端口 8000/5173 与 `scripts/dev.ps1` 全程未动（端口实测始终为空）。
- 删探针项目「读数走查」`proj_96006d2f`（连带其会话 `sess_92615660`）后：`projects.json = 9116cdf85e322fb1f74ef692c0336921`、`sessions/index.json = bd78c88524cff1a5f345bb9c27ec8451`，**与走查前基线逐字节相同**。
- 真实 KB：递归文件计数 **1802**（85 个目录含根）、**无任何文件 mtime ≥ 今天**（最新 2026-10-03 21:07）、`git status` 干净 ⇒ 本片对 KB **零文件级改动**。计划里记的 1803 不重现（差 1，无文件级证据可归因；今天只有 `business/{chains,stories,test_points}`（三目录现均 0 文件）与 `.git` 的**目录级** mtime 变化）⇒ 登记呈报。

### 呈报项（走查中发现、本片不修，等点头才开片）

1. **被中止/中断的那一轮恒报估算，且它必经是峰值** ⇒ 单调增长的长会话（恰是最需要读数的场景）在「用户按过停止」的回合里整回合只有 `estimated`。机制 = R-C1 的「跑完才有资格写真值」+ 消息只增不减。修法方向（第二片）：给被中止轮一个显式标注（如 `aborted_rounds: n`），或让快照区分「峰值来源轮是否中止」。
2. **并发子智能体共用一本账时样本错位**（`note_response` 只换 `[-1]`）⇒ 读数归属错。修法方向：按 `call_id`/线程配对样本，或给 `ContextUsage` 加锁 + 独立槽位。
3. **KB 文件计数 1802 vs 计划所记 1803**（详见「清场与基线」）。

## 终评（第一片，2026-10-09）

整枝评审区间 `fefe91f..e302a74`（C1–C8 共 20 个提交、39 个改动文件），评审在提交态上只读进行，工作树零改动。

- **Verdict：0 Critical / 0 Important / 2 Minor，Ready to merge = Yes**（0I 故不开修复轮）。
- **六条不变量逐条经最终代码 + 全局 grep 独立核验**：R-C1 真值资格是代码规则（`note_response` 唯一 ACTUAL 写入口、peak 那格样本自身须带 ACTUAL，estimated 漂不成 actual）；R-C2 全仓只有一把尺（`tiktoken/encode(` 只在 `context/meter.py`、`ContextUsage(` 生产实例化只在 `agent_runtime.py`、前端假尺残迹为零）；R-C3 三数同源且前端逐条呈递；R-C4 到线只呈递（`pct` 与发送可用性无关）；R-C5 全链异常收敛、唯一登记例外 M-5′ 经调用点核实不可达；R-C6 走查判据已回填本文件「## 走查一」。
- **门禁复跑（评审实跑）**：后端 `949 passed in 80.07s`（基线 888，+61）、前端 build 0 error（尺寸与计划「执行状态」逐字一致）；三条变异红证（C5 `inner` 口 / C4 `_before` 守护 / C7 落盘 `context`）逐字复现台账数字。
- **两条 Minor 的裁定**：**M-1** spec §3.7 对 `entry.prepared` 的行号 `:379` 与 §5 的 `:382` 内部不一致 ⇒ **当场修**（本文件已改 `:382`，基准版口径）。**M-2** `usage.error` 单槽、尺失效与闸失效同回合时后写覆盖先写 ⇒ **登记不修**（两者都是罕见异常路径的说明性信息，丢失一半无正确性影响；随第二片「读数缺口界面细化」一并处理）。
- **停车项确认**：P-1..P-7、C4/C5/C6/C7 各评审的登记不修项与走查三条呈报项，评审确认停车裁定合理——全是呈递口径/后续片范围，不构成任何不变量的反例。
