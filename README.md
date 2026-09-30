# AiTester · 测试智能体

前后端分离单仓工程。后端 Python + FastAPI + LangChain/LangGraph（七层分层骨架），
前端 TypeScript + React + Vite。`prototype/` 为已验证的 MVP 原型（静态页 + serve.js），
仅作参考，不参与构建。

## 后端（backend/）

```bash
cd backend
uv sync
uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000 --reload
```

- `GET /api/health`：联通检查
- `POST /api/chat/echo`：窄链路演示，响应 trace 穿透七层
  （interaction → services → context → orchestration → adapters → memory → storage）
- 测试：`uv run pytest`
- 配置：复制 `.env.example` 为 `.env`（当前仅 mock provider，无需 Key）

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
