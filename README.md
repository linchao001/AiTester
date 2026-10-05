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

「⚙ 设置」弹窗分三节：🧠 模型设置 / 🤖 智能体配置（「用例设计智能体」+「子智能体（被派发用）」，
系统提示词只读）/ 🛠 工具（文件处理与检索、命令执行、网页搜索、子智能体工具，跑不了的会标灰显示原因）。

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
- `POST /api/chat/send/stream`：真实 LLM 链路的唯一传输，按 `agent_id` 现装一个一次性智能体实例
  （提示词 / 有效模型 / 携带工具 / 图拓扑），该智能体的默认模型可用则用、否则回落全局默认，未知 `agent_id` 返回 404。
  守门在流开始前同步跑完（配置缺失 / 项目不可达 → 普通 400·404，prepare 阶段其余上游失败 → 502，
  detail 与迁移前逐字相同），
  过后响应 `text/event-stream`，逐 token 推 `start / delta / call / step / draft / wait / sub / done / error`
  九类事件，终态恒为一条（`done`，或被停止时 `done{stopped:true}`；流中模型失败 → `error`；
  待授权时不发终态，`wait` 之后直接断流，本轮一个字都不落盘）
- `POST /api/chat/stop`：`{run_id}` 置取消位终止在途回答；`run_id` 已结束返回 404「这条回答已经结束」，
  已生成的部分文本与已完成步骤照旧落盘，会话行标 `stopped`；待批期间同样可停：命中在途流优先，
  否则摘除待批条目并以已生成的前缀落一条 stopped 行
- `POST /api/chat/approve`：`{run_id, call_id, decision: approve|reject, remember}` 登记一条授权决策，
  成功 204 无体；`run_id` 已结束 / 已重启 → 404，这条已答过 → 409；`remember` 为真时同一会话内
  同「工具 + 目标」不再询问
- `POST /api/chat/resume/stream`：`{run_id}` 把挂起的那一回合用 `Command(resume=…)` 续跑，帧序与
  `send/stream` 同形（可能再次以 `wait` 收尾）；守门仍在 HTTP 空间，目录不可达回 400 且文案与发送时逐字相同
- `GET /api/chat/pending?agent_id=&project_id=`：待批表（进程内内存态，后端重启即空）；
  返回每条挂起回答的 `run_id / session_id / perm_mode / prefix / steps / waiting / decided / created_at`
  `agent_id` 与 `project_id` 两个参数都必填（空值 422）；响应体为 `{"runs": […]}`
- `GET /api/models` + 三个 `PUT`：模型配置运行期读写（Key 掩码返回，明文永不出口），
  对应前端顶栏「⚙ 设置」弹窗
- `POST /api/models/providers/{pid}/test`：测试连接 —— 只验证模型能否应答，
  取该提供商第一个已启用模型发一次 `max_tokens=1` 的极小补全（10s 超时、不重试）；
  可带未保存的 Key 草稿，仅本次请求使用，不落盘、不进日志与响应；
  失败归因为可照做的中文文案（Key 无效 / 地址不可达 / 连接超时 / 路径不对 / 限流），
  未知 `pid` 返回 404
- `GET /api/capabilities` + 三个 `PUT`：智能体默认模型 / 携带工具 / 工具启用停用的运行期读写
  （对应前端「⚙ 设置 · 智能体配置 / 工具」；禁用工具会从所有智能体级联摘除，重新启用不自动补回；
  响应含 `subagents` 节——「被派发用」子智能体，模型与工具同样可调，但不进项目清单与聊天页直选）
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
- `GET/POST/PUT/DELETE /api/projects`（项目 CRUD，JSON 落盘；本地文件目录与知识库配置创建后不可修改）
  项目字段由「名称 / 描述 / 本地文件目录 / 启用智能体 / 知识库」构成；知识库字段只存**别名 `kb` 且不可改**，
  界面不回显底层知识库标识（集中解析只活在后端）；聊天页已按项目取会话并统计每项目会话数，删除项目会连带回收其会话
- 聊天页 `/chat`：会话落盘（`backend/data/sessions`）、列表/搜索/分组/删除、多轮记忆（最近 40 条进 prompt）、
  真实工具调用过程展示；会话按「智能体 × 项目」归属，切项目即切会话，
  「当前智能体」下拉只列当前项目启用的智能体；智能体的文件工具与命令工具都以项目目录为工作起点，产出物落进项目；
  **边界执法由用户自控**：composer 的 🛡 芯片三档（档位存 `aitester.chat.permMode`，默认 `free`）——
  `free` 零执法（绝对路径照旧可出项目）；`boundary` 拦界外写与全部命令、界内写与知识库写入放行；
  `strict` 对界内外写、命令与 `save_to_knowledge` 逐次批准；平台智能体（如 `/kb`）无项目落点，不受辖
- 子智能体（委派式，`task` 工具）：`case_design` 携带 `task`，按提示词唤起「通用子智能体」——模型自行判断该派就派、
  用户点名必派、用户要求「同时/分别查」时一轮发多个；同线程内跑只读四件
  `read / grep_search / glob_search / web_search` 的独立实例，只回一份摘要，子内消息不进主上下文——主会话恰多一条工具结果；
  只读子面的多个任务在同一批并行跑（各占由 `call_id` 派生的独立 `checkpoint_ns`，仍共用父线程与同一份清理）；
  一旦在设置页给某个子勾上写 / 命令 / 知识库写，该子自动退回一轮一个（挂起续跑只在父 ns 上验证过）；
  失败收结构化英文错误、主线程继续；子过程实时透出（`sub` 帧建卡 + 带 `subagent` 标注的 `call`/`step` 帧），
  UI 为 🤖 折叠卡（live-only；落盘过程块只含父的 `task` 行）；子内写/命令照走三档审批（授权卡标「来自子智能体」）；
  停止级联；深度 1 结构锁（子不可再派子，设置页 `task` 置灰）；子智能体不进项目清单与聊天页直选
- 测试：`uv run pytest`
- 配置：复制 `.env.example` 为 `.env`（仅 HOST/PORT + 两个可选种子 Key）；
  运行期模型配置存 `backend/data/model_config.json`（gitignore，含密钥），
  首次启动自动从 `.env` 种子导入，之后在前端「设置 · 模型设置」管理；
  **Key 不入库、不出现在任何响应/日志/异常明文**
  能力配置（智能体默认模型、携带工具、工具启停）存 `backend/data/capability_config.json`
  （同样 gitignore，不含密钥），首次启动自动生成种子：种子启用态由本机探测推导，
  跑不了的工具不会以启用态出厂
- `backend/data/`（含 `sessions/`）由**单一后端进程**读写：同时起两个后端会互相看不见对方的会话，
  且后写者会用自己内存里的索引整文件覆盖 `sessions/index.json`

> 目录不可达：智能体产出物会丢失，到项目页确认路径（`/chat` 发送会被拦下并给出中文提示）。

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
