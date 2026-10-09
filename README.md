# AiTester · 测试智能体

面向测试设计的对话式工作台：按项目组织会话与产出，可配置模型与工具，支持知识库浏览与助手协作。

## 功能

- **项目**：绑定本地工作目录与启用的智能体；产出物落在项目目录，聊天历史保存在项目下的 `session_history/`
- **聊天**：多轮对话、会话列表与搜索、真实工具调用过程展示；可停止生成、对待执行操作做批准/拒绝
- **权限档位**：🛡 三档（自由 / 边界 / 严格）控制写文件、命令与知识库写入是否需确认
- **用例设计智能体**：围绕项目目录做测试设计；可委派只读子智能体并行检索，过程以折叠卡片展示
- **设置**：模型（Key 仅本机保存）、智能体默认模型与携带工具、本机可用工具启停（不可用的会标灰并说明原因）
- **知识库 `/kb`**：目录浏览与编辑；右侧助手只检索与起草，确认后才写入磁盘，索引随后自动更新

> 项目目录不可达时无法发送与落盘，请到项目页核对路径。

## 启动

### 一键启动（推荐，Windows PowerShell）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1
# 5173 被占用时换端口：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev.ps1 -FrontendPort 5175
```

会检查端口、安装依赖并同时拉起前后端；就绪后打开提示地址（默认 http://localhost:5173 ）。  
Ctrl+C 或按任意键停止，并清理相关进程。

首次使用可复制 `backend/.env.example` 为 `backend/.env`（可选种子 Key）；模型与能力也可在启动后于「⚙ 设置」中配置。

### 分别启动

**后端**

```bash
cd backend
uv sync
uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000 --reload
```

**前端**

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```
