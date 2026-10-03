# 聊天页 · 第 2 片：项目维度 设计

日期：2026-10-03　状态：**设计待评审**（v2 = 丁一口径：本片只做「归属 + 落点」，不做边界执法）

参考原型：`prototype/index.html` 当前项目 card `:570`、composer 橙 chip `:617`（`📁 订单系统 · Agent 工作目录`）、欢迎态副行 `:1314`、权限模式 `PERM_MODES :1451-1455`
现状依据：`services/agent_runtime.py:76-83`（`build_default_registry(cwd=".", …)`，守卫键 `:78`）、`adapters/tools/__init__.py:35-40`（工具实例携带 `cwd` 现场构造）、`adapters/tools/file_tools/fs_tool.py:23-27`（绝对路径直通，无边界）、`adapters/tools/file_tools/write.py:60`（`mkdir(parents=True)`）、`services/chat.py:106-128`（send 三态与 `agent_id` 归属校验）、`services/session_store.py:130-137,169-172,178,199,225,247`、`memory/file_memory.py:3-4,17-19`（按首冒号切分）、`services/project_config.py:60-70,85-94,165-167,202-205,212-216`、`interaction/sessions.py:35-45,62-70,73-76`、`interaction/projects.py:25,32,47,61`、`interaction/schemas.py:16-20,176-206,209-231`、`pages/KbPage.tsx:325`（`chatSend("kb-console", text, "kb_assistant")`）、`api/client.ts:156-157,285,323-359`、`pages/ChatPage.tsx:28,62-100,137-167,244`、`pages/chat/SessionPane.tsx:20,27-46`、`App.tsx:11-13,58-71`、`orchestration/agent_graph.py:84`（一次性 `graph.invoke`）、`tests/test_agent_runtime.py:161-162`（`cwd == "."` 锁定）、`services/capability_config.py:57,65,102-106`（`pwsh`/`bash` 为可勾选工具，Windows 上 `bash` 默认关）
存量数据：`backend/data/sessions/index.json` 1 条会话（`sess_993ba6df`，2 条消息，第 1 片走查探针）；`backend/data/projects.json` 为空（0 个项目）

## 背景与概念澄清

第 1 片 spec 在「范围外」里写过一句：`cwd` 换成项目 dir「**必须先设计根约束，否则模型可在用户填的目录任意读写**」。本轮设计推翻这句话的前提——**「在用户指定的目录里任意读写」不是洞，它就是项目这个概念的语义本身**。用户填 `D:/reqs`，就是为了让智能体进去读需求文档、写出用例。

真出过事故的是另一件事：`agent_runtime.py:77` 的 `cwd="."` 指向后端进程目录，2026-10-01 真机冒烟时 `case_design` 自行把用例写进了 `backend/cases/login_cases.md`——**产出物落在了项目之外**。治它只需要把装配参数从 `"."` 换成 `project.dir`，不需要任何执法。

所以本片拆成两半，只做其中一半：

- **本片（归属 + 落点）**：会话绑定项目、列表按项目过滤、产出物落进项目目录、删项目级联删会话、目录可达性验真。
- **第 5 片（边界与权限）**：越界拦截、界外授权、`strict` 模式、逐次批准。它需要 LangGraph `interrupt` + checkpointer + pending 落盘 + 批准端点 + 弹层，而现状是 `agent_graph.py:84` 的一次性 `invoke`，请求里没有「挂起等人」的位置——这套基础设施与第 4 片（流式 + 停止按钮）同源，建议连着排。

## 用户裁定（2026-10-03，逐条确认）

0. **范围 = 丁一**：本片不做任何运行时路径拦截，边界执法整块挪到第 5 片。
1. **项目是聊天页的硬前提**：`projects.json` 为空时 `/chat` 显示引导态；`SendRequest.project_id` 对非平台智能体必填。理由：只留一种会话语义，归属校验与列表过滤不必分两套。代价：第 1 片「打开就能聊」变成「先建项目」。
2. **`project_id` 只做归属字段，不进会话键**：memory 键与守卫键继续是 `{agent_id}:{session_id}`，`index.json` 行多一个 `project_id`。**改裁** 2026-09-30「键扩展含 `project_id`」。理由：`new_id` 生成的 `sess_*` 已在索引内查重防碰撞（`session_store.py:169-172`），隔离已由 sid 提供；进键买不到任何隔离，却要重写 `FileMemoryStore._split` 的首冒号规则与「字符集无冒号」那条锁定测试。
3. **选择态存 localStorage**（`aitester.chat.projectId`）：视图上下文不是数据，项目本身已落盘。代价：换浏览器不带走选择。
4. **删项目级联删会话并在确认框报数**（沿用 2026-09-30 原型口径），顺带把 `ProjectInfo.session_count` 从恒 0 接成真值（`project_config.py:165-167` 注释已把这条缝留好）。
5. **目录不可达 = 读侧探测提醒 + 发送时硬拦**：`GET /api/projects` 加 `dir_exists`（只 stat 不建目录），项目页给一行提醒；`send` 装配前校验目录存在且是目录，否则 400 中文可照做 detail。这条与边界执法无关，是落点正确性：`write.py:60` 的 `mkdir(parents=True)` 会在路径打错时**静默建出整棵目录树**（`D:/word/reqs` vs `D:/work/reqs`），用户以为写进了项目。`cwd` 第一次真被消费，就得第一次为它验真。
6. **危险根闭集四类**（仅创建时校验，`dir` 冻结故读侧不重复判）：判据一处函数 `dangerous_root_reason(dir) -> str | None`，表驱动、测试锁四类，不维护会长大的活黑名单。
   1. 文件系统根：`resolve()` 后 `p.parent == p`（`D:\`、`/`）
   2. 家目录本身：`p == Path.home()`（其下子目录放行）
   3. Windows 系统目录：`%SystemRoot%`、`%ProgramFiles%`、`%ProgramFiles(x86)%`——一律取环境变量，不硬编码盘符
   4. POSIX 系统目录闭集：`/etc /usr /var /bin /sbin /lib /boot /dev /home /root`
   存量 `projects.json` 为空 → 零迁移。
7. **`project_id` 对平台智能体可空**：`is_platform_agent(agent_id)` 为真时该字段忽略（不校验、不落盘），判据并入 `chat.py:117-121` 既有的平台短路。理由：`KbPage.tsx:325` 的 `chatSend("kb-console", text, "kb_assistant")` 不属于任何项目，必填会同时打脸 /kb 页和一批回归测试。
8. **命令执行不受影响**：本片不摘 `pwsh`/`bash`，不新增权限面板，composer 的 🛡 chip 维持第 1 片的 toast 降级（`PERM_MODES.strict` 那条 `desc` 由第 5 片兑现）。

## 数据流

```
POST /api/chat/send { project_id, agent_id, session_id, message }
  → ChatService.send
      ├─ 平台智能体：忽略 project_id，走原短路（kb-console 临时键不变）
      ├─ 非平台：project_id 空 → 400
      ├─ project = projects.get(project_id) 缺失 → 404
      ├─ Path(project.dir) 存在且是目录 否则 400            ← 裁定 5
      ├─ sess_* 续写：stored.agent_id == agent_id 且 stored.project_id == project_id 否则 404
      └─ instance = agent_runtime.build(agent_id, sid, provider_override, cwd=project.dir)   ← 唯一改动点
          → build_default_registry(cwd=<项目目录>, session_id=f"{agent_id}:{sid}", …)
```

`cwd` 是 `AgentRuntime.build` 的新参数，默认 `"."`（保持既有调用方与 echo 链路不破）；`ChatService` 构造期注入 `ProjectService`（`main.py:41-43` 已装配，与 `sessions` 的注入方式 `main.py:68` 同构）。守卫键、memory 键、`HISTORY_MAX`、`recall` 一律不动。

## 契约变更

| 位置 | 变更 | 备注 |
|---|---|---|
| `interaction/schemas.py:16-20` `SendRequest` | + `project_id: str = ""` | 条件必填见裁定 7，校验在 service 层不在 schema（schema 无法判据 `is_platform_agent`） |
| `session_store.py:130-137` index 行 | + `project_id` | 缺失视为结构不全 → 按第 1 片「坏行只丢一行、必须留话」口径丢弃并 warning |
| `session_store.py:178` `create` | + `project_id` 参数 | 调用点是 `file_memory.py:31`（`FileMemoryStore.save` 的延迟建会话分支），故 `FileMemoryStore` 构造改签 `FileMemoryStore(store, project_id)`——它在 `chat.py:142` 本来就是**每请求现构造**，项目 id 在 `send` 校验后传入即可，`_split` 的键规则一位不动。`use_file` 已蕴含非平台智能体（`chat.py:132-136`）蕴含 `project_id` 非空，不另加防御判据 |
| `session_store.py:199` `list` | `list(agent_id, project_id)` | 双条件过滤，排序不变（`updated_at` 倒序） |
| `session_store.py` 新增 | `delete_by_project(project_id) -> int` | 删 index 行 + 对应 `.jsonl`，返回条数 |
| `interaction/sessions.py:35-45` | `project_id` 必填 Query | 三端点按第 1 片口径「只服务已落盘 `sess_*`」，故收紧不砸 /kb |
| `interaction/schemas.py` `SessionInfo` | + `project_id` | 前端渲染项目归属用不到，但读侧一致性 |
| `interaction/schemas.py:176-206` `ProjectInfo` | `session_count` 接真值；+ `dir_exists: bool` | 读侧探测，不建目录 |
| `interaction/projects.py:61` DELETE | 先 `projects.delete(pid)`（校验通过并落盘）→ 再 `sessions.delete_by_project(pid)` | 顺序不可反：项目校验失败时不该丢会话；会话删除失败只 warning 留话，孤儿 `.jsonl` 归第 1 片终审议程 |
| `interaction/projects.py:32` POST | 危险根判据 → 400 | 仅创建；`update` 因 `dir` 冻结不重复判（`project_config.py:202-205`） |
| `api/client.ts:285` `chatSend` | + `projectId` 第四参 | `KbPage.tsx:325` 传 `""` |

## 前端形态

- **进 `/chat` 顺序**：`getProjects()` → 空则整页引导态（`📁 还没有项目` + 一句「请先到项目页添加需求文档所在目录」+ 一个真能跳的按钮，`App.tsx` 已是 react-router，跳 `/projects`）；非空则 `projectId` 取 localStorage 命中值，未命中或项目已删则取列表首条，并在变更时写回。
- **侧栏顶部两张 card 同构**：在 `SessionPane.tsx:27-46` 的「当前智能体」上方加「当前项目」card，复用同一 `.ctx-card` + 原生 `select` 形态与 `disabled={busy}`（busy 期间禁切项目，与切智能体同判据）。
- **级联**：智能体下拉选项 = 项目 `agents` ∩ `caps.agents`，顺序按 `caps.agents`；当前 `agentId` 不在其中则回落交集首项。交集为空（项目启用的智能体都不可见）→ 禁用 composer 并在智能体 card 下给一行「该项目未启用可见智能体，请到项目页调整」。项目 `agents` 创建时强制 ≥1（`project_config.py:85-86`），故正常路径下下拉永不为空。
- **切项目语义 = 切智能体语义**：清侧栏、`openSession(null)`、回欢迎态。
- **欢迎态副行**：`📁 <项目名> · 发送消息即在此项目开始新会话`（原型 `:1314` 的真数据版，不裸露路径）。composer 橙 chip 恢复为只读 `📁 <项目名>`（原型 `:617` 含「· Agent 工作目录」的路径语义去掉）。
- **失败出路**：400「目录不存在」→ 行内提示 + 跳项目页，不静默降级；404 归属不符 → 沿用第 1 片 detail 落 toast。
- 页面继续 0 处 `zhb`、0 个死按钮。
- 项目页：列表行显示真实会话数与不可达目录提醒；删除确认框文案「会连带删除 N 条会话，不可恢复」。

## 测试策略

后端（基线 379 passed）：

- 改 `tests/test_agent_runtime.py:161-162`：`cwd == "."` → 项目目录断言，同步删掉那句「留给项目专项的缝」注释（本片刻实把它接上了）。
- 新增：`create`/`list`/`delete_by_project` 带项目、`send` 双归属校验、`project_id` 空 → 400、目录不存在 → 400、危险根四类各一、`dir_exists` 只 stat 不建目录、删除项目的顺序（项目校验失败不丢会话）。
- **一条守 /kb 不破的回归**：平台智能体带空 `project_id` 必须照旧走临时键成功（`chat.py` 平台短路在前）。
- ChatService 测试注入 fake `ProjectService`，与既有 fake runtime 捕获 `build` 参数的缝同构。

前端：`npm run build` 门禁（项目无前端测试框架，沿用第 1 片裁定）+ 真机走查（真实 LLM 调用，**须用户当面授权后自行发消息**，探针会话用后即删）。

## 验收清单

- 项目页建项目（`dir` 指向真实需求目录）；填 `D:/` 或家目录被拒且 detail 说清原因
- 进 `/chat`：「当前项目」card 出现，切项目 → 列表跟着换、回欢迎态、切后刷新不回退
- 发消息 → 模型产出的文件落在项目目录内（核对文件位置，这是本片唯一的行为变更点）
- 无项目时进 `/chat` 是引导态，跳项目页能建、建完回来自动选中
- 重启后端：会话仍在且归属正确；`projects` 列表会话数为真值
- 删项目：确认框报出 N，删完 `sessions/` 下对应 `.jsonl` 消失
- `/kb` 页发送、文件浏览一切照旧
- 页面 0 处 `zhb`、0 个死按钮

## 范围外

- **第 3 片** 右栏工作区（项目目录浏览 + 预览/编辑，届时复用本片的 `dir_exists` 与项目 `dir`）
- **第 4 片** 流式（`graph.stream` + SSE + 停止按钮）
- **第 5 片** 边界与权限（越界拦截、界外授权、`strict` 模式、逐次批准；与第 4 片共用长任务基础设施）
- rename/pin/归档、会话级选模型、富卡片、token 统计

## 风险与已知限制

- **界外读写照旧可行**：本片之后模型仍能用绝对路径碰项目外任何地方（自由模式的既定语义，代价由用户自担）。第 5 片之前不存在「受约束的执行」。
- **归属唯一真相在 `index.json`**：索引坏到丢行时，`.jsonl` 还在但项目归属丢失 → 该会话在任何列表里不可见。回收路径（从 `.jsonl` 重建索引、孤儿清理）在第 1 片终审议程里，本片不做。
- **读侧仍不校验归属**：`GET /{sid}/messages` 与 `DELETE /{sid}` 按 id 直读，与第 1 片一致，「切项目立刻清侧栏」是 UI 侧防线而非后端保证。
- **单进程约束、明文落盘、磁盘无上限**：全部沿用第 1 片已知限制（`README.md` 后端节）。
- **`dir` 冻结**：项目目录被移动后，历史会话的 `cwd` 指向不存在的目录 → 该会话发送被裁定 5 拦下（400），历史消息仍可读。本片不提供「改绑目录」。

## 偏离登记（`QODER.md`「禁止静默偏离」条款要求）

| # | 类型 | 偏离 | 理由 |
|---|---|---|---|
| 1 | 改裁 | 作废第 1 片 spec「第 2 片必须先设计根约束」的口径，并在文档回写任务里改掉那句 | 它把「在用户指定目录里任意读写」当成洞，而那是项目的语义；实施收尾时一并回写第 1 片 spec、README，以及 `chat.py:126` 那句预告「project_id 进键」的注释（本片按裁定 2 不进键） |
| 2 | 改裁 | 会话键不含 `project_id`（推翻 2026-09-30） | 裁定 2 |
| 3 | 新增 | `project_id` 对平台智能体可空 | 裁定 7，原型与既有文档均无此约定 |
| 4 | 原型 | 引导态整页 | 原型假设永远有项目（`PERM`/card 都写死「订单系统」），无项目形态原型没有 |
| 5 | 原型 | 橙 chip 只显项目名、去掉「· Agent 工作目录」 | 裸露数据落点在第 1 片已定为不在 UI 展示；第 3 片右栏工作区再谈 |
| 6 | 原型 | 🛡 权限 chip 继续 toast 降级 | 第 1 片裁定不变；本片没有边界执法，chip 若可点会承诺做不到的事 |
| 7 | 原型 | 当前项目 card 用原生 `select` 而非原型的 div + 弹层 | 与第 1 片「当前智能体」同构（`SessionPane.tsx:27-46` 已如此），两套形态违背控件一致性 |

## 自检结论

- 覆盖：裁定 0-8 → 数据流/契约/前端/测试四节逐条有落点；无 TBD 与「类似第 N 片」。
- 一致性：`delete_by_project` 返回条数与确认框报数同源（`session_count` 走读侧探测，不额外发一次计数请求）。
- 顺序风险：删除项目先删项目后删会话，写进契约表并给出反例后果。
