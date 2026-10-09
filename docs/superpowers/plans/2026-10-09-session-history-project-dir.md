# 会话历史迁入项目 session_history Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把可见智能体会话的唯一真相迁到 `{项目.dir}/session_history/<agent_id>/`，消息行带上 `agent_id`/`session_id`，工具 `result` 全量落盘但不进入 SSE/读历史 API。

**Architecture:** 保留 `SessionStore`（单根 index+jsonl）语义；新增 `open_session_store(project_dir, agent_id)` 与薄解析器 `SessionLocator`（按 `project_id`→dir + `agent_id` 现开 store、跨 agent 计会话数）。`ChatService`/`FileMemoryStore` 经 locator 落盘；`create_app` 去掉 `sessions_dir`；会话 messages/delete API 与 list 一样强制 `project_id`+`agent_id`。

**Tech Stack:** Python 3.12 / FastAPI / pytest + TestClient；React + TypeScript（前端仅改 `client.ts` + `ChatPage.tsx` 传参）；无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-09-session-history-project-dir-design.md`

## Global Constraints

- 后端测试一律 `tmp_path` + `Settings(_env_file=None)` + MockProvider；**零真实网络/LLM**；禁止写真实 `backend/data/`。
- **禁止**回落读写 `backend/data/sessions`；旧目录不迁、不读。
- SSE `step` / `done.steps` / `GET .../messages` 的 steps **不得**含 `result`；磁盘 jsonl **必须**含全量 `result`（有工具时）。
- `recall` 仍只投影 `role`/`content`；`HISTORY_MAX=40` 不变。
- 平台智能体 / 非 `sess_*` 仍进程内，不写 `session_history`。
- 删 AiTester 项目配置：**不**删除项目 dir 下的 `session_history/`。
- 提交只在本地 master；未获同意不 `git push`。文案：API detail 中文，标识符英文。

---

## 文件结构

| 文件 | 动作 | 责任 |
|---|---|---|
| `backend/src/aitester/services/session_store.py` | Modify | `ChatMessage` +`agent_id`/`session_id`；`append` 盖章；读侧跳过缺键行；`open_session_store`；可选构造期 `agent_id` |
| `backend/src/aitester/services/session_locator.py` | Create | `SessionLocator.for_agent` / `count_by_project`；解析 project→dir |
| `backend/src/aitester/services/__init__.py` | Modify | 导出 locator（若包有显式导出） |
| `backend/src/aitester/memory/file_memory.py` | Modify | 透传已盖章 store；行为随 SessionStore |
| `backend/src/aitester/services/chat.py` | Modify | 用 locator；persist steps 含 `result`；对外剥 `result` |
| `backend/src/aitester/main.py` | Modify | 去掉 `sessions_dir`；装配 `SessionLocator` |
| `backend/src/aitester/interaction/sessions.py` | Modify | messages/delete 必填 Query |
| `backend/src/aitester/interaction/projects.py` | Modify | 用 locator 计数；删除清盘调用 |
| `frontend/src/api/client.ts` | Modify | messages/delete 带 `agent_id`+`project_id` |
| `frontend/src/pages/ChatPage.tsx` | Modify | 调用处传入当前智能体/项目 |
| `README.md` | Modify | 落盘路径与单进程约束文案 |
| `backend/tests/test_session_store.py` | Modify | 新字段 / 工厂路径 / 跳过缺键 |
| `backend/tests/test_session_locator.py` | Create | locator 解析与计数 |
| `backend/tests/test_api_chat_sessions.py` | Modify | 新 Query；落盘路径断言；result 剥离 |
| `backend/tests/test_*.py`（凡 `sessions_dir=`） | Modify | 去掉该参；经 locator/`_seed_project` 取 store |
| `docs/superpowers/specs/2026-10-09-session-history-project-dir-design.md` | Modify | 状态改「已实现」（收尾任务） |

---

### Task 1: SessionStore 消息盖章 + `open_session_store`

**Files:**
- Modify: `backend/src/aitester/services/session_store.py`
- Test: `backend/tests/test_session_store.py`

**Interfaces:**
- Consumes: 既有 `SessionStore(root)`、`append`/`messages`/`create`
- Produces:
  - `ChatMessage` 增加 `agent_id: str`、`session_id: str`（`from_dict` 缺任一则调用方跳过）
  - `SessionStore.__init__(self, root: Path, agent_id: str = "")` — `agent_id` 非空时 `append` 盖章用之；空串时仍要求 append 能写出（单测旧用法：盖章用 create 时传入的 agent，见下）
  - `append(...)` 写出的每行含 `agent_id`（优先 `self._agent_id`，否则与 index 行该 sid 的 `agent_id` 一致）与 `session_id=sid`
  - `messages()`：缺 `agent_id` 或 `session_id` 的行 **skip + warning**，不 500
  - `open_session_store(project_dir: str | Path, agent_id: str) -> SessionStore`  
    根 = `Path(project_dir).expanduser().resolve() / "session_history" / agent_id`，并传入 `agent_id=`

- [ ] **Step 1: 写失败测试**

在 `test_session_store.py` 追加：

```python
def test_open_session_store_puts_files_under_agent_subdir(tmp_path) -> None:
    from aitester.services.session_store import open_session_store

    proj = tmp_path / "myproj"
    proj.mkdir()
    store = open_session_store(proj, "case_design")
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "hi")
    store.append(sid, "user", "hi")
    store.append(
        sid, "assistant", "ok",
        steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}", "result": "FILE_BODY"}],
    )
    root = proj / "session_history" / "case_design"
    assert (root / "index.json").is_file()
    line = json.loads((root / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()[1])
    assert line["agent_id"] == "case_design"
    assert line["session_id"] == sid
    assert line["steps"][0]["result"] == "FILE_BODY"


def test_messages_skips_rows_missing_agent_or_session_id(tmp_path, caplog) -> None:
    store = _store(tmp_path)
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "x")
    path = tmp_path / "sessions" / f"{sid}.jsonl"
    path.write_text(
        json.dumps({"role": "user", "content": "old", "ts": 1, "steps": None, "stopped": False, "context": None})
        + "\n"
        + json.dumps({
            "agent_id": "case_design", "session_id": sid,
            "role": "user", "content": "new", "ts": 2,
            "steps": None, "stopped": False, "context": None,
        })
        + "\n",
        encoding="utf-8",
    )
    msgs = store.messages(sid)
    assert [m.content for m in msgs] == ["new"]
```

同步改既有 `test_append_bumps_count_updates_and_appends_jsonl`：断言 user/assistant 行含 `agent_id`/`session_id`（assistant steps 可无 result，单测可不强制）。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_session_store.py::test_open_session_store_puts_files_under_agent_subdir tests/test_session_store.py::test_messages_skips_rows_missing_agent_or_session_id -v`  
Expected: FAIL — `ImportError: cannot import name 'open_session_store'` 或缺字段

- [ ] **Step 3: 实现**

`session_store.py` 要点：

```python
def open_session_store(project_dir: str | Path, agent_id: str) -> SessionStore:
    root = Path(project_dir).expanduser().resolve() / "session_history" / agent_id
    return SessionStore(root, agent_id=agent_id)
```

`ChatMessage` 增加两字段；`to_dict`/`from_dict` 跟上。`messages()` 在 `from_dict` 前若缺键则 `logger.warning` 并 `continue`。`append` 写行时填入 `agent_id`/`session_id`。`__init__` 存 `self._agent_id`。

加载 index 时：若 `self._agent_id` 非空且行 `agent_id != self._agent_id`，丢行 + warning（spec）。

- [ ] **Step 4: 跑通本文件相关用例**

Run: `cd backend && uv run pytest tests/test_session_store.py -v`  
Expected: PASS（含既有用例；若既有断言 json 行结构，补上新键）

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/session_store.py backend/tests/test_session_store.py
git commit -m "feat(session): open_session_store 与消息行 agent/session/result 盖章"
```

---

### Task 2: SessionLocator

**Files:**
- Create: `backend/src/aitester/services/session_locator.py`
- Test: `backend/tests/test_session_locator.py`

**Interfaces:**
- Consumes: `ProjectService.get`/`_find`（用公开 `get` 或现有抛 `ConfigNotFoundError` 的读法——与 `ChatService._guard_project` 同口径取 `dir`）、`open_session_store`
- Produces:
  ```python
  class SessionLocator:
      def __init__(self, projects: ProjectService) -> None: ...
      def for_agent(self, project_id: str, agent_id: str) -> SessionStore: ...
      def count_by_project(self, project_id: str) -> int: ...
  ```
  - `for_agent`：解析项目 dir（expanduser）；`open_session_store(dir, agent_id)`；项目不存在 → 透传既有 `ConfigNotFoundError` / `ProjectConfigError`（与项目读侧一致，路由已映射）
  - `count_by_project`：若 `session_history` 不存在 → `0`；否则对每个子目录若存在 `index.json`，用 `SessionStore(subdir, agent_id=子目录名).list` 或读 index 会话行数 **相加**（计会话条数，不是 message_count 之和）
  - **不做**跨请求强缓存（每调用现开即可；YAGNI）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_session_locator.py
from pathlib import Path

from aitester.config import Settings
from aitester.services.project_config import ProjectService
from aitester.services.session_locator import SessionLocator
from aitester.services.session_store import open_session_store
from aitester.storage import FileJsonConfigRepository


def _projects(tmp_path: Path) -> tuple[ProjectService, str, Path]:
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "p.json"))
    root = tmp_path / "work"
    root.mkdir()
    pid = svc.create(name="t", desc="", dir_=str(root), agents=["case_design"])["id"]
    return svc, pid, root


def test_for_agent_writes_under_project_session_history(tmp_path) -> None:
    svc, pid, root = _projects(tmp_path)
    loc = SessionLocator(svc)
    store = loc.for_agent(pid, "case_design")
    sid = store.new_id()
    store.create(sid, "case_design", pid, "q")
    assert (root / "session_history" / "case_design" / "index.json").is_file()


def test_count_by_project_sums_sessions_across_agents(tmp_path) -> None:
    svc, pid, root = _projects(tmp_path)
    a = open_session_store(root, "case_design")
    b = open_session_store(root, "other_agent")
    for store, agent in ((a, "case_design"), (b, "other_agent")):
        sid = store.new_id()
        store.create(sid, agent, pid, "x")
    assert SessionLocator(svc).count_by_project(pid) == 2
```

（若 catalog 无 `other_agent`，第二个子目录名可用任意字符串——locator/store 不校验智能体是否在目录注册表。）

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_session_locator.py -v`  
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现 `session_locator.py`**

用 `ProjectService` 现有公开方法取项目（对照 `chat.py::_guard_project` / `project_config` 的 `get`）；`dir` 经 `Path(...).expanduser().resolve()` 再交给工厂。

- [ ] **Step 4: 跑通**

Run: `cd backend && uv run pytest tests/test_session_locator.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/session_locator.py backend/tests/test_session_locator.py
git commit -m "feat(session): SessionLocator 按项目 dir × agent 打开 store"
```

---

### Task 3: ChatService 经 locator 落盘，steps 含 result 仅磁盘

**Files:**
- Modify: `backend/src/aitester/services/chat.py`
- Modify: `backend/src/aitester/memory/file_memory.py`（若需）
- Test: `backend/tests/test_chat_service.py`（追加）+ 必要时 `test_context_wiring.py`

**Interfaces:**
- Consumes: `SessionLocator`；图事件 `step` 现仅 `_STEP_KEYS`；`finish.tool_traces` 含 `result`（见 `agent_graph.py` trace 形）
- Produces:
  - `ChatService(..., sessions: SessionLocator | None = None)`（类型从 `SessionStore` 改为 locator；属性名可仍叫 `sessions`）
  - `prepare`：`store = self.sessions.for_agent(pid, agent_id)`（非平台且 `sess_*`）；`FileMemoryStore(store, pid)`；归属校验用同一 store
  - `_fold_turn`：除按 `_STEP_KEYS` 攒 **对外** steps 外，另攒 `disk_steps`，每项多 `result`（从同轮 `finish["tool_traces"]` 按顺序对齐，或在收 `step` 时若事件无 result 则在 finish 时用 traces 覆盖 disk_steps）
  - **推荐实现**：在 `_fold_turn` 收到 `finish` 且非 pending 时，用 `outcome["tool_traces"]` 映射为 disk_steps：`{tool, ok, round, detail, result}`；对外 `done.steps` 仍用剥掉 `result` 的列表；`_persist(..., steps=disk_steps)`
  - 对外 yield 的 `step` 事件保持现状（图已不带 result）

- [ ] **Step 1: 写失败测试**

在 `test_chat_service.py` 用已有项目夹具模式：构造真 `ProjectService` + `SessionLocator` + `ChatService`（MockProvider / 可注入能产生 tool_traces 的 runtime）。**最小锁**：

```python
def test_persist_writes_tool_result_under_project_session_history(tmp_path) -> None:
    """磁盘 steps 带 result；done.steps 不带。"""
    # 1) 建 ProjectService + 可达 dir + SessionLocator
    # 2) ChatService(sessions=locator, projects=..., agent_runtime=能跑通 send 的 runtime 或手工 _persist)
    # 若全链路重，可直接：
    #   prepared = ...; svc._persist(prepared, "reply",
    #       [{"tool":"read","ok":True,"round":1,"detail":"{}","result":"BODY"}], False)
    #   然后读 open_session_store(dir,"case_design").messages 断言 result
    # 并断言若走 stream_turn，done["steps"][0] 无 result 键
```

若全链路成本高：本任务先测 `_persist` 落盘含 result + 路径在 `session_history/<agent>/`；Task 4 API 测剥 result。

同时把既有 `ChatService(..., sessions=SessionStore(...))` 改为 `SessionLocator(projects)`（本文件内凡构造处）。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_chat_service.py -k persist_writes_tool_result -v`  
Expected: FAIL（路径仍在旧 sessions 或无 result）

- [ ] **Step 3: 实现 chat.py / file_memory**

- `prepare` 用 `self.sessions.for_agent(pid, instance.agent_id)`  
- `_persist` 不变签名，传入的 steps 已含 result  
- `_fold_turn`：persist 用 `tool_traces` 转 disk_steps；`done` 用 `_public_steps(disk_steps)`：

```python
def _public_steps(steps: list[dict]) -> list[dict]:
    return [{k: s[k] for k in _STEP_KEYS} for s in steps]
```

断开/取消 pending 路径同样：落盘用含 result 的 steps，若当时只有对外 steps，则无 result 亦可（停止截断时 traces 可能不完整——有则带、无则缺键；`append` 原样写）。

- [ ] **Step 4: 跑通**

Run: `cd backend && uv run pytest tests/test_chat_service.py tests/test_context_wiring.py -v`  
Expected: PASS（先修本任务引入的破坏）

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/chat.py backend/src/aitester/memory/file_memory.py backend/tests/test_chat_service.py backend/tests/test_context_wiring.py
git commit -m "feat(chat): 会话经 SessionLocator 落盘且 steps.result 只进磁盘"
```

---

### Task 4: create_app 装配 + 会话/项目 API

**Files:**
- Modify: `backend/src/aitester/main.py`
- Modify: `backend/src/aitester/interaction/sessions.py`
- Modify: `backend/src/aitester/interaction/projects.py`
- Test: `backend/tests/test_api_chat_sessions.py`；顺带改该文件 `_app`/`_seed` 去掉 `sessions_dir`

**Interfaces:**
- Consumes: `SessionLocator`
- Produces:
  - `create_app` **删除** `sessions_dir` 参数；`app.state.sessions = SessionLocator(project_config)`；`ChatService(sessions=locator, ...)`
  - `GET/DELETE .../sessions/{id}/messages` 与 delete：`agent_id: str = Query(min_length=1)`、`project_id: str = Query(min_length=1)`；`store = locator.for_agent(project_id, agent_id)`；`get` 失败或 `agent_id` 不匹配 → 404
  - `projects._info`：`session_count=locator.count_by_project(p["id"])`
  - `projects_delete`：**删除** `delete_by_project` 调用整段 try

- [ ] **Step 1: 写/改失败测试**

`test_api_chat_sessions.py`：

1. `_app` 去掉 `sessions_dir`  
2. `_seed` 改为 `loc = application.state.sessions`；`store = loc.for_agent(pid, agent_id)` 再 create/append  
3. 所有 `GET .../messages`、`DELETE ...` 补 query：`agent_id` & `project_id`  
4. 新增：

```python
def test_messages_require_project_and_agent_query(tmp_path) -> None:
    client, sid, pid = _seed(tmp_path)
    assert client.get(f"/api/chat/sessions/{sid}/messages").status_code == 422
    assert client.delete(f"/api/chat/sessions/{sid}").status_code == 422


def test_send_persists_under_project_dir_with_result(tmp_path) -> None:
    # wired MockProvider + 能触发至少一次工具的路径；若困难则用 store.append 模拟 API 读剥 result
    # 最小：手工经 locator 写入含 result 的行，GET messages 返回的 steps[0] 无 result
    ...
```

`test_api_projects.py`：删项目后断言项目 dir 下若预先建了 `session_history/...` 文件仍在。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_api_chat_sessions.py -v`  
Expected: FAIL（create_app 仍要 sessions_dir 或 422 未出现）

- [ ] **Step 3: 实现 main / sessions / projects**

- [ ] **Step 4: 跑通会话与项目相关**

Run: `cd backend && uv run pytest tests/test_api_chat_sessions.py tests/test_api_projects.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/main.py backend/src/aitester/interaction/sessions.py backend/src/aitester/interaction/projects.py backend/tests/test_api_chat_sessions.py backend/tests/test_api_projects.py
git commit -m "feat(api): 会话落盘改项目空间；messages/delete 作用域 Query"
```

---

### Task 5: 全量测试去掉 `sessions_dir` 并改 locator 取 store

**Files:**
- Modify: 所有 `create_app(... sessions_dir=...)` 的测试文件（`test_api.py`、`test_kb_api.py`、`test_chat_pending.py`、`test_subagent_service.py`、`test_case_design_graph.py`、`test_chat_auth_api.py`、`test_chat_stream_api.py`、`test_context_e2e.py`、`test_agents.py`、`test_api_capabilities.py`、`test_project_browse.py`、`test_api_fs_pick.py`、`test_kb_lifespan.py`、`test_kb_browse.py` 等——以 `rg sessions_dir backend/tests` 为准）
- 凡 `application.state.sessions` 当 `SessionStore` 用的，改为 `for_agent(pid, agent_id)`（需已有 `_seed_project`）
- `test_api.py` 双 app 隔离用例：改为两个不同 `projects_path` + 各自 seed 不同 dir，断言会话互不可见

- [ ] **Step 1: 列出并逐文件改**

Run: `cd backend && rg -n "sessions_dir" tests`  
对每一处删除该关键字参数；破坏的断言按 locator 修复。

- [ ] **Step 2: 全量 pytest**

Run: `cd backend && uv run pytest -q`  
Expected: PASS；若失败只修测试/漏改装配，不放宽 spec

- [ ] **Step 3: Commit**

```bash
git add backend/tests
git commit -m "test(session): 去掉 sessions_dir，改经 SessionLocator 与项目 dir"
```

---

### Task 6: 前端 messages/delete 带作用域

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/ChatPage.tsx`

**Interfaces:**
- Produces:
  ```ts
  export function getSessionMessages(
    sessionId: string, agentId: string, projectId: string,
  ): Promise<...>
  export function deleteChatSession(
    sessionId: string, agentId: string, projectId: string,
  ): Promise<null>
  ```
  Query 拼法与 `getSessions` 相同。

- [ ] **Step 1: 改 client 签名与 URL**

- [ ] **Step 2: 改 ChatPage 调用处**

`openSession` / `remove` 使用当前页的 `agentId`（或现有 state 名）与 `projectId`；补依赖数组。

- [ ] **Step 3: 构建门禁**

Run: `cd frontend && npm run build`  
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/ChatPage.tsx
git commit -m "feat(chat-ui): 拉历史/删会话带上 project 与 agent 作用域"
```

---

### Task 7: README + spec 状态

**Files:**
- Modify: `README.md`（聊天落盘段、单进程约束段）
- Modify: `docs/superpowers/specs/2026-10-09-session-history-project-dir-design.md` 状态 → **已实现**

文案要点：
- 会话落盘：`{项目本地文件目录}/session_history/<agent_id>/`
- 旧 `backend/data/sessions` 不再使用
- 同一项目 dir 的 `session_history` 假定单后端进程写索引
- 删除 AiTester 项目配置不删除项目目录内聊天历史

- [ ] **Step 1: 改文档**

- [ ] **Step 2: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-09-session-history-project-dir-design.md
git commit -m "docs(session): README 与 spec 同步项目 session_history 落盘"
```

---

## Self-Review（对照 spec）

| Spec 裁定 | 对应任务 |
|-----------|----------|
| 唯一真相项目 `session_history/<agent_id>/` | T1 工厂 + T3/T4 装配 |
| 不迁旧数据、不读 backend/data/sessions | T4/T5 去掉 sessions_dir；Global Constraints |
| 全量 result 落盘 | T1/T3 |
| result 不进 SSE/API | T3 `_public_steps` + T4 测试 |
| Locator 非 Hub | T2 |
| messages/delete 双 Query | T4 + T6 |
| 删项目不清 session_history | T4 |
| 平台智能体不落盘 | 既有 prepare 分支，T3 保持 |
| HISTORY_MAX / recall 投影 | 不改（显式非目标） |
| README | T7 |

无 TBD/Similar-to 占位；`SessionLocator` / `open_session_store` / `_public_steps` 命名前后任务一致。
