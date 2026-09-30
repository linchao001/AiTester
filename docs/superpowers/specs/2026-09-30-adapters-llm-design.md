# 接入层专项设计——真实 LLM 调用与 provider 工厂

日期：2026-09-30　状态：用户已确认（范围=真实调用+工厂；非流式；先不进配置管理 CRUD）

## 1. 范围

- 落地 DeepSeek / 通义千问 DashScope 真实调用（OpenAI 兼容协议，LangChain `ChatOpenAI`），保留 MockProvider。
- `.env` 配置 provider 选择与 API Key；缺失/非法时给出可执行的中文错误指引。
- 新端点 `POST /api/chat/send` 走真实链路；`/api/chat/echo` 语义不变（永远 mock，作七层 trace 回归）。
- 不做：模型目录/密钥运行期 CRUD（属配置+存储层专项）、SSE 流式（属交互层专项）、前端聊天 UI。

## 2. 配置（config.Settings 扩展，env 名自动映射）

| 变量 | 默认 |
|---|---|
| `LLM_PROVIDER` | `mock`（可选 mock/deepseek/dashscope） |
| `DEEPSEEK_API_KEY` | 空 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-flash` |
| `DASHSCOPE_API_KEY` | 空 |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `DASHSCOPE_MODEL` | `qwen3.7-max` |

默认模型名沿用原型已验证目录，不引入 deprecated 别名。`.env` 继续只在本机，不入库。

## 3. 分层改动

- **adapters.llm**（主体）
  - `errors.py`：`ProviderError(detail)`（上游失败）与子类 `ProviderConfigError`（配置缺失/非法），detail 面向用户中文。
  - `openai_compat.py`：`OpenAICompatProvider(name, api_key, base_url, model, timeout=60)`，`complete()` 经 `ChatOpenAI.invoke`，异常统一包 `ProviderError`；`model_ref = f"{name}/{model}"`。
  - `factory.py`：`build_provider(settings)`——mock→MockProvider；deepseek/dashscope→Key 空则 `ProviderConfigError`（点名缺哪个环境变量+申请处）；未知值→报错并列出合法值。
  - `LlmProvider` 协议增加只读成员 `model_ref: str`；`MockProvider.model_ref = "mock/mock"`。
- **orchestration**：节点 `mock_llm_node → llm_node`（语义中立，行为不变，`run_echo` 名称保留）。
- **services**：抽公共 `_complete(session_id, message, provider)`（现 echo 体）；`echo()` 恒用新 MockProvider 实例；`send()` 用注入的 `self.provider`，未注入时请求期 `build_provider(get_settings())`（启动不炸、错误映射 400）。返回增加 `model`。
- **interaction**：`POST /api/chat/send`（`SendRequest/SendResponse{reply,trace,model}`；`ProviderConfigError→400`、`ProviderError→502`）；`/api/health` 增 `llm_provider` 字段；`/chat/echo` 不变。
- **frontend**：`HealthResponse` 加 `llm_provider`；ChatPage 状态行显示「当前模型提供商：xxx（mock 为回显；真实调用请在 backend/.env 配置 Key）」；构建必须过 strict。

## 4. 测试（全离线）

1. factory：mock / deepseek 空 Key 文案含 `DEEPSEEK_API_KEY` / dashscope 空 Key / 未知 provider 列出合法值。
2. OpenAICompatProvider：monkeypatch `ChatOpenAI`——参数透传、content 返回、异常→ProviderError。
3. services：send 默认走 settings（monkeypatch 模块内 `build_provider`/provider 注入）；`model_ref` 传递；echo 回归不变。
4. API：health 含 llm_provider；send 200（trace 七层全序 + model）/400（ProviderConfigError）/502（注入失败 provider）；既有 15 项（含终审新增隔离测试）保持全绿。
5. 真实联通冒烟：用户在 `backend/.env` 配真 Key + `LLM_PROVIDER` 后，跑一次 `POST /api/chat/send` 验证真实回复；日志与提交不得含 Key。

## 5. 错误语义

- 400 = 用户可自助修复的配置问题（detail 直接给出要设置的 .env 变量）。
- 502 = 上游问题（网络/鉴权失败/配额），detail 含 provider/model 名与原始错误摘要，不含 Key。
- 校验：ChatOpenAI 收到的 api_key 永不写入日志/异常文本（包装异常时截断原始消息，避免回显 Key）。
