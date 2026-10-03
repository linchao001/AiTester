# 聊天页 · 第 1 片：会话闭环 设计

日期：2026-10-03　状态：**第 1 片已实现**（会话落盘 + 三端点 + `/chat` 实页；项目维度/工作区/流式留后续片）

计划期修订（写实施计划时逐项取证发现，三处均已改入本文并同步用户）：① `.md-preview` 默认 `display:none`，气泡内须补显示规则，否则回复整段不可见；② `send` 的 404 只对 `sess_*` 形态成立，`kb-console` 等临时键按裸键走进程内记忆（否则 /kb 页与一批回归测试同时打脸）；③ 会话延迟到首条消息落盘时才创建（否则未配置模型时每发一次就留一个 0 消息幽灵会话）。

参考原型：`prototype/index.html` 聊天视图 `:566-672`、侧栏 `:568-591`、分组 `:1257`、`renderSidebar :1260-1285`、`renderWelcome :1309-1325`、`renderMessages :1290-1307`、`applyCtx :1365-1372`、新建会话 `:1375-1379`、`send :1227-1247`、标题截断 `:1435`、上下文估算 `:1393-1420`、composer `:611-624`
现状依据：`backend/src/aitester/services/chat.py:34-95`（7 层链路与 `{agent_id}:{session_id}` 键）、`memory/base.py:4-9`（MemoryStore 协议）、`memory/in_memory.py:1-9`（进程内、重启即失）、`orchestration/agent_graph.py:68-90`（`run_graph` 现只回 `{tool, result}`）、`interaction/schemas.py:16-36`（`SendRequest`/`SendResponse`）、`frontend/src/App.css:43-311`（聊天页样式已全量存在、零消费方）、`frontend/src/pages/ChatPage.tsx:11-20`（占位组件）

## 背景

聊天后端能跑（7 层链路 + agent loop + 工具注入 + `case_design` 实例），但**没有任何会话持久化**：记忆是进程内 `dict`，重启即全丢，且不存在会话列表/历史/删除端点。前端 `ChatPage.tsx` 是 21 行占位。原型的左侧会话历史因此在真机属于净新增后端能力，不是一个 UI 活。

本期只做「会话闭环」：让聊天真正可连续使用、重启不丢、会话可管理。项目维度、右栏工作区、流式输出各自独立成片（见「范围外」）。

## 用户裁定（2026-10-03，逐条确认）

1. **范围 = 第 1 片会话闭环**：会话落盘 + 三个会话端点 + `/chat` 实页；不含项目绑定、右栏工作区、流式。
2. **「＋ 新建会话」首条消息才落盘**：点击后只在前端建空会话并显示欢迎态，首次 `send` 由服务端创建并回 id。理由：列表不被没聊过的「新会话」污染。代价：未发言的会话刷新即丢——本期接受。
3. **模型 chip 只读**：展示当前生效模型，点击进列表但不可选，底部「⚙ 设置 · 模型…」跳设置。沿用模型配置专项「会话级选模型不在本期」的裁定。
4. **过程块显示真实工具调用序列**：标题 `🔧 执行过程`，每条含工具名、成败、参数摘要。
5. **欢迎态 chips 换成对齐当前智能体的文案**（本期只有 `case_design`，原型 4 条里「自动化编码：接口脚本落地」「生成测试报告」超出其职责）。
6. **加历史裁剪守卫 `HISTORY_MAX = 40`**：进 prompt 只取最近 40 条消息，磁盘保留全量。这是成本裁定——不截断则真实模型下长会话每轮 token 线性上涨。

### 相对原型的 8 处刻意偏离（用户已认可）

| # | 偏离 | 理由 |
|---|---|---|
| 1 | 分组按 `updated_at` 现算 | 原型 `:1257` 是存死字符串，无任何日期逻辑 |
| 2 | **不放** 📁 工作区按钮、`⋮ 更多`、dir chip | `⋮` 原型即 inert 死按钮；另两者属第 2/3 片，本期放了就是承诺做不到的东西 |
| 3 | `🧠 Thinking` → `🔧 执行过程` | 原型那段思考文本是写死假数据（`:1443-1444`） |
| 4 | **不做** `.case-card` / `.report-card` | 原型 `:1170-1195` 是写死 HTML 常量，非模型产物；模型输出 markdown 表格走 `mdRender` + `.md-preview` 排版 |
| 5 | 会话级模型不可切 | 见裁定 3 |
| 6 | 发送期间禁用输入/发送/侧栏操作 | 原型不 gate（它 1.4s 出假回复）；真机数秒到数十秒，不 gate 会重复发 |
| 7 | 空会话不落盘 | 见裁定 2 |
| 8 | 权限 chip 保留但不 gate 任何行为 | 原型 `:1451-1476` 本来也是纯装饰；不假装它生效 |

## 控制端裁定（工程判断，可推翻）

- **存储双文件**：`backend/data/sessions/index.json`（元信息，`FileJsonConfigRepository` 同目录 tmp + `os.replace` 原子写）+ `backend/data/sessions/<session_id>.jsonl`（消息逐行追加）。理由：每次发消息重写全量历史是写放大，jsonl append 是 O(1)。`.gitignore:17` 已整目录忽略 `backend/data/`，**无需改 gitignore**（已核，不是待办）。
- **会话 id**：`sess_` + 8 位小写十六进制（`uuid4().hex[:8]`），校验 `/^sess_[0-9a-f]{8}$/`，**服务端生成**，前端不造 id（与 `proj_` 同形态）。
- **只认 `sess_` 形态，其余传入 id 按裸键续写**（计划期发现并锁定的兼容裁定）：`send` 收到非空 `session_id` 时——`sess_*` 且不在索引 → 404；非 `sess_*` 形态（`/kb` 页的 `kb-console`、既有测试的 `s1/s2/s4`）→ 继续按 `{agent_id}:{session_id}` 走进程内记忆，**不落盘、不建会话、行为零变化**。理由：这些键是刻意的临时键，把它们升级成「必须存在于索引否则 404」会同时打脸 /kb 页与一批回归测试。
- **会话在首条消息落盘时才创建**（成本与洁净裁定）：`send` 先纯计算生成 id（不写盘）→ `agent_runtime.build()`（此处才可能抛 404/400）→ 跑图 → 成功后由 `FileMemoryStore.save` 首次 append 触发 `create`。失败发送（未知智能体 / 未配置模型 / 上游 502）**不留任何磁盘痕迹**，否则 400 一次就多一个 0 消息幽灵会话。
- **记忆键不变**：仍是 `{agent_id}:{session_id}`（`chat.py:89` 不动）。`agent_id` 与 `sess_*` 字符集均不含冒号，故 `FileMemoryStore` 按首个 `:` 切分是安全的——这条是刻意的耦合，测试锁住键形态。**是否用文件记忆按 session 段形态判**：`sess_*` → `FileMemoryStore`，否则 → 进程内（见上一条兼容裁定）。
- **平台智能体不落地**：`kb_assistant` 等 `PLATFORM_AGENT_CATALOG` 成员继续走进程内记忆，不进 sessions 目录。理由：本期不承诺 KB 助手历史持久化，且避免 /kb 页每次追问都写文件。为此在 `agents/catalog.py` 新增 `is_platform_agent(agent_id) -> bool`，并把 `agent_runtime.py:60` 那处内联集合推导换成调用它（单一判据，勿两处各写一份）。
- **标题**：首条 user 消息 `strip()` → 换行转空格 → 取前 16 字符（对齐原型 `:1435`），空则 `新会话`。生成后**不再改**（本期无 rename）。
- **时间戳**：`created_at` / `updated_at` / 消息 `ts` 一律 epoch 毫秒整数；分组与显示在前端按本地时区算（用户在中文时区，后端不做时区判断）。
- **`message_count`**：jsonl 行数（user 与 assistant 都算）。
- **不做**：rename、pin、归档、按项目过滤、会话级模型、token 用量统计。
- **并发**：`SessionStore` 内部一把 `threading.Lock` 串行化 `index.json` 重写（路由是 sync `def`，跑在线程池里，真会并发）。同会话并发 send 的消息交错顺序本期不加 per-session 锁——前端的 in-flight 禁用（偏离 6）实际杜绝它，作为已知限制记录。
- **`send` 的 `session_id` 默认值改为 `""`**（现为 `"default"`，`schemas.py:16-19`）：空 = 新建会话。`/chat/echo` 的裸 `session_id` 语义不变。现有测试若依赖默认值需显式传 id。
- **`create_app` 新增 `sessions_dir: Path | None = None` 注入缝**（与 `projects_path` 同款，默认 `DATA_DIR / "sessions"`）；测试一律 `tmp_path`，绝不写真实目录。

## 数据模型与模块接口

```python
# services/session_store.py
SESSION_ID_RE = re.compile(r"^sess_[0-9a-f]{8}$")   # 唯一形态判据：chat 装配与 store 内部共用
def is_session_id(value: str) -> bool               # SESSION_ID_RE.match 的具名包装

class Session:  id, agent_id, title, created_at, updated_at, message_count
class ChatMessage:  role, content, ts, steps: list[dict] | None

class SessionStore:
    def __init__(self, root: Path) -> None
    def new_id(self) -> str                                   # 纯计算（去重），不落盘
    def create(self, session_id: str, agent_id: str, first_message: str) -> Session
                                                              # 幂等：已存在直接返回既有；title 在此算完
    def list(self, agent_id: str) -> list[Session]            # updated_at 降序
    def get(self, session_id: str) -> Session | None
    def messages(self, session_id: str) -> list[ChatMessage]
    def append(self, session_id: str, role: str, content: str,
               steps: list[dict] | None = None) -> None       # 未注册 → SessionStoreError；bump updated_at / message_count
    def delete(self, session_id: str) -> bool                 # 删 index 条目 + 删 jsonl
```

**本期不加 `set_title`**：`create(...)` 生成期就把标题算完，另开一个改标题的 API 即是死接口（rename 在范围外）。

`SessionStoreError`（未知 id / id 形态非法）→ 路由层映射 404，detail 中文。

```python
# memory/file_memory.py  —— 实现 memory/base.py 的 MemoryStore 协议
class FileMemoryStore:
    def __init__(self, store: SessionStore) -> None
    def save(self, session_id: str, role: str, content: str) -> None
        # session_id 是 ChatService 传入的完整键 "{agent_id}:{session_id}"，此处按首个 : 切分；
        # 键的 session 段尚未注册且 role=="user" → store.create(...)（首条消息即建会话），否则 append
    def recall(self, session_id: str) -> list[dict[str, str]]          # 未注册会话返回 []
```

装配（`services/chat.py`）：`ChatService.__init__` 增可选 `sessions: SessionStore | None`。`send()` 里：非平台智能体且 `session_id` 为空 → `sessions.create(agent_id, message)`；记忆实现选择 = `sessions is not None and is_session_id(sid) and not is_platform_agent(agent_id)` → `FileMemoryStore`，否则沿用 `InMemoryMemoryStore`（终审期文字校正：代码 `ChatService.send` 的 `use_file` 判据还要求会话段是 `sess_*` 形态，缺它就会把 `kb-console` 这类临时键收进文件记忆；行号会漂，认符号不认行号）。**记忆按单次调用选，不写回 `self.memory`**：`_complete` 增可选参数 `memory: MemoryStore | None = None`（缺省仍取 `self.memory`），`send()` 把选中的实例传进去。理由：同一个 `ChatService` 进程内同时服务项目智能体与平台智能体，改实例态会让一次 `/chat` 之后污染 `/kb` 追问。平台智能体 + 空 `session_id` 的组合不建会话（平台智能体根本没有会话概念），按现状用传入键；`/kb` 页助手继续显式传 `kb-console`（`KbPage.tsx:325` 现即是），故其行为零变化。`_complete` 内 `history = self.memory.recall(key)` 之后紧跟 `history = history[-HISTORY_MAX:]`（`HISTORY_MAX = 40` 常量在此文件，注释写明「只截 prompt，磁盘保留全量」）。`create_app` 在装配处建 `SessionStore(sessions_dir)` 注入 `ChatService`（与 `projects_path` 同一条缝）。

`orchestration/agent_graph.py::run_graph` 的 `tool_traces` 每项由 `{tool, result}` 扩为 `{tool, result, ok, round, detail}`：`ok` 取 `ToolMessage.status != "error"`（langgraph 错误分支显式置 `status="error"`，`prebuilt/tool_node.py:1011,1277`）；`round` 为第几轮 agent↔tools 循环（每遇到一条带 `tool_calls` 的 `AIMessage` 递增）；`detail` 按 `tool_call_id` 回指对应 `AIMessage.tool_calls` 取该调用的 args，`json.dumps` 后截断 80 字符。`result` 仍保留（草案与 trace 逻辑依赖它，不动）。

## API 契约

| 端点 | 请求 | 响应 |
|---|---|---|
| `GET /api/chat/sessions?agent_id=case_design` | query `agent_id` 必填 | `{sessions: [{id, agent_id, title, created_at, updated_at, message_count}]}`；未知或平台智能体 → **空列表 200**（不泄露、不报错） |
| `GET /api/chat/sessions/{id}/messages` | — | `{session_id, messages: [{role, content, ts, steps}]}`；不存在 → 404 |
| `DELETE /api/chat/sessions/{id}` | — | **204 + `return None`**（与 `/api/projects` 同款）；不存在 → 404 |
| `POST /api/chat/send` | `{session_id: str = "", message: str, agent_id: str = "case_design"}` | 原 `{reply, trace, model, drafts}` **+ `{session_id, title, steps}`** |

`StepInfo = {tool: str, ok: bool, round: int, detail: str}`。`send` 响应里的 `session_id` 是服务端最终确定的会话 id（新建时为生成的 id），`title` 同新建或既有会话，`steps` 是**本轮** assistant 消息的过程块（与落进 jsonl 的那份同一对象，不是全会话累计）。`session_id` 传入非空但**形态合法（`sess_*`）且不存在** → 404（文案同消息端点），不做静默新建；形态非 `sess_*` 的传入 id（`kb-console` 等临时键）按裸键走进程内记忆，不建会话、不报错（兼容裁定见控制端段）。`trace` 字段保留原样，`/chat/echo` 的 7 层回归断言不破。错误语义：422 空 message、404 未知智能体、400 无可用模型、502 上游——全部沿用 `router.py:55-79` 现状；404 会话 detail 文案 `会话不存在或已被删除`；磁盘写异常不包装（FastAPI 500，前端 toast `会话保存失败`）。

## 前端

组件拆分（不写成 KbPage 那种 500 行大文件）：`pages/ChatPage.tsx`（装配与状态机）+ `pages/chat/SessionPane.tsx`（侧栏）+ `pages/chat/MessageList.tsx`（含 welcome 与气泡）+ `pages/chat/Composer.tsx` + `pages/chat/utils.ts`（`estTokens` / `contextUsage` / `groupSessions` / `fmtTime`）。

- **状态**：`agentId`、`sessions`、`activeId`（`null` = 未落盘新会话）、`messages`、`input`、`busy`、`toast`。无新增全局 store/context（本期够用）。
- **数据源**：进页面并发拉 `getSessions(agentId)` + `getCapabilities()` + `getModels()`。「当前智能体」下拉选项 = `/api/capabilities` 的 `agents`（本期只有 `case_design`）；第 2 片再改为按项目过滤。生效模型 = `AgentInfo.effective_uid`（`client.ts:66-67`），其 ctx = 在 `ModelsResponse` 里按 uid 找到的 `ModelInfo.context`（`client.ts:11`）。
- **分组算法**：按本地日历日差——`0 → 今天`、`1..6 → 7 天内`、`>=7 → 更早`；空分组不出标题。搜索 = 标题子串、大小写不敏感（照原型 `:1263`），且新建会话时清空搜索（照 `:1377`）。
- **发送**：`busy` 时禁用 textarea、发送、新建、切换、删除（一处 `requestGuard()`，与 /kb 页重入锁同思路 `KbPage.tsx:299-322`）。成功后用响应的 `session_id` 修正 `activeId` 并重拉列表。
- **消息渲染**：正文 `<div className="body md-preview" dangerouslySetInnerHTML={{__html: mdRender(reply)}} />`。**必须补一条显示规则**（计划期取证）：`.md-preview{display:none}`（`App.css:276`）是为工作区编辑器准备的，只在 `.ws-editor.preview` 下才 `display:block`（`:277`）——聊天气泡里直接挂 `.md-preview` 会**整段不可见**。故 App.css 新增 `.msg.agent .body.md-preview{display:block;padding:0;font-size:14px}`（同时抹掉工作区专属 `padding:16px 22px`，并把其 13px 还回聊天的 14px；规则形态照仓内先例 `App.css:526` 的 `.kb-msg .md-preview`）。安全性已核：`mdRender` **先整体转义 `& < > "`**（`pages/kb/utils.ts:95`），模型输出里的原始 HTML 变不成可执行标签，链接另有 scheme 白名单（`utils.ts:74-78`）。
- **过程块**：`<details className="thinking"><summary>🔧 执行过程</summary>` + 每条 `工具名 · 成功/失败 · detail`；`steps` 为空则整个块不渲染。
- **meta 行**：`🗀 HH:MM`（非今天补 `M月D日`）+ `⧉` 复制（`navigator.clipboard`，失败 toast）。不放模型名/token/耗时（原型也没有）。
- **上下文 meter**：照原型语义——`estTokens`（CJK≈1 token/字，其余 ÷4）估 系统提示词 + 历史消息 + 输入；`pct = used/cap`，`>=70` 加 `.warn`、`>=90` 加 `.hot` 并在 tooltip 追加「建议新建会话」；无可用模型显 `—` 并用原型 tooltip 原文 `未配置可用模型，无法估算上下文占用`。注意 `estTokens` 按「全部可见历史」估，而 prompt 实带最近 40 条（`HISTORY_MAX`），别误读成按 prompt 实长估。
- **侧栏构成**：只渲染「当前智能体」`.ctx-card`（原型 `:575-580`）；**「当前项目」card 整块不渲染**（原型 `:569-574`）——第 2 片才有项目维度，此处放出来即死控件。会话行不显示原型 `▶ Web` 标签（`:1279`，假数据）。
- **欢迎态文案**（本期只有 `case_design`）：标题 `你好，我是 用例设计`（原型 `:1313` 口径，去「智能体」后缀）；副行 `发送消息即在此智能体开始新会话 · 会话保存在本机`（原型 `:1314` 是 `📁 项目 · 发送消息即在此项目开始新会话`，项目维度不在本期 → 改「智能体」并去 📁；数据落点路径从 UI 移到 README，UI 不裸露目录）。chip 保持原型「短标签 + 长指令」双截形态（`:1316-1319`）：`📋 根据需求生成测试用例`、`🧩 等价类与边界值补覆盖`、`🔌 接口用例设计`、`🐞 回归失败归因分析`——📋/🐞 及三条长指令（📋/🔌/🐞）逐字照搬原型，🧩/🔌 两条标签与 🧩 的指令按裁定 5 换成 `case_design` 范围内表述。点击只把长指令填进输入框并聚焦，绝不自动发送（原型 `:1322-1324`）。
- **composer 构成**（原型 `:611-624`）：`.bar` 内只有 上下文 meter → 蓝 perm chip → `.spacer` → `↑` 发送。**模型 chip 在 chat-header**（原型 `:598`），不在 composer 内，此处不重复放；原型橙 chip（`:617`）是「📁 项目 · Agent 工作目录」，属项目维度 → 第 2 片再接，本期不渲染。textarea placeholder 取原型 `:614` 的可用片段 `例如：根据这份需求生成测试用例`（原型的 `↑↓ 浏览历史消息 · / 快捷指令` 两项能力本期都没有，写进 placeholder 即虚假承诺），键位提示移到 `title`。
- **权限 chip**：保留原型形态与文案（`🛡 自由权限 ▾`，`.c-chip.blue.perm`），点击只给原型同一条 toast `「严格权限」暂未开放，敬请期待`（`:1470`）——不 gate 任何行为（裁定 8），但也不做成 `cursor:pointer` 的死控件。
- **页底提示**：`.foot-tip` 逐字照搬原型 `:623` 的 `为测试人员而生 · 用例生成 / 脚本编写 / 失败分析`（数据落点说明不塞进这里，见上「副行」条）。
- **复用现成 CSS**：`.layout/.sidebar/.ctx-card/.side-head/.btn-new/.search/.session-list/.group-title/.session/.s-del`、`.chat/.chat-header/.model-chip/.pop/.messages/.msg.user/.msg.agent/.meta/details.thinking/.typing/.composer-wrap/.composer/.ctx-meter/.c-chip/.btn-send/.foot-tip/.welcome/.chips/.chip`（`App.css:43-311`，本期首次成为有消费方的活样式）。
- **布局 CSS 零改动**（已取证，勿按「三栏网格」想当然）：`.layout{display:flex;height:calc(100vh - 56px)}`（`App.css:43`）是 flex 而非网格，`.chat{flex:1;min-width:0}`（`App.css:89`）——本期不渲染 `.workspace` 时聊天列自然铺满，不需要新增两栏形态；`--w` 只是 `.workspace` 的局部变量（`App.css:231`，全文件仅此一处），不影响其他选择器。侧栏折叠直接复用现成 `.sidebar{transition:margin-left .25s ease}` + `.sidebar.collapsed{margin-left:-264px}`（`App.css:49,51`）。**唯一的 App.css 改动**是消息渲染那条显示规则（见「消息渲染」），属排版补位、不碰布局。
- **折叠交互沿用 KB 页同一套**：侧栏 « 收起、chat-header `☰` 恢复（原型 `:1380-1387` 就是这个共享 toggle，符合「所有侧栏同一折叠模式」）。

## 测试策略

后端（`uv run pytest`，全部 `tmp_path`，`Settings(_env_file=None)`，provider 注入 `MockProvider`/`_SpyProvider`，零真实网络）：

1. `SessionStore`：create/list/messages/append/delete、`updated_at` 降序、`message_count`、title 16 字与空回退、id 形态校验、未知 id → `SessionStoreError`、`.jsonl` 与 `index.json` 落盘内容。
2. `FileMemoryStore`：`{agent}:{sess}` 键切分往返、recall 顺序。
3. 装配路由：平台智能体不落盘、项目智能体落盘；`send` 空 id 建会话且 memory 与磁盘一致。
4. `HISTORY_MAX`：造 45 条历史 → `_SpyProvider` 收到的 messages 恰含最近 40 条（+system+user），磁盘仍 45 条。
5. `run_graph` steps：假 provider 产 `tool_calls` → `ok/round/detail` 断言，含一条走错误分支得 `ok=False`。
6. 端点契约：三端点 + 404 中文文案 + 204 空响应 + 列表对未知/平台 agent 返回空 200；`/chat/echo` 7 层 trace 回归不破；`send` 新字段存在。

前端：`npm run build`（项目无前端测试框架，门禁只有类型与构建）+ 真机走查。

真机走查需用户**当面授权**（真实 LLM 调用）：探针会话用后即删、`backend/data/sessions/` 走查后还原。

## 验收清单

- 发一条消息 → 侧栏出现会话行，标题是消息前 16 字，落在「今天」
- **重启后端进程 → 会话与全部消息仍在**，点开可见（本期核心承诺）
- 多轮连续：第二轮模型能引用第一轮内容（`recall` 真进 prompt）
- 切换/搜索/删除会话；删除需原生确认，确认后行消失且 jsonl 文件被删
- 过程块显示真实工具序列与成败；无工具时整块不出现
- 上下文百分比随对话增长变化（用小 ctx 模型验证 70%/90% 阈值）
- `busy` 期间确实无法重复发送
- 页面 0 处 `zhb`、0 个死按钮
- 332+ pytest 全绿、`npm run build` 0 错

## 范围外（后续各自成片）

- **第 2 片 项目维度**（设计见 `docs/superpowers/specs/2026-10-03-chat-project-design.md`，该片按其裁定 1/2 落地）：
  `project_id` 进 `SendRequest` 与 `index.json` 行，只做**归属字段与列表过滤**（`list(agent_id, project_id)`），**不进会话键**——memory 键与守卫键仍是 `{agent_id}:{session_id}`；`/chat` 当前项目→当前智能体级联；文件工具 `cwd` 从 `"."`（原 `agent_runtime.py:77`，当时有测试锁定并注释「留给项目专项的缝」）换成项目 `dir`。
  原文「**该片必须先设计根约束**，否则模型可在用户填的目录里任意读写」**已作废**：第 2 片不做任何边界执法，模型仍可用绝对路径读写项目之外（自由模式语义）；根约束/越界拦截/界外授权/`strict` 属后续第 5 片「边界与权限」。
  （2026-10-03 第 2 片定稿时改裁，见该片偏离登记 1）
- **第 3 片 右栏工作区**：项目目录浏览 + 预览/编辑（把 `kb_browse.py` 的 `_resolve/_walk_abs` 抽成通用 root）
- **第 4 片 流式**：`graph.stream` 替 `invoke` + SSE + 增量渲染 + 停止按钮（节点已是返回 `{"messages":[...]}` 的普通函数，拓扑不用改）
- rename/pin/归档、会话级模型、富卡片、token 统计

## 风险与已知限制

- **明文落盘**：`backend/data/sessions/*.jsonl` 是含对话原文的明文文件，服务只绑 `127.0.0.1`、目录已被 gitignore，但本期不做加密也不做清理——文档需写明数据位置。
- **磁盘无上限**：jsonl 只追加，本期不滚动不清理（整会话删除是唯一回收手段）。
- **裁剪是读侧**：`HISTORY_MAX` 截断后模型看不到更早内容，这是刻意的成本取舍，UI 不承诺「模型记得全部历史」。
- **并发交错**为已知限制（见控制端裁定「并发」条）。
- **单进程假设**：`backend/data/sessions` 由单一后端进程读写，本期走查真实撞上——第二个进程列表为空并对同一 id 报 404；本期以文档约束（README 后端节）而非跨进程合并解决。
- **会话归属校验**：`send` 对 `sess_*` 续写前判等 `stored.agent_id == agent_id` 是终审补上的洞（此前任何 `agent_id` 都能往别人的会话里写）；第 2 片按裁定 2 加了**第二维等值校验**（`existing.project_id == pid`，`project_id` 不进会话键），平台智能体的会话不参与该维比对。
- **上下文 meter 估算口径**：`estTokens` 估的是「全部可见历史」，而进 prompt 的只有最近 40 条（`HISTORY_MAX`），长会话重开可能显示 ≥90% 而实际未近上限——按「估算」口径保留；第 2 片未动此处（项目维度不改估算口径），留待第 4 片（流式）一并复核。
- **坏索引自愈**：自愈前把损坏的 `index.json`（不可解析，或合法 JSON 但顶层/`sessions` 结构坏）留档为 `index.json.bad-<ms>`，字段类型坏的行逐行丢弃并记 warning（正文 `.jsonl` 原地不动），本期不做从 `.jsonl` 重建索引的路径。
