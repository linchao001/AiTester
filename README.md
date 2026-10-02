# AiTester · 测试智能体

前后端分离单仓工程。后端 Python + FastAPI + LangChain/LangGraph（七层分层骨架），
前端 TypeScript + React + Vite。`prototype/` 为已验证的 MVP 原型（静态页 + serve.js），
仅作参考，不参与构建。

## 一键启动（Windows PowerShell）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1
# 5173 被占用（如 QwenPaw 常驻）时换端口：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1 -FrontendPort 5175
```

自动检查 8000/前端端口占用、安装依赖、同时拉起前后端；就绪后打开提示的地址（默认 http://localhost:5173 ），
Ctrl+C 或按任意键停止，退出时清理整棵进程树（不会残留 uvicorn/node 孤儿进程）。
也可按下方说明分别手动启动。

「⚙ 设置」弹窗分三节：🧠 模型设置 / 🤖 智能体配置（本期仅「用例设计智能体」，系统提示词只读）/ 🛠 工具
（文件处理与检索、命令执行、网页搜索，跑不了的会标灰显示原因）。

## 后端（backend/）

```bash
cd backend
uv sync
uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000 --reload
# 或按配置启动（读取 backend/.env 的 HOST/PORT，任意目录均可）：
uv run python -m aitester.main
```

- `GET /api/health`：联通检查（`llm_provider` 为运行期默认模型 uid，未配置时 `mock`）
- `POST /api/chat/echo`：窄链路演示，响应 trace 穿透七层
  （interaction → services → context → orchestration → adapters → memory → storage），恒走 mock
- `POST /api/chat/send`：真实 LLM 链路，按 `agent_id` 现装一个一次性智能体实例
  （提示词 / 有效模型 / 携带工具 / 图拓扑），该智能体的默认模型可用则用、否则回落全局默认，未知 `agent_id` 返回 404
  （配置缺失 → 400 指引；上游失败 → 502）
- `GET /api/models` + 三个 `PUT`：模型配置运行期读写（Key 掩码返回，明文永不出口），
  对应前端顶栏「⚙ 设置」弹窗
- `POST /api/models/providers/{pid}/test`：测试连接 —— 只验证模型能否应答，
  取该提供商第一个已启用模型发一次 `max_tokens=1` 的极小补全（10s 超时、不重试）；
  可带未保存的 Key 草稿，仅本次请求使用，不落盘、不进日志与响应；
  失败归因为可照做的中文文案（Key 无效 / 地址不可达 / 连接超时 / 路径不对 / 限流），
  未知 `pid` 返回 404
- `GET /api/capabilities` + 三个 `PUT`：智能体默认模型 / 携带工具 / 工具启用停用的运行期读写
  （对应前端「⚙ 设置 · 智能体配置 / 工具」；禁用工具会从所有智能体级联摘除，重新启用不自动补回）
- 工具可用性取自本机探测（`available` / `unavailable_reason`）：本机缺 shell 的工具
  只读为不可用 —— 不能启用、不能携带；旧配置在读取时自动落回禁用并摘掉携带
- `web_search`（网页搜索）：对齐 QwenPaw 原理的可插拔 provider 架构 —— 默认 Tavily keyless
  免 Key 可用；进程环境变量 `AITESTER_WEB_SEARCH_PROVIDER=anysearch` 可切 AnySearch
  （匿名免费配额，自动注册的 Key 仅进程内缓存）；限流/网络失败时错误附 pwsh / bash + curl 兜底指引
- `grep_search` / `glob_search`（文件检索）：对齐 QwenPaw `file_search.py` —— 内容检索输出
  `文件:行号:> 命中行`（支持正则、`|` 字面量 OR、大小写、前后上下文行、按文件名过滤）；
  按名查找返回排序后的相对路径（目录带 `/`）。统一跳过 `.git`/`node_modules` 等目录与
  二进制扩展名、超过 2 MB 的文件；上限 200 条命中 / ~50KB 输出 / 1 万文件，超时 30s / 15s，
  截断与超时都会附可照做的提示
- 工具面向模型的文案（工具描述 / 参数说明 / 错误与执行结果）一律英文，对齐 dsh-tool-fs
  与 QwenPaw 的原文口径；面向用户的 UI 文案（设置弹窗等）保持中文
- `/api/kb/browse/*（文件浏览与受控写盘，serve.js 语义）`：GET `tree`/`file`/`search`/`scan` + PUT/POST `file`，
  直读直写 KB 实体目录；PUT 带 `mtime` 乐观锁，磁盘已被外部改动则 409 不覆盖；POST 新建撞同名 409；
  路径穿越/隐藏目录 403；目标类型不符（非目录/是目录/父目录不存在）400；非白名单扩展名 415；超 2 MB 413
- 知识库（ReMe 进程内嵌）：backend 进程内直跑本地 wheel 的 ReMe `Application.run_job`，绝不启 HTTP 服务；
  KB 全局共享单份 `zhb_kb`（实体 `~/.reme/knowledge_bases/zhb_kb`），经 `/api/kb/*` 与前端 `/kb` 页使用；
  embedding 按 Key 启用 —— 设 `KB_EMBEDDING_API_KEY` 走语义+BM25 双路，未设则回落 `DASHSCOPE_API_KEY`，
  均无则纯 BM25；`knowledge_search` / `save_to_knowledge` 已注册为智能体工具；
  inbox 回流审核与 knowledge_dream 自动回流为后续专项
- `/kb` 三栏页（目录树 + 预览/编辑 + 助手）：右栏助手为内置平台智能体 `kb_assistant`（知识库助手），
  设置页与聊天页对其不可见，工具面强制绑定 read / grep_search / glob_search / knowledge_search /
  prepare_kb_write 五件，不随能力勾选变化；**助手零直写**——任何写盘必经「待写入草案」卡片，
  用户点「✓ 确认写入磁盘」后才经 browse POST/PUT 真正落盘；落盘后索引经 watch **约数十秒自动收敛**，
  无需手动重建；旧三区表单页已移除（`/api/kb/status|bases|search|save` 端点保留，工具与其余链路仍在用）
- 测试：`uv run pytest`
- 配置：复制 `.env.example` 为 `.env`（仅 HOST/PORT + 两个可选种子 Key）；
  运行期模型配置存 `backend/data/model_config.json`（gitignore，含密钥），
  首次启动自动从 `.env` 种子导入，之后在前端「设置 · 模型设置」管理；
  **Key 不入库、不出现在任何响应/日志/异常明文**
  能力配置（智能体默认模型、携带工具、工具启停）存 `backend/data/capability_config.json`
  （同样 gitignore，不含密钥），首次启动自动生成种子：种子启用态由本机探测推导，
  跑不了的工具不会以启用态出厂

## 前端（frontend/）

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173，/api 已代理到 127.0.0.1:8000
```

## 原型（prototype/）

```bash
cd prototype && node serve.js   # http://localhost:8899（原型页面，独立于正式工程）
```
