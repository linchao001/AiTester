# 配置+存储层专项设计——模型配置入口（设置 · 模型设置）

日期：2026-09-30　状态：四节设计均已用户确认（方案 A：storage 落盘 + services 组合）

## 1. 范围

- 正式前端顶栏最右新增「⚙ 设置」文字按钮，打开设置弹窗，本期仅含**模型设置**一节（智能体配置/工具属后续专项，不占位）。
- 运行期模型配置 CRUD：提供商 API Key 写入/清除、模型启用停用、默认 LLM 选择；改完立即生效，无需重启。
- 持久化：后端单 JSON 文件 `backend/data/model_config.json`（原子写、gitignore）；首次启动从 `.env` 已有 Key 种子导入。
- 发送链路 `POST /api/chat/send` 改为按运行期配置的默认模型真实调用；`/api/health` 的 `llm_provider` 改报运行期 `default_uid`。
- 不做：其他提供商一键添加、自定义提供商、「发现模型」、会话级选模型、SSE 流式、agent×project 绑定。

## 2. 数据模型与种子（backend/data/model_config.json）

```json
{
  "version": 1,
  "default_uid": "deepseek/deepseek-flash",
  "providers": [
    {
      "id": "deepseek",
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com",
      "api_key": "sk-…（本机明文，永不回传前端）",
      "models": [
        { "id": "deepseek-flash", "enabled": true, "max_output": 393216, "context": 1048576 },
        { "id": "deepseek-v4-pro", "enabled": true, "max_output": 393216, "context": 1048576 }
      ]
    },
    {
      "id": "dashscope",
      "name": "通义千问 · DashScope",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "",
      "models": [
        { "id": "qwen3.7-max", "enabled": true, "max_output": 8192, "context": 1000000 },
        { "id": "qwen3.8-max", "enabled": true, "max_output": 8192, "context": 131072 },
        { "id": "qwen3.7-plus", "enabled": true, "max_output": 8192, "context": 1000000 },
        { "id": "qwen3.6-plus", "enabled": true, "max_output": 8192, "context": 1000000 }
      ]
    }
  ]
}
```

- 目录/模型种子完全沿用原型已验证配置；仅两提供商，无 deprecated 别名。
- 模型引用一律 uid 串 `providerId/modelId`（与原型一致）。
- 文件不存在时首次启动自动创建；创建时读取 `.env` 的 `DEEPSEEK_API_KEY`/`DASHSCOPE_API_KEY` 迁入作种子并选定默认（有 Key 的提供商首选 deepseek-flash，否则 dashscope qwen3.7-max；都无 Key 则 default_uid 为空）。
- `backend/data/` 加入 `.gitignore`；写入=同目录临时文件 + `os.replace` 原子替换。

## 3. 后端分层与 API

- **storage**：新增 `model_config_repo.py`——`ModelConfigRepository` 协议（`load() -> dict | None` / `save(config)`）+ `FileModelConfigRepository(path)`；损坏 JSON 抛带文件路径的中文错误。
- **services**：新增 `ModelConfigService`（组合根；持 repo + 种子目录 + Settings 仅用于首次种子）：
  - `get_view()` → `{default_uid, providers:[{id, name, base_url, has_key, key_masked, models[…]}]}`；`key_masked` 只露首尾 4 位（Key 短于 8 位时全掩），明文 Key 永不出接口。
  - `update_api_key(provider_id, api_key)`：`None`/省略=不变，空串=清除。
  - `set_model_enabled(provider_id, model_id, enabled)`。
  - `set_default(uid)`：校验 uid 存在、模型已启用、提供商已配 Key，否则 `ProviderConfigError`（中文指引到「设置 · 模型设置」）。
  - `build_default_provider()` → 按 default_uid 构造 `OpenAICompatProvider`（复用接入层）；无默认/未配 Key 抛 `ProviderConfigError`。
  - **级联**：停用/清除 Key 导致当前默认不可用时，default 自动回落到其他「已启用+已配 Key」模型（顺序：provider 列表序→模型表序）；无可回落则清空 default。
- **config.py 瘦身**：保留 HOST/PORT 与两个 `*_API_KEY`（仅首次种子读取）；删除 `llm_provider` 与 `*_BASE_URL`/`*_MODEL` 字段；接入层 `factory.build_provider`（按 LLM_PROVIDER 选择）随本期废弃删除，`MockProvider`/`OpenAICompatProvider`/错误类型保留（echo 与配置服务继续用）。
- **services/chat.py**：`send()` 的 provider 解析改为注入的 `ModelConfigService.build_default_provider()`（构造可注入 fake provider 覆盖）；响应 `model` 即 uid。`echo()` 不变（恒 mock）。
- **interaction**：health `llm_provider` = 运行期 `default_uid`，未配置时 `"mock"`。新端点（仅本机）：
  - `GET /api/models`
  - `PUT /api/models/providers/{pid}/key`　body `{api_key: str | null}`
  - `PUT /api/models/providers/{pid}/models/{mid}/enabled`　body `{enabled: bool}`
  - `PUT /api/models/default`　body `{uid: str}`
  - 错误语义沿用接入层：配置类 400（detail 可照做）、未知 pid/mid 404、上游失败 502。
- 端点由 `create_app()` 装配：`app.state.model_config_service`，路由从其读取（与 chat_service 同模式，保证实例隔离测试仍成立）。

## 4. 前端（设置弹窗与聊天页联动）

- 顶栏最右「⚙ 设置」文字按钮（非纯图标）；弹窗开关状态提升到 App。
- 弹窗标题「设置 · 模型设置」，布局照原型：
  - 左：提供商列表（名称 + ● Key 已配置 / ○ 未配置），点击切换右侧。
  - 右：Base URL 只读；API Key 输入（已配置时 placeholder `当前 sk-a…9f（已配置，留空则不变）`）+「保存 Key」；「默认 LLM」下拉——全部模型列出，仅已启用+已配 Key 可选，其余灰显标注 已停用/未配 Key；模型表（模型 ID｜最大上下文｜最大输出｜启用 checkbox）。
- 聊天页状态行：`当前默认模型：{default_uid}`；不可用时提示「未配置可用模型，请打开 设置 · 模型设置」且可点击直接开弹窗（App 下发 `onOpenSettings`）。
- health 拉取上移到 App（props 下发 ChatPage）；弹窗任何保存成功后 App 重取 `GET /api/models` + health，状态行即时更新（操作必有可见结果）。
- 样式延用原型体系：暖米色底 + 橙色主色，写入 App.css。
- `client.ts` 新增：`ModelView` 等类型与四端点封装（含 400/404 的 detail 透出）。

## 5. 测试与验收（TDD，全离线）

1. storage repo：空 load=None；save/load 往返；原子写无残留；损坏 JSON 报错含路径。
2. ModelConfigService：种子三态（deepseek Key/dashscope Key/无 Key）；update_api_key 三态；掩码规则（首尾 4 位、短 Key 全掩）；set_default 三重校验；级联回落与清空；build_default_provider 参数透传（monkeypatch `openai_compat.ChatOpenAI`）。
3. API：GET /api/models body 不含埋入的明文 Key；四端点成功路径 + 404/400；send 用配置默认（注入 fake，`model`=uid）；health 报 default_uid / 未配置 "mock"；既有测试中 send/health 语义同步改造，echo 全保持，其余全绿。
4. 前端：`tsc && vite build` strict 通过。
5. 人工验收：`scripts/dev.ps1` → ⚙ 设置填 Key → 选默认 → 聊天页状态行实时更新；用户配真实 Key 后跑一次 `POST /api/chat/send` 真实冒烟（补接入层欠项）。
6. 安全不变量：Key 只存本机 `backend/data/model_config.json`（gitignore）与 `.env`；任何响应/日志/异常文本不含明文 Key。
