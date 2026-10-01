# AiTester 知识库（ReMe 进程内嵌）专项设计

日期：2026-10-01
状态：已与用户逐条确认裁定，待实施

## 目标

以 `lib/reme_ai-0.4.1.8-py3-none-any.whl` 为底座，在 backend（FastAPI，Python 3.11+）内嵌 ReMe，获得知识库五项能力：路径确定、访问、索引、检索、回流更新；参考 qwenpaw 的接入方式（`D:\code\github\qwenpaw\src\qwenpaw\agents\memory\reme_light_memory_manager.py` / `reme_config.py`）。

## 已定裁定（用户确认，2026-10-01）

1. **进程内嵌，禁 HTTP 服务形态**。`ReMe(**config_dict)` → `await start()` → `await run_job(name, **kwargs)`，配置为纯 dict（不加载 reme yaml）。ReMe 的 `reme start` / `service` 后端一律不启用（qwenpaw 中 `service: {backend: "http"}` 仅为占位，从未启动服务）。
2. **embedding 按 Key 启用**。模型设置中存在向量模型 API Key 时注入 `as_embedding` + `embedding_store` 组件（语义+BM25 双路 RRF）；无 Key 时降级纯 BM25（`file_store.default.embedding_store = ""`）。换 embedding 模型后需触发 `reindex` 重建向量索引。
3. **manager 按 (project_id, agent_id) 粒度实例化**。每实例独占一个 workspace：`backend/data/workspaces/<project_id>/<agent_id>/`。禁止两个 ReMe 实例同开一个 workspace_dir（BM25 整库 pickle 后写覆盖、faiss/chunks 无 workspace 级锁，源码证据见 `bm25_index.py:362-365`、`application.py:54-68`）。
4. **KB 全局单份共享**。`knowledge_base_id` 为应用级配置（默认 `zhb_kb`，实体 `~/.reme/knowledge_bases/zhb_kb`，可被 `REME_KNOWLEDGE_BASES_DIR`/配置项覆盖）。所有 (project, agent) 实例经 junction/symlink 把同一实体挂到各自 `knowledge/`；写并发依赖 ReMe 自带跨进程文件锁（`reme/knowledge/lock.py`，每 KB 一把 write.lock），不自造锁。读侧各实例索引经 watch 循环最终一致（秒级收敛）。

## 架构

```
frontend /kb 页（占位升级）
   │  fetch
   ▼
interaction/router.py  /api/kb/* 端点（异步）
   │
   ▼
services/kb/manager.py  RemeKbManager（应用级单例，FastAPI lifespan 启停）
   ├─ 专属事件循环线程（所有 ReMe 协程在此运行，调用方经 run_coroutine_threadsafe 桥接）
   ├─ pool: dict[(project_id, agent_id)] -> Application（lazy start）
   └─ config: services/kb/config.py  build_reme_config() 纯 dict
   │
   ▼
reme（whl，进程内）  run_job: status / list_bases / knowledge_search /
save_to_knowledge / reindex / inbox 四件套
   │
   ├─ workspace: backend/data/workspaces/<project>/<agent>/（个人记忆位，本期不用）
   └─ knowledge/ ──junction──▶ ~/.reme/knowledge_bases/zhb_kb（全局共享实体）

adapters/tools  KbSearchTool / KbSaveTool → 注册进 ToolRegistry，
经 TOOL_CATALOG 供智能体（如 case_design）勾选
```

### 模块划分

- `services/kb/config.py`：`build_reme_config(workspace_dir, kb_settings) -> dict`。jobs 只声明 `{"backend": "base", "steps": [{"backend": "<step>"}]}`（模式经 reme 单测 `test_knowledge_base.py:261-307` 实证可行）；embedding 注入/降级仿 qwenpaw `_apply_embedding_config`（`reme_config.py:850-885`）。
- `services/kb/manager.py`：`RemeKbManager`。单例事件循环线程；`run_job_sync/async`（工具用 sync、端点用 async）；`close_all()` 挂 lifespan；KB 未启用/启动失败 → `KbUnavailableError`（映射 503），不静默假成功。
- `interaction/`：`/api/kb/status|bases|search|save|inbox/*` 端点 + pydantic schemas，DTO 风格对齐现有 `schemas.py`。
- `adapters/tools/kb_tools.py` + `capability_config.TOOL_CATALOG` 两条目（知识库工具组）；`build_default_registry(..., kb=..., agent_id=...)` 扩展可选参数，`AgentRuntime.build` 透传（agent_runtime.py:43、61-71）。
- `frontend/pages/KbPage.tsx`：应用级页面（KB 是全局资产，入口不放项目级）。三区：状态卡（kb_id、实体路径、embedding 开/关）、检索（query+limit+bucket→结果列表带路径/片段）、写入（title/content/bucket→save，写后可在检索区验证）。带标签控件，无裸图标按钮。

### API 面（本期）

| 端点 | job | 说明 |
|---|---|---|
| GET /api/kb/status | status | 启动/索引概览 |
| GET /api/kb/bases | list_bases | 枚举 KB 与实体路径 |
| POST /api/kb/search | knowledge_search | {query, limit=5, bucket?} |
| POST /api/kb/save | save_to_knowledge | {title, content, bucket}（持 ReMe 写锁） |
| GET/POST /api/kb/inbox/... | list/promote/merge/reject | 回流审核通道（后端先通，UI 后置） |

端点响应统一 `{success, answer, metadata}` 透传 ReMe Response（schemas.py 定义 `KbResponse`）。控制台检索使用固定会话键 `("default", "console")` 的实例。

### 配置（Settings 扩展，config.py）

```
kb_enabled: bool = True
kb_id: str = "zhb_kb"
kb_bases_dir: str = ""          # 空→REME_KNOWLEDGE_BASES_DIR 或 ~/.reme/knowledge_bases
kb_create_missing: bool = True   # create_knowledge_base，新机器自动建骨架
kb_embedding_api_key: str = ""   # 空则回落 dashscope_api_key；再无→纯 BM25
kb_embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
kb_embedding_model: str = "text-embedding-v4"
kb_embedding_dimensions: int = 1024
```

### 依赖

`backend/pyproject.toml`：`reme-ai` 经 `[tool.uv.sources]` 指向本地 whl（`../lib/reme_ai-0.4.1.8-py3-none-any.whl`）。**不装 `reme-ai[core]`**（agentscope/faiss/neo4j 等重依赖本期不需要：file_store 用 local，向量走 openai 兼容 API）。BM25 分词器默认后端为 `regex`（default.yaml:884-886），无需 jieba/rjieba。

## 测试策略

- 纯函数：`build_reme_config` 的 embedding 注入/降级、jobs 键齐全（无 reme 依赖即可跑）。
- 真 Application 回路（tmp_path 隔离，仿 reme 单测）：save→reindex→search 命中、list_bases 路径解析、无 reme 环境时 manager 降级 KbUnavailableError。同步测试经 `run_job_sync`，不引入 pytest-asyncio（现状全同步，239 例基线）。
- API 层：TestClient + monkeypatch fake manager（不打真 ReMe），断言路由/DTO/503 映射；`_isolated_client` 模式复用（test_api.py:29）。
- 工具层：spy provider 同 test_api.py 先例；KbTool 调 manager 桩断言 job 名与参数。

## 成本门禁

embedding 首配后的一次检索会实际调用向量 API——属真实成本：默认不自动开跑，需用户确认后用配置的 Key 实测一次（对齐既有"真实成本验证"惯例）。knowledge_dream/auto_memory 等 LLM 参与 job 本期不接。

## 本期不做（后续专项）

- `knowledge_dream` 回流自动化、inbox 审核 UI、cron 任务；
- 个人记忆域（auto_memory/search 双路合并——裁定 3 的 workspace 布局已为此预留）；
- 项目级多 KB 切换 UI（机制已内置：换 kb_id 即可）；
- KB 浏览树/图谱视图。

## 风险

| 风险 | 缓解 |
|---|---|
| Windows junction 创建失败（目标在别盘/权限） | mount 走 reme 自带逻辑；失败显式 KbUnavailableError，不静默 |
| 各实例索引收敛延迟（读侧秒级不一致） | 写入后 UI 提示"索引刷新中"；控制台实例检索前可手动 reindex |
| whl 与源码仓漂移 | 以 whl METADATA 0.4.1.8 为准；计划中的 job/step 名已从 whl 解包内容核实 |
| rjieba 在 win/py3.11 安装 | Task 1 首步即 `uv sync` 实测，失败则 tokenizer 配置切 `backend: jieba` 纯 Python 实现 |
