# AiTester 工程骨架设计（分层占位 + 窄链路打通）

日期：2026-09-30
状态：已与用户确认布局方案 A、占位深度（窄链路）、不做 git 初始化

## 1. 目标与范围

将已验证的 MVP 原型（`prototype/`）演进为正式前后端分离单仓工程。本次只搭架构骨架：

- 后端七层（交互层、服务层、编排 loop 层、上下文管理、记忆层、接入层、存储层）各建目录 + 接口 + 最小实现；
- 一条窄链路（health + echo 对话端点）从前端穿透全部后端分层，证明管道与依赖方向正确；
- 前端 React + TS 骨架，三视图路由占位（聊天 / 知识库 / 项目管理）。

不在本次范围：真实业务逻辑、真实 LLM 调用、持久化设计、认证、OpenAPI codegen、CI。`prototype/` 原样保留、不进构建；仓库不做任何 git 操作。

## 2. 技术栈

| 侧 | 选型 |
|---|---|
| 后端 | Python 3.11+，uv 管理依赖，FastAPI（HTTP/SSE），LangChain + LangGraph（编排层），pydantic-settings 读 `.env` |
| 前端 | Node 24 + npm，Vite + React 18 + TypeScript，react-router 三路由；开发期 Vite proxy `/api` → 后端 |
| 后端默认端口 | 8000（127.0.0.1） |

## 3. 仓库布局

```
AiTester/
├─ backend/
│  ├─ pyproject.toml            # uv 项目，包名 aitester
│  ├─ .env.example              # 占位配置模板
│  └─ src/aitester/
│     ├─ main.py                # FastAPI 应用装配（仅组装各层，不写业务）
│     ├─ config.py              # Settings（pydantic-settings）
│     ├─ interaction/           # 交互层
│     ├─ services/              # 服务层
│     ├─ orchestration/         # 编排 loop 层
│     ├─ context/               # 上下文管理
│     ├─ memory/                # 记忆层
│     ├─ adapters/              # 接入层
│     └─ storage/               # 存储层
├─ frontend/
│  ├─ package.json
│  ├─ vite.config.ts            # /api proxy → http://127.0.0.1:8000
│  └─ src/
│     ├─ main.tsx / App.tsx     # 顶部导航 聊天/知识库/项目管理
│     ├─ pages/{Chat,KnowledgeBase,Projects}.tsx   # 空白占位页
│     └─ api/client.ts          # fetch 封装，含 health 调用
├─ prototype/                   # 保留，不参与构建
└─ README.md                    # 启动方式说明
```

## 4. 后端七层职责与最小实现

依赖方向（只允许上层依赖下层，禁止反向/跨层绕行）：

```
interaction → services → orchestration → context / memory → adapters / storage
```

各层以「协议（Protocol/ABC）+ 最小实现 + `__init__.py` 导出」占位：

1. **交互层 interaction**：FastAPI APIRouter。端点：`GET /api/health`；`POST /api/chat/echo`（body `{message}`，调服务层返回 `{reply, trace}`）。只做协议转换与 DTO，不含业务。
2. **服务层 services**：`ChatService` 门面。接收交互层请求，装配上下文后调用编排层；记录本次穿透的层名列表 `trace`，供窄链路验证。
3. **编排 loop 层 orchestration**：LangGraph `StateGraph`，当前仅一个节点 `mock_llm_node`（调接入层 mock provider 回显文本），`EchoState`(TypedDict) 定义 state。后续专项开发在此扩展多节点 loop。
4. **上下文管理 context**：`ContextBuilder` 协议：输入会话消息，输出编排层可用的 prompt 消息列表。最小实现：原样透传 + 附加一条系统提示。
5. **记忆层 memory**：`MemoryStore` 协议：`save(session_id, msg)` / `recall(session_id)`。最小实现：进程内 dict（重启即失，注释标明后续替换）。
6. **接入层 adapters**：外部集成适配。`LlmProvider` 协议（对齐原型 provider 化模型配置方向）+ `MockProvider`（echo 返回）；目录预留 `kb/`、`tools/` 空包。
7. **存储层 storage**：`Repository` 协议（session/project 等实体的 CRUD 抽象）+ `InMemoryRepository` 最小实现。后续换 SQLite/DB。

trace 机制：服务层依次标注经过的层，echo 响应返回如 `["interaction","services","context","orchestration","adapters","memory","storage"]`，作为窄链路打通的证据。

## 5. 前端骨架

- 顶部导航三个 tab 对应三路由：`/chat`、`/kb`、`/projects`，均为占位空白页（各一句「后续专项开发」提示）；视觉不还原原型（原型仅验证流程，正式 UI 后续按原型重做）。
- `api/client.ts` 提供 `getHealth()`；`/chat` 页加载时调用并在页面显示 `{ok: true}` 状态，作为前后端联通证据。
- 严格 TS（`strict: true`），不做 SSR。

## 6. 配置与启动

- `backend/.env.example`：`HOST=127.0.0.1`、`PORT=8000`、占位 `LLM_PROVIDER=mock`、（预留）`DEEPSEEK_API_KEY=`、`DASHSCOPE_API_KEY=`。真实 `.env` 由用户复制后自填，`.gitignore` 不适用（本仓暂不 git，仍写一份 backend/.gitignore 备用，忽略 `.env`、`__pycache__`、`.venv`）。
- 启动：`backend`: `uv sync && uv run uvicorn aitester.main:app --reload`；`frontend`: `npm install && npm run dev`（5173）。README 写明两步启动，并保留原型 `node serve.js`(8899) 的说明避免混淆（用户习惯 localhost 预览）。

## 7. 测试与验收

- `backend/tests/test_health.py`、`test_echo_trace.py`：pytest httpx 断言两端点 200 且 trace 覆盖七层中的必经层。
- 验收清单：① `uv run pytest` 全绿；② uvicorn 起服务后 `GET /api/health` 返回 ok；③ `POST /api/chat/echo` 返回含逐层 trace；④ `npm run dev` 打开 http://localhost:5173/chat 显示后端联通状态；⑤ `npm run build` 通过。

## 8. 决策记录

- 单仓 `backend/`+`frontend/`（用户确认，弃 apps/packages monorepo 与分层分包方案，YAGNI）。
- 占位深度 = 窄链路打通（用户确认）。
- 接入层 = 外部集成适配（模型提供商/工具/知识库），交互层 = 前端 API 端点（用户确认）。
- 不初始化 git；prototype/ 保留原位（用户确认）。
