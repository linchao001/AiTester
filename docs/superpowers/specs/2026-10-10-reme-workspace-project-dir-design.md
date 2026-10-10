# Reme workspace 对齐项目目录

日期：2026-10-10  
状态：用户确认裁定（1A + 2A），已实施；**落点子树已被**  
[`2026-10-10-aitester-project-runtime-dir-design.md`](2026-10-10-aitester-project-runtime-dir-design.md)  
**覆盖**：项目 Reme `workspace_dir` 现为 `{project.dir}/.AiTester`（不再直接落项目根）。  
废止/覆盖：

- `2026-10-01-knowledge-base-reme-design.md` 裁定 3（`(project, agent)` 独占 `backend/data/workspaces/...`）
- `2026-10-10-memory-layer-reme-design.md` 裁定 3 中同口径句（实例粒度与 workspace 落点）

保留不变：KB 实体全局单份（`~/.reme/knowledge_bases/<kb_id>`）、进程内嵌、embedding 按 Key、个人记忆 job 面。

## 背景（为何改）

Reme 知识库路径语义是 **workspace 相对** 的：

- 挂载点：`{workspace_dir}/knowledge` → 共享实体（junction/symlink）
- 索引 / 检索前缀：`knowledge/business/...`（`knowledge_scope_path_prefixes`）

AiTester 此前把 Reme `workspace_dir` 放在 `backend/data/workspaces/<project>/<agent>/`，而智能体文件工具 `cwd` 是项目绑定目录。检索返回的 `knowledge/...` 对项目空间 `read`/`grep` 对不上，项目目录里也看不到挂载。

对照 QwenPaw：`workspace_dir = working_dir`。

## 已定裁定（用户确认）

1. **1A — Reme `workspace_dir` = 项目绑定目录**  
   项目根下会出现 `knowledge/`（junction）以及 Reme 侧目录（`daily/`、`digest/`、`metadata/`、`session/`、`mem_session/`、`resource/` 等）。接受该落盘形态；不另造隐藏子树。

2. **2A — 实例池按项目（实际按解析后的 workspace 路径）单开**  
   同项目多智能体共用一个 Reme `Application`。禁止两实例同开同一 `workspace_dir`（Reme BM25/索引约束不变）。  
   后果：个人记忆（`daily`/`digest`/`auto_memory`）变为 **项目级共享**，不再按 agent 隔离。

3. **平台智能体（无项目）仍用内部 fallback workspace**  
   `/kb`、`kb_assistant`、console 等无项目绑定目录时：`workspace_dir = backend/data/workspaces/_platform`（可沿用现有 `default` 语义，实施时统一命名）。其 `cwd` 与 Reme workspace 仍对齐，保证助手侧相对路径可用。

4. **KB 实体仍全局单份**  
   各项目 `knowledge/` 均 junction 到同一 `kb_id` 实体；写锁仍用 Reme 自带锁。

## 目标架构

```
项目智能体（cwd = project.dir）
        │
        ▼
RemeMemoryManager
  pool key = resolve(project.dir)   ← 同路径共享一实例
  workspace_dir = project.dir
        │
        ├─ knowledge/ ──junction──▶ ~/.reme/knowledge_bases/<kb_id>
        └─ daily|digest|metadata|…  （项目级个人记忆）

/kb · kb_assistant（无项目）
        │
        ▼
  workspace_dir = data/workspaces/_platform
  cwd = 同上
```

### 实例解析

| 调用方 | `project_id` | `workspace_dir` |
|--------|--------------|-----------------|
| 聊天 / case_design / 项目工具 | 真实 `proj_*` | `Path(project["dir"]).resolve()` |
| `/api/kb/*`、`kb_assistant`、缺省 console | `default` / 空 | `data/workspaces/_platform` |

池键用 **解析后的绝对路径字符串**，避免两个项目误配同一 `dir` 时双开同 workspace。  
`run_job(..., agent_id=...)` 保留形参（兼容工具/KbClient），但 **不再参与池键**。

### 必改接线

- [`manager.py`](../../../backend/src/aitester/memory/reme/manager.py)：注入/回调解析项目 `dir`；`workspace_dir(project_id)`；池 `_apps: dict[str, App]`。
- [`kb_tools.py`](../../../backend/src/aitester/adapters/tools/kb_tools.py) + [`build_default_registry`](../../../backend/src/aitester/adapters/tools/__init__.py)：工具携带 `project_id`，`run_job_sync` 传入（现状默认 `default`，项目会话也会打到错 workspace）。
- [`KbClient`](../../../backend/src/aitester/case_design/kb.py) / `CaseDesignEnv`：透传 `project_id`。
- [`agent_runtime._build_platform_agent`](../../../backend/src/aitester/services/agent_runtime.py)：cwd / 预热改为 `_platform` 口径。
- 项目 `dir` 变更或删除：关闭并摘掉对应路径上的实例，避免挂旧目录。

### `/kb` browse

浏览/草案写盘仍可直打 **实体根**（`kb_root_dir`）；与挂载点内容同一实体。索引收敛仍靠各已启动 workspace 的 watch；平台实例与项目实例都会挂同一实体。

## 明确不做

- 不把 KB 实体搬进项目目录（仍全局单份）
- 不恢复 `(project, agent)` 双键实例
- 不隐藏 Reme 侧目录（1A 已接受可见）
- 不做个人记忆按 agent 再切片（2A 已接受项目共享）
- 不强制迁移 / 删除旧 `backend/data/workspaces/<proj>/<agent>/` 历史目录（可文档注明废弃；可选后续清场）

## 测试策略

- `workspace_dir`：有项目 → 等于绑定 dir；无项目 → `_platform`。
- 同 `project_id`、不同 `agent_id` 两次 `_get_app` → 同一实例。
- 两项目同 dir → 同一池键 / 同一实例。
- 项目会话 `knowledge_search` 传入的 `project_id` 命中项目 workspace（非 default）。
- 挂载后 `(project.dir / "knowledge").is_dir()` 且为 junction/symlink；相对路径 `knowledge/...` 在 `cwd=project.dir` 下可读。
- 存量 KB / personal memory 单测按新池键与签名改绿；禁触真实 `~/.reme/knowledge_bases`（测用临时 `REME_KNOWLEDGE_BASES_DIR`）。

## 风险

| 风险 | 缓解 |
|------|------|
| 项目目录被 Reme 子目录「弄脏」 | 1A 已确认；README/项目页可一句说明 |
| 项目 dir 变更后旧实例仍占盘 | update/delete 时 `drop_workspace(old_path)` |
| 两项目同 dir 未校验 | 池键按路径合并；可选后续加 dir 唯一校验 |
| Windows junction 跨盘失败 | 沿用 Reme `KnowledgeMountError` → `KbUnavailableError` |
| 旧 data/workspaces 残留 | 废弃说明；不自动 rmtree（防误伤 junction） |
