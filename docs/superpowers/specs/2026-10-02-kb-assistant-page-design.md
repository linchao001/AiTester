# /kb 知识库页对齐原型（目录树 + 预览编辑 + reme 聊天助手）设计

> 状态：已与用户逐条确认裁定，已实施（实现 commit c810b42..a32cfb3，十一任务账本见 `.superpowers/sdd/2026-10-02-kb-assistant-page/`）
> 前序：`2026-10-01-knowledge-base-reme-design.md`（KB 底座四条裁定，已实施 master@3547804）
> 原型真相：`prototype/index.html`（:8899 三栏页）+ `prototype/serve.js`（KB 文件 API）

## 用户裁定（本轮 AskUserQuestion 确认）

1. **页面形态**：/kb 保持和原型一致——左目录树 + 中知识库预览/编辑 + 右知识库聊天助手；现三区表单页作废。
2. **助手身份**：新增内置智能体 `kb_assistant`（知识库助手）。
3. **旧表单处置**：直接删除页面呈现；`/api/kb/status|bases|search|save` 后端端点保留（工具与其他链路仍在用）。
4. **写入安全线**：草案卡片确认——助手零直写，产出「待写入草案」卡（新建/修改 + 绝对路径 + 摘要 + 行级 diff），用户点「✓ 确认写入磁盘」后才真正落盘。

## 关键事实修正（相对口头设计稿）

口头设计里写了「SSE draft 事件」；核实现网后修正：**现应用无 SSE**（`chat_send` 是同步 `POST /api/chat/send` → `SendResponse{reply, trace, model}`；`ChatPage.tsx` 仅 21 行占位）。草案传输改为 **SendResponse 扩 `drafts` 字段**，语义等价、更轻。本期仍不做 SSE/聊天页专项。

## 架构

```
前端 /kb 三栏
 ├─ 左/中（文件面）→ 新 FastAPI 路由 /api/kb/browse/*（直接读写 KB 实体目录，serve.js 语义逐条移植）
 │                     md 文件变更 → 各 reme 实例 index_update_loop watch 收敛（既有裁定4，不自造同步 reindex）
 └─ 右（助手）    → POST /api/chat/send {agent_id:"kb_assistant", session_id:"kb-console"}
                       AgentRuntime → 强制绑定工具面（含新工具 prepare_kb_write）
                       prepare_kb_write 产出草案（模型不可见 artifact 通道）→ SendResponse.drafts → 前端草案卡
                       卡片「确认写入磁盘」→ 前端调 /api/kb/browse/file POST/PUT（带 mtime）
```

## 后端契约

### A. `/api/kb/browse/*`（新增，`backend/src/aitester/interaction/kb_browse.py` + schemas 追加）

根解析：`KbConfig` 同款三级（配置 `kb_bases_dir` > `REME_KNOWLEDGE_BASES_DIR` > `~/.reme/knowledge_bases`）拼 `kb_id`，**不硬编码**；`kb_enabled=False` → 503（与 KB 面边界一致）；实体目录不存在 → 404 中文 detail。

六端点逐条对齐 serve.js（handler 表见 `prototype/serve.js:93-198`）：

| 端点 | 语义 | 错误码 |
|---|---|---|
| `GET /api/kb/browse/tree?path=` | 逐级列目录，dirs-first、`zh` 排序，返回 `{root, rel, items:[{name,rel,dir,size,mtime}]}` | 越界403 / 不存在404 / 非目录400 |
| `GET /api/kb/browse/file?path=` | `{rel,name,ext,content,size,mtime,editable}` | 403/404/400(目录) / 非文本415 / >2MB 413（均带中文 error，415/413 附 editable:false） |
| `GET /api/kb/browse/search?q=&limit=` | 文件名子串全库扫（≤400，默认120），`{root,total,truncated,hits[]}` | — |
| `GET /api/kb/browse/scan?path=&limit=&md=` | 深度 ≤12 收集 .md（≤3000，默认800；>512KB 跳过），解析 frontmatter 顶层 kv，`{root,scanned,truncated,docs[]}` | 403/404 |
| `PUT /api/kb/browse/file?path=&mtime=` | 编辑保存；`mtime` 与磁盘不符 → **409 `{error:"文件在页面打开后被外部修改，未覆盖", mtime}`**；body `{content}`；成功回 `{rel,size,mtime}` | 403/415/404(请用新建)/400/409/413 |
| `POST /api/kb/browse/file?path=` | 新建；已存在 → 409「同名文件已存在，请换个名字」；父目录不存在 → 400；成功 201 | 同上族 |

安全规则逐条平移：`kbPath` 解析（拒 `\0`、resolve 后必须严格在根内）、`HIDDEN_DIRS`(.git/.idea/.vscode/.locks/__pycache__/node_modules/.obsidian)、`hiddenEntry`（点前缀、.pyc 等）、`TEXT_EXT` 白名单、2MB 上限。仅监听本机由现有部署形态保证。

### B. 新工具 `prepare_kb_write`（`adapters/tools/kb_tools.py` 追加）

- 入参（模型侧，英文文案）：`{op: "create"|"modify", path: KB 根相对路径, content: 全文, summary: 一句话}`。
- 服务端校验：path 在根内且 `.md`；modify → 文件存在（读当前磁盘内容为 `base`、取 `mtime`）；create → 不存在且父目录存在。校验失败返回可读英文错误（模型可自纠）。
- **零写盘**。成功时返回短确认句（模型可见）+ 结构化草案走 ToolMessage **artifact 通道**（模型不可见，对齐文件工具 diff 的既有模式）：`{op, path, abs_display, summary, content, base, mtime}`。
- `orchestration/agent_graph.py::run_graph` 收集 ToolMessage artifacts 进返回值；`chat_send` 将其中 `prepare_kb_write` 的草案填入 `SendResponse.drafts: list[KbDraft]`（schemas 扩字段，默认空列表；echo 链路不动）。

### C. 内置智能体 `kb_assistant`（`agents/catalog.py` + `agents/prompts/kb_assistant.md`）

- AgentSpec：id=`kb_assistant`、名称「知识库助手」、`default_tool_ids = (read, grep_search, glob_search, knowledge_search, prepare_kb_write)`（与现有工具注册名逐一对齐）。**不给** `write`/`edit`/`save_to_knowledge`（一切写经草案）。
- 强制绑定：`AgentRuntime.build` 对 `kb_assistant` 特例——工具面 = 上述清单 ∩ 实际可注册集合，**不受能力配置的携带勾选/禁用级联管辖**（平台功能智能体；`capability_config.py` 的 AGENT_CATALOG/TOOL_CATALOG 不新增条目，设置页与聊天页智能体下拉维持现状不可见它，遵守「智能体配置只做列出的」既有裁定）。
- 会话与实例键：`project_id="default"`、`agent_id="kb_assistant"`（reme manager 实例池键固定，KB 全局不随项目切换）；对话记忆键 `kb_assistant:kb-console`（前端固定传 session_id=`kb-console`）。
- 提示词要点（中文）：检索优先 `knowledge_search` 并原样给出命中路径+得分；文件名/目录类诉求用 glob/grep/read 三件套；**任何写入必须且只能经 `prepare_kb_write` 产出草案，明确告知用户「点确认后才会落盘」，绝不声称已写入**；目录索引/P0 清单/查重类任务先扫描再产草案；回答简洁中文。
- workspace：`backend/data/workspaces/default/kb_assistant/`，`knowledge/` junction 挂载（Task 3 既有机制），grep/glob/read 走相对路径 `knowledge/...`，与 reme 检索结果路径同形。

## 前端（`frontend/src/pages/KbPage.tsx` 重写 + 新组件）

- 组件拆分：`pages/kb/KbTreePane.tsx`、`KbEditorPane.tsx`、`KbAssistantPane.tsx`、`KbDraftCard.tsx`（KbPage 只做三栏布局与状态编排，缓解现单文件 400 行的既有 deferred）。
- 三栏行为逐条对齐原型：懒加载树（展开才拉子级）+ 文件名搜索框 + 计数 num + 根路径脚注；中栏 frontmatter 元信息 chips（10 键表 + 体积/时间）、预览/编辑 tab（非 md 只读「原文」、编辑 tab 隐藏）、保存/放弃/重载按钮 + `confirm` 文案 + 409 自动重载 toast、状态栏行数字数；新建笔记弹窗（标题→slug 文件名、描述、桶快捷 chips 七个照抄 `KB_BUCKETS`、模板 frontmatter `updated_by_agent: aitester_kb_assistant` 同原型）。
- 右栏助手：消息流（me/ai 气泡 + md 渲染）、`POST /api/chat/send`（agent_id/session_id 固定）、忙态锁、快速指令 chips 五条照抄原型文案、Ctrl/Cmd+Enter 发送；`drafts` 非空时逐条渲染草案卡（新建/修改徽标、绝对路径、摘要、行级 diff——create 显前 14 行 add、modify 显 kbDiff 增删 ≤50 行、「✓ 确认写入磁盘」→ 原生 confirm 二次确认 → POST/PUT browse/file → 成功卡片转绿「已写入 · 时间」按钮移除；取消置灰「已取消」；409 → toast 重载）。
- 面板一致性规约（用户既有反馈）：« 收起 + 「📁 目录」/「💬 助手」带文字恢复按钮 + 两条拖拽 resizer；CSS 沿用原型 `.kb-*` 类名族迁入 App.css（替换现三区表单样式段）。
- 客户端 API：client.ts 追加 browse 六函数 + `KbDraft` 类型；`chatSend` 复用现有函数（响应形状向后兼容 +drafts）。

## 测试策略

- browse 路由：tmp 假 KB 根 + TestClient——路径穿越/`\0`/隐藏目录 403、404/400/415/413、tree dirs-first 排序、search/scan 截断与 frontmatter 解析、PUT mtime 不符 409、POST 撞名 409、成功回写字段。
- `prepare_kb_write`：校验分支（越界/非md/存在性互斥）、modify 抓到 base+mtime、确认句非空英文、artifact 形状；零写盘断言（文件 mtime 不变）。
- run_graph/chat_send：假模型驱动工具 → `SendResponse.drafts` 透传；无工具时 drafts 空；echo 链路回归不动。
- AgentRuntime：`kb_assistant` 强制绑定不受 capability 勾选影响、其余智能体行为不变；实例池键 (default, kb_assistant)。
- 前端：`npm run build` 0 错误；真机走查**另起端口**（`scripts/dev.ps1 -FrontendPort`，绝不碰用户 8000/5173 在跑的服务——走查用的后端也由我们临时起在别的端口，或经用户同意后复用）。
- 成本门禁：单测零真实 LLM/向量调用；助手真机对话（真实模型调用）需用户当时明确同意。
- 真实写盘验收：草案卡确认链路 = 真实写 `zhb_kb`，沿用「仅用户当面同意后」门禁；测试节点用后即清。

## 明确不做（本期）

SSE 流式与聊天页专项；inbox 审核 UI、knowledge_dream 自动回流（前序遗留不变）；多 KB 切换；图谱视图；助手写文件的 mtime 乐观锁以外的冲突策略；原型「助手本地规则引擎」复刻（能力由真智能体+工具承担）。

## 风险与对策

| 风险 | 对策 |
|---|---|
| browse 直写与 reme save 写锁并存，编辑大文件瞬间 watch 读到半个文件 | 写操作 `writeFileSync` 单调用原子性足够；索引收敛容错已是既有轮询模型 |
| scan 全库 1637+ 条目变慢 | 与 serve.js 同限（limit 截断 + >512KB 跳过）；默认 md-only |
| 模型不调 prepare_kb_write 而口头承诺写入 | 提示词硬约束 + 无直写工具（write/edit/save 均未绑定），物理上写不进去 |
| drafts 字段破坏既有 SendResponse 消费方 | pydantic 默认空列表，旧前端不读该字段即无感 |
| kb_assistant 特例绑定被遗忘在能力配置迭代中 | 代码集中一处（AgentRuntime.build 特例 + 注释指向本 spec）；专测锁行为 |
