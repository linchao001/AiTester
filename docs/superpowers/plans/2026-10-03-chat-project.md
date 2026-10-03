# 聊天页第 2 片（项目维度：归属 + 落点）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让聊天会话绑定项目、按项目隔离与过滤，并把文件工具的 `cwd` 从固定的 `"."` 换成项目目录，使智能体的产出物落进项目内。

**Architecture:** 后端新增「归属」这一维度：`project_id` 只作为 `index.json` 的会话元字段与过滤/校验条件，**不进会话键**（memory 键与守卫键继续是 `{agent_id}:{session_id}`）；`ChatService.send` 在装配前完成项目解析、目录验真与双归属校验，再把 `project.dir` 作为 `cwd` 传给 `AgentRuntime.build`。前端 `ChatPage` 长出「当前项目 → 当前智能体」级联，选择态存 localStorage，项目为空时整页引导。

**Tech Stack:** Python 3.11 + FastAPI + Pydantic v2 + LangGraph（`uv run pytest`）；Vite + React 18 + TypeScript（`npm run build`，项目无前端测试框架）。

**Spec:** `docs/superpowers/specs/2026-10-03-chat-project-design.md`（丁一口径：本片只做归属与落点，**不做任何运行时路径拦截**）

## Global Constraints

每一任务的要求都隐式包含本节。

- **不 push**；在 `master` 上逐任务提交（第 1 片 SDD 口径）。
- **不许动正在跑的 8000 / 5173 服务**；真机走查自起端口（`scripts/dev.ps1 -FrontendPort`，第 1 片用 5176），走查用的探针会话**用后即删**。
- **真实 LLM 调用须用户当面授权**，且由用户本人发消息走查；agent 只证明免费部分（单测 + build）。
- 后端门禁：`cd backend && uv run pytest -q`，**基线 379 passed**，每任务收尾必须仍是全绿。
- 前端门禁：`cd frontend && npm run build`，0 error（无前端测试框架，这是既有裁定）。
- **本片不得夹带**：越界路径拦截、界外授权面板、`strict` 权限模式、从工具面摘掉 `pwsh`/`bash`、`SendRequest` 之外的新错误类型。这些属第 5 片。
- UI 文案一律中文；页面 **0 处 `zhb`、0 个死按钮**；新增控件必须复用既有形态（`.ctx-card` + 原生 `select`），不新增色值。
- 所有 4xx `detail` 中文且**可照做**；`dir` 是用户自填路径，允许原样回显（第 1 片「不泄露 sessions 目录以外的绝对路径」口径不适用于此）。
- 落盘数据只走 `FileJsonConfigRepository` 原子写；`backend/data` 单进程约束沿用第 1 片。
- 行号会漂：计划里的 `:123` 只作定位提示，**认符号不认行号**。

## File Structure

后端（改 10 个文件，不新建文件——危险根判据与 `dir_exists` 属项目配置的既有落点）：

| 文件 | 责任 | 动作 |
|---|---|---|
| `backend/src/aitester/services/project_config.py` | 危险根闭集判据、`dir_exists` 只读探测、`list_projects` 摘掉恒 0 | Modify |
| `backend/src/aitester/services/session_store.py` | `Session.project_id`、`create`/`list` 带项目、`count_by_project`、`delete_by_project`、读侧缺归属即丢行 | Modify |
| `backend/src/aitester/memory/file_memory.py` | 构造期接收 `project_id`，延迟建会话时透传 | Modify |
| `backend/src/aitester/services/agent_runtime.py` | `build(..., cwd=...)` 透传给注册表 | Modify |
| `backend/src/aitester/services/chat.py` | `send` 的项目解析/验真/双归属/`cwd` 装配 | Modify |
| `backend/src/aitester/interaction/schemas.py` | `SendRequest.project_id`、`SessionInfo.project_id`、`ProjectInfo.dir_exists` | Modify |
| `backend/src/aitester/interaction/router.py` | `chat_send` 传 `project_id`、`ProjectConfigError`→400 | Modify |
| `backend/src/aitester/interaction/sessions.py` | 列表端点 `project_id` 必填 | Modify |
| `backend/src/aitester/interaction/projects.py` | 读侧补 `session_count`/`dir_exists`、删除级联回收会话 | Modify |
| `backend/src/aitester/main.py` | `ChatService(projects=project_config)` 接线 | Modify |

前端（改 7 个文件）：`api/client.ts`、`pages/ChatPage.tsx`、`pages/chat/SessionPane.tsx`、`pages/chat/MessageList.tsx`、`pages/chat/Composer.tsx`、`pages/KbPage.tsx`、`pages/ProjectsPage.tsx`。

`ChatPage.tsx` 本片会明显变胖（多一条项目加载与级联收敛的线）。本期不拆：项目选择与智能体选择在同一个状态机里，拆成 hook 文件会把「谁先就绪」的时序切成两半，比留在原文件更难读。第 3 片右栏工作区进来时再一并拆。

---

### Task 1: 危险根闭集判据（项目创建时拒整盘/家目录/系统目录）

**Files:**
- Modify: `backend/src/aitester/services/project_config.py`（`_validate_dir` 附近，约 `:60-70`）
- Test: `backend/tests/test_project_config.py`

**Interfaces:**
- Consumes: 无
- Produces: `dangerous_root_reason(dir_: str) -> str | None`（返回中文原因即拒绝，`None` 放行）；`_validate_dir` 在形态校验之后调用它

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_project_config.py` 末尾追加。文件顶部需要 `import os` 与 `from pathlib import Path`（已有则不重复加）：

```python
# ---------- 危险根闭集（spec 裁定 6：四类判据是闭集，不是会长大的黑名单）----------

def test_filesystem_root_is_refused() -> None:
    # os.path.abspath(os.sep) 在 Windows 给 "C:\\"、POSIX 给 "/"，两侧都是「没有父目录」的形态
    assert dangerous_root_reason(os.path.abspath(os.sep)) is not None


def test_home_dir_itself_is_refused() -> None:
    assert dangerous_root_reason(str(Path.home())) is not None


def test_windows_env_dir_is_refused(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    pf = tmp_path / "Program Files"
    monkeypatch.setenv("ProgramFiles", str(pf))
    assert dangerous_root_reason(str(pf)) is not None


@pytest.mark.skipif(os.name == "nt", reason="Windows 上 /usr 会被 resolve 成 C:\\usr，不属 POSIX 闭集分支")
def test_posix_system_dir_is_refused() -> None:
    assert dangerous_root_reason("/usr") is not None


def test_ordinary_subdir_is_allowed(tmp_path) -> None:
    target = tmp_path / "work" / "reqs"
    assert dangerous_root_reason(str(target)) is None


def test_create_refuses_filesystem_root(tmp_path) -> None:
    svc = _svc(tmp_path)  # 复用本文件既有的 ProjectService 构造助手
    with pytest.raises(ProjectConfigError) as exc:
        svc.create(name="整盘", desc="", dir_=os.path.abspath(os.sep), agents=["case_design"])
    assert "项目目录" in exc.value.detail


def test_create_allows_nested_dir(tmp_path) -> None:
    root = tmp_path / "reqs"
    created = _svc(tmp_path).create(name="订单系统", desc="", dir_=str(root), agents=["case_design"])
    assert created["dir"] == str(root)
```

> `_clean_dir` 会剥掉尾部分隔符（`project_config.py:50-57`），所以 `tmp_path` 本身（盘根之下的一级）不会被 `parent == self` 误杀；`D:/` 这类剥完塌成裸盘符时保留原形态，resolve 后仍命中盘根判据。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_project_config.py -q`
Expected: FAIL —— `NameError: name 'dangerous_root_reason' is not defined`

- [ ] **Step 3: 实现判据**

`project_config.py` 顶部补 `import os`（已有 `re`、`uuid`），并在 `_validate_dir` 上方插入：

```python
POSIX_SYSTEM_ROOTS = frozenset({
    "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/boot", "/dev", "/home", "/root",
})

# Windows 侧一律取环境变量，不硬编码盘符：机器可能装在不同的系统盘
_WIN_ROOT_ENV_VARS = ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")


def dangerous_root_reason(dir_: str) -> str | None:
    """四类危险根判据（spec 裁定 6）：命中即返回中文原因，全部不命中返回 None。

    只判四类闭集，不维护黑名单——判据是「文件系统根 / 家目录本身 / 环境变量给出的 Windows
    系统目录 / 写死的 POSIX 系统目录」，第四条之外一律放行，用户填 D:/work 这类容器目录是他的选择。
    """
    raw = _clean_dir(dir_)
    if not raw:
        return "请填写本地文件目录"
    try:
        target = Path(raw).expanduser().resolve()
    except OSError:
        return None  # 解析不了不等于危险：形态校验已把住入口，此处不额外拦人
    if target.parent == target:
        return f"不能把整个磁盘「{target}」作为项目目录，请选择盘下的具体目录"
    if target == Path.home().resolve():
        return f"不能把用户主目录「{target}」作为项目目录，请选择其下的具体项目目录"
    system_roots = {
        Path(value).resolve() for value in (os.environ.get(v) for v in _WIN_ROOT_ENV_VARS) if value
    }
    if target in system_roots:
        return f"不能把系统目录「{target}」作为项目目录，请选择项目自己的目录"
    if target.as_posix().rstrip("/") in POSIX_SYSTEM_ROOTS:
        return f"不能把系统目录「{target.as_posix().rstrip('/')}」作为项目目录，请选择项目自己的目录"
    return None
```

本文件需要 `from pathlib import Path`（若未导入则补）。`_validate_dir` 末尾改为：

```python
def _validate_dir(dir_: str) -> str:
    """只校验形态（绝对路径）与危险根，不 stat、不建目录。仅创建时调用——dir 冻结后不再重复校验。"""
    clean = _clean_dir(dir_)
    if not clean:
        raise ProjectConfigError("请填写本地文件目录")
    if not ABS_PATH.match(clean):
        raise ProjectConfigError("目录必须是绝对路径，例如 D:/work/projects/order-system")
    if len(clean) > DIR_MAX:
        raise ProjectConfigError(f"本地文件目录不能超过 {DIR_MAX} 字")
    reason = dangerous_root_reason(clean)
    if reason:
        raise ProjectConfigError(reason)
    return clean
```

同时把文件头注释里那句「本地文件目录只校验绝对路径形态，不触磁盘、不建目录」改成「只校验绝对路径形态与危险根闭集，不 stat 存在性、不建目录」，别让注释比代码乐观。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_project_config.py -q`
Expected: PASS（含既有断言——若既有测试里有拿 `tmp_path` 根或家目录当 `dir` 的，按本判据改成其下子目录，并在提交说明里点明）

- [ ] **Step 5: 全量后端回归 + Commit**

```bash
cd backend && uv run pytest -q          # 期望 379+ passed
cd .. && git add backend/src/aitester/services/project_config.py backend/tests/test_project_config.py
git commit -m "feat(projects): 危险根闭集四类判据——整盘/家目录/系统目录不可作为项目根"
```

---

### Task 2: `SessionStore` 加项目归属（index 行、双条件过滤、计数、按项目回收）

**Files:**
- Modify: `backend/src/aitester/services/session_store.py`（`Session` `:52-62`、`_load_index` `:118-145`、`create` `:178-197`、`list` `:199-202`、`delete` `:247-257`）
- Test: `backend/tests/test_session_store.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `Session(id, agent_id, project_id, title, created_at, updated_at, message_count)`
  - `create(session_id: str, agent_id: str, project_id: str, first_message: str) -> Session`
  - `list(agent_id: str, project_id: str) -> list[Session]`
  - `count_by_project(project_id: str) -> int`
  - `delete_by_project(project_id: str) -> int`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_session_store.py`：本文件里**每一处** `store.create(sid, "case_design", "...")` 与 `store.list("case_design")` 都要按新签名补项目参数——统一用模块级常量，别各处写散值：

```python
PROJECT = "proj_11111111"

# 全文件替换示例（认符号不认行号）：
#   store.create(sid, "case_design", "原始问题")            → store.create(sid, "case_design", PROJECT, "原始问题")
#   store.list("case_design")                              → store.list("case_design", PROJECT)
#   store.list("ghost")                                    → store.list("ghost", PROJECT)
```

再追加新断言：

```python
def test_index_row_requires_project_id_or_row_is_dropped(tmp_path, caplog) -> None:
    # 归属是硬字段：第 2 片之前的旧行（无 project_id）按「丢一行且留话」处理，正文原地不动
    store = SessionStore(tmp_path / "sessions")
    sid = store.new_id()
    store.create(sid, "case_design", PROJECT, "问题")
    raw = tmp_path / "sessions" / "index.json"
    data = json.loads(raw.read_text(encoding="utf-8"))
    data["sessions"][0].pop("project_id")
    raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    reloaded = SessionStore(tmp_path / "sessions")
    assert reloaded.list("case_design", PROJECT) == []
    assert "缺少项目归属" in caplog.text
    assert (tmp_path / "sessions" / f"{sid}.jsonl").exists()


def test_list_filters_by_both_agent_and_project(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    mine = store.new_id()
    other_agent = store.new_id()
    other_project = store.new_id()
    store.create(mine, "case_design", PROJECT, "我的")
    store.create(other_agent, "kb_assistant", PROJECT, "别的智能体")
    store.create(other_project, "case_design", "proj_22222222", "别的项目")
    assert [s.id for s in store.list("case_design", PROJECT)] == [mine]
    assert store.get(mine).project_id == PROJECT


def test_count_by_project(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    store.create(store.new_id(), "case_design", PROJECT, "一")
    store.create(store.new_id(), "case_design", PROJECT, "二")
    store.create(store.new_id(), "case_design", "proj_22222222", "三")
    assert store.count_by_project(PROJECT) == 2
    assert store.count_by_project("proj_99999999") == 0


def test_delete_by_project_removes_rows_and_files(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions")
    a = store.new_id()
    b = store.new_id()
    keep = store.new_id()
    store.create(a, "case_design", PROJECT, "一")
    store.create(b, "case_design", PROJECT, "二")
    store.create(keep, "case_design", "proj_22222222", "留")
    assert store.delete_by_project(PROJECT) == 2
    assert not (tmp_path / "sessions" / f"{a}.jsonl").exists()
    assert not (tmp_path / "sessions" / f"{b}.jsonl").exists()
    assert store.get(keep) is not None
    assert store.delete_by_project(PROJECT) == 0  # 幂等：再来一次不炸
```

本文件顶部需要 `import json`（若未导入）。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_session_store.py -q`
Expected: FAIL —— `TypeError: create() takes 4 positional arguments but 5 were given`

- [ ] **Step 3: 实现**

`session_store.py` 改动四处：

```python
@dataclass
class Session:
    id: str
    agent_id: str
    project_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int
```

`_load_index` 里紧跟 `agent_id` 检查之后插入（并保持 `record` 里加上 `"project_id": project_id`，顺序与 `Session` 字段一致）：

```python
            agent_id = str(item.get("agent_id") or "")
            if not agent_id:
                continue
            project_id = str(item.get("project_id") or "")
            if not project_id:
                # 项目归属自第 2 片起是硬字段：缺了就没有任何列表能安全地显示它。
                # 与坏行同口径——丢一行必须留话，正文 .jsonl 原地不动，用户还能从日志找回
                logger.warning("会话索引中 %s 行缺少项目归属（第 2 片前的旧数据），已丢弃该行", sid)
                continue
```

```python
    def create(self, session_id: str, agent_id: str, project_id: str, first_message: str) -> Session:
        if not is_session_id(session_id):
            raise SessionStoreError(MISSING_SESSION_DETAIL)
        with self._lock:
            existing = self._index_by_id().get(session_id)
            if existing is not None:  # 幂等：重入不覆盖标题与 created_at
                return Session(**existing)
            now = _now_ms()
            record = {
                "id": session_id,
                "agent_id": agent_id,
                "project_id": project_id,
                "title": _title_from(first_message),
                "created_at": now,
                "updated_at": now,
                "message_count": 0,
            }
            self._index.sessions.append(record)
            self._save_index()
            self._path(session_id).touch()
            return Session(**record)

    def list(self, agent_id: str, project_id: str) -> list[Session]:
        # 双条件过滤：列表是「这个项目下这个智能体的会话」，缺一即跨项目串列（spec 裁定 1/2）
        rows = [
            s for s in self._index.sessions
            if s["agent_id"] == agent_id and s["project_id"] == project_id
        ]
        rows.sort(key=lambda s: (s["updated_at"], s["created_at"]), reverse=True)
        return [Session(**s) for s in rows]

    def count_by_project(self, project_id: str) -> int:
        return sum(1 for s in self._index.sessions if s["project_id"] == project_id)

    def delete_by_project(self, project_id: str) -> int:
        with self._lock:
            rows = [s for s in self._index.sessions if s["project_id"] == project_id]
            if not rows:
                return 0
            for row in rows:
                self._index.sessions.remove(row)
            # 与 delete 同款刻意顺序：先写索引再 unlink，宁可留孤儿正文也不留指向不存在文件的悬空行
            self._save_index()
            for row in rows:
                self._path(row["id"]).unlink(missing_ok=True)
            return len(rows)
```

`delete()` 与 `append()` 不动。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_session_store.py tests/test_file_memory.py -q`
Expected: `test_session_store.py` 全绿；`test_file_memory.py` **本任务预期仍红**（`create()` 参数不符）——它归 Task 3 修，此处只确认失败原因就是签名，不是别的

- [ ] **Step 5: Commit（只提交本任务的两个文件）**

```bash
git add backend/src/aitester/services/session_store.py backend/tests/test_session_store.py
git commit -m "feat(sessions): 会话行加 project_id 归属，双条件过滤 + 按项目计数与回收"
```

---

### Task 3: `FileMemoryStore` 构造期接收 `project_id`

**Files:**
- Modify: `backend/src/aitester/memory/file_memory.py`（`:12-32`）
- Modify: `backend/src/aitester/services/chat.py:142`（调用点先临时补第三实参，Task 6 再传真值）
- Test: `backend/tests/test_file_memory.py`

**Interfaces:**
- Consumes: Task 2 的 `SessionStore.create(session_id, agent_id, project_id, first_message)`
- Produces: `FileMemoryStore(store: SessionStore, project_id: str)`（第二参**必填**，无默认值——静默空串会造出 Task 2 判据必丢的行）

- [ ] **Step 1: 写失败测试**

`test_file_memory.py` 里所有 `FileMemoryStore(store)` 改为带项目常量，并新增一条归属断言：

```python
PROJECT = "proj_11111111"

#   mem = FileMemoryStore(store)                                  → mem = FileMemoryStore(store, PROJECT)
#   mem = FileMemoryStore(SessionStore(tmp_path / "sessions"))     → mem = FileMemoryStore(SessionStore(tmp_path / "sessions"), PROJECT)


def test_lazy_created_session_carries_project(tmp_path) -> None:
    # 延迟建会话的裁定不变，但建出来的行必须带上归属——否则第一次刷新列表就把它丢了
    store = SessionStore(tmp_path / "sessions")
    mem = FileMemoryStore(store, PROJECT)
    sid = _new_sid(tmp_path)
    mem.save(f"case_design:{sid}", "user", "生成登录用例")
    assert store.get(sid).project_id == PROJECT
    assert [s.id for s in store.list("case_design", PROJECT)] == [sid]
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_file_memory.py -q`
Expected: FAIL —— `TypeError: FileMemoryStore.__init__() missing 1 required positional argument: 'project_id'`

- [ ] **Step 3: 实现**

```python
class FileMemoryStore:
    """落盘记忆：会话键不变，项目归属由构造期带入（第 2 片裁定 2：只作归属字段，不进键）。"""

    def __init__(self, store: SessionStore, project_id: str) -> None:
        self._store = store
        self._project_id = project_id
```

`save` 里的延迟建会话改为透传：

```python
        if self._store.get(sid) is None:
            # 首条消息建会话（延迟落盘裁定）：失败发送不留 0 消息幽灵会话
            self._store.create(sid, agent_id, self._project_id, content if role == "user" else "")
```

`_split` 与其文档注释（按首冒号切分、字符集无冒号）**一位不动**——键形态是本片刻意保留的东西。

调用点 `chat.py:142` 本任务先让它编译得过，传占位（Task 6 换成真 `pid`）：

```python
            memory = FileMemoryStore(self.sessions, "")
```

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_file_memory.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/memory/file_memory.py backend/src/aitester/services/chat.py backend/tests/test_file_memory.py
git commit -m "feat(memory): FileMemoryStore 构造期注入 project_id，延迟建会话带上归属"
```

---

### Task 4: 项目读侧——真实会话数与目录可达探测

**Files:**
- Modify: `backend/src/aitester/services/project_config.py`（`list_projects` `:165-167`，新增 `dir_exists`）
- Modify: `backend/src/aitester/interaction/schemas.py`（`ProjectInfo` `:176-186`）
- Modify: `backend/src/aitester/interaction/projects.py`（GET `:25-29`）
- Test: `backend/tests/test_api_projects.py`、`backend/tests/test_project_config.py`

**Interfaces:**
- Consumes: Task 2 的 `SessionStore.count_by_project`
- Produces: `project_config.dir_exists(dir_: str) -> bool`；`ProjectInfo` 多出 `dir_exists: bool`，`session_count` 由路由填真值

- [ ] **Step 1: 写失败测试**

`test_project_config.py` 追加：

```python
def test_dir_exists_reads_disk_without_creating_it(tmp_path) -> None:
    real = tmp_path / "reqs"
    real.mkdir()
    ghost = tmp_path / "typed-wrong"
    assert dir_exists(str(real)) is True
    assert dir_exists(str(ghost)) is False
    assert dir_exists("") is False
    assert not ghost.exists()  # 探测绝不建目录：write 工具会建，这里必须不建
```

`test_api_projects.py`：把既有断言（约 `:59-60`）改成认新字段与新语义——`list_projects` 不再塞 `session_count: 0`，真值来自路由：

```python
    assert p["kb"] == "kb" and p["dir_exists"] is False   # 测试里填的目录不存在
    assert set(p) == {"id", "name", "desc", "dir", "agents", "kb", "session_count", "dir_exists"}
```

再追加一条端到端计数：

```python
def test_session_count_reflects_store(tmp_path) -> None:
    application = _app(tmp_path)  # 复用本文件既有的 create_app 助手；须同时给 projects_path 与 sessions_dir
    pid = _create(application, "订单系统", str(tmp_path / "reqs"))["id"]
    store = application.state.sessions
    store.create(store.new_id(), "case_design", pid, "一")
    store.create(store.new_id(), "case_design", pid, "二")
    rows = _get(application)["projects"]
    assert next(p for p in rows if p["id"] == pid)["session_count"] == 2
```

> 若既有 `_app` 助手还没传 `sessions_dir`（默认落 `DATA_DIR`），本任务必须给它补上 `sessions_dir=tmp_path / "sessions"`，否则测试会往真实 `backend/data/sessions` 写数据——第 1 片就是靠「文件 size + mtime 双快照」防这个的。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_project_config.py tests/test_api_projects.py -q`
Expected: FAIL —— `NameError: dir_exists` / `KeyError: 'dir_exists'`

- [ ] **Step 3: 实现**

`project_config.py` 新增（放在 `dangerous_root_reason` 之后，同属目录判据）：

```python
def dir_exists(dir_: str) -> bool:
    """只读探测：存在且是目录。绝不 mkdir——write 工具会建目录，探测若也建就分不出「用户填错」与「用户还没建」。"""
    if not (dir_ or "").strip():
        return False
    try:
        return Path(dir_).expanduser().is_dir()
    except OSError:
        return False
```

`list_projects` 摘掉恒 0（`session_count` 属读侧组合字段，不该由项目真相伪造）：

```python
    def list_projects(self) -> list[dict[str, Any]]:
        # 会话数与目录可达性是「项目 × 会话」的组合事实，由路由层用 SessionStore 现算（第 2 片接真值）
        return [copy.deepcopy(p) for p in self._config["projects"]]
```

`interaction/schemas.py`：

```python
class ProjectInfo(BaseModel):
    """项目条目：kb 只回别名，真实知识库 id 与实体路径不外泄。"""

    id: str
    name: str
    desc: str
    dir: str
    agents: list[str]
    kb: str
    session_count: int = 0
    dir_exists: bool = True
```

`interaction/projects.py`：GET 组合两个真相源，并加一个 `_sessions` 助手（与 `sessions.py:24-26` 的 `_store` 同一写法）：

```python
def _sessions(request: Request):
    return request.app.state.sessions  # type: ignore[return-value]


@router.get("", response_model=ProjectsResponse)
def projects(request: Request) -> ProjectsResponse:
    store = _sessions(request)
    return ProjectsResponse(
        projects=[
            ProjectInfo(
                **p,
                session_count=store.count_by_project(p["id"]),
                dir_exists=dir_exists(p["dir"]),
            )
            for p in _svc(request).list_projects()
        ]
    )
```

导入补 `from aitester.services.project_config import ProjectConfigError, ProjectService, dir_exists`。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_project_config.py tests/test_api_projects.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归 + Commit**

```bash
cd backend && uv run pytest -q
cd .. && git add backend/src/aitester/services/project_config.py backend/src/aitester/interaction/schemas.py backend/src/aitester/interaction/projects.py backend/tests/test_api_projects.py backend/tests/test_project_config.py
git commit -m "feat(projects): 会话数接真值 + dir_exists 只读探测（不建目录）"
```

---

### Task 5: `AgentRuntime.build` 接收 `cwd`

**Files:**
- Modify: `backend/src/aitester/services/agent_runtime.py`（`build` `:48-91`）
- Test: `backend/tests/test_agent_runtime.py`（`test_registry_uses_dot_cwd_and_shared_observations` `:129-165`）

**Interfaces:**
- Consumes: 无
- Produces: `AgentRuntime.build(agent_id: str, session_id: str, provider_override: LlmProvider | None = None, cwd: str = ".") -> AgentInstance`

- [ ] **Step 1: 写失败测试**

把 `test_agent_runtime.py:129-165` 那条测试改名并拆成两条（默认值与显式传入是两个可各自被否的断言）：

```python
def test_registry_uses_given_cwd_and_shared_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ...  # 前半段（_Registry / fake_registry / monkeypatch / 三个 service 构造）一字不动地保留

    runtime.build("case_design", "s9", provider_override=MockProvider())
    assert captured["cwd"] == "."  # 默认值：echo 链路与既有调用方不破
    assert captured["session_id"] == "case_design:s9"  # 守卫键与 memory/storage 一样按智能体 scoped
    assert captured["observed"] is store
    assert captured["kb"] is kb
    assert captured["agent_id"] == "case_design"


def test_registry_uses_project_cwd_when_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    # 复用上一条的 _Registry / fake_registry 搭法（本文件已有 monkeypatch 形态，照搬）
    runtime = AgentRuntime(capability, model_config, FileObservationStore())
    root = tmp_path / "reqs"
    root.mkdir()
    runtime.build("case_design", "s9", provider_override=MockProvider(), cwd=str(root))
    assert captured["cwd"] == str(root)  # 第 2 片刻意把落点交给项目目录：产出物不再落进仓库
```

> 原 `:161` 那句注释「项目目录接入是留给项目专项的缝」在本刻**兑现**，删掉它，别再留一张过期借条。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -q`
Expected: FAIL —— `TypeError: build() got an unexpected keyword argument 'cwd'`

- [ ] **Step 3: 实现**

```python
    def build(
        self,
        agent_id: str,
        session_id: str,
        provider_override: LlmProvider | None = None,
        cwd: str = ".",
    ) -> AgentInstance:
```

非平台分支的注册表调用：

```python
            registry = build_default_registry(
                cwd=cwd,
                session_id=f"{agent_id}:{session_id}",
                observed=self._observations,
                kb=self._kb,
                agent_id=agent_id,
            )
```

`_build_platform_agent` **不动**：它自己算 `kb.workspace_dir("default", spec.id")` 作 cwd（`:109-113`），项目 `cwd` 对平台智能体无意义——传进去也会被忽略，所以 `build` 里平台短路（`:60-61`）保持在这条线之前。

`cwd` 参数默认 `"."` 是刻意的：`echo` 链路与所有既有调用方（含测试）零改动。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/agent_runtime.py backend/tests/test_agent_runtime.py
git commit -m "feat(runtime): build 接收 cwd 并透传注册表——产出物落点交给项目目录"
```

---

### Task 6: `ChatService.send` 的项目解析、目录验真与双归属校验

**Files:**
- Modify: `backend/src/aitester/interaction/schemas.py`（`SendRequest` `:16-20`）
- Modify: `backend/src/aitester/services/chat.py`（`__init__` `:30-44`、`send` `:106-156`）
- Modify: `backend/src/aitester/interaction/router.py`（`chat_send` `:57-70`）
- Modify: `backend/src/aitester/main.py:70-72`
- Test: `backend/tests/test_chat_service.py`、`backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 1 `ProjectService`/`ProjectConfigError`、Task 2 `Session.project_id`、Task 3 `FileMemoryStore(store, project_id)`、Task 5 `build(..., cwd=...)`
- Produces: `ChatService.__init__(..., projects: ProjectService | None = None)`；`ChatService.send(session_id: str, message: str, agent_id: str, project_id: str = "") -> dict[str, Any]`

- [ ] **Step 1: 写失败测试**

`test_chat_service.py` 顶部加项目夹具，并把**所有** `svc.send("...", "...", "case_design")` 补第四个实参 `pid`（本文件约 14 处，认符号不认行号；平台智能体那条 `kb_assistant` 保持不传）：

```python
from aitester.services.project_config import ProjectConfigError, ProjectService
from aitester.storage import FileJsonConfigRepository

@pytest.fixture
def project(tmp_path):
    """真实 ProjectService + 真实存在的目录：本片第一次消费 dir，测试不能给个字符串了事。"""
    root = tmp_path / "reqs"
    root.mkdir()
    svc = ProjectService(FileJsonConfigRepository(tmp_path / "projects.json"))
    created = svc.create(name="订单系统", desc="", dir_=str(root), agents=["case_design"])
    return svc, created["id"], root
```

新增四条：

```python
def test_send_requires_project_for_visible_agent(tmp_path, project) -> None:
    svc, pid, _ = project
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=SessionStore(tmp_path / "sessions"), projects=svc)
    with pytest.raises(ProjectConfigError) as exc:
        chat.send("", "生成用例", "case_design")           # 空 project_id
    assert "项目" in exc.value.detail


def test_send_rejects_unreachable_project_dir(tmp_path, project) -> None:
    svc, pid, root = project
    root.rmdir()                                            # 目录被移走/删掉：发送必须响亮失败
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=SessionStore(tmp_path / "sessions"), projects=svc)
    with pytest.raises(ProjectConfigError) as exc:
        chat.send("", "生成用例", "case_design", pid)
    assert str(root) in exc.value.detail and "项目页" in exc.value.detail


def test_send_builds_instance_with_project_cwd(tmp_path, project) -> None:
    svc, pid, root = project
    runtime = _recording_runtime(tmp_path)                  # 记 kwargs 的 runtime 替身，本文件已有同类形态
    chat = ChatService(agent_runtime=runtime, sessions=SessionStore(tmp_path / "sessions"), projects=svc)
    sid = chat.send("", "生成用例", "case_design", pid)["session_id"]
    assert runtime.calls[-1]["cwd"] == str(root)
    assert chat.sessions.get(sid).project_id == pid          # 延迟建会话也把归属落进去


def test_send_rejects_project_mismatch(tmp_path, project) -> None:
    svc, pid, _ = project
    other = svc.create(name="支付中心", desc="", dir_=str(tmp_path / "pay"), agents=["case_design"])["id"]
    store = SessionStore(tmp_path / "sessions")
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path), sessions=store, projects=svc)
    sid = chat.send("", "一", "case_design", pid)["session_id"]
    with pytest.raises(SessionStoreError) as exc:
        chat.send(sid, "二", "case_design", other)          # 往别的项目的会话里写
    assert "项目" in exc.value.detail


def test_platform_agent_ignores_project(tmp_path, project) -> None:
    svc, pid, _ = project
    chat = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path),
                       sessions=SessionStore(tmp_path / "sessions"), projects=svc)
    result = chat.send("kb-console", "记一笔", "kb_assistant")   # 空 project_id：/kb 链路不破（spec 裁定 7）
    assert result["session_id"] == "kb-console"
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_chat_service.py -q`
Expected: FAIL —— `TypeError: send() got an unexpected keyword argument` / `__init__() got an unexpected keyword argument 'projects'`

- [ ] **Step 3: 实现：schema 与注入缝**

`interaction/schemas.py`：

```python
class SendRequest(BaseModel):
    # 空串 = 服务端新建会话；sess_* 续写；其余形态按临时键处理（chat.py 三态判据）
    session_id: str = ""
    message: str = Field(min_length=1)
    agent_id: str = "case_design"
    # 必填判据在 service 层：schema 无从知道 is_platform_agent（平台智能体天然不属于项目）
    project_id: str = ""
```

`chat.py` 导入与构造：

```python
from aitester.services.project_config import ProjectConfigError, ProjectService
from pathlib import Path
```

```python
    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
        agent_runtime: AgentRuntime | None = None,
        sessions: SessionStore | None = None,
        projects: ProjectService | None = None,
    ) -> None:
        ...
        self.sessions = sessions
        self.projects = projects
```

- [ ] **Step 4: 实现：`send` 的项目段与校验顺序**

签名与项目解析插在 `agent_runtime is None` 检查之后、`sid` 三态之前——**顺序即裁定**：项目没验真之前，绝不能走到任何会落盘或建目录的路径。

```python
    def send(self, session_id: str, message: str, agent_id: str, project_id: str = "") -> dict[str, Any]:
        """真实链路：装配一次性实例（提示词/模型/工具/拓扑）后按图执行。

        项目维度（第 2 片）：可见智能体的会话必须属于一个项目，落点取项目 dir；
        平台功能智能体不属于项目，project_id 一律忽略（spec 裁定 7，/kb 链路依赖此）。
        """
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        platform = is_platform_agent(agent_id)
        project: dict[str, Any] | None = None
        pid = ""
        if not platform:
            pid = (project_id or "").strip()
            if not pid:
                raise ProjectConfigError("请先选择项目，再发送消息")
            if self.projects is None:
                raise ProjectConfigError("服务未装配项目配置，请通过 create_app 启动后端")
            project = self.projects.get(pid)          # 未知项目 → ConfigNotFoundError → 路由 404
            root = Path(project["dir"]).expanduser()
            if not root.is_dir():
                raise ProjectConfigError(
                    f"项目「{project['name']}」的目录 {project['dir']} 不存在或不可访问，"
                    "请到项目页确认路径"
                )
```

三态判据里的归属校验补第二维（`chat.py:118-128` 现有结构保持）：

```python
        elif self.sessions is not None and is_session_id(sid):
            existing = self.sessions.get(sid)
            if existing is None:
                raise SessionStoreError(MISSING_SESSION_DETAIL)
            # 会话归属校验：sess_* 续写前先判等 agent_id，否则任何 agent_id 都能往别人的会话里写
            if existing.agent_id != agent_id:
                raise SessionStoreError("会话不属于该智能体")
            # 第二维（第 2 片）：项目归属同判据；平台智能体不落的会话不参与比对
            if not platform and existing.project_id != pid:
                raise SessionStoreError("会话不属于该项目")
```

原先那句预告「第 2 片 project_id 进键前必须关的洞」的注释**必须改写**——本片按裁定 2 不进键，留着就是把过期借条当现状：

```python
        # 装配落点：可见智能体用项目 dir（产出物归位），平台智能体沿用 _build_platform_agent 内部算的 workspace
        instance = self.agent_runtime.build(
            agent_id,
            sid,
            provider_override=self.provider,
            cwd=project["dir"] if project is not None else ".",
        )
```

记忆装配（Task 3 的占位在此换成真值）：

```python
            memory = FileMemoryStore(self.sessions, pid)
```

- [ ] **Step 5: 实现：路由与接线**

`interaction/router.py` 的 `chat_send`：

```python
        result = service.send(req.session_id, req.message, req.agent_id, req.project_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
```

导入补 `from aitester.services.project_config import ProjectConfigError`。映射表就这一条新增：**不新建错误类型**，400/404/502 的既有分工不动。

`main.py`：

```python
    application.state.chat_service = ChatService(
        agent_runtime=application.state.agent_runtime,
        sessions=sessions,
        projects=project_config,
    )
```

- [ ] **Step 6: 跑到通过**

Run: `cd backend && uv run pytest tests/test_chat_service.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 7: 全量后端回归**

Run: `cd backend && uv run pytest -q`
Expected: 全绿。`test_api_chat_sessions.py:50,175` 与本文件里剩余的 `store.create(sid, agent, msg)` 三参调用、`test_session_store` 之外的 `list(agent)` 单参调用会在此刻暴露——按 Task 2/3 的签名补齐项目参数，**这是预期内的既有测试修缝，不是新 bug**

- [ ] **Step 8: Commit**

```bash
git add backend/src/aitester/interaction/schemas.py backend/src/aitester/interaction/router.py \
        backend/src/aitester/services/chat.py backend/src/aitester/main.py \
        backend/tests/test_chat_service.py backend/tests/test_api.py backend/tests/test_api_chat_sessions.py
git commit -m "feat(chat): send 解析项目并验真目录，双归属校验 + 实例 cwd 落项目"
```

---

### Task 7: 会话列表端点收紧（`project_id` 必填）

**Files:**
- Modify: `backend/src/aitester/interaction/sessions.py`（`sessions_list` `:35-45`）
- Modify: `backend/src/aitester/interaction/schemas.py`（`SessionInfo` `:209-215`）
- Test: `backend/tests/test_api_chat_sessions.py`

**Interfaces:**
- Consumes: Task 2 `store.list(agent_id, project_id)`、Task 6 `Session.project_id`
- Produces: `GET /api/chat/sessions?agent_id=&project_id=`，两个都 `min_length=1`；`SessionInfo` 多出 `project_id`

- [ ] **Step 1: 写失败测试**

`test_api_chat_sessions.py`：既有请求全部补 `project_id`，并加缺参断言：

```python
def test_list_requires_project_id(client) -> None:
    r = client.get("/api/chat/sessions", params={"agent_id": "case_design"})
    assert r.status_code == 422          # 必填参数缺失由 FastAPI 校验层拦，service 不必再判


def test_list_filters_by_project(client, store) -> None:
    a = store.new_id(); b = store.new_id()
    store.create(a, "case_design", "proj_11111111", "本项目")
    store.create(b, "case_design", "proj_22222222", "别的项目")
    rows = client.get(
        "/api/chat/sessions",
        params={"agent_id": "case_design", "project_id": "proj_11111111"},
    ).json()["sessions"]
    assert [r["id"] for r in rows] == [a]
    assert rows[0]["project_id"] == "proj_11111111"
```

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_api_chat_sessions.py -q`
Expected: FAIL —— 返回体无 `project_id` / 过滤未生效

- [ ] **Step 3: 实现**

```python
class SessionInfo(BaseModel):
    id: str
    agent_id: str
    project_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int
```

```python
@router.get("", response_model=SessionsResponse)
def sessions_list(
    request: Request,
    agent_id: str = Query(min_length=1),
    project_id: str = Query(min_length=1),
) -> SessionsResponse:
    # 未知或平台智能体 → 空列表 200：列表是「此处没有会话」，不是错误。
    # project_id 必填不砸 /kb：三端点按第 1 片口径只服务已落盘的 sess_* 会话，临时键一律 404/空表
    # model_validate 而非 **vars：与读路径 steps 同款口径，Session 数据类日后多出字段也不会炸
    return SessionsResponse(
        sessions=[
            SessionInfo.model_validate(vars(s)) for s in _store(request).list(agent_id, project_id)
        ]
    )
```

`/{session_id}/messages` 与 `DELETE /{session_id}` **不加归属参数**（spec 已知限制：读侧仍按 id 直读，UI 只从正确列表取 id 是本期防线）。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_api_chat_sessions.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归 + Commit**

```bash
cd backend && uv run pytest -q
cd .. && git add backend/src/aitester/interaction/sessions.py backend/src/aitester/interaction/schemas.py backend/tests/test_api_chat_sessions.py
git commit -m "feat(api): 会话列表按 (agent_id, project_id) 双条件过滤，project_id 必填"
```

---

### Task 8: 删除项目级联回收会话

**Files:**
- Modify: `backend/src/aitester/interaction/projects.py`（`projects_delete` `:61-68`）
- Test: `backend/tests/test_api_projects.py`

**Interfaces:**
- Consumes: Task 2 `SessionStore.delete_by_project`
- Produces: `DELETE /api/projects/{id}` 成功后该项目的 `index` 行与 `.jsonl` 一并消失

- [ ] **Step 1: 写失败测试**

```python
def test_delete_project_cascades_sessions(tmp_path) -> None:
    application = _app(tmp_path)
    pid = _create(application, "订单系统", str(tmp_path / "reqs"))["id"]
    other = _create(application, "支付中心", str(tmp_path / "pay"))["id"]
    store = application.state.sessions
    sid = store.new_id()
    kept = store.new_id()
    store.create(sid, "case_design", pid, "该删")
    store.create(kept, "case_design", other, "该留")
    r = application.state  # 仅为可读性；断言走 client
    assert _delete(application, pid).status_code == 204
    assert store.get(sid) is None
    assert not (tmp_path / "sessions" / f"{sid}.jsonl").exists()
    assert store.get(kept) is not None            # 别的项目一条不少
    assert (tmp_path / "sessions" / f"{kept}.jsonl").exists()


def test_delete_project_last_one_keeps_sessions(tmp_path) -> None:
    # 顺序是刻意的：项目校验没过（剩 1 条禁删）时绝不能先把会话删了
    application = _app(tmp_path)
    pid = _create(application, "唯一项目", str(tmp_path / "only"))["id"]
    store = application.state.sessions
    sid = store.new_id()
    store.create(sid, "case_design", pid, "别跟着死")
    assert _delete(application, pid).status_code == 400
    assert store.get(sid) is not None
```

> `_delete` / `_create` 用本文件既有的 HTTP 助手；若没有 `_delete`，按 `_create` 同款补一个 `application.test_client().delete(f"/api/projects/{pid}")`。

- [ ] **Step 2: 跑到失败**

Run: `cd backend && uv run pytest tests/test_api_projects.py -q`
Expected: FAIL —— 项目删了但 `store.get(sid)` 仍在（级联尚未实现）；第二条测试应**已经通过**

- [ ] **Step 3: 实现**

```python
logger = getLogger(__name__)


@router.delete("/{project_id}", status_code=204)
def projects_delete(project_id: str, request: Request) -> None:
    try:
        _svc(request).delete(project_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProjectConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    # 先删项目再收会话：顺序反了就会在「剩 1 条禁删」这类校验失败时把会话陪葬掉
    try:
        _sessions(request).delete_by_project(project_id)
    except (OSError, SessionStoreError) as exc:
        # 项目已从真相里消失，此刻删不动的会话是孤儿：留话即可，绝不回滚项目删除
        logger.warning("项目 %s 已删除，但其会话回收失败：%s", project_id, exc)
```

导入补 `from logging import getLogger` 与 `from aitester.services.session_store import SessionStoreError`。

- [ ] **Step 4: 跑到通过**

Run: `cd backend && uv run pytest tests/test_api_projects.py -q`
Expected: PASS

- [ ] **Step 5: 数据目录零污染自查 + Commit**

`backend/data` 整目录被 gitignore，`git status` 无证明力；按第 1 片口径以**文件 size + mtime 双快照**验证本轮测试没往真实数据目录写：

```bash
cd backend && uv run pytest -q
cd .. && git add backend/src/aitester/interaction/projects.py backend/tests/test_api_projects.py
git commit -m "feat(projects): 删除项目级联回收会话，顺序保证校验失败不陪葬"
```

---

### Task 9: 前端 API 层与 /kb 调用点

**Files:**
- Modify: `frontend/src/api/client.ts`（`chatSend` `:285-290`、`ChatSession` `:292-299`、`getSessions` `:308-312`、`Project` `:323-331`）
- Modify: `frontend/src/pages/KbPage.tsx:325`
- Test: 无（项目无前端测试框架，门禁是 `npm run build`）

**Interfaces:**
- Consumes: Task 6/7 的后端契约
- Produces: `chatSend(sessionId, message, agentId, projectId)`、`getSessions(agentId, projectId)`、`ChatSession.project_id: string`、`Project.dir_exists: boolean`

- [ ] **Step 1: 改 client**

```ts
export function chatSend(
  sessionId: string, message: string, agentId: string, projectId: string,
): Promise<SendResponse> {
  return apiFetch<SendResponse>("/api/chat/send", {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ session_id: sessionId, message, agent_id: agentId, project_id: projectId }) });
}

export interface ChatSession {
  id: string;
  agent_id: string;
  project_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export function getSessions(agentId: string, projectId: string): Promise<{ sessions: ChatSession[] }> {
  return apiFetch<{ sessions: ChatSession[] }>(
    `${sessionsApi()}?${new URLSearchParams({ agent_id: agentId, project_id: projectId }).toString()}`);
}
```

`Project` 加 `dir_exists: boolean;`（`session_count` 已在）。

- [ ] **Step 2: 改 KbPage 调用点**

`KbPage.tsx:325` —— 平台智能体不属于任何项目，第四参传空串（后端 `chat.py` 平台分支忽略它，见 spec 裁定 7）：

```ts
      const resp = await chatSend("kb-console", text, "kb_assistant", "");
```

那行上方的注释（`:318`，「session_id/agent_id 固定值不可改」）补一句：`project_id 传空串——平台助手不属于项目，后端对该字段短路忽略`。

- [ ] **Step 3: 类型门禁**

Run: `cd frontend && npm run build`
Expected: **FAIL** ——`ChatPage.tsx` 仍以 3 参调用 `chatSend`、以 1 参调用 `getSessions`，报 `TS2554`。这是本任务刻意的中间态：`ChatPage` 归 Task 10，`KbPage` 此处已修

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/KbPage.tsx
git commit -m "feat(api-client): chat/send 与会话列表带 project_id，Project 增 dir_exists"
```

（Task 10 会把 build 修回绿；本任务提交的是一个尚未收敛的中间态，提交说明里点明 `ChatPage` 待接。）

---

### Task 10: `ChatPage` 项目状态机与级联

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`
- Test: `npm run build` 门禁 + Task 11 真机走查

**Interfaces:**
- Consumes: Task 9 的 `getProjects`（`client.ts:356` 已有）/`getSessions(agentId, projectId)`/`chatSend(…, projectId)`
- Produces: `SessionPane` 与 `MessageList`/`Composer` 的新 props（Task 11 消费）：`projects`、`projectId`、`agentOptions`、`onProjectChange`、`projectName`

- [ ] **Step 1: 加状态与导入**

`ChatPage.tsx` 顶部：

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, chatSend, deleteChatSession, getCapabilities, getModels,
  getProjects, getSessionMessages, getSessions,
  type AgentInfo, type ChatMessage, type ChatSession, type HealthResponse, type Project,
} from "../api/client";

/** 视图上下文，不是数据：项目本身已落盘在后端，这里只记「这次打开 /chat 看着哪个」。
 *  换浏览器不带走选择——这是第 2 片裁定 3 的代价，写在注释里免得后来人当 bug 修。 */
const PROJECT_STORAGE_KEY = "aitester.chat.projectId";
```

组件内新增（紧跟 `const [agentId, setAgentId] = useState("");`）：

```tsx
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectsLoaded, setProjectsLoaded] = useState(false);   // 引导态判据：拉成功且确实为空，区别于「还没拉到」
  const navigate = useNavigate();
```

- [ ] **Step 2: `reloadMeta` 只认清单，不再代选智能体**

`reloadMeta` 里删掉这两行——`agentId` 的归属从此由级联决定（Step 3 的 effect），留着它会让「项目里没有的智能体」被无条件选中：

```tsx
      if (!agentId && caps.agents.length) {
        setAgentId(caps.agents[0].id);
      }
```

其余保持不动，包括 `const agent = caps.agents.find((a) => a.id === agentId) || caps.agents[0];` 与 `metaOkRef` 的降级判据（第 1 片终审项，勿动）。

- [ ] **Step 3: 项目加载与级联收敛**

在 `reloadMeta` 之后加：

```tsx
  const reloadProjects = useCallback(async () => {
    try {
      const j = await getProjects();
      setProjects(j.projects);
      // 函数式更新：不读 projectId 闭包，避免「切项目」与「拉项目列表」交叉时拿到过期快照
      setProjectId((cur) => {
        if (cur && j.projects.some((p) => p.id === cur)) return cur;
        const saved = window.localStorage.getItem(PROJECT_STORAGE_KEY) || "";
        const hit = j.projects.find((p) => p.id === saved) || j.projects[0];
        return hit ? hit.id : "";
      });
      setProjectsLoaded(true);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
    }
  }, [toast]);

  useEffect(() => { void reloadProjects(); }, [reloadProjects]);

  // 级联：智能体下拉只给「当前项目启用的」∩「可见目录里的」，顺序按 caps 原序（两套序会漂，认一份）
  const agentOptions = useMemo(() => {
    const proj = projects.find((p) => p.id === projectId);
    if (!proj) return agents;
    const allowed = new Set(proj.agents);
    return agents.filter((a) => allowed.has(a.id));
  }, [agents, projects, projectId]);

  useEffect(() => {
    if (!agentOptions.length) {
      if (agentId) setAgentId("");
      return;
    }
    if (!agentOptions.some((a) => a.id === agentId)) setAgentId(agentOptions[0].id);
  }, [agentOptions, agentId]);
```

`reloadSessions` 与 `send` 跟上项目参数（`deps` 加 `projectId` → 切项目自动重拉整表，`listSeq` 的 stale 护栏已在那儿）：

```tsx
  const reloadSessions = useCallback(async () => {
    if (!agentId || !projectId) return [];
    const seq = ++listSeq.current;
    try {
      const j = await getSessions(agentId, projectId);
      ...
  }, [agentId, projectId, toast]);

      const resp = await chatSend(activeId ?? "", text, agentId, projectId);
  ...
  }, [activeId, agentId, guard, input, projectId, reloadSessions]);
```

`send` 的空值守卫跟着收紧：`if (!text || !guard() || !agentId || !projectId) return;`（交集为空时 `agentId` 为空串，发不出去，出路见 Task 11 的提示行）。

- [ ] **Step 4: 切项目处理 + 无项目引导态**

`onAgentChange` 旁边加（语义与切智能体同构：guard → 清列表 → 回欢迎态）：

```tsx
  const onProjectChange = useCallback((id: string) => {
    if (!guard()) return;
    window.localStorage.setItem(PROJECT_STORAGE_KEY, id);
    setProjectId(id);
    setSessions([]);
    void openSession(null);
  }, [guard, openSession]);
```

在 `if (healthError) { ... }` 之后、主 `return` 之前插入引导态分支（`projectsLoaded` 必须先为真，否则首帧会闪一下「还没有项目」）：

```tsx
  if (projectsLoaded && !projects.length) {
    return (
      <div className="page">
        <p className="p-empty">
          📁 还没有项目<br />
          请先到项目页添加需求文档所在目录，智能体就在那里读写文件
          {" "}<button className="mini-btn" onClick={() => navigate("/projects")}>去项目页</button>
          {" "}<button className="mini-btn" onClick={retryAll}>重试</button>
        </p>
        {toastEl}
      </div>
    );
  }
```

- [ ] **Step 5: 把 props 递下去**

```tsx
      <SessionPane
        projects={projects}
        projectId={projectId}
        agents={agentOptions}
        agentId={agentId}
        onProjectChange={onProjectChange}
        ...（其余 props 原样）
      />
```

`MessageList` 加 `projectName={projects.find((p) => p.id === projectId)?.name ?? ""}`；`Composer` 同样加 `projectName`。`const agent = agentOptions.find((a) => a.id === agentId);`（原 `:203` 用 `agents`，级联后要认 `agentOptions`）。

- [ ] **Step 6: 类型门禁**

Run: `cd frontend && npm run build`
Expected: **FAIL** —— `SessionPane`/`MessageList`/`Composer` 尚无这些 props（`TS2322` 未知属性）。Task 11 补齐后即绿

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/ChatPage.tsx
git commit -m "feat(chat-ui): 项目加载与项目→智能体级联，选择态存 localStorage，无项目走引导态"
```

---

### Task 11: 侧栏「当前项目」card、欢迎副行与 composer 橙 chip

**Files:**
- Modify: `frontend/src/pages/chat/SessionPane.tsx`（props `:3-18`、侧栏头 `:25-46`、空态 `:58-63`）
- Modify: `frontend/src/pages/chat/MessageList.tsx`（welcome 段 `:48-54`）
- Modify: `frontend/src/pages/chat/Composer.tsx`（bar `:51-60`）
- Test: `npm run build` 门禁 + 真机走查

**Interfaces:**
- Consumes: Task 10 的 props
- Produces: 无（叶子组件）

- [ ] **Step 1: SessionPane props 与 card**

props 接口加三行（`agents` 语义不变，但 ChatPage 传的是 `agentOptions`）：

```tsx
import type { AgentInfo, ChatSession, Project } from "../../api/client";

interface Props {
  projects: Project[];
  projectId: string;
  onProjectChange: (id: string) => void;
  agents: AgentInfo[];
  ...（其余原样）
}

const selectStyle = {
  width: "100%", border: "none", background: "transparent", font: "inherit", fontWeight: 700,
} as const;
```

`aside` 内第一个 `.ctx-card` 之前插入项目 card，并把智能体 card 的 `style={{…}}` 换成 `style={selectStyle}`（两张 card 共用一处定义，这是「同形控件同一套形态」的落点）：

```tsx
      <div className="ctx-card">
        <div className="ctx-label">当前项目 ({p.projects.length})</div>
        {/* busy 期间禁切项目：与切智能体同判据，半途切换会让 activeId 与列表错位 */}
        <select
          className="ctx-value"
          style={selectStyle}
          value={p.projectId}
          disabled={p.busy}
          onChange={(e) => p.onProjectChange(e.target.value)}
          title="切换项目会刷新会话列表"
        >
          {p.projects.map((x) => (
            <option key={x.id} value={x.id}>{x.name}</option>
          ))}
        </select>
      </div>
```

`:20` 那句「「当前项目」card 整块不渲染（第 2 片才接项目维度）」的注释**必须删掉**——它就是本片刻意兑现的东西。

智能体 card 下方补交集为空的出路（不是死提示：告诉用户去哪儿改）：

```tsx
        {!p.agents.length && (
          <div className="empty-tip">
            该项目未启用可见智能体<br />请到项目页调整
          </div>
        )}
```

会话列表空态文案里那句「「{agent.name}」下暂无会话」保持，但改成同时认项目上下文更直观：`「{projectName} · {agentName}」下暂无会话`——需要 `const project = p.projects.find((x) => x.id === p.projectId)` 后取 `project?.name`。

- [ ] **Step 2: 欢迎副行**

`MessageList.tsx` props 加 `projectName: string;`，欢迎段（现文案 `发送消息即在此智能体开始新会话 · 会话保存在本机`）换成原型 `:1314` 的真数据版：

```tsx
          <p>📁 {p.projectName} · 发送消息即在此项目开始新会话 · 会话保存在本机</p>
```

- [ ] **Step 3: composer 橙 chip**

`Composer.tsx` props 加 `projectName: string;`，bar 内 meter 之后、蓝 perm chip 之前插入（原型 `:617` 的橙 chip 位置）：

```tsx
            {/* 只读展示：路径不进 UI（第 2 片偏离 5）。必须压掉 .c-chip 的 cursor:pointer，
                否则纯装饰 span 会伪装成可点控件——第 1 片「0 个死按钮」的同一条判据 */}
            <span className="c-chip orange" style={{ cursor: "default" }} title="智能体在此目录读写文件">
              📁 {p.projectName}
            </span>
```

`:18-20` 那段「原型橙chip 属项目维度 → 第 2 片才接，本期不渲染」的注释同步改掉。

- [ ] **Step 4: 类型门禁**

Run: `cd frontend && npm run build`
Expected: **PASS，0 error**（Task 9/10 刻意留下的两个中间态在此一并收敛）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/chat/SessionPane.tsx frontend/src/pages/chat/MessageList.tsx frontend/src/pages/chat/Composer.tsx
git commit -m "feat(chat-ui): 当前项目 card、项目级欢迎副行与只读橙 chip"
```

---

### Task 12: 项目页显示真实会话数、目录提醒与级联删除文案

**Files:**
- Modify: `frontend/src/pages/ProjectsPage.tsx`（删除确认 `:49`、目录单元格 `:97`、会话数列 `:103`）
- Test: `npm run build` 门禁 + 真机走查

**Interfaces:**
- Consumes: Task 4/9 的 `Project.session_count` / `dir_exists`

- [ ] **Step 1: 删除确认报数**

```tsx
    if (!window.confirm(
      `删除项目「${p.name}」？会连带删除 ${p.session_count} 条会话，删除后不可恢复。`
    )) return;
```

`p.session_count === 0` 时句子仍成立（「会连带删除 0 条会话」读着别扭，但比条件式两套文案更少歧义——本期取直白单一口径，评审若要润色在此处改）。

- [ ] **Step 2: 目录不可达提醒**

`:97` 的目录单元格改为（色值只复用既有的 `var(--text-2)`，不新增）：

```tsx
                  <td>
                    <span className="p-dir" title={p.dir}>{p.dir}</span>
                    {!p.dir_exists && (
                      /* 提醒而不拦截：项目页照常可编辑名称/智能体，只有发送时后端才硬拦（spec 裁定 5） */
                      <span style={{ color: "var(--text-2)" }}> · 目录当前不可访问</span>
                    )}
                  </td>
```

- [ ] **Step 3: 会话数列确认走真值**

`:103` 已是 `{p.session_count}`，无需改动——但 `Project` 类型缺 `dir_exists` 时 build 会炸，Task 9 已补；此处跑门禁即验证。

- [ ] **Step 4: 门禁 + Commit**

```bash
cd frontend && npm run build
cd .. && git add frontend/src/pages/ProjectsPage.tsx
git commit -m "feat(projects-ui): 真实会话数、目录不可达提醒与级联删除报数"
```

---

### Task 13: 文档回写、探针数据清理与全量回归

**Files:**
- Modify: `README.md`（能力段，约 `:72-75`）
- Modify: `docs/superpowers/specs/2026-10-03-chat-session-design.md`（「范围外」第 2 条那句根约束口径）
- Modify: `docs/superpowers/specs/2026-10-03-chat-project-design.md:3`（状态行）
- Delete: `backend/data/sessions/sess_993ba6df.jsonl` 与 `index.json` 里那一行（第 1 片走查探针，本片刻定即删）

**Interfaces:**
- Consumes: 本片实现结果
- Produces: 无（文档）

- [ ] **Step 1: 清掉探针会话**

`sess_993ba6df` 是第 1 片走查留下的 2 条消息，**没有 `project_id`**，按 Task 2 的读侧判据会被丢行并留 warning。与其让日志里长期挂着一条 warning，不如按计划清掉：

```bash
cd backend/data/sessions && python - <<'PY'
import json, pathlib
idx = pathlib.Path("index.json")
data = json.loads(idx.read_text(encoding="utf-8"))
before = len(data["sessions"])
data["sessions"] = [s for s in data["sessions"] if s.get("project_id")]
idx.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
for gone in before - len(data["sessions"]):
    pass
for p in pathlib.Path(".").glob("sess_*.jsonl"):
    if not any(s["id"] == p.stem for s in data["sessions"]):
        p.unlink()
        print("removed", p.name)
print("index rows:", len(data["sessions"]))
PY
```

Expected: `removed sess_993ba6df.jsonl`、`index rows: 0`。这个脚本只清**当前被丢的旧行**，不做通用重建（从 `.jsonl` 重建索引仍在第 1 片终审议程里，本片不许顺手实现）。

- [ ] **Step 2: 回写第 1 片 spec 那句过期口径**

`docs/superpowers/specs/2026-10-03-chat-session-design.md` 的「范围外」第 2 条现为：

```markdown
- **第 2 片 项目维度**：`project_id` 进 `SendRequest` 与会话键、当前项目→当前智能体级联、会话按项目作用域、文件工具 `cwd` 从 `"."`（`agent_runtime.py:77`，有测试锁定并注释「留给项目专项的缝」）换成项目 `dir` —— **该片必须先设计根约束**，否则模型可在用户填的目录里任意读写
```

改为（保留出处，把结论换成实际裁定的形态，并明确边界执法落到了哪一片）：

```markdown
- **第 2 片 项目维度**（设计见 `2026-10-03-chat-project-design.md`，本片按丁一口径落地）：`project_id` 进 `SendRequest` 与**会话归属字段**（刻意**不进**会话键——`sess_*` 已全局唯一，进键只增加 `_split` 的解析成本）、当前项目→当前智能体级联、会话按项目作用域过滤、文件工具 `cwd` 从 `"."` 换成项目 `dir`。
  原文「必须先设计根约束，否则模型可在用户填的目录里任意读写」**已作废**：在用户指定的目录里任意读写是项目语义本身，不是洞；真问题只是 `cwd="."` 让产出物落进仓库。边界执法（越界拦截、界外授权、`strict` 权限模式）另立**第 5 片**，与第 4 片流式共用长任务基础设施。
```

- [ ] **Step 3: README 能力段**

在 `:75` 那行聊天页能力之后加一行（口径与既有各条一致）：

```markdown
- 项目维度：聊天会话绑定项目并按项目隔离，智能体在该项目目录内读写文件（目录不可访问时发送即报错并指引去项目页）；越界路径拦截与授权模式尚未开放，属后续「权限与长任务」专项
```

- [ ] **Step 4: 本 spec 状态行**

`docs/superpowers/specs/2026-10-03-chat-project-design.md:3` 改为：

```markdown
日期：2026-10-03　状态：**已实现**（项目归属 + 落点；边界执法留第 5 片）
```

- [ ] **Step 5: 全量门禁**

```bash
cd backend && uv run pytest -q
cd ../frontend && npm run build
```
Expected: pytest 全绿（本片后端新增约 35 个用例，基线 379 → 415 上下）；build 0 error。

数据目录零污染：以**文件 size + mtime 双快照**为准（`git status --porcelain backend/data` 被 `.gitignore` 架空、恒空、无证明力——第 1 片已把这条口径写进计划）。

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-03-chat-session-design.md docs/superpowers/specs/2026-10-03-chat-project-design.md
git commit -m "docs(chat): 第 2 片收尾——能力段、根约束口径作废与状态回写"
```

---

## 真机走查清单（Task 13 之后，由用户本人在自起端口上发真实消息完成）

- 项目页建项目，`dir` 指向真实需求目录；填盘根（`D:/`）或家目录被拒，detail 说清原因
- 无项目时进 `/chat` 是引导态，「去项目页」真跳，建完回来自动选中
- 侧栏出现「当前项目」card，切项目 → 列表跟着换、回欢迎态、刷新后仍是该项目（localStorage）
- 智能体下拉只列该项目启用的智能体；把项目改成没有可见智能体时，出现「请到项目页调整」而不是静默空白
- 发消息 → 核对**产出文件落在项目目录里**，不再出现在 `backend/` 下（本片唯一的行为变更点）
- 重启后端：会话仍在、归属正确；项目页会话数为真值
- 把项目目录改名 → 项目页出现「目录当前不可访问」，发送报 400 且 detail 指路；历史消息仍可读
- 删项目：确认框报出 N，删完对应 `.jsonl` 消失，别的项目一条不少
- `/kb` 页发送与文件浏览一切照旧（第四参空串的回归位）
- 页面 0 处 `zhb`、0 个死按钮；两张 card 形态一致

## 计划自检结论（写完回看 spec）

- **覆盖**：裁定 0（不执法）→ 全片无 `path_guard`，Task 1 只判配置形态、Task 6 只验目录存在性，均未越界；裁定 1 → Task 6 Step 4 + Task 10 Step 4；裁定 2（不进键）→ Task 2/3，`_split` 注释在 Task 3 Step 3 明确「一位不动」；裁定 3 → Task 10 Step 1/3/4；裁定 4 → Task 2 `count_by_project` + Task 4 + Task 8 + Task 12 Step 1；裁定 5 → Task 4 `dir_exists` + Task 6 目录验真 + Task 12 Step 2；裁定 6 → Task 1；裁定 7 → Task 6 `platform` 短路 + Task 9 Step 2；裁定 8 → 全片未摘 `pwsh`/`bash`、未动 Composer 的 🛡 分支。偏离表 7 条落点：#1 → Task 13 Step 2；#2 → Task 2/3；#3 → Task 6；#4 → Task 10 Step 4；#5 → Task 11 Step 3；#6 → 本片无改动（`Composer.tsx` 的 perm 分支原样）；#7 → Task 11 Step 1 共用 `selectStyle`。
- **无占位**：全部步骤给出可执行命令与实际代码；Task 5 Step 1 的 `...` 是显式指示「前半段照搬」，并点名照搬对象，不是 TBD。
- **类型一致**：`create(session_id, agent_id, project_id, first_message)`、`list(agent_id, project_id)`、`count_by_project`、`delete_by_project`、`FileMemoryStore(store, project_id)`、`build(..., cwd=...)`、`send(session_id, message, agent_id, project_id="")`、`dangerous_root_reason`、`dir_exists` 在各任务间的命名与形参顺序一致；前端 `getSessions(agentId, projectId)`、`chatSend(sessionId, message, agentId, projectId)`、props `projects/projectId/agentOptions/onProjectChange/projectName` 前后一致。
- **两处刻意的中间态**：Task 9 Step 3 与 Task 10 Step 6 预期 build 失败，均在 Task 11 Step 4 收敛；提交说明里点明，别让「build 红过」看起来像事故。
- **顺序风险已编进任务**：Task 8 Step 1 第二条测试专门锁「项目校验失败时不得删会话」；Task 2 的读侧丢行判据先于 Task 6 开始落 `project_id`，两者之间用 Task 3 的构造注入接上，任何一步单独跑都会红，这是有意的。
