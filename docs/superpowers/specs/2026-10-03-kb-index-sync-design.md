# 知识库索引同步入口（增量重扫 + 全量重建）设计

日期：2026-10-03　状态：**待批准**（未开工，无代码/无 commit）

参考实现面：`backend/src/aitester/services/kb/config.py:54-88`（已注册未调用的 `reindex` job、后台 `index_update_loop`）、`backend/src/aitester/services/kb/manager.py:127-151`（run_job 通道）、`backend/src/aitester/interaction/router.py:198-268`（`/api/kb/*` 端点形态与 503 口径）、`backend/.venv/.../reme/knowledge/setup.py:14-42`（发布桶 watch_dirs 注入白名单）

## 背景

reme 的索引构建能力**我们已经注册了却从未调用**：`_KB_JOBS["reindex"]`（`clear_store_step` → `init_changes_step` → `update_index_step`）自知识库专项落地起就存在，没有任何端点或 UI 使用它。`interaction/router.py:232-234` 的注释记录了当时的裁定——save 后不同步补跑 reindex，靠后台 `index_update_loop` 收敛。

收敛确实存在（watchfiles：debounce 5s + poll 5s），但它留下两个无人工出路缺口：

1. **写完想立刻可检索**：用户/助手刚落盘一个 md 后，检索要等约 10-30s 才追得上；
2. **watcher 静默漏扫**：一旦后台环某一轮没吃到变更（进程被挂起、超大批量改动、磁盘抖动），当前没有任何入口能主动纠正，只能重启实例。

本期就补这个「主动催索引」的入口。

## 用户裁定（2026-10-03，逐条确认）

1. **两个动作都要**：增量重扫（非破坏性）+ 全量重建（清盘重建），分开、不混成一个按钮。
2. **入口在 `/kb` 页树栏头部**，用文字按钮（不做纯图标控件）。
3. **全量重建进 UI，带原生二次确认**。
4. **同步等到底再返回**：不引入轮询、不做进度流。前提是实测代价可接受（见下）。
5. **同步范围 = `/kb` 页会用到的两份实例**：`(default, console)` 与 `(default, kb_assistant)`，串行跑。
6. **`/kb/save` 保持不动**：沿用 2026-10-02「不在保存链路同步补跑 reindex」的裁定。

## 控制端裁定（用户未逐一指定，按既有口径与实测补齐）

- **增量 job 必须取名 `index_sync`**。reme 在启动期只给白名单 `_WATCH_JOB_NAMES = (index_update_loop, reindex, index_sync, digest_watch_loop)` 追加发布桶绝对路径（`knowledge/setup.py:14-42`）。取别的名字 → `init_changes_step` 的 watch 规则只覆盖 workspace 内那个空 `knowledge` 目录 → **扫到 0 条并回「up to date」**，按钮会谎报成功。此坑已实测踩过（冷索引工作区上 `rescan_index` 0.00s / added=0，改名 `index_sync` 后同一场景 added=1638）。
- **全量重建复用已注册的 `reindex`**，不新造 job。
- **单飞锁加在我们这一层**：`RemeKbManager` 按 `(project_id, agent_id)` 维护 `asyncio.Lock`，两个索引动作跨类型互斥；锁必须在专属事件循环协程内获取（`asyncio.Lock` 绑定 loop）。reme 的 local file store 侧无任何锁（`LocalFileStore.clear/upsert/dump` 均无锁，只有 FAISS 后端有 `_faiss_dump_lock`），所以这层守卫是唯一的。
- **在途再点 → 409**，detail 中文：「索引任务进行中，请等待当前任务完成」。
- **端点契约**：`POST /api/kb/index/sync` 与 `POST /api/kb/index/rebuild`，无请求体。返回 `{"results": [{"agent_id", "counts": {added, modified, deleted}, "elapsed_ms", "success"}], "total_elapsed_ms": int}`；**逐实例串行、单个失败不中断其余**，失败项带 `error` 字段。整体只要有任一失败则 `success=false`，HTTP 仍 200（部分失败不是服务端错误）；仅当整个 KB 不可用（`KbUnavailableError`，如 `kb_enabled=false`）→ 503，与现有 `/api/kb/*` 一致。
- **UI 结果必须可见**（守住「动作不能只留一个空响应」这条）：toast 回报真实条数与耗时，例如「索引已同步：新增 0 · 修改 1 · 删除 0（2 个实例 · 0.4s）」。
- **二次确认文案要写清全量重建的边界**：清的是派生索引（chunk store `file_chunks_default_v1.jsonl.zst`、BM25 pkl、file graph），**不动向量缓存**（`LocalEmbeddingStore` 的 `.npz`，按文本 sha256 命中，故当前形态零网络）；并提示「重建期间请勿写入知识库」。
- **脱敏口径不变**：新文案一律只出现别名 `kb`，UI 任何位置不得出现 `zhb` 或实体根路径；API 响应层按既有裁定继续不收敛。
- **测试硬门禁（本期新增，保护用户真实知识）**：任何真起 reme `Application` 的测试，`kb_bases_dir` 必须指向**临时副本**，绝不允许指向真实 `~/.reme/knowledge_bases`。原因见「风险」节。

## 实测数据（2026-10-03，真实 KB 的临时副本，1638 个 md / 1802 文件）

| 动作 | 耗时 | 返回 |
|---|---|---|
| `index_sync` 无变更（纯扫描+diff） | **0.15s** | `counts` 全 0 |
| `index_sync` 改 1 个文件 | **0.21s** | `added=0 modified=1 deleted=0` |
| `index_sync` 冷索引全建 | 2.85s | `added=1638` |
| `reindex` 全量重建 | 2.72s | `cleared=True added=1630` |
| 重建后 `knowledge_search` | 0.00s | `success=True` hits=3 |

按裁定 5 串行跑两份实例 ⇒ 日常点击约 0.3s、最坏（冷/全量）约 7s。当前环境**无 `.env`** → embedding 关闭 → 索引动作纯本地零网络。若日后配上 embedding Key，全量重建在冷向量缓存上会打批量 embedding 请求，这属于成本动作，须用户当面同意——二次确认文案里要带这条条件性提示。

## 设计细节

**后端**
- `config.py::_KB_JOBS` 增 `index_sync`：`backend: base` + `watch_dirs: ["knowledge"]` + `watch_suffixes: ["md"]` + 单步 `init_changes_step(monitor_type=file_store, monitor_name=default, dispatch_steps=[update_index_step])`。形态即 `index_update_loop` 首步抽出成一次性 job（对齐 reme 自带 `index_sync` 用法）。`dispatch_steps` 用字符串名 ⇒ `update_index_step` 走默认 `persist`，结果落盘。
- `manager.py` 增索引通道：取锁 → 按 `[("default","console"), ("default","kb_assistant")]` 顺序 `run_job`，逐实例计时与捕获异常 → 释锁。实例未启动时按现有 `_get_app` 语义**懒启动**（启动本身就会做一次全扫，因此该实例的 `counts` 合理地为 0）。
- `router.py` 增两个 POST 端点 + `IndexBusyError → 409` 映射。

**前端**
- `/kb` 树栏头部（现展示 `KB · 根名` 那一行所在区域）加两个带文字按钮：「同步索引」（主操作）与「全量重建索引」（次级、危险态样式）。
- 在途期间两个按钮一起置灰并显示「索引同步中…」；`apiFetch` 沿用现有超时口径（同步等待最长约 7s，远低于现超时上限）。
- 全量重建走原生 `window.confirm`（与 `/projects` 删除、`/kb` 写盘同款；CDP 自动化会挂、人工点击无碍，已有留痕）。

## 风险与刻意取舍

- **与后台 watcher 的竞态**：`reindex` 的 `clear()` 与 watcher 的 `update_index_step` 理论上可交叠。最坏后果是磁盘 store 少若干条（**不是丢知识文件**：清的全是派生索引，源头 md 不动），且下一次写盘收敛或再点一次本入口即恢复。reme 的 `BackgroundJob` 只有 `_start/_close`、**没有 pause/flush API**，所以刻意不做「暂停 watcher」，改由确认文案提示「重建期间请勿写入知识库」。
- **junction 陷阱（本期实测撞到，故升格为测试门禁）**：`ensure_knowledge_mount` 会把 `workspace/knowledge` 做成指向真实 KB 的 Windows junction。`shutil.rmtree` 会跟随 junction 递归——一旦临时目录被清理时跟着它删，**删的就是用户那 1802 个真实知识文件**。本专项的实测脚本第一次清场就撞了只读对象权限错，改为「先 `os.rmdir` 摘链、再删其余」才安全。因此所有真起实例的测试必须把 `kb_bases_dir` 指到临时副本；评审时这条按硬门禁查。
- **不做实例选择器**：聊天侧智能体（如 `case_design`）若也携带 kb 工具，它那份索引不在本入口覆盖内，仍靠 watch 收敛。这是裁定 5 的直接后果，属范围外，UI 与文案都不承诺「所有智能体已同步」。

## 范围外（本期明确不做）

- 聊天页/项目维度的索引目标选择、多 KB 切换后的实例编排
- 索引进度流、任务队列、取消
- 把 `index_sync` 塞回 `/kb/save` 链路
- watcher 规则自定义、索引健康面板、非 `.md` 后缀纳入

## 验收标准

- 后端：`index_sync` 注册生效且**冷索引场景真能 added>0**（守住取名那条坑）；`index_sync` 落盘后磁盘 store 存在（不假设 `persist` 默认值）；并发第二请求 409；两实例串行、单实例失败不吞掉另一个；`kb_enabled=false` → 503；全部测试用临时副本 KB，绝不指真实 `~/.reme`。
- 前端：两按钮 + 二次确认 + 在途置灰 + toast 真实条数可见；页面上 `zhb` 命中数 0。
- 真机走查（用户在场）：`/kb` 页新建一个探针 md → 点「同步索引」→ 立刻检索命中；不点时对照约 10-30s 才收敛；探针文件用后即删并再点一次同步。
