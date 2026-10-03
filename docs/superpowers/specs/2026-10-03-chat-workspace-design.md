# 聊天页 · 第 3 片：右栏工作区 设计

日期：2026-10-03　状态：**设计已定稿**（裁定五条 2026-10-03；待写实施计划、待实施）

参考原型：`prototype/index.html` ws aside `:631-671`、工作区 JS `:1480-1677`、`wsIcon :1519-1522`、`mdRender :1612-1649`、`dragBar :1652-1669`、chat-header 📁 `:603`、resizerChat `:628`；`.ws-*`/`.e-*`/`.resizer` 样式已在 `frontend/src/App.css:238-320` 全量落好（第 1 片移植，至今零消费方）
现状依据：`interaction/kb_browse.py:53-64,117-142,145-163,221-244`（路径锁/树/读/写，契约蓝本）、`interaction/projects.py:33-47`（`_svc`/`_info` 装配模式）、`services/project_config.py:122-156,256-257`（`dir_exists`/`_validate_dir`/`get`）、`services/chat.py`（send 的 `cwd = Path(dir).expanduser()` 消费点）、`services/kb/paths.py:37-51`（`is_hidden`/`hidden_segment`/`mtime_ms`）、`frontend/src/pages/KbPage.tsx:28-47,88-95,124-146,174-184`（`bindDragBar` 抽取源/懒加载缓存/脏确认/409 处理）、`pages/ChatPage.tsx:264-270,381-397`（切项目接线点/chat-header）、`api/client.ts:224-256`（kb browse 函数族形状）、`tests/test_kb_browse.py`（回归锁）、`tests/test_api_projects.py:29-62`（`_app` 助手蓝本）
存量数据：无新增落盘形态（浏览只读项目 dir 与磁盘，不写任何索引）

## 背景与概念澄清

第 1 片 spec 的「范围外」写着：第 3 片 = 右栏工作区（项目目录浏览 + 预览/编辑，把 `kb_browse.py` 的路径锁抽成通用件）。第 2 片把 `cwd` 落进了项目目录——**模型现在真的在往项目里写产出物，但用户看不见它们**。本片给这些产出物一扇窗：右栏工作区。

- **根的唯一真相 = 项目 `dir` 的 expanduser 结果**，与 `send` 的 `cwd`、`dir_exists` 的探测同入口。第 2 片的 `~` 教训（消费对称性）在这里是硬约束：工作区浏览根解析必须与发送落点认同一个结果，专项一条测试锁住。
- **工作区不是知识库**：无桶、无 frontmatter、无索引收敛、无搜索/扫描（KB 的 `/search`、`/scan` 不迁移）。它只是「这个项目目录长什么样」的诚实镜像 + 一个保底的文本编辑器。
- **浏览与写入是一等公民，新建/删除不是**：模型侧本来就有 write 工具通道；UI 本期只给「看和改」，不给「增和删」（裁定 2，与 KB 同口径）。

## 用户裁定（2026-10-03，逐条确认）

1. **可编辑范围 = 所有文本文件**：`.md/.markdown` 默认进预览页签、可切「编辑」；非 md 文本直接进编辑态（原型 `wsOpenFile :1552-1554` 判据）。写入走 mtime 乐观锁（409 不覆盖）。
2. **「＋ 新建文件」本期不做**：工具栏只渲染「↻ 刷新」（原型的 `.ws-btn.main` 不渲染）；后端**不做** POST 端点——无消费方的端点就是死接口（「0 个死按钮」的接口版判据）。
3. **状态栏 = `📁 项目名 · 绝对目录`**：原型 `:1516` 的智能体标签去掉（目录跟项目走，不跟智能体走）；目录是用户自填值，原样回显（第 2 片「dir 允许原样回显」口径）。
4. **隐藏项 = 与知识库同判据**：直接消费 `services/kb/paths.py` 的 `is_hidden`/`hidden_segment`（点前缀 + `.git/.idea/.vscode/.locks/__pycache__/node_modules/.obsidian` + `.pyc/.pyo/.pack/...` 噪音后缀），浏览与写入两侧同一谓词。
5. **发送成功后自动刷新工作区树**（模型刚写入的产出物立即出现），同时保留手动「↻ 刷新」。

## 数据流

```
GET  /api/projects/{pid}/browse/tree?path=        → 懒加载列一层（dirs-first、隐藏剔除、size/mtime）
GET  /api/projects/{pid}/browse/file?path=        → 读文本（415/413 附 editable:false）
PUT  /api/projects/{pid}/browse/file?path=&mtime= → mtime 乐观锁写回（409 不覆盖、回传当前 mtime）
（无 POST / 无 search / 无 scan）

root = Path(project["dir"]).expanduser()      ← 与 chat.send 的 cwd 同一消费入口
  ├─ 未知 pid → 404「未知项目「…」」（ProjectService.get 的 ConfigNotFoundError 直传）
  ├─ root 不是目录（创建后被删/移走）→ 404 中文指路项目页
  └─ rel：NUL/越界/隐藏段 → 403；不存在 404；目录当文件 400
```

前端：`WorkspacePane` 自持 `kids/expanded/doc`（模式照 KbPage 懒加载缓存：kids 缺失才拉、展开过的目录留缓存）；`key={projectId}` 换项目即重挂（树、打开文件、拖宽全部归零）。发送成功 → ChatPage `wsSeq+1` → pane 静默重拉「根 + 已展开目录」；手动 ↻ 走同一套重拉 + toast。切项目前 ChatPage 查 `wsDirtyRef`，有脏文档先 confirm。

## 契约变更

后端（新增 1 模块 + 抽 1 共用件 + 接线 1 行；KB 行为一位不变）：

| 位置 | 变更 | 备注 |
|---|---|---|
| 新增 `interaction/browse_common.py` | 从 `kb_browse.py` 抽：`TEXT_EXT`、`MAX_TEXT`、`BrowseWriteBody`、`resolve_within(root, rel, *, outside_detail)`（`_resolve` 参数化 403 文案）、`is_text_file`（原 `_is_text`）、`stat_item`（原 `_stat_d`）、`rel_of`（原 `_rel_of`）、`listing(root, target)`（树条目组装 + dirs-first 排序） | `_resolve` 的三段判据（NUL/越界/隐藏段）参数化后两侧共用；`test_kb_browse.py` 一字不改全绿 = 行为不变的回归锁 |
| `interaction/kb_browse.py` | 改 import 共用件、删被迁代码 | 端点/文案/状态码零变化 |
| 新增 `interaction/project_browse.py` | `GET /{pid}/browse/tree`、`GET /{pid}/browse/file`、`PUT /{pid}/browse/file`；prefix `/api/projects` | 根 = 项目 dir（expanduser）；未知名 404 直传服务层 detail；目录缺失 404 指路；越界/隐藏 403「路径超出项目目录范围」 |
| `main.py` | `include_router(project_browse_router)` | 与 projects 同 prefix 不同子路径，无路由冲突 |
| 不动 | `project_config.py`、`session_store.py`、`chat.py`、KB 一切 | 本片零迁移 |

错误表（detail 一律中文且可照做）：

| 场合 | 码 | detail |
|---|---|---|
| 未知项目 | 404 | `未知项目「{pid}」`（直传服务层原话） |
| 项目目录不存在/不是目录 | 404 | `项目目录不存在或已被移动，请到项目页确认路径` |
| 越界/含 NUL/隐藏段 | 403 | `路径超出项目目录范围` |
| 目标不存在（tree / file / PUT） | 404 | `目录不存在` / `文件不存在` / `保存失败：文件已不存在（可能被移动或删除）` |
| 文件当目录 / 目录当文件 | 400 | `不是目录` / `是目录，不是文件` |
| 非文本（GET / PUT） | 415 | `暂不支持预览该文件类型` / `只允许写入文本文件`（附 `editable:false`） |
| 超 2MB（GET） | 413 | `文件超过 2MB，无法打开`（附 `editable:false`） |
| 超 2MB（PUT 内容） | 413 | `内容过大`（附 `editable:false`） |
| mtime 冲突（PUT） | 409 | `文件在页面打开后被外部修改，未覆盖`（回传当前 `mtime`） |

前端：

| 位置 | 变更 | 备注 |
|---|---|---|
| `api/client.ts` | + `WsItem`/`WsTreeResponse`/`WsFileResponse`/`WsWriteResponse` 类型 + `wsTree(pid, path)` / `wsReadFile(pid, path)` / `wsPutFile(pid, path, content, mtime)` | 形状照 `:224-256` kb 函数族；新类型不复用 `Kb*`（同名不同源，将来漂移不互累） |
| 新增 `components/dragBar.ts` | `bindDragBar` 从 `KbPage.tsx:28-47` 原样提出（第二个消费方出现才抽） | `KbPage.tsx` 改 import，行为不变 |
| 新增 `pages/chat/WorkspacePane.tsx` | 整栏 = `resizer + aside.workspace` fragment；自持 kids/expanded/doc/treeHidden；折叠与刷新由父控制 | props：`project/collapsed/refreshSeq/onCollapse/onToast/onDirtyChange` |
| `pages/ChatPage.tsx` | + `wsCollapsed`/`wsSeq`/`wsDirtyRef`；`onProjectChange` 脏拦截；`send` 成功 `wsSeq+1`；chat-header 收起态渲染「📁 工作区」恢复钮；主布局挂 `<WorkspacePane key={projectId}>` | 折叠状态不落盘（视图态，同第 2 片裁定 3 口径） |

## 前端形态

- **布局**：`SessionPane | main.chat |（resizer）| aside.workspace`。折叠钮 = ws-head 右侧 `»`（`.icon-btn`，同会话历史 `«` 形制）；收起后 chat-header 出现「📁 工作区」文字钮（`.mini-btn`，仅收起时渲染）——「恢复钮只出现在相邻栏头部」是用户历史裁定（KB 页「📁 目录」先例），且避免纯图标钮（◧ 曾被当成「不支持隐藏」）。
- **树**：懒加载；目录行 `▸/▾ + 📁/📂`，文件行用 `wsIcon`（📜/📝/📊/📄）；当前文件 `.ws-node.active`；点击目录展开（首次才拉）、点击文件打开。**目录栏隐藏**：ws-side 头「📁 目录」+ `«`；隐藏后 ws-head 出现 `☰`（title「显示目录栏」）——与「会话历史隐藏」同套交互（原型 `btnTreeShow :634`）。
- **打开文件**：清 inline `--w` + `.wide` 变宽（原型 `:1546-1551`）；`.md` 默认预览（`.e-tabs` 出现、预览态藏保存钮），非 md 直接编辑；脏点 `.dirty.on` = `content !== disk`。
- **保存**：`.e-foot`「💾 保存」；409 → toast「文件在别处被改过，已重新载入磁盘最新内容（未保存的修改已丢弃）」+ `force` 重载（KB「409 自动重载语义统一」）；PUT 404（外部被删）→ toast 服务端 detail + 静默刷树，编辑器内容留着（用户还能拷走）。
- **脏守卫**：切文件 / 关文件（✕）/ 切项目 遇 `content !== disk` → 各自 confirm，确认后丢弃。切项目守卫在 ChatPage（`SessionPane` 的 select 受控，早退即保住旧选择）。
- **工具栏**：只「↻ 刷新」（带文字）；手动刷新 toast「已刷新目录」（原型 `:1590`）；`refreshSeq` 触发的静默刷新不 toast。
- **树区状态**：根加载失败 → 错误行 +「↻ 重试」；空目录 → 一行「目录是空的 · 智能体的产出会出现在这里」；均复用既有 `.empty-tip` 形态。
- **状态栏**：`📁 {项目名} · {project.dir}`（自填值原样）。
- **编辑器脚**：文件信息 = `体积 · mtime`（复用 `fmtSize`/`fmtTime`）。
- **拖拽夹持**：目录树 `[180, 560]`（写 `--tw`，KB 已裁定放宽口径）；聊天↔工作区 `[260, innerWidth-620]`（写 `--w`，保住原型「聊天区留 620」）。
- 页面 0 处 `zhb`、0 个死按钮；不新增色值（全部消费第 1 片已落的 `.ws-*`/`.e-*`/`.resizer` 样式）。

## 测试策略

后端（基线 437 passed）：

- **先锁回归**：`test_kb_browse.py` 在抽共用件前后各跑一遍，一字不改全绿。
- 新增 `test_project_browse.py`（装配蓝本 = `test_api_projects.py` 的 `_app` + `_NoopKbManager`，`projects_path`/`sessions_dir` 都指 tmp）：
  - 树：dirs-first、隐藏剔除（`.git`/`.scratch` 不可见）、逐段 403（`../`、`.git`、NUL）、不存在 404、文件当目录 400；
  - 项目侧：未知 pid 404；项目目录创建后删除 → 404 且 detail 带「项目页」；**`~` 对称锁**：monkeypatch USERPROFILE/HOME 后项目 dir 填 `~/reqs`，建项目与浏览命中同一目录；
  - 文件：文本可读 `editable:true`；`.exe` 415、>2MB 413 均附 `editable:false`；404；
  - 写：roundtrip + `mtime` 回传；409 不覆盖且回传当前 mtime；415；PUT 已删文件 404；内容 >2MB 413；
  - **无新建口锁**：`POST …/browse/file` → 405（本期刻意不提供）。
- 前端：`npm run build` 0 error（无前端测试框架，沿用既有裁定）+ 真机走查（真实 LLM 发消息段须用户当面授权后本人操作）。

## 验收清单

- 进 `/chat`：右栏工作区出现，状态栏 = `📁 项目名 · 目录`；树能展开、隐藏文件不出现
- 打开 `.md`：预览渲染；切「编辑」改字 → 脏点亮 → 保存 → toast；重开内容在；再改后直接关闭 → confirm
- 打开 `.py`/`.ts`：直接进编辑态；保存成功
- 手动 ↻ 与发消息成功后：树里能看到模型刚写的文件
- 切项目：树换根、打开文件归零；有脏文件时先 confirm（取消则停在原项目）
- 收起工作区 → chat-header「📁 工作区」恢复；目录栏 `«` 隐藏后 `☰` 恢复
- 拖两处分隔条到两端夹持值；打开文件自动变宽、关闭回默认宽
- 项目目录被删：树区错误行指路项目页
- 0 处 `zhb`、0 个死按钮

## 范围外

- 新建/重命名/删除文件（裁定 2，与 KB 同口径）
- 搜索/扫描（KB 两端点不迁移）
- 消息 file-card 点击定位工作区（原型 `:1601-1609` 的 mock 特性；正式消息流无 file-card）
- 边界执法（第 5 片）：工作区自身锁在项目目录内是本片既有（KB 同款判据），但**模型**用绝对路径越界读写仍不拦（第 2 片已知限制不变）
- 流式与停止按钮（第 4 片）
- 多标签编辑器、语法高亮、图片/二进制预览

## 风险与已知限制

- **消费对称性**：root 解析与 `send` 都 `expanduser()`，一条测试锁住；但 `dir` 冻结（第 2 片限制）下目录被移走 → 工作区 404（指路项目页），不提供改绑。
- **409 会丢弃手工编辑**：用户在编辑器里改着、智能体刚写完同一文件 → 保存必 409 → 强制重载（toast 明说丢弃）。KB 同款裁定；「保留本地内容、冲突可见」留待后续专项。
- **隐藏判据是闭集**：非常见隐藏目录（如 `dist`、`build`）仍然可见；与 KB 严格一致（裁定 4）。
- **树一次只列一层**：深目录逐层展开，无深度限制（KB tree 同款）。
- **单进程约束、明文落盘、无权限体系**：全部沿用第 1/2 片已知限制。
- **本片实现仅过单测与 build 门禁，未走真机**：树/编辑/刷新的实际观感与「模型产出物立即出现」未经真机验证，走查清单见「验收清单」，由用户本人自起端口逐条补齐。

## 偏离登记（`QODER.md`「禁止静默偏离」条款要求）

| # | 类型 | 偏离 | 理由 |
|---|---|---|---|
| 1 | 原型 | 工具栏不渲染「＋ 新建文件」 | 裁定 2；无 UI 消费方则后端不做 POST |
| 2 | 原型 | 状态栏去掉智能体标签 | 裁定 3 |
| 3 | 原型 | 折叠后恢复钮 = chat-header「📁 工作区」文字钮（仅收起时渲染）；原型是 chat-header 里恒显的 📁 图标钮 | 用户历史裁定「栏隐藏必须与会话历史同套交互」+ 纯图标钮被当成过缺失功能；KB 页「📁 目录」先例 |
| 4 | 原型 | 「↻」刷新钮带文字 | 同上——纯图标钮先例风险 |
| 5 | 原型 | e-foot 文件信息 = `体积 · mtime`（原型只有体积） | 工作区文件会被智能体异步改写，mtime 是判断「这版是不是我看的那版」的第一眼依据 |
| 6 | 原型 | 树区空态一行字 / 根错误行 +「↻ 重试」 | 原型 mock 永非空、永不出错；空态与失败态必须有出路 |
| 7 | 原型 | 消息 file-card 点击定位不做 | 正式消息流无 file-card |
| 8 | 文案 | GET 413「文件超过 2MB，无法打开」（KB 是「只读不加载」）；PUT 404 不指「新建接口」 | 工作区没有只读视图、没有新建接口，照抄 KB 文案会指向不存在的东西 |

## 自检结论

- 覆盖：裁定 1-5 → 契约/前端/测试三节逐条有落点；无 TBD 与「类似第 N 片」。
- 一致性：错误表与前端 toast 全部指向同一批 detail；「0 死按钮」在接口层同样成立（无消费方的 POST 不做）。
- 顺序风险：抽共用件先行且以回归锁守住 KB；`key={projectId}` 重挂与脏守卫的先后关系写进前端形态节（先 confirm 后切，select 受控早退保住旧值）。
