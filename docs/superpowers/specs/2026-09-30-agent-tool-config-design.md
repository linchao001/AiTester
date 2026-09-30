# 能力配置专项设计——设置 · 智能体配置 / 工具（只做配置入口）

日期：2026-09-30　状态：设计已用户确认（方案 A：独立 capability JSON 落盘 + CapabilityConfigService，单向依赖 ModelConfigService）

## 1. 范围

- 正式前端「⚙ 设置」弹窗由单节变为三节 tab：**🧠 模型设置 / 🤖 智能体配置 / 🛠 工具**（对齐原型 `#paneAgent` `#paneTool`）。模型设置一节行为完全不变。
- 本期智能体**只注册「用例设计智能体」一个**（原型有 3 个，测试执行/自动化编码随各自后续专项再注册，本期不出现、不置灰）。
- 运行期能力配置 CRUD：智能体默认模型（跟随全局默认 / 指定模型）、智能体携带工具、工具启用停用（含级联摘除）。改完立即落盘生效，无需重启。
- 持久化：`backend/data/capability_config.json`（原子写、随 `backend/data/` 已在 gitignore）。
- **系统提示词只读展示**（「由平台统一维护」），不可编辑、不落盘。
- 不做：智能体增删、提示词编辑、工具增删与自定义工具、项目↔智能体绑定（后端项目模块尚未实现）、以及任何让本配置真正影响编排/prompt 拼装/工具调用的实现逻辑——这些属后续专项。

## 2. 数据模型与种子

`capability_config.json` 只存**用户可改的状态**，注册表本身是代码种子：

```json
{
  "version": 1,
  "tool_state": { "read": true, "write": true, "edit": true, "pwsh": true, "bash": false, "web_search": true },
  "agents": {
    "a1": { "default_uid": "", "tool_ids": ["read", "write", "edit", "web_search"] }
  }
}
```

- 文件不存在时首次启动按上述种子创建。种子值取自原型已验证配置；`bash` 默认禁用（本项目跑在 Windows）。
- **工具注册表** `TOOL_CATALOG`（顺序即展示顺序，按 group 分组）：
  | id | group | icon | label | 平台 | 说明要点 |
  |---|---|---|---|---|---|
  | read | 文件处理工具 | 📖 | read | 全平台 | 读文本文件带行号，改动前确认现状 |
  | write | 文件处理工具 | ✍️ | write | 全平台 | 整文件写入/新建 |
  | edit | 文件处理工具 | ✏️ | edit | 全平台 | 精确字符串替换，目标不唯一或未命中即失败 |
  | pwsh | 命令执行工具 | 🖥 | pwsh | Windows | 执行 PowerShell，回收 stdout/stderr/退出码 |
  | bash | 命令执行工具 | 🐚 | bash | macOS | 同 pwsh，Windows 上默认禁用 |
  | web_search | 网页搜索工具 | 🔎 | web_search | 全平台 | 查官方文档/错误码/版本变更 |
- **智能体注册表** `AGENT_CATALOG`：`a1 用例设计智能体`（icon 📋、desc、prompt 全部照抄原型 `AGENTS[0]`，中文多行 Markdown，只读）。
- 模型引用一律 uid 串 `providerId/modelId`，与模型配置一致；`default_uid=""` 语义为「跟随全局默认」。
- 写入=同目录临时文件 + `os.replace` 原子替换（与模型配置同规则）。

## 3. 后端分层与 API

- **storage**：现有 `FileModelConfigRepository` 行为本就与「模型」无关，本期把 `storage/model_config_repo.py` 泛化改名为 `storage/json_config_repo.py`，导出 `JsonConfigRepository`（协议）/ `FileJsonConfigRepository`（实现）/ `ConfigStorageError`；同步改 `storage/__init__.py`、`main.py`、`test_model_config_repo.py` 的引用。不保留旧别名（内部代码，无兼容需求）。
- **services**：新增 `CapabilityConfigService(repo, model_config)`——注入 `ModelConfigService` 只做**读侧校验**（uid 是否「已启用 + 提供商已配 Key」、全局默认是什么），不反向写模型配置，两服务之间无写级联：
  - `get_view()` → `{tools:[…], agents:[…]}`：
    - tool = `{id, group, icon, label, os, desc, enabled, carried_by:[agentId]}`
    - agent = `{id, icon, name, desc, prompt, default_uid, effective_uid, tool_ids}`；`effective_uid` = 自带 `default_uid` 可用则用之，否则回落 `model_config.default_uid`（模型被停用/清 Key 后配置不自愈，靠这里兜住并在前端标注）；全局默认也为空时 `effective_uid=""`。
  - `set_agent_default_model(agent_id, uid)`：`uid=""` 直接落；非空必须存在且可用，否则 `CapabilityConfigError`（中文，指引到「设置 · 模型设置」）。
  - `set_agent_tools(agent_id, tool_ids)`：未知 tool id → `ConfigNotFoundError`；含**已禁用**工具 → `CapabilityConfigError`（指引到「工具」）。保持传入顺序写入，去重。
  - `set_tool_enabled(tool_id, enabled)`：禁用时在同一次 `save` 里把该工具从所有智能体 `tool_ids` 摘除（级联原子）；重新启用**不自动补回**，只把状态置 true。
  - 异常类型：`CapabilityConfigError(RuntimeError)` 新增于 `services/capability_config.py`，带 `.detail`（面向用户中文），形态与 `adapters.llm.ProviderConfigError` 一致；未知 id 复用 `services/model_config.ConfigNotFoundError`（同为「未知配置项」语义，不重复定义）。
- **interaction**：新端点（仅本机，全部返回完整 `{tools, agents}` 视图，前端一次刷新两个 pane）：
  - `GET /api/capabilities`
  - `PUT /api/capabilities/agents/{aid}/default-model`　body `{uid: str}`
  - `PUT /api/capabilities/agents/{aid}/tools`　body `{tool_ids: [str]}`
  - `PUT /api/capabilities/tools/{tid}/enabled`　body `{enabled: bool}`
  - 错误语义沿用既有：`CapabilityConfigError` → 400（detail 可照做）、`ConfigNotFoundError` → 404、无上游调用故无 502。
- **装配**：`create_app(model_config_path=None, capability_config_path=None, settings=None)` 里建 `app.state.capability_config`，与 `model_config`/`chat_service` 同模式，测试可注入独立临时路径。
- **不动**：`/api/health`、`/api/models`、`/api/chat/*` 行为与响应体不变；本配置本期不被 `ChatService`/编排层读取。

## 4. 前端（设置弹窗三节）

- `SettingsModal.tsx` 拆为外壳 + 三个 pane 组件：`components/settings/SettingsModal.tsx`（tab 条 + 状态）、`ModelPane.tsx`（现有模型 UI 原样迁移）、`AgentPane.tsx`、`ToolPane.tsx`。外壳持 `tab: 'model' | 'agent' | 'tool'`，默认 `'model'`；切 tab 不卸载模型数据。
- 标题改为「⚙ 设置」；`.modal` 固定高度 620px（切 tab 不跳高），各 pane 内部 `overflow-y: auto`。默认 LLM 下拉仍只在模型 pane 出现。
- **智能体 pane**：左=智能体列表（本期 1 项：`📋 用例设计智能体` + 副行「{effective_uid} · 携带 n 个工具」，`default_uid` 为空时打「跟随默认」小标签）；右=职责（只读文本）/ 默认模型 select（首项「跟随全局默认（{…}）」，其余全部模型列出，未启用或未配 Key 置灰标注（不可用））/ 携带工具 checkbox（禁用工具置灰不可勾，title 显示说明）/ 系统提示词「查看系统提示词 ↔ 收起系统提示词」按钮 + 只读 `<pre>` + 「n 行 · m 字」统计。
- **工具 pane**：分组表格（表头 工具｜说明｜平台｜状态｜操作；组名单独一行 `🧩 文件处理工具` 等），说明下副行「被 用例设计 携带 / 未被任何智能体携带」，状态 `● 已启用 / ○ 已禁用`，操作「禁用 / 启用」；表头副标题标注「已启用 x/6」；禁用前 `window.confirm` 列出将被摘掉该工具的智能体名，确认后提示「已禁用 …，相关智能体不再具备该能力」；启用后提示「可在 智能体配置 里勾选携带」。
- 保存返回的 `{tools, agents}` 同时刷新两个 pane，避免级联结果不同步（操作必有可见结果）；已启用计数只在工具 pane 表头副标题显示，不放进 tab 标签（与原型一致）。
- 样式延用原型体系（暖米底 + 橙主色）写入 `App.css`：`.s-tabs`、`.s-tab(.active)`、`.set-pane-list/.pane-item(.active)`、`.check-opts`、`.prompt-view`、`.tool-state(.on/.off)`、`.tgrp`；控件一律带文字标签，不做纯图标按钮。
- `client.ts` 新增 `ToolInfo / AgentInfo / CapabilityResponse` 类型与四端点封装（detail 透出到弹窗错误行）。

## 5. 测试与验收（TDD，全离线）

1. storage：`FileJsonConfigRepository` 往返/原子无残留/损坏 JSON 含路径（现有 repo 测试随改名迁移，覆盖率不减）。
2. CapabilityConfigService：种子三态（文件缺失→落盘种子；已有文件→原样读回，重启不重置）；`set_agent_default_model` 空串/合法/未启用/未配 Key/未知 uid 五路；`set_agent_tools` 含禁用工具报错、顺序保持、去重、未知 tool id 404；`set_tool_enabled(false)` 从所有智能体摘除且一次落盘、`(true)` 不补回；`effective_uid` 在模型被停用/清 Key 后回落全局默认、全局默认为空时为 `""`。
3. API：`GET /api/capabilities` 结构断言（含 prompt 全文、carried_by）；四端点成功路径 + 400/404 的 detail 中文可读；`/api/models`、`/api/health`、`/api/chat/*` 既有断言全部保持；`cd backend && uv run pytest` 全绿（现 45 项基础上增）。
4. 前端：`npm run build`（tsc strict + vite）通过。
5. 人工验收：`scripts/dev.ps1` → http://localhost:5173 打开「⚙ 设置」走查三节（切 tab 不跳高、用例设计智能体唯一、改默认模型/勾选工具后重开弹窗仍在、禁用 pwsh 级联摘除并提示、启用后不补回、提示词只读展开）；确认 `backend/data/capability_config.json` 落盘内容正确。
6. 安全不变量：`capability_config.json` 不含任何密钥；API Key 仍只在本机 `model_config.json`/`.env`，任何响应不含明文 Key。
