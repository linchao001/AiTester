# 智能体实例专项设计——按 agent_id 装配可运行实例（后端）

日期：2026-09-30　状态：设计已用户确认（A 可选图构建器 / 提示词 md 文件化 / 仅后端装配）

## 1. 范围

把「智能体」从配置条目变成可运行实例——`POST /api/chat/send` 按 `agent_id` 装配并执行：

- 系统提示词注入（来自 `agents/prompts/<id>.md`）
- 智能体默认模型生效（`default_uid` 可用则用、否则读侧回落全局默认）
- 携带工具注入（`tool_ids` → 工具注册表）

扩展性设计（长期多智能体，本期只有 `case_design` 一个）：**数据缝 + 行为缝**两层——

- **数据缝（默认路径）**：新增智能体 = 加一条 `AgentSpec` + 一份提示词 md，零机制代码；提示词 / 默认模型 / 默认携带工具全在数据里。
- **行为缝（逃生门）**：循环范式不同的智能体在 `GRAPH_BUILDERS` 注册自己的 LangGraph 拓扑，不提供则走默认 `react`（现有工具循环）。

智能体 ID 可读化：`a1` → `case_design`（后续同规则：`test_execution`、`automation_coding`）；本机已有配置由归一化一次性迁移。

不做：前端任何改动（含最小对话入口）、流式/SSE、项目目录接入（工具 cwd 恒 `.`）、权限确认、提示词编辑、智能体增删、`chat/echo` 链路改动。

## 2. 分层与新增包

```
backend/src/aitester/
├─ agents/                          # 新增：智能体领域数据包（零外部依赖；任何人可 import 它，它不 import 任何业务层）
│  ├─ __init__.py                   # 导出 AgentSpec / AGENT_CATALOG / DEFAULT_AGENT_STATE / LEGACY_AGENT_IDS / find_agent
│  ├─ spec.py                       # AgentSpec（frozen dataclass）
│  ├─ catalog.py                    # 注册表 + 提示词加载 + 旧 id 迁移表
│  └─ prompts/case_design.md        # 系统提示词（自 capability_config.py 迁出，文案逐字不变）
├─ orchestration/graph_registry.py  # 新增：GRAPH_BUILDERS = {"react": build_agent_graph} + get_graph_builder()
├─ services/agent_runtime.py        # 新增：AgentInstance + AgentRuntime（装配器，组合根）
└─ services/{capability_config,model_config,chat}.py、interaction/{router,schemas}.py   # 改动
```

依赖规则沿用既有（interaction 只 import services；services 为组合根），新增一条：**`agents` 包零依赖**（只 import 标准库）。图构建器注册表住 orchestration（构建器需要 adapters 类型）。注册表文件名最终定为 `graph_registry.py`，避免与既有 `agent_graph.py` 撞名。

## 3. 数据模型

### AgentSpec（`agents/spec.py`）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | str | 稳定标识，小写 snake_case；进 API 与落盘配置 |
| icon / name / desc | str | 对齐原型展示 |
| prompt | str | 由 `prompts/<id>.md` 载入；导入期读取，文件缺失抛 `FileNotFoundError`（含路径），不静默 |
| default_tool_ids | tuple[str, ...] | 出厂携带工具（仅作种子，用户改动落能力配置） |
| graph_builder | str = `"react"` | 行为缝：注册表键名 |

### 注册表（`agents/catalog.py`）

- `AGENT_CATALOG: tuple[AgentSpec, ...]`：本期仅 `case_design`（📋 用例设计智能体；`desc` 与提示词文案逐字自现有种子迁出）。
- `DEFAULT_AGENT_STATE`：由 catalog 派生 `{id: {"default_uid": "", "tool_ids": [...]}}`，携带种子与现行一致（read / write / edit / grep_search / glob_search / web_search 共 6 件，不含 pwsh）。
- `LEGACY_AGENT_IDS = {"a1": "case_design"}`：**一次性迁移表**（旧键 → 新键），不是别名机制。归一化时新键缺失而旧键存在 → 把旧键状态搬到新键（保住用户已改的默认模型与携带工具），落盘后旧键消失；迁移完成、无存量后此表可删。

### 落盘不变

`capability_config.json` 形状不变（`{version, tool_state, agents}`）、原子写不变、归一自愈不变；唯一新增 = 上述 id 迁移。

## 4. 装配与执行

### AgentInstance / AgentRuntime（`services/agent_runtime.py`）

```python
GraphBuilder = Callable[[LlmProvider, list[AiTooler]], CompiledStateGraph]

@dataclass(frozen=True)
class AgentInstance:
    agent_id: str
    system_prompt: str
    provider: LlmProvider
    tools: list[AiTooler]
    build_graph: GraphBuilder
```

`AgentRuntime(capability, model_config, observations)` 的 `build(agent_id, session_id, provider_override=None) -> AgentInstance`：

1. `find_agent(agent_id)`；未知 → `ConfigNotFoundError("未知智能体「x」")`（HTTP 404）。
2. **模型解析**：`capability.effective_uid(agent_id)`（新增公开方法：智能体默认「可用」（`is_usable_uid`：模型已启用且提供商已配 Key）则用之，否则回落全局默认，**读侧回落不写回**；`get_view` 复用同一计算）；`provider = provider_override or model_config.build_provider(effective_uid)`（注入 provider 时短路、不解析模型）——空 uid → 既有 400 文案「尚未配置默认模型…」。
3. **工具装配**：`tool_ids` 非空才建 `build_default_registry(cwd=".", session_id=…, observed=…)` 并 `get_many(tool_ids)`；`cwd` 恒 `"."`（项目目录是留给项目专项的缝）；观察记录跨请求共享（沿用 `app.state.file_observations` 同一实例）。
4. **图构建器**：`get_graph_builder(spec.graph_builder)`。

实例**每请求现装现弃**（对齐 QwenPaw：agent 对象每请求新建、用完即关），不常驻、不缓存。

### 行为缝（`orchestration/graph_registry.py`）

- `GRAPH_BUILDERS: dict[str, GraphBuilder] = {"react": build_agent_graph}`；`get_graph_builder(name)` 未注册 → `ValueError`（开发者错误，由 catalog 测试在 CI 拦截，不做运行时兜底）。
- `build_agent_graph(provider, tools)` 增补：**无工具时退化为单节点图**（不 `bind_tools`、不建 `ToolNode`），使 send 永远走实例图、语义统一。
- 新增 `run_graph(build, provider, tools, messages) -> {reply, tool_traces}`；现 `run_agent(provider, tools, messages)` 保留为 `run_graph(build_agent_graph, …)` 的特例（既有测试不动）。
- 未来新范式：注册新键 + 相应模块；共享节点（agent 节点 / 工具节点 / 将来的人工确认节点等）逐步沉淀，**本期不预建**。

### 一次 send 的数据流

```
POST /api/chat/send {session_id, message, agent_id}
→ interaction：仅协议转换（不再自己拼工具）
→ services.ChatService.send → runtime.build(agent_id, session_id, provider_override=self.provider)
→ context.build(instance.system_prompt, history, message)
→ run_graph(instance.build_graph, provider, tools, messages)
→ memory / storage 落迹（键 = f"{agent_id}:{session_id}"）
→ {reply, trace, model = provider.model_ref}
```

- `ChatService.send` 要求已装配 `agent_runtime`（未装配 → `ProviderConfigError("服务未装配智能体运行时，请通过 create_app 启动后端")`，对齐现有「服务未装配模型配置」风格）；`provider` 注入仍优先（测试缝）。`echo` 一字不动（mock + 占位 prompt + 7 层 trace）。
- 会话键 `agent_id:session_id`（memory.recall/save 与 repo.put 同步 scoped）：对齐域模型「会话绑定 智能体×项目」，多智能体时不串扰；API 形状不变。项目维度待项目专项：届时键扩展为含 `project_id`（如 `project_id:agent_id:session_id`），会话列表按 (当前项目, 当前智能体) 过滤。
- trace 保持 `services / context / orchestration / adapters / (tool:x)* / memory / storage`，响应仍是 `["interaction"] + trace`（既有断言保持）。

## 5. API 与错误语义

| 项 | 现状 | 本期 |
|---|---|---|
| `POST /api/chat/send` 请求体 | `{session_id, message, agent_id="a1"}` | 同形状，`agent_id` 默认值改 `"case_design"` |
| 未知 `agent_id` | 静默忽略（当无工具跑） | **404**「未知智能体「x」」（收紧） |
| 未配模型 / Key 失效 | 400（可照做文案） | 不变（装配期同源错误） |
| 上游失败 | 502 | 不变 |
| `model` 响应字段 | 全局默认 provider 的 `model_ref` | 同源，但为**该智能体有效模型** |
| `GET /api/capabilities` | `prompt` 来自代码常量 | 同形状，`prompt` 来源改 md 文件（内容逐字不变） |
| `GET /api/health`、`POST /api/chat/echo` | — | 一字不动 |

## 6. 测试与验收

1. **agents**：catalog 仅 `case_design`；`prompts/case_design.md` 存在且非空、prompt 与之逐字一致；每个 spec 的 `graph_builder` 能在注册表解析（防漏注册）。
2. **model_config**：`build_provider(uid)` 全分支（可用 → provider 字段正确；空 uid → 「尚未配置默认模型…」；未配 Key / 已停用 → 现有文案）；`build_default_provider()` 收敛为其特例，既有断言不变。
3. **capability**：`agent_state()` 与 `effective_uid()` 公开读入口；**a1 → case_design 一次性迁移**（旧键状态保留、旧键消失）；既有能力配置测试按新 id 更名迁移，行为断言不变。
4. **runtime**：未知智能体 404；智能体默认模型生效；不可用 → 回落全局；全局也没有 → 400；只注入「已启用且已携带」工具；无携带工具时不建注册表；每请求独立实例、观察记录共享。
5. **api**：send 端到端（mock provider，断言系统提示词来自 md、`model` 字段、trace 层名不变）；未知 agent → 404；既有 400/502 断言保持。
6. **回归**：`cd backend && uv run pytest -q` 全绿（基线 177 项）；`cd frontend && npm run build` 绿（前端零改动）；设置弹窗人工看一眼（提示词照常显示）。
7. **真机冒烟**（前提：本机 `model_config.json` 两个提供商 Key 当前均为空、`default_uid` 为空，需先在「⚙ 设置 · 模型设置」配好）：起后端后 `POST /api/chat/send`（`agent_id=case_design`）验证三件事——① 回复体现用例设计身份（如生成登录模块用例表）；② `model` 字段 = 配置的有效模型；③ 让其读一个真实文件，证明工具注入与循环可用。

## 7. 扩展新智能体的操作清单（可扩展性验收）

1. `agents/prompts/<id>.md` 放提示词；
2. `catalog.py` 加一条 `AgentSpec`（默认 `react`）；
3. 仅当循环范式不同：在 `orchestration/graph_registry.py` 注册新图构建器键。

其余装配、配置、API、前端零改动；设置弹窗自动列出新智能体（由既有 `/api/capabilities` 驱动）。

## 8. 决策记录

- 行为缝 = **A 可选图构建器**（用户选定，2026-09-30）；B 统一图+钩子（假灵活）、C 每智能体子类（配置面与代码面割裂）落选；QwenPaw 为参照（单运行时 + 数据化定义，反例是过重的 builder 与每请求全量重建的复杂度）。
- 提示词 = **每智能体一份 md**（用户选定）：注册表引用文件、导入期加载；wheel 分发包需声明带上 md（hatchling 一条配置）。
- 交付 = **仅后端装配**（用户选定），前端零改动。
- 智能体 ID = `case_design`（用户要求可读化，替换 `a1`）；此后新智能体沿用同规则。
- 多智能体「随时切换」的语义 = **A 会话级切换**（用户选定，2026-09-30）：会话列表按当前智能体过滤，切智能体即切到它的会话范围；会话强绑定单个（智能体×项目），本期会话键 `agent_id:session_id` 即其落地。**跨智能体自动接力**（显式调用/子智能体，QwenPaw 式）为后续独立特性，行为缝是其承载点，本期不做。
- 项目维度同理 = **切项目即切会话范围**（用户确认，2026-09-30，原型语义）：同一智能体可在多个项目间切换，会话随之切换；会话绑定 (项目 × 智能体)。本期后端项目模块未实现（§1 不做项目目录接入），会话键只落智能体维度；项目专项落地时键扩展为含 `project_id`、会话列表按 (当前项目, 当前智能体) 过滤——与原型 `s.ws===ctx.ws && s.agent===ctx.agent` 一致。
