# 项目管理页（项目 CRUD + 只读知识库配置）设计

日期：2026-10-02　状态：已实施（commit 范围 `3500a6b..HEAD`，即 `git log` 中 projects 提交系列）
参考原型：`prototype/index.html` 项目页 `:675-693`、项目弹窗 `:788-823`、数据形状 `:1064-1068`、`projBadges :1729`、`renderProjects :1736`、`openProjModal :1768-1783`、`ABS_PATH :1809`、`btnProjSave :1847-1878`、`delProject :1880-1891`

## 背景

`/projects` 现在只有占位组件（`frontend/src/pages/ProjectsPage.tsx`），后端**没有**任何 project 实体、落盘或端点——本期从零落项目 CRUD。原型里知识库是**全局单份**概念（`prototype/serve.js:8` 把 `KB_ROOT` 写死成 `zhb_kb`，对所有项目同一份），因此「每个项目一条知识库配置」是相对原型的净新增。

## 用户裁定（2026-10-02，逐条确认）

1. **范围 = 仅项目 CRUD**：本期不动聊天链路——`SendRequest` 不加 `project_id`、工具 `cwd` 仍恒 `"."`、会话键仍是 `agent_id:session_id`。聊天页按项目隔离留给后续专项。
2. **知识库为默认不可改配置，且 UI 脱敏**：**界面**任何位置都不得出现 `zhb` 字样或 reme 实体根路径，只显示别名 `kb`；`kb → zhb_kb` 的映射只活在后端。脱敏口径止于 UI——**API 响应层不做脱敏**（2026-10-02 用户裁定）：`/api/kb/*` 的 `root`/`abs_display` 继续回真实路径，那是前端渲染与调试需要的既有契约，不算遗漏。`/api/projects` 侧的 `kb` 字段本就是别名，不含真实标识属设计结果，非额外收敛动作。
3. **dir 服务端只校验绝对路径**：不扫描磁盘、不校验存在性、不自动建目录；`浏览…` 保持原型语义（浏览器 Folder Picker 只能拿到文件夹名，用于拼接）。

## 控制端裁定（用户未逐一指定、按既有裁定与原型口径补齐）

- **`id` 形态**：`proj_` + 8 位小写十六进制（`uuid4().hex[:8]`），校验 `/^proj_[0-9a-f]{8}$/`。原因：`project_id` 未来要做 workspaces 目录名与会话键组成，中文名不能当键；原型 `w1/w2` 自增序列在多进程/删除后易撞，弃用。
- **种子 = 空列表**：不造假项目、不造假目录（对齐「默认目录/种子配置保持精简」的用户偏好）。既有 `backend/data/workspaces/default/` 属于 reme 实例池 scratch，由 `DEFAULT_PROJECT="default"` 常量继续寻址，与项目注册表无关。
- **`会话数` 本期恒 0**：会话键无项目维度，显别的数字就是假数据。列保留（对齐原型表头），值写 0。
- **不做「当前项目」**：原型的 `当前` 标记与「设为当前」按钮依赖聊天页 `ctx.ws`，本期聊天页无项目概念，故表格不出这两样（后续聊天专项再加）。
- **`agents` 白名单**：只允许非平台智能体的注册目录项（本期即 `case_design`）；`kb_assistant` 等平台智能体既不出现在选项里，写入它也 400。沿用「能力面隐身」裁定。
- **默认勾选**：新建时预选目录中第一个可见智能体（对齐原型 `buildAgentOpts(p ? p.agents : [AGENTS[0].id])`）。
- **删除保护**：项目列表只剩 1 条时删除 → 400（原型 `WSS.length <= 1` 口径）。本期无会话级联可删，确认文案不带会话数分支。

## 数据模型与持久化

`backend/data/projects.json`（gitignore、`JsonConfigRepository` 原子写，与 `model_config.json`/`capability_config.json` 同构）：

```json
{ "projects": [ { "id": "proj_1a2b3c4d", "name": "订单系统", "desc": "…", "dir": "D:/work/projects/order-system", "agents": ["case_design"], "kb": "kb" } ] }
```

- `name`：非空、trim、≤30 字、列表内唯一（编辑时排除自身）
- `desc`：可空、trim、≤200 字
- `dir`：非空、trim、剥尾部 `/` 或 `\`、必须匹配原型同款 `ABS_PATH = /^([A-Za-z]:[\\/]|\/|\\\\|~[\\/])/`
- `agents`：≥1，逐条在白名单内
- `kb`：恒为已注册别名，默认 `"kb"`

## 后端 API 契约

新建 `backend/src/aitester/interaction/projects.py`（router，prefix `/api/projects`），服务层新建 `backend/src/aitester/services/project_config.py`（`ProjectService`），schemas 加 `ProjectInfo` / `ProjectCreateRequest` / `ProjectUpdateRequest`。

| 端点 | 语义 | 失败 |
| --- | --- | --- |
| `GET /api/projects` | 全量列表（含 `session_count: 0`） | — |
| `POST /api/projects` | 校验后落盘，201 + `ProjectInfo` | 400 校验失败（中文 detail） |
| `PUT /api/projects/{id}` | 只改 `name/desc/agents`；携带 `dir` 或 `kb` 且与存量不同 → 400 | 404 未知 id、400 校验/不可改字段 |
| `DELETE /api/projects/{id}` | 删除，204 | 404 未知 id、400 唯一项目不可删 |

- 校验失败一律 400 + 中文可照做 detail（沿用 `/capabilities` 的 `CapabilityConfigError → 400` 映射风格）。
- 响应体**不含** reme kb_id、KB 根路径、任何 `zhb` 子串——`ProjectInfo.kb` 就是别名 `"kb"`。
- `ProjectUpdateRequest` 的 `dir`/`kb` 用 `str | None = None`（不传即不改），传了且与存量不等 → 400「本地文件目录/知识库配置创建后不可修改」。

## 知识库别名层

新建 `backend/src/aitester/services/kb/aliases.py`：

- `PROJECT_KB_DEFAULT = "kb"`
- `registered_aliases() -> list[str]`（本期只有 `"kb"`）
- `resolve_kb_id(alias: str, settings: Any) -> str` → `settings.kb_id`（即 `zhb_kb`）；未知别名抛 `UnknownKbAlias`（`settings` 由调用方传入，避免 `config` 与 `services` 循环导入）
- `resolve_kb_root_for(alias: str, settings: Any) -> Path` → `resolve_kb_bases_dir(settings) / resolve_kb_id(alias, settings)`

本期**检索/写盘链路不改**（KB 全局单份裁定不变）：`/api/kb/browse/*` 与 reme 实例池继续用 `settings.kb_id`。别名层只承担三件事：项目 `kb` 字段的写时校验、`kb → zhb_kb` 的集中解析口径、后续多 KB 专项的接线点。禁止在**任何 UI 文案**里回显解析结果（实体根路径由展示层用别名 `kb` 拼；API 响应层按裁定②不收敛）。

## 前端

- `frontend/src/api/client.ts`：`Project` 接口 + `getProjects/postProject/putProject/deleteProject`（与既有 `getKbStatus/kbSave` 等命名口径一致），沿用现有 `ApiError`（非 2xx 抛、detail 透出）。
- `ProjectsPage.tsx` 替换占位：
  - 页头「项目管理」+「共 N 个」+「＋ 新建项目」
  - 表头：项目名称 / 描述 / 本地文件目录 / 启用智能体 / 知识库 / 会话数 / 操作（知识库列插在启用智能体后会话数前，值 `kb`）
  - 行操作：编辑、删除（`mini-btn danger`）；空列表出引导态
  - 弹窗四字段照原型（名称* / 描述 / 本地文件目录* + 浏览… / 启用智能体* 多选）+ **末行只读「知识库：`kb`」**，hint 中文「知识库配置默认且不可修改」；编辑态 `dir`、`kb` 均 `disabled`，`浏览…` 按钮编辑态隐藏（同原型 `:1776`）
  - 校验分两层：弹窗本地校验只做形态四连——请填写项目名称 → 请填写本地文件目录 → 目录必须是绝对路径 → 请至少选择一个智能体；同名判定刻意不放客户端（交服务端裁定，避免两处判据漂移）；服务端 400 的 detail 覆盖到同一 tip 位
  - 服务端 `ProjectService.create` 判据顺序：名称/描述（空值与字数上限）→ 智能体（为空 → 平台内置不可启用 → 未知）→ 知识库别名 → 目录形态（`_validate_dir`，仅 create 调用，dir 冻结后编辑期不重复校验）→ 同名唯一（`_ensure_name_free`）；`update` 先查项目（未知 id → 404），再判冻结字段：编辑态 `dir`/`kb` 冻结——传与存量相同值放行、传不同值 400「本地文件目录/知识库配置创建后不可修改」，随后 `_validate`（用存量 `kb`）与同名判定（排除自身）
  - 删除走 `window.confirm`：`删除项目「x」？删除后不可恢复。`（与 `/kb` 页同款原生确认；注意原生 confirm 会挂 CDP 自动化，人工点击无碍）
  - 成功 toast：`已创建项目「x」` / `已保存项目「x」` / `已删除项目「x」`
- 样式复用既有 modal/table/field/hint/row-acts 类；不引入新框架。折叠/显隐控件遵循同一套模式且必带文字标签。

## 文案与注释口径（沿用既有裁定）

模型侧英文、UI 与 API detail 中文、注释/docstring 中文；文档禁「立即重建」类表述。本期无模型可见文案改动（不新增工具）。

## 非本期（明确划走）

聊天页当前项目/当前智能体级联、会话按项目隔离与真实会话数、`project.dir` 作为工具 `cwd`、`SendRequest.project_id`、多 KB 切换与 KB 浏览树、项目级 KB 绑定改变检索范围、目录存在性校验与后端列目录端点、项目导入导出。

## 验收

1. `uv run pytest -q` 全绿（新增：service 校验/唯一名/不可改字段/删除保护/落盘重载 单测 + 端点 TestClient 用例；含「响应与代码文案中不含 `zhb`」的脱敏断言）；`npm run build` 0 错。
2. 真机走查（需用户当面同意）：新建/编辑/删除全链、dir 与 kb 编辑态不可改、同名拒绝、非绝对路径拒绝、唯一项目不可删、刷新后持久、知识库列恒显 `kb` 且界面无 `zhb`。走查新建项目用后即清。
