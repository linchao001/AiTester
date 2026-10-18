# AiTester 记忆层（ReMe）专项设计

日期：2026-10-10  
状态：用户确认裁定（1A + 层2），待实施

## 目标

把「智能体个人记忆」与「知识库（项目记忆）」收拢为同一 **记忆层**，统一由进程内嵌 ReMe 支撑。AiTester 只做基于 ReMe API 的生命周期 / 个人记忆 / 知识库封装，并在聊天与工具链路的合适位置调用。

参考：`D:\code\github\QwenPaw\src\qwenpaw\agents\memory\reme_light_memory_manager.py`（形态与 job 面），不照搬 AgentScope 中间件 / cron / inbox UI。

## 已定裁定

1. **会话聊天记录 ≠ 个人记忆，但前者是后者的数据源（靠近 A）**  
   - 会话记录继续由 `FileMemoryStore` → `SessionStore` 落盘（UI + LLM 短上下文）。  
   - Reme 个人记忆（`auto_memory` / `search`）写入 workspace 下日笔记等，**不读写 SessionStore**。  
   - 两者共享同一 `session_id`（传给 Reme 前做稳定 hash，对齐 QwenPaw `_to_reme_session_id`），存储隔离、互不覆盖。

2. **本轮做到层2**  
   - 记忆层包结构 + Reme 生命周期统一。  
   - 接通 `search` + `auto_memory`。  
   - 回合钩子：回合前自动检索注入；回合成功落盘后异步抽取。  
   - 既有 KB 能力（检索/写入/inbox/case_nodes）迁入记忆层知识面子面，行为与 API 兼容。

3. **实例粒度保持 `(project_id, agent_id)`**  
   - 每实例独占 `backend/data/workspaces/<project>/<agent>/`（个人记忆落此处）。  
   - 全局共享 KB 仍经 junction 挂到 `knowledge/`（既有裁定不变）。

4. **LLM 参与 job 注入 AiTester 已配置聊天对象（Reme 0.4.1.13+）**  
   - `auto_memory` 走 Reme `as_llm`（config backend=`langchain`）。  
   - **禁止**另造 AgentScope `OpenAIChatModel`；从 `ModelConfigService` 取出与主对话相同的 `LlmProvider`（或内部 `ChatOpenAI`），经 `Application.update_component("as_llm", "default", model=...)` 注入；Reme `LangChainChatModel` / `is_langchain_compatible` 负责适配。  
   - `agent_wrapper` 仍为 agentscope（内层工具环），仅模型对象复用 AiTester。

5. **失败策略**  
   - 个人记忆钩子失败：记日志，不阻断聊天回合、不改 SessionStore。  
   - KB 不可用：既有 `KbUnavailableError` → 503，不变。

## 明确不做（本专项）

- `auto_dream` / `knowledge_dream` / `daily_paper` / cron  
- inbox 审核 UI、reranker  
- 会话历史迁入 Reme dialog  
- 把 `MemoryStore` Protocol 改成 Reme 后端（会话层名字可注释澄清，类型面不动）

## 目标架构

```
ChatService / tools / /api/kb/*
        │
        ▼
memory/                          ← 记忆层
  session/   FileMemoryStore …   ← 会话聊天记录（非 Reme）
  reme/
    manager.py   RemeMemoryManager   生命周期 + 实例池 + run_job
    config.py    build_reme_config   KB jobs + search/auto_memory + as_llm 占位
    personal.py  PersonalMemory      search / auto_memory / session_id hash
    knowledge.py KnowledgeMemory     既有 KB job 薄封装（含 case_nodes）
    llm_bridge.py                    ModelConfig → agentscope OpenAIChatModel
        │
        ▼
reme.Application（进程内）
  workspace: …/workspaces/<project>/<agent>/     ← 个人记忆
  knowledge/ ──junction──▶ ~/.reme/.../zhb_kb   ← 项目/共享知识库
```

### 回合钩子

```
prepare → recall SessionStore
       → PersonalMemory.auto_search(query←user message)  # 失败忽略
       → 命中则把摘要注入 messages（SystemMessage 或等价一条，前缀固定便于剥离）
       → stream_graph …
       → _persist SessionStore 成功
       → PersonalMemory.note_user_turn(...)   # 累计用户回合；达 interval 才入队
       → FIFO 单 worker → auto_memory(messages, session_id)  # 失败忽略
```

`auto_memory` **不是按墙钟定时**，而是按用户回合累计（对齐 QwenPaw `auto_memory_interval`，默认 5）。删会话时 `flush_session` 把未处理 pending 立刻入队（对齐 `/new` 摘要语义的一部分）。本期不做上下文压缩提前 flush、`/memorize`、`/compact`。

注入内容**不落** SessionStore（只影响当回合 LLM 上下文）。`auto_memory` 入参消息从累计回合的 user+assistant 快照组装为 Reme 期望的 Msg dict 列表，与 SessionStore 行无关。

### 配置（Settings 扩展）

```
# 既有 kb_* 保留
personal_memory_enabled: bool = True          # 总开关；False 时钩子与 search job 不跑
auto_memory_search_enabled: bool = True       # 回合前检索
auto_memory_enabled: bool = True              # 回合后抽取
auto_memory_search_limit: int = 5
auto_memory_interval: int | None = 5          # 每 N 用户回合 flush；None/<=0 关闭周期
```

无 embedding Key 时 `search` 仍可走 BM25（与既有 KB 降级一致）；`auto_memory` 需要有效聊天模型 Key，否则跳过并打日志。

### 对外兼容

| 旧入口 | 新落点 |
|--------|--------|
| `RemeKbManager` | `RemeMemoryManager`；过渡期 `RemeKbManager = RemeMemoryManager` 别名 |
| `services/kb/*` | 迁入 `memory/reme/`；`aitester.services.kb` 可保留 re-export 一个版本周期 |
| `/api/kb/*`、KbTools、`KbClient` | 改调 `KnowledgeMemory` / manager，DTO 与 job 名不变 |
| `FileMemoryStore` | 路径可迁到 `memory/session/`，行为不变 |

## 测试策略

- 纯函数：`build_reme_config` 含 `search`/`auto_memory`/`as_llm`；session_id hash 稳定。  
- PersonalMemory：fake manager 断言 job 名与 kwargs；开关关闭不调 job。  
- ChatService 钩子：spy PersonalMemory，断言 prepare 后 search、persist 后 auto_memory；钩子抛错回合仍 done。  
- 存量 KB 测试：经别名/ re-export 全绿，禁触真实 `~/.reme/knowledge_bases`。  
- 可选付费探针：隔离 `REME_KNOWLEDGE_BASES_DIR` + 真 Key 跑一轮 search→auto_memory（用户确认后）。

## 风险

| 风险 | 缓解 |
|------|------|
| agentscope 模型与 LangChain 配置漂移 | 只从 ModelConfigService 读字段；不缓存过期 Key |
| auto_memory 成本/延迟 | 异步、不阻塞流式 done；默认可关 |
| 包迁移大面积 import 炸 | 先 manager+别名，再搬文件留 re-export，测试分批绿 |
| Reme Msg 字段与 SessionStore 行不一致 | 组装专用 `to_reme_messages()`，不复用 ChatMessage.to_dict 扩展字段当契约 |
