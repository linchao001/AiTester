# 聊天页 · 第 5 片：边界执法与授权 设计

参考原型：`prototype/index.html:1451-1455`（`PERM_MODES`，`free` 与 `strict` 两档，`strict` 现在 `disabled:true`，其 `desc` 逐字为「每次写盘 / 执行命令前需你授权」）。
现状依据（行号为 2026-10-04 实测值，第 13 项传输层修复后已漂移）：
`orchestration/agent_graph.py:103,125`（两处 `compile()` **无 checkpointer**）、`:105`（`ToolNode(tools, handle_tool_errors=_tool_error_message)`）、`:121`（`add_node("tools", …)`）、`:123-124`（`agent` 的条件边）、`:136-218`（`stream_graph` 折叠机，`:186` 按 `payload.get("tools")` 消费 updates）、`:153`（`stream_mode=["custom","updates"]`）；
`interaction/router.py:103`（`chat_send_stream`）、`:116`（`app.state.run_registry`）、`:120-124`（守门 `_GUARD_TYPES`/`_GUARD_MAP` 四类）、`:135`（`frames()` 分派）、`:195-208`（泵线程 + `relay()`，第 13 项修复件）、`:211-213`（`/chat/stop`，取消失败 → 404）；
`services/chat.py:106`（`prepare`）、`:118`（平台智能体短路）、`:130`（`dir_exists` 发送守卫）、`:158`（`~` 单点展开的注释与理由）、`:190-195`（`_persist`：user + assistant + steps + stopped）、`:199`（`stream_turn`）、`:239`（正常收尾落盘）、`:265`（断开落末轮已投递前缀 + `stopped=True`）、`:268`（`send` 折叠壳）；
`services/run_registry.py:15,19-39`（`new_run_id` / `start` / `cancel` / `finish`，**无 pending 槽**）；
`adapters/tools/__init__.py:19-40`（`build_default_registry(cwd, …)`，工具实例携带 `cwd` 现场构造）、`file_tools/fs_tool.py:19,23-26`（`cwd` 与 `_resolve`，绝对路径直通、**从不展 `~`**）、`file_tools/write.py:24-40`（`WriteInput.file_path`）、`file_tools/edit.py:29-50`（`EditInput.file_path`）、`command_tools/shell.py:41-48`（`ShellInput` 三参数 `command` / `timeout` / **`cwd`**）、`:182,199`（`pwsh` 与 `bash` 两个实例）；
`services/capability_config.py:15-96`（`TOOL_CATALOG` 十件，字段只有 id/group/icon/label/os/desc，**无危险度**）；`agents/catalog.py:22-38`（`case_design` 默认工具面）、`:47-64`（`kb_assistant` 工具面 = read/grep/glob/knowledge_search/prepare_kb_write，**无 write/edit/shell**）；
`memory/base.py`（`save(session_id, role, content, steps, stopped)`）、`memory/file_memory.py:24-36`（`save` → `SessionStore.append`，**纯追加，无改写最后一条的能力**）；
`services/project_config.py:26`（`ABS_PATH` 允许 `~/…`）、`:85`（`dangerous_root_reason`）、`:122`（`dir_exists`）、`:138`（`_validate_dir` 仅 create 调用）；
`frontend/src/api/client.ts:314`（`STREAM_EVENTS` 白名单 7 类）、`:346`（不在表内静默丢）、`frontend/src/pages/chat/streamState.ts:36-60`（折叠机，terminal 之后一律忽略）、`frontend/src/pages/chat/Composer.tsx:73-75`（🛡 chip busy 置灰 + `onToast("「严格权限」暂未开放，敬请期待")`）、`frontend/src/pages/kb/KbDraftCard.tsx:5`（内嵌卡状态机 `pending/writing/done/canceled/failed`）；
门禁基线：后端 **487 passed**、前端 `npm run build` **0 error**、`origin/main` = `8f907f4`。
存量数据：`backend/data/projects.json` 为空（0 项目），故本片**零迁移**。

## 背景与概念澄清

第 2 片把「归属 + 落点」做完时，把「边界执法」整块挪到了本片，并在 `2026-10-03-chat-project-design.md:18` 写了要件清单：「越界拦截、界外授权、`strict` 模式、逐次批准。它需要 LangGraph `interrupt` + checkpointer + pending 落盘 + 批准端点 + 弹层」。那片 `:35` 裁定 8 同时留了一句「`PERM_MODES.strict` 那条 `desc` 由第 5 片兑现」——这是本片对用户已经承诺过的文案。

两处措辞的**范围不一样**，是本片第一个要裁的东西：原型的 `desc` 是「每次写盘 / 执行命令前需你授权」（全量），第 2 片的要件是「越界拦截、界外授权」（只界外）。本片按用户裁定把它做成**三档**，两个范围都进去，用户自己选，不存在「哪个措辞被牺牲」。

第二个概念澄清：**执法必须是用户可控开关，不是默认洞也不是默认墙**。本片默认档 `free` 与第 4 片行为逐字相同（零行为变化，回归锁钉死），`boundary` / `strict` 是用户显式选择的档位。任何「顺手加一道拦」都不在本片范围。

第三个：第 2 片已经确立「项目 dir 就是产出物的落点，在里面任意读写是项目的语义本身」。所以本片的「界」= **项目 dir**，界内自动、界外才谈拦截；危险根四类闭集那条（第 2 片裁定 6）仍只在**创建时**校验，本片不重复判，也不扩大那张表。

## 前提实测（2026-10-04，假模型 + `InMemorySaver`，零网络、零写盘、未碰用户的 8000；一次性探针 `D:/tmp/probe_interrupt5.py`、`probe_gate_placement.py`、`probe_multi_interrupt.py`、`probe_gate_node.py`，用完即弃、不入库）

| # | 实测结论 | 对本片的意义 |
|---|---|---|
| P1 | `interrupt(payload)` 在 `stream_mode=["custom","updates"]` 下的形状是 updates 分片多一个键：`{"__interrupt__": (Interrupt(id=…, value=payload),)}`；**流干净结束、不抛异常**；`get_state().next == ("gate",)`、`get_state().interrupts` 给出载荷 | 挂起可观测、可重建弹层，不需要额外的挂起通道 |
| P2 | resume 那一段 `get_stream_writer()` 照样写得出来 | 续跑的过程行沿用同一个 custom 通道，前端折叠机只多认一支 |
| P3 | **无 checkpointer 时 `interrupt` 静默通过**：不报错、不挂起、工具一个都不跑，图直接提前收尾 | checkpointer 是硬前提而非可选项；漏装的表现是「回答凭空断掉 + 执法等于零」 |
| P4 | 同一 `thread_id` 复跑会把历史累积进 state（实测第二次跑完 `messages` 6 条，含上一轮的） | `thread_id` 必须 = `run_id`（每轮新建），只有 pending→resume 复用同一个 |
| P5 | **拦截点不能放在工具内部**：一条 AIMessage 里两个 gated 并行调用时 interrupt 逐个挂；答第二个时整个工具节点体从头重跑，**第一个工具的函数体连副作用一起又执行一遍**（实测 `RUNS=['write_a:a.md:approve','write_a:a.md:approve','write_b:b.md:reject']`） | `write` 重复覆盖、`edit` 再改一遍、`shell` 重复执行一次 `del`/`mkdir`——都是真事故，此形态否决 |
| P6 | **gate 节点形态成立**（`agent→gate→tools`，gate 逐条 `interrupt`、工具内部零 interrupt）：gate 体被重跑 3 次，**真写入各只 1 次**；用同 id 覆盖最后一条 AIMessage（`tool_calls` 只留批准的）后 `ToolNode` 只见批准的那条；被拒的调用由 gate 合成 `ToolMessage(status="error")` 回给模型，图继续跑到收尾；两条都拒时真写入为空 | 拦截点定在 gate；拒绝用「改写待执行清单 + 合成错误结果」表达，正对齐裁定「拒绝单条、本轮继续」 |
| P7 | gate 重跑会把**已答过的** custom 帧再发一遍（resume#2 段里 `gate c1` 重出现） | 前端折叠必须按 `call_id` 幂等去重，否则授权卡状态会被打回 |

## 用户裁定（2026-10-04，逐条确认）

1. **范围 = 三档模式**（选项 3）：`free` 全自动 / `boundary` 只批界外 / `strict` 写盘与命令全批。用户改裁了第 2 片裁定 8 里「不新增权限面板、🛡 chip 维持 toast 降级」那条——本片把 chip 变成真的三选，`PERM_MODES.strict` 的 `desc` 逐字兑现原型文案。
2. **挂起生命周期 = 内存**：`InMemorySaver` checkpointer + 内存 pending 表。刷新可复，**后端重启即丢**。与第 4 片 `RunRegistry` 只挂 `app.state` 的既有口径同形，不新建持久层。
3. **拒绝 = 单条否决、本轮继续**：被拒的调用以错误结果回给模型（走 P6 的合成 `ToolMessage`），本轮继续跑。「不想再跟这条回答纠缠」仍由第 4 片的停止按钮负责，拒绝按钮不兼任。
4. **记住 = 精确到路径/命令**：勾选后键为 `(工具 id, 规范化绝对路径 或 命令原文)`，同键不再问；写 `cases/a.md` 放行后再写 `a.md` 不问、写 `b.md` 仍问。记住表存服务端内存（与裁定 2 同生命周期），**不让前端持有放行权**。
5. **模式存 localStorage 并随请求传**：`aitester.chat.permMode`，`send` 请求带 `perm_mode` 字段，后端不落盘。平台智能体（`/kb`）不发该字段，也不受执法（见裁定 8）。已知代价：换浏览器不带走选择；字段由前端给出、本机单用户拓扑下可被伪造（与第 4 片已登记的「`run_id` 无鉴权」同性质，见风险节）。
6. **界外判据 = 写按路径判、命令全挂、读不拦**：`write`/`edit` 用 resolve 后的真实路径判定是否在项目 dir 内；`pwsh`/`bash` 在 `boundary` 档一律挂起（影响范围静态不可判，且 `ShellInput.cwd` 可把工作目录改到界外）；`read`/`grep_search`/`glob_search`/`web_search`/`knowledge_search` 不拦，只登记观察。
7. **挂起即断流，批准后续跑**：撞 `interrupt` 时 SSE 送一帧 `wait` 后**正常收尾**；批准/拒绝后前端另起一条流式请求续跑，输出接在同一条回答上。
8. **挂起时不落盘**（控制方对裁定 7 的措辞纠正，用户认可）：第 7 问的选项原文写了「本轮已投递前缀按待批挂起落盘」，实测后判定该措辞代价被低估——落半截行需要给 `SessionStore` 加「改写最后一条」的能力（`file_memory.py:24-36` 现为纯追加），blast radius 变大，且第 4 片读侧本就是「不落半截行」。改为：**前缀文本存 pending 表内存，刷新恢复用；续跑收尾时才一次性落全行**。重启后这条回答整条不存在（与裁定 2 的「重启即丢」对齐），而不是存在但残缺。
9. **平台智能体不受辖**：`kb_assistant` 的工具面（`catalog.py:53-59`）里没有 `write`/`edit`/`pwsh`/`bash`，唯一的写路径是 `prepare_kb_write` → 草案卡由用户点「✓ 确认写入磁盘」，那条确认不经工具执行。故本片对 `/kb` 无执法对象，🛡 chip 只出现在聊天页，`/kb` 请求不带 `perm_mode`。
10. **待批语义四条**（控制方裁定、用户认可）：同一会话**允许多条待批并存**（新发送不隐式取消旧 pending，避免静默作废别人正在等的轮）；**待批期间按停止 = 摘除该 pending 并落截断痕 `stopped=true`**（第 4 片停止语义的延伸，用户永远有出口）；**删会话级联清该会话的 pending**（与第 2 片删项目级联同口径）；**不设 TTL、不设鉴权**。

## 架构与拦截点

`build_agent_graph` 的拓扑由 `agent↔tools` 变为 `agent→gate→tools→agent`，两处 `compile()` 都改成 `compile(checkpointer=InMemorySaver())`（P3：不装就是假执法）。无工具退化分支同样装 checkpointer，不含 gate。

gate 节点体只做三件事，且**必须是纯判定**（P5 的教训：含 interrupt 的节点体重跑，重跑前的一切副作用都会二次执行）：

1. 取最后一条 AIMessage 的 `tool_calls`，逐条算 `needs_approval(...)`；
2. 需要批且记住表未命中的，按数组顺序**逐条** `interrupt(payload)`（P1/P7：一次只挂一条）；
3. 按决策改写清单：批准的留在 `tool_calls` 里，被拒的从 `tool_calls` 剔除并为它合成 `ToolMessage(content="用户拒绝了此操作：…", status="error")`，用**同 id 覆盖**那条 AIMessage（P6）。

`ToolNode` 一行不改——它永远只执行已批准的调用，工具内部零 interrupt，因此不存在副作用二次执行。gate 之后用**条件边**：有批准 → `tools`，全拒 → 直接回 `agent`。理由是把「拒绝」的语义建立在实测过的行为上，而不是 `ToolNode` 拿到空 `tool_calls` 时「恰好不报错」这个巧合（P6 场景 3 实测确实不抛，但那是未承诺行为）。

装配参数：`thread_id = run_id`（P4，每轮新建，pending→resume 复用）；`config.configurable` 同时带既有的 `RUN_CONTROL_KEY`，取消位仍能在节点之间被读到——待批期间用户按停止就是走这条路。

「记住」表的**写入**放在拿到决策之后，同键同值覆盖幂等；判定函数只读表。三档判据、路径规范化、记住表读取全部是纯函数，这是 gate 可安全重跑的前提，也是本片最容易实现错的地方，必须配判别性测试（P5/P6 那条重跑计数）。

## 三档判据（一张表 + 一处函数）

执法类别（类别只由工具 id 决定，不新增 catalog 字段——`capability_config.py:15-96` 的字段表保持原样，避免配置面与权限面两套真相）：

| 类别 | 工具 id | 载荷取用字段 | `boundary` 档 | `strict` 档 |
|---|---|---|---|---|
| 写盘类 | `write` `edit` | `file_path` | 越出项目 dir 才挂 | 每次挂 |
| 命令类 | `pwsh` `bash` | `command`（`cwd` 若有则一并展示） | **一律挂** | 每次挂 |
| 知识库写 | `save_to_knowledge` | 无路径参数（服务端构造） | 视为界内，不挂 | 挂 |
| 只读类 | `read` `grep_search` `glob_search` `web_search` `knowledge_search` | — | 不拦 | 不拦 |

`free` 档对以上全部直接放行，**零 interrupt、零挂起**。

`needs_approval(tool_id, args, perm_mode, project_dir, remembered) -> bool` 是唯一判据口。两条硬要求（第 2 片消费对称性教训的正面写法）：

- **路径解析复用 `fs_tool._resolve` 那同一个口**，不在 gate 里另写一份 `Path(...)` 拼接；
- `project_dir` 取 `expanduser().resolve()` 之后的值（`chat.py:158` 已有的单点展开），**不是 `projects.json` 里的原串**——否则 `~` 项目会出现「判定说界内、执行落进 `<进程目录>/~/…`」。

载荷与展示：授权卡显示「图标 + 工具名 + 中文动作 + 目标」。路径**相对项目根**展示（沿用 KB 卡片不外泄 `abs_display` 的口径）；命令**不截断、全文可见**——`DETAIL_MAX=80` 只用于过程行摘要，批准前必须看全要执行的命令。

锁档：`perm_mode` 存进 pending 条目，续跑按**创建时那一档**判定，不读请求当下的字段值；有 pending 时 chip 置灰（复用 Composer busy 置灰那条既有形态，不新增控件类）。

## 数据流与端点契约

```
POST /api/chat/send/stream  {…, perm_mode}
  → ChatService.prepare（守门一字不动：四类 400/404 detail 逐字保持）
  → stream_turn → graph.stream(custom+updates, thread_id=run_id)
  → gate 撞 interrupt
      → stream_graph 折出 {type:"wait", run_id, call_id, tool, args_display, command}
      → router frames() 发 SSE `wait` 帧后正常收尾（泵线程照旧 close，不落盘——裁定 8）
      → PendingRegistry[run_id] = {thread_id, key, agent_id, project_id, perm_mode,
                                   queue, decided, prefix_text, steps, created_at}

用户点「✓ 批准 / ✕ 拒绝」（可勾「本次会话内同路径不再询问」）
  → POST /api/chat/approve {run_id, call_id, decision, remember}   → 204（只登记决策 + 落记住表）
  → POST /api/chat/resume/stream {run_id}                          → SSE（同一条通道）
      → 重跑 send 同款目录守卫（P: 挂起期间目录被删时，write 的 mkdir(parents=True) 会静默建出整棵树）
      → graph.stream(Command(resume={decision, remember}), thread_id=同一)
      → 若队列里还有下一条 → 再出一帧 wait；否则跑到收尾 → _persist 一次性落全行

刷新 / 重开页面
  → GET /api/chat/pending?session_id=…   → 待批卡 + prefix_text（内存表；重启后为空）
```

契约变更：`SendRequest` 多一个可选 `perm_mode`（缺省 `free`，非 `free`/`boundary`/`strict` 三值之一 → 400 中文 detail）；新增 `ApproveRequest` / `ResumeRequest` / `PendingResponse` 三个 schema；`run_graph` / `stream_graph` 的事件表多一种 `wait`，`finish` 多一个 `pending: bool`（区分「跑完」与「挂在 gate 上」）。落盘四层（`memory` → `session_store` → 读侧 schema → client）**不动**——裁定 8 的直接收益。

## pending 表与生命周期

`PendingRegistry` 与 `RunRegistry` 同层同风格，只挂 `app.state`（裁定 2）。条目字段：

| 字段 | 用途 |
|---|---|
| `run_id` / `thread_id` | 同一个值（P4），`approve`/`resume`/`stop` 都以它定位 |
| `key`（`{agent_id}:{session_id}`） | 会话归属；删会话据此级联清（裁定 10） |
| `perm_mode` | **创建时那一档**，续跑按它判定，不读请求当下的字段值（锁档） |
| `queue` | 尚未决策的待批项（工具 id、规范化目标、命令原文、展示用名） |
| `decided` | 已答项与决策，供刷新时把卡片渲染成「已批准 / 已拒绝」痕迹 |
| `prefix_text` / `steps` | 已投递正文前缀与过程行，**只存内存**，供刷新恢复渲染（裁定 8） |
| `created_at` | 展示与排查用；无 TTL（裁定 10） |

停止与 pending 的交叠（裁定 10 第二条）：待批期间 `POST /chat/stop` 命中该 run → 摘除 pending、以 `prefix_text` + `steps` 落一条 `stopped=true` 的 assistant 行（这是本片唯一一次在收尾之前落盘，由停止触发，与第 4 片断开落盘同语义）、图与线程随 `RUN_CONTROL_KEY` 取消位收摊；此后 `resume` 必回 404。

`stop` 与 `approve` 同时到达时以表内状态定先后：先摘除者胜，续跑装配时条目不在即 404，不做乐观重试。

## 错误处理（detail 逐字，全中文、可照做）

| 场景 | 码 | detail 逐字 |
|---|---|---|
| `perm_mode` 非三值之一 | 400 | `无效的权限模式，请选择自由权限、只批界外或严格权限` |
| `approve` 的 `run_id` 不在 pending 表（未知、已停止、或后端已重启） | 404 | `这条回答已经结束，无法再批准` |
| `approve` 的 `call_id` 不在该 run 的待批队列（已答过） | 409 | `这条授权请求已经处理过了` |
| `resume` 的 `run_id` 不在 pending 表 | 404 | `这条回答已经结束，无法再批准` |
| `resume` 时项目目录守卫不过 | 400 | **与 `send` 逐字相同**：`项目「{name}」的目录 {dir} 不存在或不可访问，请到项目页确认路径` |
| 续跑段里模型/工具异常 | — | 沿用第 4 片口径：出 `error` 帧，toast 一律固定中文兜底，英文异常原文不进 UI |

`resume` 的目录守卫必须复用 `prepare` 那一段判据（同函数、同 detail），不另写一份——第 2 片「守门只有一段」那条教训在这里同样适用：挂起可能持续很久，期间用户会把目录改名或删掉，而 `write.py` 的 `mkdir(parents=True)` 会在路径打错时静默建出整棵目录树。

## 事件与前端折叠

新增 SSE 事件 `wait`，帧格式沿用 `router.py:81-83` 的 `event: <name>\ndata: <json>\n\n`。三处接线，一处不缺也不多：

- `router.py:135` 的 `frames()` 多一支 `wait`；
- `client.ts:314` 的 `STREAM_EVENTS` 从 7 类变 8 类（`:346` 的「未知事件静默丢」逻辑保持，忘了加白名单的症状是授权卡根本不出现）；
- `streamState.ts:36-60` 折叠机多一支：**按 `call_id` 幂等**（P7 重发同一张卡不能把已决策的状态打回），且 `wait` 之后 terminal 收尾要把该轮标成「等待授权」而不是「已完成」。

`AuthCard` 复用 `KbDraftCard` 那族形态（内嵌在气泡里，不做遮罩弹窗——同类控件同一种形态），状态机三态 `pending / approved / rejected`，已决策的卡留在流里做痕迹。授权期间 live 气泡保留并显示「⏳ 等待授权」，停止按钮照旧可用（裁定 10 第二条）。

chip 三档文案（`free` 与 `strict` 的 `desc` 逐字取原型 `:1452-1453`，`boundary` 为新增）：

| id | icon | label | desc |
|---|---|---|---|
| `free` | 🛡 | 自由权限 | 所有操作（写文件、执行命令等）自动执行，无需你授权 |
| `boundary` | ⚑ | 只批界外 | 项目目录外的写入、以及所有命令执行需你授权 |
| `strict` | 🔒 | 严格权限 | 每次写盘 / 执行命令前需你授权 |

## 测试策略

判别性优先，每条都要能因代码错而失败：

1. **判定矩阵表驱动**：三档 × 四类别 × 界内界外 × 记住命中/未命中，逐格钉 `needs_approval` 的返回值；`free` 全 False 单独钉（默认档零行为是红线）。
2. **gate 重跑不重复副作用**（P5/P6 钉成回归锁）：一条 AIMessage 里两个待批并行调用，两个都批准后真执行次数必须**恰为 2**；只批一个则真执行为 1、被拒的那条不执行；两个都拒则 `ToolNode` 一次不跑且本轮继续收尾。
3. **拒绝的形状**：合成 `ToolMessage` 的 `status == "error"`、内容含中文「用户拒绝了此操作」、`tool_call_id` 对齐；覆盖后的 AIMessage 同 id 且 `tool_calls` 只留批准的。
4. **同 id 覆盖 + 条件边**：全拒时不经过 `tools` 节点（断言节点访问序列，不依赖 `ToolNode` 空跑行为）。
5. **`thread_id` 隔离**（P4）：同一会话连发两轮，两个 `run_id` 各自独立，state 消息不累积；resume 用同一 `thread_id` 才续得上。
6. **`~` 项目的对称性**：项目 dir 配成 `~/x` 时，判定用的解析口与 `fs_tool._resolve` 一致，界内文件真的落在展开后的家目录路径下（第 2 片教训的正面锁）。
7. **事件与折叠**：`wait` 帧的字段严格取键；`streamState` 对同一 `call_id` 重发的 `wait` 幂等；`wait` 后收尾不误标「已完成」。
8. **端点面**（码与文案对齐错误处理表）：`perm_mode` 非三值 → 400 逐字；`approve` 未知/已结束 run → **404** 逐字；`call_id` 不在该 run 队列 → **409** 逐字；`resume` 时目录守卫不过 → 400 且 detail 与 `send` **逐字相同**（TestClient 断言字串，且断言目录**没被建出来**）；`GET /pending` 重启后为空；approve→resume 端到端在 TestClient 上跑到收尾并**只落一行 assistant**（裁定 8）。
9. **停止与 pending 交互**（裁定 10）：待批期间 `POST /chat/stop` → pending 摘除 + 落 `stopped=true` 截断行 + 该 run 再 `resume` 回 404「这条回答已经结束」。
10. **删会话级联**：删会话后 `GET /pending` 不含其条目。
11. **回归门禁**：既有 487 条**只增不减**；`free` 档下事件序列与第 4 片逐字相同（既有流式测试整套即回归锁）；前端 `npm run build` 0 error；`/kb` 全链路零变化（裁定 9）。

## 验收清单（真机走查项，实现完成后逐条过）

1. 默认档是「自由权限」，发消息行为与第 4 片完全一致，一张授权卡都不弹。
2. chip 点开是三档、文案逐字对齐上表；切到「只批界外」后刷新页面档位保留。
3. `boundary` 档：让模型在项目 dir 内写文件 → 不挂起、直接落盘、树自动刷新。
4. `boundary` 档：让模型往界外写（如 `D:/tmp/…`）→ 气泡里出授权卡，工具**尚未执行**（磁盘上无该文件）。
5. 卡上点「✓ 批准」→ 文件真出现，路径在项目 dir 之外由用户当场许可，回答继续到收尾。
6. 卡上点「✕ 拒绝」→ 模型收到拒绝并继续作答，本轮不被终止，界外文件始终不存在。
7. 「本次会话内同路径不再询问」勾上后再让模型写同一文件 → 不弹卡；换个路径 → 仍弹。
8. `strict` 档：项目 dir 内的 `write` 也弹卡；一次并行两个写 → **串行两张卡，逐条批**，最终两个文件都存在且各只写一次。
9. `strict` 档：`pwsh` 命令卡里命令全文可见（不截断），批准后确实执行。
10. `boundary` 档下 `read`/`grep` 读界外文件 → 不弹卡（读不拦）。
11. 待批期间刷新页面 → 授权卡与前缀文本恢复；点批准仍能续跑。
12. 待批期间点「停止」→ 卡消失、会话里留下截断痕（「（已停止）」）、再 resume 回 404。
13. 待批期间挂起的项目目录被删 → 批准后续跑被 400 拦下，detail 与发送时逐字相同，且**没被 `mkdir` 建出来**。
14. 后端重启后旧待批消失，会话里不出现半截 assistant 行（裁定 8 的正向确认）。
15. 有 pending 时 chip 置灰；档位与续跑判定一致。
16. `/kb` 页：无 chip、无授权卡，草案卡照旧，链路零回归。
17. 页面 0 处 `zhb`、0 个死按钮、0 新增色值；console 0 error。
18. 存量 `projects.json` 为空 → 走查前后 md5 对照，探针产物全部还原。

## 范围外

- **不做持久化 pending**（裁定 2）：重启即丢，不给 `SessionStore` 加改写能力，不新建落盘件。
- **不做逐命令白名单/正则规则编辑**（业界有 Cursor auto-run allowlist 那种形态，本片只做三档 + 路径/命令级记住；要规则编辑器是另一片）。
- **不摘任何工具**：`pwsh`/`bash` 照旧由能力配置勾选，本片只在执行前加判定。
- **不做鉴权**：`perm_mode` 与 `approve`/`resume` 端点均无身份校验，本机单用户拓扑；已登记进风险。
- **不动危险根四类表**（第 2 片裁定 6），不新增黑名单，不在运行时判危险根——界就是项目 dir。
- **不改 KB 草案卡机制**（裁定 9）。
- **不做批量批准/一次批准整轮**（P5 之后逐条批是本片接受的代价）。

## 风险与已知限制

- **假执法风险已闭死但形态脆**：P3 那条（无 checkpointer 时静默通过）意味着「忘了装 checkpointer」不会报错、只会让边界形同虚设。测试 5 与验收 4 是它的双人锁；后续任何人把 `compile()` 改回无 checkpointer，验收 4 会出现「不弹卡且界外文件已经写出去了」。
- **同轮已批准的调用要等后面那条批完才执行**：一条 AIMessage 里两个待批时，interrupt 逐个挂（P5/P7），批准第一条后 `ToolNode` 并不开跑，而是等第二条也决策完才一起执行（P6 实测：`resume#1` 段无 ToolMessage，`resume#2` 段两条一起出）。语义无害——工具确实一个都没提前跑，界外文件在批准前不存在——但长任务里表现为「批了才动」，验收 8 是它的走查锁。
- **前端可伪造 `perm_mode`**：字段随请求走、无鉴权、无落盘，与第 4 片已登记的「`run_id` 无鉴权」同性质。本片按用户裁定接受（执法是用户可控开关，不是对抗性安全边界）；后端一旦搬远端，这条必须重审。
- **无 TTL 的 pending 会攒**：同一会话允许多条并存且不自动过期，只靠后端重启清理。内存表条目很小（前缀文本 + 队列），但长时间不重启的会话理论上可累积。登记不修。
- **续跑期间取消与批准的竞态**：`stop` 与 `approve` 同时到达时，pending 摘除与续跑装配的先后由表内状态决定，需在实现里显式判（摘除后续跑必 404）。
- **`updates` 分片形状**：带 checkpointer 后实测 `{"tools": {"messages": [...]}}` 形状不变（P6），但多了 `__interrupt__` 键的分片；折叠机对未知键走 `payload.get("tools") or {}` 天然跳过，已实测。真模型 + 并行分支下的形状差异不在假模型覆盖范围，归走查第 8 项。
- **读侧仍不校验归属**（第 2 片遗留不变）。

## 偏离登记

| # | 偏离 | 理由 |
|---|---|---|
| 1 | 改裁第 2 片裁定 8 的「不新增权限面板、🛡 chip 维持 toast 降级」 | 用户裁定三档；`Composer.tsx:73-75` 那条 toast 文案随之下线 |
| 2 | 改裁第 2 片 `:18` 要件里的「pending 落盘」为「pending 存内存」 | 用户裁定 2；不给 `SessionStore` 加改写能力 |
| 3 | 控制方纠正第 7 问选项原文（挂起时落半截行 → 不落盘） | 裁定 8，用户认可 |
| 4 | 拦截点从「工具内部」改到「gate 节点」 | P5 实测重复副作用；用户认可（「认可」第 1~2 节） |
| 5 | `strict` 档的执法范围含 `save_to_knowledge`，`boundary` 档不含 | 它的写入路径由服务端构造，无「界外」语义；裁定 6 表 |
| 6 | 待批态下停止按钮兼任唯一退出出口（拒绝按钮不终止本轮） | 用户裁定 3：拒绝只管单条 |
| 7 | 第 2 片 spec `:18` 的「越界拦截」措辞在本片扩为三档（含界内全批） | 用户裁定 1；回写第 2 片 spec 的裁定 8 与本条由实施计划最后一个任务执行 |

## 自检结论

自检跑过，发现并就地修掉三处：① 错误处理一节在初稿压缩时被漏掉，而测试 8 已引用逐字文案 → 补了「错误处理」表（六个场景，码 + detail 逐字）并新增「pending 表与生命周期」小节（字段表 + 停止/并发的先后规则）；② 测试 8 的响应码与新表不一致（原写 400/400，实为 404/409）→ 已对齐；③ 一条风险描述的是**被 P5 否决的那个形态**（「未挂起的并行调用也会等到批准后一起执行」）→ 改成 gate 形态下的真实行为「同轮已批准的调用要等后面那条批完才执行」，并配 P6 实测证据与验收 8 走查锁。

其余四项检查：占位符扫描无 TBD/TODO/「适当处理」，所有 detail 文案、字段名（`file_path`/`command`/`cwd`）、工具 id、事件名、localStorage 键名（`aitester.chat.permMode`）均为确定值。前后一致性：三档判据表 ↔ 裁定 1/6 ↔ 错误处理表 ↔ 测试 1/8 ↔ 验收 3~10；`thread_id=run_id` ↔ P4 ↔ 测试 5；不落盘（裁定 8）↔ pending 字段表的 `prefix_text` ↔ 测试 8「只落一行 assistant」↔ 验收 12/14 ↔ 范围外第 1 条；`wait` 事件 ↔ 三处接线 ↔ 测试 7 ↔ 验收 4。歧义检查：「界外」有唯一判据函数与唯一解析口（复用 `fs_tool._resolve` + `expanduser().resolve()`）；「记住」的键、写入时机、存储位置钉死；全拒路径走条件边而非 `ToolNode` 空跑；锁档规则写明续跑用创建时的档位。范围检查：单一实施计划可承载，预估 9 个任务（后端判定纯函数 → gate 节点与 checkpointer → pending 表 → 事件 `wait` 与 `finish.pending` → 三端点与守卫复用 → 前端传输与折叠幂等 → `AuthCard` → chip 三档与接线 + 第 2 片 spec 回写 → 全量门禁与走查交接）。
