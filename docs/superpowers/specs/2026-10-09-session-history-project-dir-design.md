# 会话历史迁入项目空间 · 设计

日期：2026-10-09　状态：**已实现**

现状依据：`services/session_store.py`（单根 `backend/data/sessions`，`index.json` + `sess_*.jsonl`）、`memory/file_memory.py`、`services/chat.py`（`HISTORY_MAX=40`、`_persist` / `_STEP_KEYS`）、`orchestration/agent_graph.py`（`tool_traces` 含 `result`，SSE `step` 刻意不外泄）、`interaction/sessions.py`（list 已要 `agent_id`+`project_id`；messages/delete 仅 `session_id`）、`main.py`（`SessionStore(DATA_DIR / "sessions")`）、`interaction/projects.py`（删项目后 `delete_by_project`）。

## 背景

会话真相目前在后端进程数据目录 `backend/data/sessions/`。产物与对话历史和「项目本地文件目录」脱节：换机器/备份项目目录带不走聊天史；落盘 steps 只有 `{tool, ok, round, detail}`，工具完整输出（图内 `result`）被剥掉，审计与复盘不够。

本期把**可见智能体**的会话唯一真相迁到项目空间，并扩展消息行字段（含全量工具输出）；**不**改变「进 prompt 只取 role/content、最近 40 条」与「SSE/读历史 API 不回 result」的对外契约。

## 用户裁定（2026-10-09，逐条确认）

1. **整体迁移（A）**：`{项目.dir}/session_history/` 为唯一真相；停止以 `backend/data/sessions` 为读写路径。
2. **不迁移旧数据（A）**：上线后旧目录会话不再被本系统读取；用户视为新开。
3. **工具输出全量落盘（A）**：`steps[].result` 原样写入，不截断、不分文件。
4. **按智能体分子目录（B）**：`session_history/<agent_id>/index.json` + `sess_*.jsonl`。
5. **result 只落盘（A）**：SSE `step` 与聊天历史 API 仍只暴露 `{tool, ok, round, detail}`。
6. **实现路径 1**：按「项目 dir × 智能体」现开 `SessionStore`（可短缓存），不新建 Hub 封装层。
7. **删 AiTester 项目配置不删磁盘 `session_history/`**（项目空间产物归用户目录）。
8. **messages / delete 端点**：`project_id` 与 `agent_id` 均必填（与 list 对称）。

## 控制端裁定（工程判断，可推翻）

- **工厂**：`open_session_store(project_dir: Path | str, agent_id: str) -> SessionStore`，根为  
  `Path(project_dir).expanduser().resolve() / "session_history" / agent_id`。  
  `SessionStore` 本体契约（锁、原子写索引、jsonl 追加、坏索引留档）尽量不动，只换根与消息字段。
- **去掉** `create_app(..., sessions_dir=...)` 单根注入；测试改为给临时「项目 dir」+ 工厂，或注入可替换的 `open_session_store`。  
  应用态若仍需 `app.state.sessions`，改为持有「解析器」：输入 `project_id`（查 ProjectService 得 dir）+ `agent_id` → 调工厂；**不是**独立 SessionHub 业务层，禁止在解析器里塞列表缓存策略以外的逻辑。
- **`FileMemoryStore`**：构造期带入已打开的 `SessionStore`（或 `project_dir`+`agent_id` 现开）以及写行所需的 `agent_id`/`session_id`；`save` 时每行写入这两字段；assistant 的 `steps` 透传含 `result` 的完整项。
- **`ChatService._persist` / `_fold_turn`**：收集 steps 时保留 `result`（来自图 `tool_traces` 或等价通道）；yield SSE `step` / `done` / 读 API 映射时剥掉 `result`。不得为了落盘而把 `result` 塞进模型可见消息。
- **`recall`**：仍只投影 `role`/`content`；`HISTORY_MAX=40` 不变。
- **平台智能体**：继续 `InMemoryMemoryStore`，不写 `session_history`。
- **sid 唯一性**：仅在「该项目 dir × 该 agent 目录」内保证；跨项目允许同名 `sess_*`（故 API 必须带齐作用域）。
- **删项目**：`projects_delete` 不再调用会清空会话文件的 `delete_by_project`（删除该调用即可；若保留方法名则必须是统计用、禁止 unlink）。确认弹窗的会话数：扫目标项目 `dir/session_history/*/index.json`，**计数 index 内会话行数之和**（不是把各行 `message_count` 加总）。
- **项目 dir 不可达**：列表/读写/发送守门失败 → 既有中文 detail 口径；**禁止**回落 `backend/data/sessions`。
- **README**：会话落盘路径改为项目 `session_history/`；删掉「单进程共享 `backend/data/sessions`」中与旧路径绑定的表述，改为「同一项目 dir 下的 session_history 仍假定单后端进程写索引」（多进程写同一项目目录仍不受支持）。

## 目录与数据模型

```
{项目.dir}/session_history/
  <agent_id>/
    index.json
    sess_<8hex>.jsonl
```

**index 行**（不变字段集）：`id, agent_id, project_id, title, created_at, updated_at, message_count`  
（`agent_id` 须与所在目录名一致；不一致行加载时丢弃并打 warning，与缺 `project_id` 同口径。）

**jsonl 行**（Reme 最低可用 + AiTester 扩展；磁盘不写 `ts`）：

```json
{
  "name": "user|assistant",
  "role": "user|assistant",
  "content": "...",
  "created_at": "2026-03-10T10:00:00+08:00",
  "id": "msg_<hex>",
  "agent_id": "case_design",
  "session_id": "sess_a207bc14",
  "steps": [
    {"tool": "read", "ok": true, "round": 1, "detail": "{...}", "result": "...全量..."}
  ],
  "stopped": false,
  "context": null
}
```

- Reme 最低可用：`name`（默认等于 `role`）、`role`、`content`、`created_at`（本地时区 ISO-8601）、`id`（`msg_` + hex）。
- 内存/API 仍有 `ts`（epoch ms）：由 `created_at` 反算；老行只有 `ts` 时读侧合成 `created_at`，不回写磁盘。
- user 行：`steps` 为 `null`，`stopped` 为 `false`，`context` 为 `null`；仍写 `agent_id`/`session_id`。
- 缺 `agent_id` 或 `session_id` 的行（含手塞旧形态）：**跳过该行并 warning**，不整段 500；不补默认空串冒充合法行。
- `StepInfo`（API schema）：**不**增加 `result`；`model_validate` 遇多余字段默认忽略（Pydantic），磁盘多 `result` 不影响读历史。

## 模块与 API 变更

| 位置 | 变更 |
|------|------|
| `session_store.py` | `ChatMessage` +`agent_id`/`session_id`；`append`/`create` 写入；steps 原样存盘（含 result） |
| 新或同文件工厂 | `open_session_store(project_dir, agent_id)` |
| `file_memory.py` / `chat.py` | 按项目现开 store；persist steps 带 result；对外剥 result |
| `agent_graph.py` | 尽量不动；若 fold 只吃 `_STEP_KEYS`，改为 persist 路径另取完整 traces |
| `interaction/sessions.py` | messages、delete 增加 Query：`project_id`、`agent_id`（均 `min_length=1`） |
| `interaction/projects.py` | 去掉清盘式 `delete_by_project` |
| `main.py` / README | 去掉单根 sessions_dir |
| 前端 `api/client.ts` 等 | messages/delete 请求带上当前 `project_id`、`agent_id` |

HTTP 行为：缺参 422；会话不在该作用域 → 404「会话不存在或已被删除」（文案保持）。

## 测试要点

1. 临时项目 dir 下出现 `session_history/<agent_id>/index.json` 与 jsonl；行含 `agent_id`、`session_id`；assistant `steps[].result` 等于工具返回全文。
2. `GET .../messages` 与 SSE `step`/`done.steps` **无** `result` 键（或验证 schema 不含）。
3. messages/delete 缺 `project_id` 或 `agent_id` → 422；错误 agent/project 组合 → 404。
4. 删除 AiTester 项目配置后，临时 dir 内 `session_history/` 仍在。
5. 平台智能体 / 非 `sess_*` 键仍不落盘。
6. 回归：延迟首条落盘、停止/断开 stopped 行、待批不落盘等既有语义保持。

## 非目标

- 旧 `backend/data/sessions` 迁移或双读  
- `result` 截断、分文件、进 prompt、进 UI  
- 个人记忆 / 跨会话记忆  
- 调整 `HISTORY_MAX`  
- 多后端进程共写同一项目 `session_history`

## 风险

- **磁盘膨胀**：全量 `result`（尤其 `read`/`pwsh`）可使单会话 jsonl 很大；已由用户裁定接受。  
- **API 破坏**：messages/delete 新增必填 Query——前端必须同步改，否则 422。  
- **删项目不再收会话文件**：与旧行为不同；已裁定，需在 README/删除确认文案中一句说明「聊天历史留在项目目录」。
