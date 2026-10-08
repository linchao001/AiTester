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

同一回合跨 wait/resume 必须连续：`resume_stream` 复用 `entry.prepared`（`services/chat.py:379`），prepared 携带同一 provider 实例，故累加件天然连续——**实现必须保住这条链路，并有测试钉住**（续跑后 `rounds` 递增而非归零）。

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
2. **闸门真管**：让模型跑一条**故意**把 stdout 打爆的命令（业务话术，判据不入提示），实测 `truncations` 留痕、步序行可见、且模型不假称拿到了全文。
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
