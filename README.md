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

## 后端（backend/）

```bash
cd backend
uv sync
uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000 --reload
# 或按配置启动（读取 backend/.env 的 HOST/PORT，任意目录均可）：
uv run python -m aitester.main
```

- `GET /api/health`：联通检查（含当前 `llm_provider`）
- `POST /api/chat/echo`：窄链路演示，响应 trace 穿透七层
  （interaction → services → context → orchestration → adapters → memory → storage），恒走 mock
- `POST /api/chat/send`：真实 LLM 链路，按配置构建 provider，响应含 `model`
  （未配 Key → 400 指引；上游失败 → 502）
- 测试：`uv run pytest`
- 配置：复制 `.env.example` 为 `.env`。默认 `LLM_PROVIDER=mock` 无需 Key；
  真实调用填 `DEEPSEEK_API_KEY` 或 `DASHSCOPE_API_KEY` 并把 `LLM_PROVIDER` 改为对应值。
  **Key 仅保留在本机 `.env`（已 gitignore），不会入库或出现在日志/错误信息中**；
  `.env` 的 HOST/PORT 经 `python -m aitester.main` 生效

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
