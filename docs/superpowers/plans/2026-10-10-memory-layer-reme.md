# Memory Layer (ReMe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify Reme-backed personal memory and knowledge base under `aitester.memory`, wire turn hooks for `search` + `auto_memory`, keep SessionStore chat history isolated.

**Architecture:** Evolve `RemeKbManager` into `RemeMemoryManager` (same pool/lifecycle). Add `PersonalMemory` / `KnowledgeMemory` facades. ChatService injects search hits pre-turn and schedules `auto_memory` post-persist. Session `FileMemoryStore` stays the chat log and the *source data* for auto_memory inputs only.

**Tech Stack:** Python 3.11+ / FastAPI / Reme 0.4.1.13 / agentscope 2.0.9（仅 agent_wrapper 环）/ LangChain + AiTester `LlmProvider` 注入 `as_llm` / LangGraph 主对话不变 / pytest.

**Spec:** [docs/superpowers/specs/2026-10-10-memory-layer-reme-design.md](../specs/2026-10-10-memory-layer-reme-design.md)

## Global Constraints

- Reme 0.4.1.13 local wheel; no Reme HTTP service.
- **禁止**为 `auto_memory` 另造 AgentScope `OpenAIChatModel`；注入 AiTester 已配置的聊天对象（`LlmProvider` / 其内部 `ChatOpenAI`），由 Reme `LangChainChatModel` 适配。
- Never read/write real `~/.reme/knowledge_bases` in tests; use `tmp_path` + `REME_KNOWLEDGE_BASES_DIR`.
- SessionStore / `FileMemoryStore` behavior and key shape `agent_id:session_id` unchanged.
- Personal-memory hook failures must not fail the chat turn.
- Do not implement dream/cron/inbox UI/reranker in this plan.

## File map

| Path | Role |
|------|------|
| `backend/src/aitester/memory/session/{base,file_memory,in_memory,__init__}.py` | Existing session MemoryStore (moved) |
| `backend/src/aitester/memory/reme/manager.py` | `RemeMemoryManager` (from kb/manager) |
| `backend/src/aitester/memory/reme/config.py` | `build_reme_config` + personal jobs + as_llm |
| `backend/src/aitester/memory/reme/{paths,aliases,steps}.py` | Moved from `services/kb/` |
| `backend/src/aitester/memory/reme/personal.py` | `PersonalMemory` |
| `backend/src/aitester/memory/reme/knowledge.py` | `KnowledgeMemory` |
| `backend/src/aitester/memory/reme/llm_bridge.py` | ModelConfig → AiTester `LlmProvider`（供 Reme as_llm 注入） |
| `backend/src/aitester/memory/reme/messages.py` | History → Reme Msg dicts; session_id hash |
| `backend/src/aitester/services/kb/__init__.py` | Re-export shims for one cycle |
| `backend/src/aitester/services/chat.py` | Turn hooks |
| `backend/src/aitester/config.py` | New personal_memory_* settings |
| `backend/src/aitester/main.py` | Construct `RemeMemoryManager` |

---

### Task 1: Package skeleton + session move + re-exports

**Files:**
- Create: `backend/src/aitester/memory/session/{__init__,base,file_memory,in_memory}.py`
- Modify: `backend/src/aitester/memory/__init__.py`
- Keep thin shims at old `memory/file_memory.py` etc. OR update all imports in one step (prefer move + update `__init__` exports; grep-fix imports).
- Test: existing `backend/tests/test_session_store.py` and chat memory tests still pass.

**Interfaces:**
- Produces: `from aitester.memory import FileMemoryStore, InMemoryMemoryStore, MemoryStore` still works; optionally `aitester.memory.session.*`.

- [ ] **Step 1: Move session modules under `memory/session/`**

Move `base.py` / `file_memory.py` / `in_memory.py` into `memory/session/`. Update `memory/session/__init__.py` and top-level `memory/__init__.py` to re-export the same three names. Update any direct imports (`aitester.memory.file_memory` → `aitester.memory.session.file_memory` or only via package root).

- [ ] **Step 2: Run session/chat memory tests**

Run: `cd backend && uv run pytest tests/test_session_store.py tests/test_chat_service.py -q --tb=line`  
Expected: PASS (or only unrelated failures already known).

- [ ] **Step 3: Commit**

```bash
git add backend/src/aitester/memory backend/tests
git commit -m "$(cat <<'EOF'
refactor(memory): move session MemoryStore under memory.session

EOF
)"
```

---

### Task 2: Expand Reme config (as_llm + search + auto_memory)

**Files:**
- Modify: `backend/src/aitester/services/kb/config.py` (still here until Task 3 moves it)
- Modify: `backend/src/aitester/config.py` (settings flags)
- Test: `backend/tests/test_kb_config.py` (create if missing; else extend existing config tests)

**Interfaces:**
- Produces: `build_reme_config` returns jobs `search`, `auto_memory` and component `as_llm`（**backend: langchain**）+ `agent_wrapper` stubs（占位 credential；运行时注入 AiTester 聊天对象）。
- Consumes: existing `_KB_JOBS` / embedding injection.

- [ ] **Step 1: Write failing config tests**

```python
def test_build_reme_config_includes_personal_memory_jobs():
    from aitester.services.kb.config import KbConfig, build_reme_config
    cfg = build_reme_config(KbConfig(workspace_dir="/tmp/ws"))
    assert "search" in cfg["jobs"]
    assert cfg["jobs"]["search"]["steps"][0]["backend"] == "search_step"
    assert "auto_memory" in cfg["jobs"]
    assert cfg["jobs"]["auto_memory"]["steps"][0]["backend"] == "auto_memory_step"
    assert "as_llm" in cfg["components"]
    assert "agent_wrapper" in cfg["components"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_kb_config.py::test_build_reme_config_includes_personal_memory_jobs -v`  
Expected: FAIL (missing jobs) or file missing.

- [ ] **Step 3: Implement jobs + components**

In `config.py`, merge into `_KB_JOBS` (or a sibling `_MEMORY_JOBS` then union):

```python
"search": {
    "backend": "base",
    "steps": [{
        "backend": "search_step",
        "vector_weight": 0.7,
        "candidate_multiplier": 3.0,
        "expand_links": True,
        "max_links_per_direction": 10,
    }],
},
"auto_memory": {
    "backend": "base",
    "steps": [{"backend": "auto_memory_step"}],
},
```

Add to `components` (always, not only when embedding key present), aligned with QwenPaw `_base_components` stubs:

```python
"as_llm": {
    "default": {
        "backend": "langchain",
        "model": "aitester-injected",
        "stream": True,
        "context_size": 200000,
        "max_retries": 3,
        "credential": {"api_key": "", "base_url": ""},
        "parameters": {"max_tokens": 8192},
    },
},
"agent_wrapper": {
    "default": {
        "backend": "agentscope",
        "as_llm": "default",
        "builtin_tools": False,
        "permission_mode": "bypass",
        "react_config": {"max_iters": 30},
        "context_config": {
            "trigger_ratio": 0.8,
            "reserve_ratio": 0.1,
            "tool_result_limit": 50000,
        },
        "model_config": {"max_retries": 1},
    },
},
```

Also ensure `file_catalog` / digest paths needed by Reme personal memory exist in components if Application validation requires them (probe against Reme 0.4.1.13; add minimal stubs from QwenPaw `_base_components` if construct fails).

Settings in `config.py` (aitester Settings):

```python
personal_memory_enabled: bool = True
auto_memory_search_enabled: bool = True
auto_memory_enabled: bool = True
auto_memory_search_limit: int = 5
```

- [ ] **Step 4: Run config tests**

Run: `cd backend && uv run pytest tests/test_kb_config.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/kb/config.py backend/src/aitester/config.py backend/tests/test_kb_config.py
git commit -m "$(cat <<'EOF'
feat(memory): add Reme search/auto_memory jobs and as_llm stubs

EOF
)"
```

---

### Task 3: RemeMemoryManager + move `services/kb` → `memory/reme`

**Files:**
- Create: `backend/src/aitester/memory/reme/{__init__,manager,config,paths,aliases,steps}.py`
- Modify: `backend/src/aitester/services/kb/__init__.py` (re-export)
- Modify: `backend/src/aitester/main.py`, `agent_runtime.py`, imports across codebase
- Rename class: `RemeKbManager` → `RemeMemoryManager` with alias
- Test: `backend/tests/test_kb_*.py`, `test_api.py` (noop manager still works)

**Interfaces:**
- Produces: `RemeMemoryManager.start/close_all/run_job/run_job_sync/is_enabled/kb_root_dir` same signatures as today; plus optional `inject_llm(project_id, agent_id, model)` calling `app.update_component`.
- Consumes: `build_reme_config` from new path.

- [ ] **Step 1: Move modules and add alias**

```python
# memory/reme/manager.py
class RemeMemoryManager:
    ...  # body from RemeKbManager

RemeKbManager = RemeMemoryManager  # compat
```

`services/kb/__init__.py`:

```python
from aitester.memory.reme.manager import RemeKbManager, RemeMemoryManager, KbUnavailableError
from aitester.memory.reme.config import KbConfig, build_reme_config
# ... paths/aliases as needed
```

Leave empty stub modules or delete old files after import greps are clean.

- [ ] **Step 2: Update `main.py` to construct `RemeMemoryManager`**

Keep `app.state.kb_manager` attribute name for one cycle (or rename to `memory_manager` and update router helpers in same commit if grep is small). Prefer rename to `memory_manager` with router `_kb(request)` reading `request.app.state.memory_manager` — update all `kb_manager` state refs in one commit.

- [ ] **Step 3: Full KB/regression tests**

Run: `cd backend && uv run pytest tests/test_kb_config.py tests/test_api.py tests/test_case_design_graph.py -q --tb=line`  
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/aitester/memory/reme backend/src/aitester/services/kb backend/src/aitester/main.py
git commit -m "$(cat <<'EOF'
refactor(memory): move Reme manager under memory.reme as RemeMemoryManager

EOF
)"
```

---

### Task 4: llm_bridge + PersonalMemory + KnowledgeMemory facades

**Files:**
- Create: `backend/src/aitester/memory/reme/llm_bridge.py`
- Create: `backend/src/aitester/memory/reme/messages.py`
- Create: `backend/src/aitester/memory/reme/personal.py`
- Create: `backend/src/aitester/memory/reme/knowledge.py`
- Test: `backend/tests/test_personal_memory.py`, `backend/tests/test_reme_messages.py`

**Interfaces:**
- Produces:
  - `to_reme_session_id(session_id: str) -> str` → `aitsid_sha256_<hex>`
  - `to_reme_messages(rows: list[dict]) -> list[dict]` with Reme Msg fields (`name`, `role`, `content`, `created_at`, `id`)
  - `resolve_chat_provider(model_config_service, agent_id) -> LlmProvider`（与主对话同源配置对象）
  - `PersonalMemory(manager, settings, model_config_service)` with:
    - `search(project_id, agent_id, query, limit) -> str`
    - `auto_memory(project_id, agent_id, session_id, messages: list[dict]) -> None`
    - `auto_search_for_turn(...) -> str` (respects settings flags; returns "" if disabled/fail)
  - `KnowledgeMemory(manager)` wrapping existing job names used by API/tools/`KbClient`

- [ ] **Step 1: Message/hash unit tests**

```python
def test_to_reme_session_id_stable():
    from aitester.memory.reme.messages import to_reme_session_id
    a = to_reme_session_id("sess_abc")
    assert a == to_reme_session_id("sess_abc")
    assert a.startswith("aitsid_sha256_")
    assert a != to_reme_session_id("sess_other")
```

- [ ] **Step 2: Implement messages + llm_bridge**

`llm_bridge.py`：从 `ModelConfigService` 解析出与主对话相同的 `LlmProvider`（或等价 `ChatOpenAI`），**不**构造 AgentScope `OpenAIChatModel`。

```python
def resolve_chat_provider(model_config: Any, agent_id: str):
    """Return AiTester LlmProvider for agent_id (same path chat uses)."""
    return model_config.provider_for_agent(agent_id)  # 以 ModelConfigService 实装方法名为准
```

注入前可用 Reme 自带鸭型校验（可选断言）：

```python
from reme.components.as_llm import is_langchain_compatible
assert is_langchain_compatible(provider)
```

- [ ] **Step 3: PersonalMemory with fake manager**

```python
class FakeMgr:
    def __init__(self):
        self.calls = []
        self.injected = []
    def run_job_sync(self, name, **kw):
        self.calls.append((name, kw))
        return type("R", (), {"success": True, "answer": "hit", "metadata": {}})()
    def inject_llm(self, project_id, agent_id, model):
        self.injected.append((project_id, agent_id, model))

def test_auto_search_respects_flag(monkeypatch):
    ...
```

`PersonalMemory.auto_memory` must:
1. `resolve_chat_provider(model_config, agent_id)` → AiTester 聊天对象。
2. `manager.inject_llm(project_id, agent_id, provider)` → `app.update_component("as_llm", "default", model=provider)`（Reme 内自动 `coerce_to_chat_model`）。
3. `run_job_sync("auto_memory", messages=..., session_id=to_reme_session_id(sid), ...)`。

`search` calls `run_job_sync("search", query=..., limit=...)`.

- [ ] **Step 4: KnowledgeMemory thin wrap**

Methods: `status`, `search`, `save`, inbox ops, and pass-through used by `KbClient` (`case_nodes_*`). Prefer updating `case_design/kb.py` to accept either manager or `KnowledgeMemory` with `run_job_sync` — keep `KbClient` calling `run_job_sync` on the object it holds (manager still works).

- [ ] **Step 5: Run facade tests**

Run: `cd backend && uv run pytest tests/test_personal_memory.py tests/test_reme_messages.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/aitester/memory/reme backend/tests/test_personal_memory.py backend/tests/test_reme_messages.py
git commit -m "$(cat <<'EOF'
feat(memory): add PersonalMemory and KnowledgeMemory facades

EOF
)"
```

---

### Task 5: ChatService turn hooks

**Files:**
- Modify: `backend/src/aitester/services/chat.py`
- Modify: `backend/src/aitester/main.py` (pass PersonalMemory into ChatService)
- Test: `backend/tests/test_chat_personal_memory_hooks.py`

**Interfaces:**
- Consumes: `PersonalMemory.auto_search_for_turn`, `PersonalMemory.schedule_auto_memory` (or thread pool wrapper).
- Produces: prepare path may append one system message; `_persist` success schedules extract.

Injection marker (fixed string for stripping if ever persisted by mistake):

```text
[aitester_personal_memory]
...search answer...
```

- [ ] **Step 1: Failing hook tests with fake PersonalMemory**

```python
def test_prepare_injects_search_hit():
    # ChatService.prepare / _prepare with spy personal
    # assert messages contain marker when search returns text

def test_persist_schedules_auto_memory():
    # after successful fold/persist, spy.auto_memory called with session_id

def test_search_failure_does_not_break_turn():
    # personal.search raises → stream still yields done
```

- [ ] **Step 2: Wire ChatService**

In `_prepare` after `messages = _to_langchain_messages(...)`:

```python
if self.personal is not None and not platform:
    hit = self.personal.auto_search_for_turn(
        project_id=pid, agent_id=agent_id, query=message,
    )
    if hit:
        messages.insert(1, SystemMessage(content=f"[aitester_personal_memory]\n{hit}"))
        # index 1: after system prompt; adjust if _to_langchain_messages layout differs
```

In `_persist` after successful saves:

```python
if self.personal is not None:
    rows = [
        {"role": "user", "content": prepared.message},
        {"role": "assistant", "content": reply},
    ]
    self.personal.schedule_auto_memory(
        project_id=prepared.project_id,
        agent_id=prepared.agent_id,
        session_id=prepared.session_id,
        messages=rows,
    )
```

`schedule_auto_memory` = `threading.Thread(target=..., daemon=True).start()` catching all exceptions.

- [ ] **Step 3: Construct PersonalMemory in `create_app`**

```python
personal = PersonalMemory(kb, s, model_config)
ChatService(..., personal=personal)
```

- [ ] **Step 4: Run hook + chat tests**

Run: `cd backend && uv run pytest tests/test_chat_personal_memory_hooks.py tests/test_chat_service.py tests/test_api.py -q --tb=line`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/chat.py backend/src/aitester/main.py backend/tests/test_chat_personal_memory_hooks.py
git commit -m "$(cat <<'EOF'
feat(memory): hook auto_memory search/extract into chat turns

EOF
)"
```

---

### Task 6: Agent tool surface + regression closeout

**Files:**
- Modify: `backend/src/aitester/adapters/tools/kb_tools.py` (optional: add `memory_search` tool id for personal `search`, or document that auto-inject covers layer2 without a new tool)
- Decision locked: **no new agent tool in this plan** — layer2 auto-inject + auto_memory is sufficient; KB tools unchanged via KnowledgeMemory/manager.
- Modify: docs only if README knowledge section needs one line on personal memory.
- Test: full backend pytest.

- [ ] **Step 1: Full test suite**

Run: `cd backend && uv run pytest -q --tb=line`  
Expected: PASS.

- [ ] **Step 2: Import sanity**

Run: `cd backend && uv run python -c "from aitester.memory.reme import RemeMemoryManager, PersonalMemory; from aitester.services.kb import RemeKbManager; assert RemeKbManager is RemeMemoryManager"`  
Expected: exit 0.

- [ ] **Step 3: Commit closeout**

```bash
git add -A
git commit -m "$(cat <<'EOF'
test(memory): close out memory-layer Reme regression suite

EOF
)"
```

---

## Spec coverage check

| Spec item | Task |
|-----------|------|
| SessionStore isolated; shared session_id | T1, T4 messages, T5 |
| Reme lifecycle unify | T3 |
| search + auto_memory jobs | T2 |
| as_llm inject（AiTester LlmProvider / LangChain，禁 AgentScope 模型对象） | T2 backend=langchain, T4 llm_bridge + PersonalMemory |
| Turn hooks | T5 |
| KB API/tools/case_nodes compat | T3 re-export, T4 KnowledgeMemory |
| No dream/cron | omitted |
| Hook failure soft | T5 tests |

## Placeholder scan

None intentional. Task 4 须用 `ModelConfigService` 上真实方法名解析 provider（grep `provider_for` / `build_provider` 后写入，禁止臆造 API）。
