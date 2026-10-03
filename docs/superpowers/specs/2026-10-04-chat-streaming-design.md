# 聊天页 · 第 4 片：流式与停止 设计

日期：2026-10-04　状态：**设计已定稿**（用户裁定 2026-10-04：粒度=token 逐字+步骤逐条 / 停止=服务端真停 / 范围=聊天页+`/kb` 都切 / 截断=留痕并标注 / 裁定 1=删一次性 `send` 端点 / 裁定 2=上下文 meter 口径对齐）；待写实施计划、待实施

参考原型：`prototype/index.html` `send() :1427-1447`（只有三点占位 + 1400ms 后整块替换，**无逐字模拟、无停止钮、无逐条步骤**）、`.typing :198-201`、`details.thinking :143-148`、composer `:611-624`、上下文 meter `:1411-1421`
现状依据：`orchestration/agent_graph.py:38-69`（两个普通函数节点）、`:72-117`（`run_graph` 一次性 `graph.invoke` + `tool_traces` 组装）、`adapters/llm/base.py:8-17`（协议只有 `invoke_messages`）、`openai_compat.py:52-63`、`mock.py`、`services/chat.py:54-107`（`_complete`）、`:114-189`（`send` 的守卫段 124-156 / 装配段 158-167 / 落盘 169-185）、`services/agent_runtime.py:48-92`、`interaction/router.py:58-92`（`chat_send` 错误映射与 `drafts/steps` 容错口径）、`interaction/schemas.py:16-50`（`SendRequest/StepInfo/SendResponse`）、`:239-247`（`ChatMessageInfo`）、`services/session_store.py:67-83`（`ChatMessage` 与 `from_dict`）、`:260-283`（`append`）、`memory/base.py`（`MemoryStore.save` 形参契约）、`memory/file_memory.py:24-35`、`frontend/src/api/client.ts:306-312`、`pages/ChatPage.tsx:203-234`（`send`）、`pages/chat/MessageList.tsx:23-39`（`Steps`）、`:86-91`（`busy` 三点占位）、`pages/chat/Composer.tsx:31,77-81`、`pages/chat/utils.ts:12-24`（`contextUsage`）、`pages/KbPage.tsx:275-310`（`chatSend("kb-console", …)` 与 `replaceLastAi`）、`vite.config.ts`（`/api` 代理）
存量数据：`backend/data/sessions/*.jsonl` 已有行**零迁移**（新增可选字段 `stopped`，读侧缺省 `False`）

## 背景与概念澄清

第 1 片 spec 的「范围外」写着：第 4 片 = `graph.stream` 替 `invoke` + SSE + 增量渲染 + 停止按钮，并备注「节点已是返回 `{"messages":[...]}` 的普通函数，拓扑不用改」。读完现状要修正半句：**拓扑不改，但节点体必须改**——`agent_node` 现在调 `invoke_messages`，一次返回整条 `AIMessage`，模型侧没有流；`LlmProvider` 协议里也没有流。所以本片真正的改动面是 provider 协议 → 节点 → 编排流 → 传输 → 前端渲染五段一条线。

- **流的是两样东西**：正文 token（最终轮）与过程事件（工具发起/完成/草案）。第 2、3 片已经把过程块落进 UI（`MessageList.Steps`），本片只是让它从「结束后一次性给出」变成「边跑边出现」。
- **停止必须是服务端的**：前端 abort 只让 UI 停下来，后端线程仍会把这一轮跑完并落盘全量回复——用户以为停了，下一轮模型看到的却是它没见过的完整答案。所以本片要的是「取消信号能进到节点 token 循环里」，`RunRegistry` 是它的载体，也正是第 5 片 `interrupt`（挂起等人）要用的长任务入口。
- **传输只留一条**：裁定 1 删掉 `POST /api/chat/send`。`trace` 与 `model` 两个响应字段前端零消费方（全仓只有 `resp.reply/resp.steps/resp.drafts/resp.session_id/resp.title` 被读），随端点一起消失；`/api/chat/echo` 的 `trace` 是七层回归链路的验收物，不动。
- **一份实现，两种消费**：`stream_graph` 是唯一执行路径，`run_graph` 改成「把事件折回今天那个 `{reply, tool_traces, drafts}` dict」的薄壳。这样既有的 `test_agent_graph.py` 全套用例直接变成流式核心的回归锁，也避免出现「invoke 与 stream 两套节点行为」这条第 2 片教训里的缝。

## 前提实测（2026-10-04，假模型、零网络、零副作用；脚本 `D:/tmp/probe_stream.py`，一次性探针，不入库）

| # | 前提 | 结果 |
|---|---|---|
| P1 | langgraph 1.2.12 的同步 `graph.stream(stream_mode="custom")` 能把节点内 `get_stream_writer()` 写的载荷**实时**送出来 | 成立。16 个 delta，首个 0.025s、末个 0.311s，模型自身耗时 0.300s → 逐 token 透传，不是攒完再吐 |
| P2 | `stream_mode="messages"` 够不够 | 它能拿到 LLM chunk，但工具结果不是 LLM 调用、拿不到 → **不用**，改走 custom writer（`call/step/draft` 与 `delta` 同一通道） |
| P3 | 非流式 `invoke` 下 `get_stream_writer()` 会不会炸 | 不炸，写成 no-op。（本片最终不依赖这条：`run_graph` 也走 stream；仍登记，因为它意味着「必要时可退回一次性路径」） |
| P4 | 消费端提前 `close()` 生成器 | 干净退出，异常不外泄 → 这是「浏览器断开即停」的落点 |
| P5 | `ChatOpenAI.bind_tools([...]).stream` | 存在（`ChatOpenAI.stream` 与 bind 后对象的 `stream` 均在）；真实 token 流与 tool_calls 增量合并留真机走查实测 |
| P6 | **未验**：取消信号经 `config.configurable` 进节点（`langgraph.config.get_config()` 读得到） | 实施计划第一个任务先钉这条测试。读不通即退回把 `RunControl` 作为 `GraphBuilder` 第三形参注入（牵动 `graph_registry` 与 `agent_runtime`，但不改拓扑） |

## 用户裁定（2026-10-04，逐条确认）

1. **粒度 = token 逐字 + 步骤逐条**：正文逐 token 追加；工具按「发起（⏳）→ 完成（✓/✗）」两条事件增量出现。
2. **停止 = 服务端真停**：`POST /api/chat/stop {run_id}` 置取消位；节点在每个 token 之间和每轮 agent 之前检查；已生成的部分文本与已完成步骤照落盘。
3. **范围 = 聊天页 + `/kb` 助手都切流式**：`/api/chat/send` 本片删除，传输收敛成一条（`KbPage.tsx:305` 是它最后一个消费方）。
4. **截断留痕**：assistant 行 content 存已生成原文，消息行加可选 `stopped: true`；「（已停止）」只进 UI，不进 content——进了就污染复制件，也会被喂进下一轮 prompt。
5. **裁定 1**：删 `POST /api/chat/send`（留它就等于两套传输长期并存）。
6. **裁定 2**：上下文 meter 口径对齐——前端 `contextUsage` 只统计最近 40 条，与后端 `chat.py:30` 的 `HISTORY_MAX = 40` 认同一个常数（第 1 片挂起、第 2 片登记「留待第 4 片一并复核」的那条）。

## 数据流

```
前端 fetch POST /api/chat/send/stream {session_id,message,agent_id,project_id}
  │  ├─ 守门未过 → 普通 HTTP 400/404（detail 逐字不变，前端走既有 ApiError→toast 路径）
  │  └─ 守门已过 → 200 text/event-stream，逐事件推送，直到唯一终态
  ├─ 点「■ 停止」 → POST /api/chat/stop {run_id} → 200 / 404
  └─ 关页/刷新    → StreamingResponse 关闭生成器 → GeneratorExit → finally 置同一取消位

orchestration.stream_graph(build, provider, tools, messages, control=None) -> Iterator[dict]
  agent_node:  provider.stream_messages() 逐 chunk → writer({"type":"delta","round":n,"text":…})
               轮末有 tool_calls → writer({"type":"call",…}) ；轮末无 tool_calls → 该轮即正文
  tools 节点:  由 graph.stream 的 "updates" 分片观察 ToolMessage → 折出 step/draft
  finish:      {"type":"finish","reply","tool_traces","drafts","stopped"}   # 恒有一条

services/stream_turn(...) -> Iterator[dict]   # 事件即 SSE 载荷
  start → (call|step|delta|draft)* → done | error
  终态前落盘：memory.save(key,"user",…) + memory.save(key,"assistant",reply,steps=…,stopped=…) + repo.put
```

事件表（`event: <type>\ndata: <单行 JSON>\n\n`；前端只认下表类型，未知类型忽略）：

| 事件 | 载荷 | 出处 | 前端动作 |
|---|---|---|---|
| `start` | `{run_id, session_id}` | 流一开始 | 记 run_id（停止要用）；`session_id` 非空即认领新会话 |
| `delta` | `{round, text}` | 每个非空 token chunk | 追加进该轮的 live 文本 |
| `call` | `{tool, round, detail}` | agent 轮末宣告 tool_calls | 过程块加一行 ⏳（`detail` 同 `DETAIL_MAX=80` 截断） |
| `step` | `{tool, ok, round, detail}` | ToolMessage 落地 | 把该行 ⏳ 换成 ✓/✗ |
| `draft` | `{draft}` | `prepare_kb_write` 的 artifact | 草案卡立即出现（不等流结束） |
| `done` | `{reply, steps, session_id, title, stopped}` | 唯一正常终态 | 正文落 `reply`；非最终轮的 live 文本折进过程块；`stopped` 时条尾挂「（已停止）」 |
| `error` | `{detail}` | 流中失败（HTTP 已经 200，只能走事件） | toast `detail` + 撤乐观气泡回填原文（与今天 `catch` 分支同款） |

终态恒为一条：`done` 或 `error`。（聊天里给的设计另有 `cancelled` 事件，此处收敛成 `done{stopped:true}`——前端只需一个终态判据，见偏离登记 2。）

## 契约变更

后端（新增 3 模块 + 改 5 处；KB 检索链路零改动）：

| 位置 | 变更 | 备注 |
|---|---|---|
| `adapters/llm/base.py` | 协议加 `stream_messages(messages: list[Any]) -> Iterator[AIMessageChunk]` | 与 `invoke_messages` 并列，不替换：节点在流里用它，折返壳也走流 |
| `adapters/llm/openai_compat.py` | `stream_messages` = `self._client.stream(messages)` 逐 chunk 透传；异常按 `invoke_messages` 同款包法（`ProviderError` + key 打星） | 失败口径两侧一致，不出现「invoke 报中文、stream 报裸异常」 |
| `adapters/llm/mock.py` | `stream_messages` 按定长 4 字符切片产出 `AIMessageChunk` | 测试可断言 delta 条数 = `ceil(len/4)`；粒度是测试常数，不代表真实 token 边界（风险节登记） |
| `orchestration/agent_graph.py` | 节点体改流：`answer_node`/`agent_node` 用 `provider.stream_messages` 累加 `AIMessageChunk`（`merged += chunk`）后返回 `merged.message`；每个非空 chunk 调 `get_stream_writer()`；每个 chunk 之间与每轮之前查 `control`，命中即停止累加并按当前已生成文本收尾 | 拓扑（节点/边/`should_continue`）一字不改 |
| 同上 | 新增 `stream_graph(build, provider, tools, messages, control=None) -> Iterator[dict]`；`run_graph(...)` 改为薄壳 `fold(stream_graph(...))` → `{reply, tool_traces, drafts}` | `tool_traces` 的 `ok/round/detail` 严格取键口径不变 |
| 新增 `orchestration/run_control.py` | `RunControl`：`threading.Event` 包装 + `cancelled` 属性。**取消不走异常**（聊天里设计的 `TurnCancelled` 作废）——它带着已生成的产物，是正常终态，统一由 `finish{stopped:true}` 表达 | 放 orchestration 而非 services：节点要 import 它，方向必须停在 adapters/orchestration 之下 |
| `services/run_registry.py`（新增） | `RunRegistry.start(run_id) -> RunControl` / `cancel(run_id) -> bool` / `finish(run_id)`，`threading.Lock` 保护；`run_id = uuid4().hex` | 进程内、内存态：后端重启即在途 run 消失（`/api/chat/stop` 回 404，UI 已在 `done` 前断开，无害） |
| `services/chat.py` | `send` 拆成 `prepare(session_id, message, agent_id, project_id) -> PreparedRun`（守卫 124-156 + 装配 158-167 + 记忆/键选择 169-176，异常与 detail 一字不变）与 `stream_turn(prepared)`（事件流 + 终态落盘）。`_complete` 只留 `echo` 用的 `build is None` 分支 | **一段守门两处消费**：400/404 文案与判据不许在 stream 路径重写一遍（第 2 片消费对称性教训的直接应用） |
| `memory/base.py` + `in_memory.py` + `file_memory.py` | `save(..., stopped: bool = False)`（keyword 形参，与 `steps` 同款「实现者不接受即断链」的注释口径）；`FileMemoryStore` 透传给 `SessionStore.append` | `InMemoryMemoryStore` 忽略 `stopped`（进程内记忆没有持久语义，同 `steps`） |
| `services/session_store.py` | `ChatMessage` 加 `stopped: bool = False`；`from_dict` 读 `raw.get("stopped") is True`；`append(..., stopped=False)` | 老行缺字段 → `False`，零迁移 |
| `interaction/schemas.py` | 删 `SendResponse`；加 `StreamStopRequest{run_id}` 与 `StreamStopResponse{ok}`；`ChatMessageInfo` 加 `stopped: bool = False` | 读侧字段只增不删：`GET /{sid}/messages` 的形状向后兼容 |
| `interaction/router.py` | 删 `POST /chat/send`；加 `POST /chat/send/stream`（先同步跑 `prepare`，异常照 `chat_send` 的 5 个 except 分支映射 400/404/502，之后才返回 `StreamingResponse(media_type="text/event-stream")`）与 `POST /chat/stop` | `drafts` 逐条 `model_validate` 容错、`steps` 严格构造这两条口径原样搬进事件序列化 |
| `main.py` | `application.state.run_registry = RunRegistry()`，注入 `ChatService(..., runs=…)`，`include_router` 不变 | 与 sessions/projects 同款装配位 |

错误口径（流开始前 = HTTP，流开始后 = `error` 事件；detail 一律中文）：

| 场合 | 落点 | detail |
|---|---|---|
| 未选项目 / 目录不存在 / 未知项目 / 会话不属于该智能体·项目 / 模型未配置 | HTTP 400·404（逐字沿用 `chat.py` 现文案与 `router.py` 现映射） | 不变 |
| 模型调用失败（流中） | `error` 事件 | `调用 {model_ref} 失败: …`（`ProviderError.detail` 原样，key 已打星） |
| `run_id` 已结束或不存在 | HTTP 404 | `这条回答已经结束` |
| 事件序列化遇到畸形 dict | `error` 事件 + 服务端 `logger.warning` | 与今天 `drafts` 容错同口径：丢一条不砸整条流 |

前端：

| 位置 | 变更 | 备注 |
|---|---|---|
| `api/client.ts` | 删 `chatSend`/`SendResponse`；加 `chatSendStream(body, onEvent, signal)`（`fetch` + `resp.body.getReader()` + 按 `\n\n` 分帧，缓冲尾包在 `done/error` 后 flush）与 `chatStop(runId)` | 不用 `EventSource`（不能 POST，且要带 JSON body）；不用 WebSocket（请求-响应语义，SSE 是这套拓扑的业界默认） |
| 新增 `pages/chat/streamState.ts` | `StreamingState`（`rounds: {round, text}[]`、`pending: Map<tool+round>`、`steps`、`drafts`、`stopped`）与两个纯函数：`applyEvent(state, ev)`、`finalize(state, doneEvent)` | 折叠规则只在 `done` 时算一次：非最终轮文本进过程块。放独立文件便于纯函数单测思路（本期无前端测试框架，故只求「可一眼读死」） |
| `pages/ChatPage.tsx` | `send` 改事件驱动：乐观气泡照旧 → `chatSendStream` → 逐事件更新 `live` state → 终态落 `messages`；新增 `runIdRef`/`stopRequestedRef`；`wsSeq+1` 移到 `done`（`stopped` 也移，工具可能已写了文件） | `busy` 语义扩到整个流期；`reloadSessions` 在 `done` 后照旧跑一次 |
| `pages/chat/MessageList.tsx` | 新增 `live: StreamingState \| null`：`busy` 且 live 为空 → 三点占位（首 token 前的等待）；live 有内容 → live 气泡（正文 + 光标）取代占位；`Steps` 接 pending 行 | 自动滚底沿用 `useEffect`，依赖加 `live` |
| `pages/chat/Composer.tsx` | `busy` 时发送钮位换成「■ 停止」（复用 `.btn-send` + 新增 `.btn-send.stop` 变体），`onClick` → `onStop`；`stopRequested` 后禁用并置 title「停止中…」 | 带文字不裸图标（UI 约定）；textarea 保持 `disabled`（见限制节「边生成边打字」不在本期） |
| `pages/chat/utils.ts` | `HISTORY_MAX = 40` 常数 + `contextUsage` 只统计 `history.slice(-HISTORY_MAX)` | 裁定 6；与 `chat.py:30` 同一数字，注释互相指名 |
| `pages/KbPage.tsx` + `pages/kb/KbAssistantPane.tsx` | `chatSend("kb-console", …)` → 同一 `chatSendStream`；`draft` 事件即 `replaceLastAi`+草案卡（今天 `drafts` 在结束时一次性给）；同样给停止钮 | `kb-console` 是临时键：`use_file=False` 路径不变，不落 jsonl |
| `App.css` | `.btn-send.stop`、`.t-step.pending`、`.live-caret`（光标）；全部沿用既有 CSS 变量，零新增色值 | 第 1 片「不新增色值」约定 |

## 真机行为定义（写清楚，免得实现时各自解释）

- **首个 delta 之前**：三点占位仍在（它表达的是「连接活着、模型还没开口」，这正是那段真实等待）；一旦有 delta 或被停止，占位让位。
- **工具轮次期间**：过程块可见且逐条增长；正文气泡显示「当前轮」的 live 文本。非最终轮的文本在 `done` 时被折进过程块（`📝` 行），不留在正文。
- **停止**：`■ 停止` → POST stop → 后端在下一个检查点断开累加 → `done{stopped:true}` → 前端把已流出的文本作为该条正文落定，条尾「（已停止）」。落盘 content 与 UI 正文逐字相同。
- **刷新/关页**：后端收到 `GeneratorExit`，同样置取消位并落盘截断（`stopped:true`）；重开会话能看到那条半截回复。
- **`/kb` 临时键**：流式行为一致，但会话行不写 jsonl（`use_file=False`），停止后不留痕。

## 测试策略

后端（基线 448 passed）：

- **先钉 P6**：一条测试证明 `control` 经 `config.configurable` 读得进节点（`langgraph.config.get_config()`）。读不通即按「前提实测 P6」的退路改 `GraphBuilder` 签名，并在本片 spec 里回写。
- **回归锁（不改一字）**：`test_agent_graph.py` 全部既有用例在 `run_graph = fold(stream_graph)` 下必须照旧全绿——它现在锁的是流式核心的正确性。
- 新增 `test_stream_graph.py`：delta 顺序与条数（Mock 4 字符粒度）、`call`/`step` 配对与 `round` 递增、`draft` 在 `finish` 之前、`control` 命中 → `finish{stopped:true}` 且 `reply` = 已生成前缀、取消后不再产生事件。
- 新增 `test_chat_stream_api.py`（装配蓝本 = `test_kb_browse.py`/`test_api_projects.py` 的 tmp 目录 `_app`）：
  - `TestClient.stream("POST", "/api/chat/send/stream", …)` + `iter_lines`：事件序列 `start → (delta|call|step)* → done`；`data:` 单行 JSON 可 `json.loads`；
  - 守门仍在流前：未选项目 400、坏目录 400、跨智能体续写 404，detail 逐字等于既有断言（复用 `test_api_chat_sessions.py` 的原文案，防两条路径漂移）；
  - 流中 `ProviderError` → `error` 事件而非 HTTP 502（且 HTTP 状态已 200）；
  - 生成器 `close()`（模拟断开）→ 该 run 的 control 被置位，落盘行 `stopped:true`；
  - `POST /api/chat/stop` 命中在途 run 200、重复 stop/已结束 404「这条回答已经结束」；
  - 无 `POST /api/chat/send`：`test_api.py` 里 22 处 `chat_send`/`/api/chat/send` 引用迁到 stream 或改断 405（刻意留一条 405 锁死「本期没有一次性口」，与第 3 片「无新建口锁」同款）。
- 新增/扩 `test_session_store.py`、`test_file_memory.py`：`stopped` 行 roundtrip；老行（缺字段）读成 `False`；`GET /{sid}/messages` 回 `stopped`；被停止的那条进下一轮 prompt 时是截断原文（`recall` 逐字断言）。
- 前端：`npm run build` 0 error（无前端测试框架，沿用既有裁定）+ 真机走查（真实 LLM 段须用户当面授权）。

## 验收清单

- `/chat` 发消息：正文逐字出现，首 token 前是三点占位；工具行 ⏳→✓ 增量出现；过程块不用等结束
- 点「■ 停止」：立刻不再增长，条尾「（已停止）」；刷新页面重开该会话，那条半截回复原样在，且 steps 是已完成的部分
- 生成中刷新/关页：后端不再继续烧 token（日志或 `stop` 404 佐证），落盘同样是截断+标注
- 停止后接着发下一句：能发（`busy` 已清），模型看到的是截断原文，不重复已完成动作
- `/kb` 助手：同一套逐字流；`prepare_kb_write` 的草案卡在流结束前就出现；停止钮同形
- 上下文 meter：长会话（>40 条）重开后百分比按最近 40 条算，不再虚高
- 守门不变：坏目录/未选项目仍是 toast 原文，且不产生任何 SSE 帧
- 页面 0 处 `zhb`、0 个死按钮（停止钮在终态后必须变回发送钮）

## 范围外

- 第 5 片 边界执法/授权：`interrupt` + checkpointer + pending 落盘 + 批准弹层。本片只交付它的前置件（`RunControl`/`RunRegistry`/事件通道），不做「挂起等人」。
- 断线重连与流恢复：刷新即丢 live，历史以落盘为准；不做 resume/Last-Event-ID。
- 会话级模型切换、token 统计与耗时展示、多会话并发流式。
- 「生成期间允许打下一句」：textarea 维持 `disabled`（第 1 片既有语义）。
- rename/pin/归档（第 1 片既有范围外）。

## 风险与已知限制

- **P6 未验**：取消信号进节点的唯一路径若 `config.configurable` 读不通，退路是改 `GraphBuilder` 签名（牵动 `graph_registry.py`、`agent_runtime.py` 与所有 builder 实现）。第一个任务先钉这条测试，就是为了把它变成开工 30 分钟内known 的事。
- **真实 token 流与 tool_calls 增量**：P1/P5 用假模型验的是通道，不是 DeepSeek/DashScope 兼容端点行为。`ChatOpenAI.stream` 在部分兼容端点上对 `tool_calls` 的 chunk 合并有差异，落不进 `merged.message.tool_calls` 就会「看不见工具」→ 真机走查必测一条「带工具调用的流式」。
- **vite 代理与 SSE**：`/api` 走 vite dev proxy。StreamingResponse 逐帧透传需要 `Content-Encoding: identity` 且无中间缓冲；若走查发现「攒完再吐」，第一嫌疑是代理/压缩层，解法是显式 `X-Accel-Buffering: no` 与关 gzip（登记待实测，不预先加保险丝）。
- **断开即停依赖生成器关闭**：P4 只验了直接 `close()`；`StreamingResponse` 在 uvicorn 下把 `GeneratorExit` 送进生成器的时机未经实测。最坏退化 = 后端跑完但无人消费，此时取消位仍未置，落盘成**全量**且 `stopped:false`（用户看不到，重开才看到完整回复）。走查逐条实测这一项。
- **Mock 粒度是测试常数**：4 字符一片只为断言条数，与真实 token 边界无关。
- **中间轮文本 live 可见但不落盘**：`done` 把它折进过程块，重开会话后只剩最终回复。这与今天一次性 `send` 的行为完全一致（`run_graph:98` 一直是「后写的非工具轮 content 覆盖前面的」），本片不改语义，只让它先被看见一次。
- **`run_id` 无鉴权**：服务只绑 `127.0.0.1`、单用户本机；知道 run_id 就能停别人的流。与第 1/2 片「读侧不校验归属」同级限制，本期不补。
- **单进程假设、明文落盘、坏索引自愈**：全部沿用第 1/2 片已知限制。
- **本片实现只过单测与 build 门禁**：逐字观感、停止响应延迟、代理缓冲都要真机。

## 偏离登记（`QODER.md`「禁止静默偏离」条款要求）

| # | 类型 | 偏离 | 理由 |
|---|---|---|---|
| 1 | 原型 | 停止钮、live 气泡、pending 步骤行全部新造 | 原型只有 1400ms 后整块替换，没有这些形态；「原型不是真相源」沿用第 1 片裁定 |
| 2 | 设计过程 | 终态事件从聊天里说的三条（`done`/`cancelled`/`error`）收敛成两条，停止走 `done{stopped:true}` | 前端只要一个终态判据；三条终态会让「撤 busy」写三处 |
| 3 | 接口 | 删 `POST /api/chat/send`，`trace`/`model` 字段随聊天链路消失 | 裁定 1；两字段前端零消费方，`echo` 的 `trace` 是验收物不动 |
| 4 | 接口 | 一次性响应换成 7 类 SSE 事件 | 逐 token 只能靠推流；`EventSource` 不能 POST，故 `fetch`+流解析 |
| 5 | UI | 停止钮复用 `.btn-send` 钮位并带文字「■ 停止」 | 「不做纯图标开关」的既有 UI 约定；原型此处恒为 `↑` |
| 6 | UI | 首 token 前保留三点占位，有 delta 后立刻让位 | 占位表达的正是「连接活着、模型还没开口」，删掉它这段等待就没有反馈（违反「动作必须有可见结果」） |
| 7 | 口径 | 上下文 meter 从「全部可见历史」改成「最近 40 条」 | 裁定 6，与 `HISTORY_MAX` 对齐；这是第 1 片登记、第 2 片转来的欠账 |

## 自检结论

- 覆盖：六条裁定各有落点（粒度→事件表+节点；真停→`RunControl`/`RunRegistry`/stop 端点；范围→前端两张表；留痕→`stopped` 字段链；删 send→契约表+405 锁；meter→`chat/utils.ts`）。无 TBD、无「类似第 N 片」。
- 一致性：错误表与前端 toast 指向同一批 detail；「守门只有一段」写进契约表并配一条逐字相同的测试；终态事件与「撤 busy」的对应关系在事件表下方明确。
- 顺序风险：P6 测试是第一个任务；`run_graph` 折返壳必须在改节点体之前先绿，否则回归锁失效；`stopped` 字段链（memory→store→schema→client）跨四层，任一层漏传就是静默丢标注——计划里按「先落盘层、再服务层、最后 UI」排。
- 歧义收敛：中间轮文本的归属（过程块，不落盘）、「已停止」不进 content、`kb-console` 临时键不留痕，三处都在正文写死判据，不留实现期解释空间。
