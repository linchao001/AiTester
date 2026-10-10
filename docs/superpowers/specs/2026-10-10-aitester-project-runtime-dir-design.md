# 项目运行时目录收口到 `.AiTester`

日期：2026-10-10  
状态：用户确认裁定（cwd=项目根 + 路径改写；不自动迁移）  
覆盖：

- `2026-10-10-reme-workspace-project-dir-design.md` 裁定 1A 中「不另造隐藏子树 / 接受 Reme 目录落在项目根」
- `2026-10-09-session-history-project-dir-design.md` 中 `{项目.dir}/session_history/` 落点
- case_design 产物 `{项目.dir}/design/`

保留不变：KB 实体全局单份（`~/.reme/knowledge_bases/<kb_id>`）、池键按解析后 workspace 路径、平台 fallback `data/workspaces/_platform`、智能体文件工具 cwd=项目绑定目录。

## 已定裁定

1. **平台产物唯一根**：`{project.dir}/.AiTester/`  
   含 Reme workspace（`knowledge/` junction、`daily/`、`digest/`、`metadata/`、`session/`、`mem_session/`、`resource/`）、`session_history/`、`design/`。

2. **cwd 仍为项目根**  
   检索 / 个人记忆返回的 Reme 相对路径（如 `knowledge/...`）在工具与注入文本中改写为 `.AiTester/knowledge/...`，以便 `read`/`grep` 对得上。

3. **不自动迁移**  
   项目根下历史散落的 `session_history/`、`knowledge/`、`daily/`、`design/` 等不挪不删；新写入只走 `.AiTester/`。

4. **删项目配置**  
   仍不删除磁盘上的 `.AiTester/`（含 session_history）；只 `drop_workspace(.AiTester)` 摘 Reme 实例。

## 目标布局

```
{project.dir}/                 ← 用户项目空间（cwd）
  .AiTester/                   ← AiTester 运行时根（点前缀，browse 默认隐藏）
    knowledge/ ──junction──▶ ~/.reme/knowledge_bases/<kb_id>
    daily/ digest/ metadata/ session/ mem_session/ resource/
    session_history/<agent_id>/
    design/                    ← case_design 产物
  …用户自己的源码与文档…
```

## 接线

- [`project_runtime.py`](../../../backend/src/aitester/project_runtime.py)：常量与路径辅助、`reme_paths_for_project_cwd`
- [`manager.py`](../../../backend/src/aitester/memory/reme/manager.py)：`workspace_dir` → `project_runtime_root`
- [`session_store.py`](../../../backend/src/aitester/services/session_store.py) / [`session_locator.py`](../../../backend/src/aitester/services/session_locator.py)
- [`case_design/env.py`](../../../backend/src/aitester/case_design/env.py)：`design` → `.AiTester/design`
- [`kb_tools.py`](../../../backend/src/aitester/adapters/tools/kb_tools.py)、[`personal.py`](../../../backend/src/aitester/memory/reme/personal.py)：对外路径改写

## 明确不做

- 不改 cwd 到 `.AiTester`
- 不在项目根再挂 `knowledge` junction
- 不做旧目录自动迁移 / 清理
- 不把 KB 实体搬进项目目录
