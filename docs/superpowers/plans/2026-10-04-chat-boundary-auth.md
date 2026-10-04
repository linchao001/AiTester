# 聊天页第 5 片（边界执法与授权）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给聊天页加三档权限（自由 / 只批界外 / 严格），写盘与命令执行前在 LangGraph 的 gate 节点逐条 `interrupt` 求授权，批准后续跑，刷新可复。

**Architecture:** 图拓扑由 `agent↔tools` 改为 `agent→gate→tools→agent`；gate 节点是纯判定 + 逐条 `interrupt`，工具内部零 interrupt（P5 实测：工具内部挂起会让副作用二次执行）。checkpointer 是进程级单例（跨请求存活），`thread_id = run_id`；挂起时 SSE 发 `wait` 帧后正常收尾、**不落盘**，决策与已投递前缀存进程内 `PendingRegistry`，批准后另起一条流式请求用 `Command(resume=…)` 续跑，收尾时一次性落全行。

**Tech Stack:** Python 3.10+ / FastAPI / langgraph 1.2.12（`interrupt` + `Command` + `InMemorySaver`）/ pytest；React 18 + TypeScript（fetch 手解 SSE）。

**Spec:** `docs/superpowers/specs/2026-10-04-chat-boundary-auth-design.md`（本计划的约束来源；P1~P7 实测结论与十条用户裁定都在里面，冲突时以 spec 为准）

## Global Constraints

- 默认档 `free` **零行为变化**：既有的流式事件序列、落盘口径、`/kb` 全链路逐字不变（spec 测试 11 的回归锁）。
- 执法必须是**用户可控开关**，不新增未被要求的拦截；类别只由工具 id 决定，不给 `TOOL_CATALOG` 加危险度字段（两套真相禁令）。
- 所有面向用户的 detail 一律中文、逐字对齐 spec「错误处理」表；英文异常原文不进 UI。
- 守门只有一段：`resume` 的目录守卫必须复用 `prepare` 那一个函数、同一条文案（第 2 片教训）。
- 路径解析只认一个口：`fs_tool.resolve_path`（`_resolve` 委托它）；`project_dir` 一律取 `expanduser().resolve()` 后的值，绝不用 `projects.json` 原串。
- 内存挂起（裁定 2）：不新建落盘件、不给 `SessionStore` 加改写能力，pending 与记住表只挂 `app.state`；不做鉴权、不做 TTL。
- 门禁基线：后端 **487 passed / 0 skipped** 只增不减；前端 `npm run build` **0 error**。
- 跑后端测试一律用 `cd backend && .venv/Scripts/python -m pytest …`：仓库根的 `python` 会加载一个坏掉的 zframe pytest 插件（实测）。下文各 Step 里的 `python -m pytest` 都按这条执行。
- 每个任务收尾时全量必须 **0 failed**（不许带着红进下一个任务，也不许用 `--deselect`/`skip` 蒙）；确实要到后续任务才成立的要求，用 `@pytest.mark.xfail(strict=True, reason=…)` 钉住，由那个任务删标记（strict 会把 XPASS 变成失败，删不掉就是假账）。计划里的绝对条数只是估算，以实测数为准并写进报告。
- 提交信息沿用仓库风格（`feat(chat): …` / `test(chat): …` / `refactor(chat): …`）；本地提交自由，**push 需用户另行授权**。
- 真机走查（Task 11）需要真实模型调用与实写盘，**必须当面向用户取授权**；用户的后端在 `127.0.0.1:8000`、vite 在 `[::1]:5173`，不得占用或杀掉；绝不运行 `scripts/dev.ps1`。

## 计划期裁定（spec 没细到、实现必须先定；执行者按这些走，不要另起炉灶）

| # | 裁定 | 理由 / 代价 |
|---|---|---|
| R1 | 任务数 11（spec 估 9）：后端 7 + 前端 3 + 门禁 1 | gate 拓扑、`wait` 折叠、pending 表、三端点、页面接线各要独立评审面；`client.ts` 与 `streamState.ts` 必须同任务落地（`applyEvent` 的 switch 是穷尽的，事件加一支而不加分支就 `tsc` 红），所以合成一件；只拆合，不改范围 |
| R2 | 测试用**真工具** + 观察记录计数器，不造第二个工具实现 | `FileObservationStore.mark` 是每次成功写入的唯一钩子（`write.py:65`），双执行在它面前藏不住；MagicMock 过不了 `ToolNode` 的 schema 校验 |
| R3 | gate 无改动时返回 `{"messages": []}`，绝不返回 `{}` | langgraph 的 updates 分片对返回空 dict 的节点给 `None`，现有折叠机靠形状吃饭；返回空 list 才稳定给 `{"gate": {"messages": []}}` |
| R4 | 同 id 覆盖用 `last.model_copy(update={"tool_calls": kept})` | 保 `id` / `additional_kwargs` / `usage_metadata`；手工重建（探针那样）会丢字段 |
| R5 | 记住表键加会话维度：`{agent_id}:{session_id}` 分组 + `(tool_id, 规范化目标)` | spec 裁定 4 只写后两段，但卡片文案是「本次会话内同路径不再询问」，且第 4 片「改前必读」记录就按会话隔离；不加等于 A 会话的放行解锁 B 会话。代价：跨会话同路径要重问一次（符合文案） |
| R6 | `resume` 带 `run_id` 却没有任何已登记决策 → 400 `这条回答还在等你批准` | spec 错误表未覆盖（UI 发不出这种请求）。不做「无决策续跑」，否则等于把挂起状态摊开让 langgraph 自己猜 |
| R7 | `approve` 的 `call_id` 既不在队列也未答过 → 同用 409 文案 | 两种情况对队列的影响完全一致（都不改变任何东西），不为不可能分支再造一条 detail |
| R8 | 停止优先于挂起：`finish.pending` 且 `control.cancelled` → 不落 pending，按已投递前缀落 `stopped=true` 的行并发 `done` 帧 | 覆盖「续跑途中按停止」的竞态（spec 风险节那条）；用户永远有出口，也不会留下一个 control 已摘的空 pending |
| R9 | 拒绝不进 `steps`：合成拒绝 `ToolMessage` 由 gate 产出，折叠只认 updates 的 `tools` 键 | 与第 4 片「过程行 = 真跑过的工具」同口径。代价：重开会话看不到「曾经拒绝过某条」，模型正文里会提到 |
| R10 | 走查只做 `boundary` 档，`strict` 由单测覆盖 | 走查项 8 要真模型**并行**发两个写调用，不可复现（spec 风险节自己也承认）；不为走查表造假绿 |
| R11 | `wait` 帧字段 = `run_id + call_id + tool + action + target + command + cwd` | spec 数据流里的 `args_display` 拆成 `action`/`target`：卡片要「中文动作 + 目标」，而 `DETAIL_MAX=80` 的截断口径不能用于批准前展示（命令必须全文可见） |
| R12 | 多 pending 的 UI：历史消息在上、每条 pending run 各一个 agent 气泡在下、在途流气泡在最后 | 裁定 10 允许多条并存，一条一个气泡是唯一不自相矛盾的画法；刷新后在途流必已结束，`live` 与 pending 气泡不会双画同一条 |
| R13 | 续跑收尾走 `await chatResumeStream(...)` 之后的同一段结算代码重取 pending 表，**不给 `chatSendStream` 加第 4 参回调** | 曾担心 `onEvent` 写 ref 与 `await` 之间没有可靠时序；第 4 片真机走查已证反——`send()` 正是 await 之后读 `liveRef.current` 收尾的，19 项全过。传输层保持三参一条口，不给两个函数各挂一条只有续跑用得上的参数 |
| R14 | 待批取数按「智能体 × 项目」（`view_for` / `GET /chat/pending?agent_id=&project_id=`），`PendingRunInfo` 多露 `session_id` | 挂起那一轮一个字都没落盘（裁定 8），刷新后前端只认得当前智能体与项目，那条会话在项目侧查不到；按会话取数等于让走查项 11 在最常见路径（新会话第一条就界外写）必然失败。`session_id` 露出来是给「待批期间停止」后判断要不要重拉会话正文用的。代价：同一项目下另一条会话的待批也会一起显示——语义正是「这个项目有回答在等你」 |

---

## 文件结构（先定边界，再排任务）

**后端新建**
- `orchestration/auth_rules.py` — 执法类别表 + `needs_approval` + `plan_target` + `validate_perm_mode` + `PermModeError`（纯判定层，不 import langgraph）
- `orchestration/checkpoint.py` — 进程级 `InMemorySaver` 单例 + `drop_thread` + `new_thread_id`
- `orchestration/gate.py` — `GATE_KEY` / `GateContext` / `plan_items` / `make_gate_node` / 决策与拒绝文案
- `services/pending.py` — `PendingEntry` / `PendingRegistry`（含 `view_for` 的 agent×项目取数口）/ 记住表 / 三个异常与逐字 detail

**后端修改**
- `adapters/tools/file_tools/fs_tool.py:23-27` — `_resolve` 委托模块级 `resolve_path`
- `orchestration/agent_graph.py` — checkpointer、gate 节点与条件边、`wait` 事件、`finish.pending`、`thread_id`/`gate`/`resume` 形参
- `orchestration/__init__.py` — 导出
- `services/chat.py` — `prepare` 收 `perm_mode`、抽 `_guard_project`、共用 `_fold_turn`、挂起分支、`resume_stream` / `approve` / `cancel_pending` / `pending_view_for` / `drop_session`
- `interaction/schemas.py` — `SendRequest.perm_mode` + 授权侧四个 schema
- `interaction/router.py` — `wait` 帧、`_stream_response` 抽口、三端点、`/chat/stop` 双路、`_GUARD_MAP` 两行
- `interaction/sessions.py` — 删会话级联
- `main.py` — `app.state.pending_registry` 装配

**前端新建**：`pages/chat/AuthCard.tsx`
**前端修改**：`api/client.ts`、`pages/chat/streamState.ts`、`pages/chat/MessageList.tsx`、`pages/ChatPage.tsx`、`pages/chat/Composer.tsx`、`App.css`（全项目只有这一份样式文件，聊天样式集中在 `:200-235` 与 `:546-560` 两段）

**测试新建**：`tests/test_chat_auth.py`（判定 + gate + 折叠）、`tests/test_chat_pending.py`（pending 表 + 服务层）、`tests/test_chat_auth_api.py`（端点面）。本片前端无 vitest 设施（第 4 片同口径），折叠逻辑由 `npm run build` 的类型检查 + 走查钉。

---

### Task 1: 执法判据纯函数（auth_rules + 路径解析单口）

**Files:**
- Create: `backend/src/aitester/orchestration/auth_rules.py`
- Modify: `backend/src/aitester/adapters/tools/file_tools/fs_tool.py:23-27`
- Test: `backend/tests/test_chat_auth.py`（新建；本任务只放判定矩阵那组）

**Interfaces:**
- Consumes: 无（本片第一个任务）
- Produces:
  - `resolve_path(cwd: str, file_path: str) -> Path`（`fs_tool` 模块级；`FsTool._resolve` 委托它）
  - `PERM_MODES: tuple[str, str, str]`、`DEFAULT_PERM_MODE = "free"`、`PERM_MODE_DETAIL: str`
  - `PermModeError(ValueError)` 带 `.detail`
  - `validate_perm_mode(perm_mode: str) -> str`
  - `WRITE_IDS / SHELL_IDS / KB_WRITE_IDS / READ_ONLY_IDS: tuple[str, ...]`、`tool_ids_for(perm_mode) -> tuple[str, ...]`
  - `AuthTarget(category, action, target, command, cwd, remember_key)`
  - `plan_target(tool_id: str, args: dict[str, Any], project_dir: str) -> AuthTarget | None`
  - `needs_approval(tool_id: str, args: dict[str, Any], perm_mode: str, project_dir: str, remembered: set[str]) -> bool`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_chat_auth.py`：

```python
"""第 5 片边界执法：判定矩阵、gate 决策重放、wait 折叠（按任务递进追加）。

判别性要求（spec 测试 1）：三档 × 四类别 × 界内界外 × 记住命中/未命中逐格钉死，
`free` 全 False 单独钉——默认档零行为是本片的红线。
"""

from pathlib import Path

import pytest

from aitester.adapters.tools.file_tools.fs_tool import resolve_path
from aitester.orchestration.auth_rules import (
    PERM_MODE_DETAIL,
    PERM_MODES,
    PermModeError,
    needs_approval,
    plan_target,
    tool_ids_for,
    validate_perm_mode,
)


@pytest.fixture
def project(tmp_path: Path) -> str:
    root = tmp_path / "proj"
    root.mkdir()
    return str(resolve_path(str(root), "."))


def _file(path: str) -> dict:
    return {"file_path": path, "content": "x"}


def test_free_mode_never_asks(project: str) -> None:
    for tool, args in (("write", _file("D:/tmp/a.md")), ("edit", _file("D:/tmp/a.md")),
                        ("pwsh", {"command": "del x"}), ("bash", {"command": "rm x"}),
                        ("save_to_knowledge", {"title": "t", "content": "c"})):
        assert needs_approval(tool, args, "free", project, set()) is False
    assert tool_ids_for("free") == ()


def test_boundary_write_only_out_of_bounds(project: str) -> None:
    inside = str(resolve_path(project, "cases/a.md"))
    outside = str(resolve_path(project, "../out.md"))
    assert needs_approval("write", _file(inside), "boundary", project, set()) is False
    assert needs_approval("write", _file(outside), "boundary", project, set()) is True
    assert needs_approval("edit", {"file_path": inside, "old_string": "a", "new_string": "b"},
                          "boundary", project, set()) is False
    assert needs_approval("edit", {"file_path": outside, "old_string": "a", "new_string": "b"},
                          "boundary", project, set()) is True


def test_boundary_shell_always_asks_even_with_cwd_inside(project: str) -> None:
    # 裁定 6：命令的影响范围静态不可判，且 cwd 能把工作目录改到界外 → boundary 档一律挂
    for tool in ("pwsh", "bash"):
        assert needs_approval(tool, {"command": "pytest"}, "boundary", project, set()) is True
        assert needs_approval(tool, {"command": "pytest", "cwd": project},
                              "boundary", project, set()) is True
        assert needs_approval(tool, {"command": "pytest"},
                              "boundary", project, {f"{tool}|pytest"}) is False


def test_boundary_kb_write_counts_as_in_bounds(project: str) -> None:
    assert needs_approval("save_to_knowledge", {"title": "t", "content": "c"},
                          "boundary", project, set()) is False


def test_strict_asks_every_mutation_tool(project: str) -> None:
    for tool in tool_ids_for("strict"):
        args = {"command": "ls"} if tool in ("pwsh", "bash") else (
            {"title": "t", "content": "c"} if tool == "save_to_knowledge" else _file("a.md"))
        assert needs_approval(tool, args, "strict", project, set()) is True


def test_read_only_and_unknown_tools_never_ask(project: str) -> None:
    for tool in ("read", "grep_search", "glob_search", "web_search", "knowledge_search",
                 "prepare_kb_write", "not_a_real_tool"):
        for mode in ("boundary", "strict"):
            assert needs_approval(tool, _file("D:/tmp/a.md"), mode, project, set()) is False


def test_remembered_key_blocks_next_time(project: str) -> None:
    outside = str(resolve_path(project, "../out.md"))
    plan = plan_target("write", _file(outside), project)
    assert plan is not None and plan.remember_key == f"write|{outside}"
    assert needs_approval("write", _file(outside), "boundary", project, {plan.remember_key}) is False


def test_relative_and_absolute_forms_agree(project: str) -> None:
    # spec 三档判据硬要求①：判定用 resolve 后的真实路径，与 fs_tool._resolve 同一个口
    assert plan_target("write", _file("cases/a.md"), project).target == str(Path("cases") / "a.md")
    assert plan_target("write", _file("../escape.md"), project).action == "写入项目目录外的文件"
    assert plan_target("edit", {"file_path": "../e.md", "old_string": "a", "new_string": "b"},
                       project).action == "修改项目目录外的文件"


def test_perm_mode_validation_words() -> None:
    assert PERM_MODES == ("free", "boundary", "strict")
    assert validate_perm_mode("strict") == "strict"
    assert validate_perm_mode("") == "free"                 # 缺省即自由权限（/kb 根本不发这字段）
    with pytest.raises(PermModeError) as exc:
        validate_perm_mode("yolo")
    assert exc.value.detail == PERM_MODE_DETAIL
    assert PERM_MODE_DETAIL == "无效的权限模式，请选择自由权限、只批界外或严格权限"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'aitester.orchestration.auth_rules'`

- [ ] **Step 3: `resolve_path` 落地，`_resolve` 改为委托**

`fs_tool.py`：在 `class FsTool` 之前加模块级函数，并把 `:23-27` 的方法体换成一行委托。

```python
def resolve_path(cwd: str, file_path: str) -> Path:
    """唯一路径解析口：绝对路径直通、相对路径挂 cwd、最后 resolve。

    边界执法（第 5 片）与工具执行必须走同一个口，否则会出现「判定说界内、执行落别处」。
    与历史行为逐字一致：从不展 `~`（`~` 只在 services/chat.py 装配时展开那一次）。
    """
    target = Path(file_path)
    if not target.is_absolute():
        target = Path(cwd) / target
    return target.resolve()
```

```python
    def _resolve(self, file_path: str) -> Path:
        return resolve_path(self.cwd, file_path)
```

- [ ] **Step 4: 写 `auth_rules.py`**

```python
"""边界执法判据：类别只由工具 id 决定，判定与展示全是纯函数。

纯函数是 gate 可安全重跑的前提（P5 教训：含副作用的节点体重跑会把副作用再执行一遍），
也是本片最容易实现错的地方，所以判定表、展示文案与逐字 detail 全集中在这一层。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitester.adapters.tools.file_tools.fs_tool import resolve_path

PERM_MODES: tuple[str, str, str] = ("free", "boundary", "strict")
DEFAULT_PERM_MODE = "free"

# 逐字取自 spec「错误处理」表：改一个字，单测与走查会同时对不上
PERM_MODE_DETAIL = "无效的权限模式，请选择自由权限、只批界外或严格权限"


class PermModeError(ValueError):
    """权限模式非法。路由按 400 落 `.detail`（`_GUARD_MAP` 认这个属性）。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def validate_perm_mode(perm_mode: str) -> str:
    """空串按默认档：`/kb` 与旧客户端根本不发这个字段（裁定 9）。"""
    mode = (perm_mode or DEFAULT_PERM_MODE).strip()
    if mode not in PERM_MODES:
        raise PermModeError(PERM_MODE_DETAIL)
    return mode


# 与 services/capability_config.py 的 TOOL_CATALOG 十件对齐；不新增危险度字段（两套真相禁令）
WRITE_IDS: tuple[str, ...] = ("write", "edit")
SHELL_IDS: tuple[str, ...] = ("pwsh", "bash")
KB_WRITE_IDS: tuple[str, ...] = ("save_to_knowledge",)
READ_ONLY_IDS: tuple[str, ...] = ("read", "grep_search", "glob_search", "web_search",
                                  "knowledge_search", "prepare_kb_write")


def tool_ids_for(perm_mode: str) -> tuple[str, ...]:
    """该档会过闸门的工具 id（测试与说明用；判定本身仍逐条走 `needs_approval`）。"""
    if perm_mode == "free":
        return ()
    if perm_mode == "strict":
        return WRITE_IDS + SHELL_IDS + KB_WRITE_IDS
    return WRITE_IDS + SHELL_IDS


@dataclass(frozen=True)
class AuthTarget:
    category: str        # "write" | "edit" | "shell" | "knowledge"
    action: str          # 卡片上的中文动作
    target: str          # 界内相对项目根；界外原样（解析后的绝对路径不外泄）
    command: str         # 命令全文（批准前必须看全，DETAIL_MAX 不适用）
    cwd: str             # 命令的工作目录（ShellInput.cwd，可空）
    remember_key: str    # 记住表键（R5：外层再按会话分组）


def _root(project_dir: str) -> Path:
    return Path(project_dir).resolve()


def _inside(project_dir: str, raw: str) -> bool:
    """界内判定：与展示、执行同一个解析口，resolve 后再比根。"""
    return resolve_path(project_dir, raw).is_relative_to(_root(project_dir))


def _shown(project_dir: str, resolved: Path, raw: str) -> str:
    """界内给相对项目根的展示串，界外给模型原文——KB 卡片不外泄 abs_display 的同一条口径。"""
    root = _root(project_dir)
    try:
        return str(resolved.relative_to(root))
    except ValueError:                       # 符号链接等 resolve 后仍不同根：按界外处理
        return raw


def plan_target(tool_id: str, args: dict[str, Any], project_dir: str) -> AuthTarget | None:
    """执法对象 → 卡片展示与记住所需的一切；只读类与未知工具返回 None（不拦）。"""
    payload = args or {}
    if tool_id in WRITE_IDS:
        category = "write" if tool_id == "write" else "edit"
        raw = str(payload.get("file_path") or "")
        resolved = resolve_path(project_dir, raw)
        inside = resolved.is_relative_to(_root(project_dir))
        return AuthTarget(
            category=category,
            action=f"{'写入' if category == 'write' else '修改'}项目目录"
                   f"{'内' if inside else '外'}的文件",
            target=_shown(project_dir, resolved, raw),
            command="", cwd="",
            remember_key=f"{tool_id}|{resolved}",
        )
    if tool_id in SHELL_IDS:
        command = str(payload.get("command") or "")
        cwd = str(payload.get("cwd") or "")
        return AuthTarget(category="shell", action="执行命令", target=cwd,
                          command=command, cwd=cwd, remember_key=f"{tool_id}|{command}")
    if tool_id in KB_WRITE_IDS:
        # 写入路径由服务端构造，无「界外」语义：boundary 放行、strict 挂（裁定 6 表 + 偏离 5）
        return AuthTarget(category="knowledge", action="写入知识库", target="", command="",
                          cwd="", remember_key=f"{tool_id}|{str(payload.get('title') or '')}")
    return None


def needs_approval(tool_id: str, args: dict[str, Any], perm_mode: str,
                   project_dir: str, remembered: set[str]) -> bool:
    """唯一判据口。`free` 一律 False（默认档零行为）；记住表命中一律 False。"""
    if perm_mode == "free":
        return False
    plan = plan_target(tool_id, args or {}, project_dir)
    if plan is None:
        return False
    if perm_mode == "boundary":
        # 只批界外：界内写入与知识库写入直接放行；shell 一律往下走（裁定 6）
        if plan.category == "knowledge":
            return False
        if plan.category in ("write", "edit") and _inside(
                project_dir, str((args or {}).get("file_path") or "")):
            return False
    return plan.remember_key not in remembered
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q`
Expected: `9 passed`

- [ ] **Step 6: 确认委托没砸既有工具套件**

Run: `cd backend && python -m pytest tests/test_file_tools.py tests/test_command_tools.py tests/test_file_search_tools.py -q`
Expected: 全 passed，数量与改动前一致

- [ ] **Step 7: Commit**

```bash
git add backend/src/aitester/orchestration/auth_rules.py backend/src/aitester/adapters/tools/file_tools/fs_tool.py backend/tests/test_chat_auth.py
git commit -m "feat(chat): 边界执法判据纯函数——三档类别表、界内外判定与统一解析口"
```

---

### Task 2: checkpointer 进程级单例 + `stream_graph` 的线程与 resume 通道

**Files:**
- Create: `backend/src/aitester/orchestration/checkpoint.py`
- Modify: `backend/src/aitester/orchestration/agent_graph.py:103,125`（两处 `compile()`）、`:136-154`（`stream_graph` 形参与 config）
- Modify: `backend/src/aitester/orchestration/__init__.py`
- Test: `backend/tests/test_chat_auth.py`（追加本任务三条）

**Interfaces:**
- Consumes: 无新接口依赖
- Produces:
  - `get_checkpointer() -> InMemorySaver`（进程内唯一实例）、`new_thread_id() -> str`、`drop_thread(thread_id: str) -> None`
  - `stream_graph(build, provider, tools, messages, control=None, thread_id="", resume=None)`

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_chat_auth.py` 末尾；文件顶部补 import：

```python
from langchain_core.messages import AIMessage, HumanMessage

from aitester.adapters.llm import MockProvider
from aitester.orchestration import build_agent_graph, stream_graph
from aitester.orchestration.checkpoint import drop_thread, get_checkpointer
from streaming_fakes import ChunkedStreamMixin


class ScriptProvider(ChunkedStreamMixin):
    """剧本逐条给 AIMessage；bind_tools 返回自己（与 test_agent_graph.ScriptedProvider 同形）。"""

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke_messages(self, messages):
        return self._script.pop(0) if self._script else AIMessage(content="done")


def _thread_state(graph, thread_id: str):
    """StateSnapshot 是纯 NamedTuple（实测 4.2.0：字符串下标会 TypeError），必须走 .values。"""
    return graph.get_state({"configurable": {"thread_id": thread_id}})


def test_graph_compiles_with_the_shared_checkpointer(tmp_path: Path) -> None:
    """P3：无 checkpointer 时 interrupt 静默通过 = 假执法。「装了、而且装的是单例」钉成回归锁。"""
    graph = build_agent_graph(MockProvider(), [])
    assert graph.checkpointer is get_checkpointer()      # 跨请求存活才谈得上续跑
    graph2 = build_agent_graph(MockProvider(), [])
    assert graph2.checkpointer is graph.checkpointer


def test_thread_id_isolates_two_runs(tmp_path: Path) -> None:
    """P4：thread_id 每轮新建；同会话连发两条线程的消息互不累积。"""
    graph = build_agent_graph(MockProvider(), [])
    for tid in ("runA", "runB"):
        list(stream_graph(build_agent_graph, MockProvider(), [],
                          [HumanMessage(content="生成用例")], thread_id=tid))
        assert len(_thread_state(graph, tid).values["messages"]) == 2      # human + ai
    drop_thread("runA")
    assert _thread_state(graph, "runA").next == ()                   # 摘干净：不留残断
    assert not _thread_state(graph, "runA").values                   # 删除判据：只有真删了才空
    assert len(_thread_state(graph, "runB").values["messages"]) == 2 # 另一条线程不受牵连


def test_threadless_callers_still_work() -> None:
    """带 checkpointer 后缺 thread_id 会 ValueError（实测）：历史入口必须自造线程而不是炸。"""
    events = list(stream_graph(build_agent_graph, MockProvider(), [],
                               [HumanMessage(content="生成用例")]))
    assert events[-1]["type"] == "finish"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q -k "checkpointer or thread"`
Expected: collection error — `ModuleNotFoundError: No module named 'aitester.orchestration.checkpoint'`

- [ ] **Step 3: 写 `checkpoint.py`**

```python
"""进程级 checkpointer 单例：实例每请求现装现弃，挂起状态必须跨请求活着。

装配期每次 `build_agent_graph` 都新建图，但同一个 run 的 pending→resume 是两个请求，
所以 checkpointer 绝不能 per-build——否则续跑拿到空线程，中断状态直接蒸发。
清理口是 `delete_thread`（langgraph-checkpoint 4.2.0 实测：未知 id 不报错）。
"""

from __future__ import annotations

import uuid

from langgraph.checkpoint.memory import InMemorySaver

_SAVER = InMemorySaver()


def get_checkpointer() -> InMemorySaver:
    return _SAVER


def new_thread_id() -> str:
    return uuid.uuid4().hex


def drop_thread(thread_id: str) -> None:
    """终态（落全行或截断）后释放该 run 的检查点：不删就是每轮泄漏一条线程。"""
    _SAVER.delete_thread(thread_id)
```

- [ ] **Step 4: 装 checkpointer + 线程/resume 通道**

`agent_graph.py` 顶部 import 补：

```python
from langgraph.types import Command
from aitester.orchestration.checkpoint import get_checkpointer, new_thread_id
```

`:103` 与 `:125` 两处 compile 都改为：

```python
        return plain.compile(checkpointer=get_checkpointer())
```
```python
    return graph.compile(checkpointer=get_checkpointer())
```

`stream_graph` 的签名与装配替换为（折叠循环本体本任务一行不动）：

```python
def stream_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
    control: RunControl | None = None,
    thread_id: str = "",
    resume: Any = None,
) -> Iterator[dict[str, Any]]:
    """按指定拓扑执行一轮，把执行过程实时折成事件流。

    带 checkpointer 后 stream() 必须给 thread_id（缺键直接 ValueError，实测），所以这里一律
    给值：SSE 路由给 run_id（P4：pending→resume 复用同一个），run_graph 这类历史入口给空串自造。
    resume 非 None 表示「从 gate 的中断处续跑」，此时不再投新输入——投了就变成新回合语义。
    """
    graph = build(provider, tools)
    configurable: dict[str, Any] = {"thread_id": thread_id or new_thread_id()}
    if control is not None:
        configurable[RUN_CONTROL_KEY] = control
    stream = graph.stream(
        Command(resume=resume) if resume is not None else {"messages": messages},
        config={"configurable": configurable},
        stream_mode=["custom", "updates"],
    )
```

- [ ] **Step 5: 补 `orchestration/__init__.py` 导出**

```python
from aitester.orchestration.auth_rules import needs_approval, plan_target, validate_perm_mode
from aitester.orchestration.checkpoint import drop_thread, get_checkpointer, new_thread_id
```

`__all__` 按现有字母序补 `"drop_thread" "get_checkpointer" "needs_approval" "new_thread_id" "plan_target" "validate_perm_mode"`。

- [ ] **Step 5b: 给 `tests/test_run_control.py` 的两处裸 `graph.invoke` 补 `thread_id`**

进程级 checkpointer 一装，langgraph 对每次运行强制要 `thread_id`；这两条用例不经 `stream_graph`
直接 `invoke`，会当场缺键失败（实测）。只许给 `config` 补键——两条各用互不相同、也不与
`runA`/`runB` 相撞的 id，断言本体与 `_ToolCallingProvider` 一字不动。这不是「改既有断言」，
是新拓扑要求的必填参数；红线那条只管 `test_agent_graph.py` / `test_stream_graph.py` /
`test_chat_stream.py` 三个文件。

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_run_control.py -q`
Expected: 全 passed，条数与改动前一致

- [ ] **Step 6: 跑测试 + 全量**

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q`
Expected: `12 passed`

Run: `cd backend && python -m pytest -q`
Expected: `499 passed, 0 skipped`（487 基线 + Task 1 的 9 + 本任务 3），**0 failed**。特别是 `test_agent_graph.py` / `test_stream_graph.py` / `test_chat_stream.py` 一条断言都不改就继续绿——那是 `free` 零行为变化的第一道锁。

- [ ] **Step 7: Commit**

```bash
git add backend/src/aitester/orchestration backend/tests/test_chat_auth.py
git commit -m "feat(chat): 图装进程级 checkpointer 单例，stream_graph 开线程与 resume 通道"
```

---

### Task 3: gate 节点——逐条 interrupt、同 id 覆盖、合成拒绝、条件边

**Files:**
- Create: `backend/src/aitester/orchestration/gate.py`
- Modify: `backend/src/aitester/orchestration/agent_graph.py`（`should_continue` 的 `"tools"` → `"gate"`，`:119-125` 拓扑，新增 `route_after_gate` 与 `_gate_context`）
- Modify: `backend/src/aitester/orchestration/__init__.py`
- Test: `backend/tests/test_chat_auth.py`（追加 gate 决策重放组）

**Interfaces:**
- Consumes: Task 1 的 `plan_target` / `needs_approval`；Task 2 的 checkpointer
- Produces:
  - `GATE_KEY = "aitester_gate"`、`APPROVE = "approve"`、`REJECT = "reject"`、`REJECT_PREFIX = "用户拒绝了此操作："`
  - `GateContext(perm_mode: str, project_dir: str, session_key: str, remembered: set[str])`
  - `build_gate_context(perm_mode, project_dir, session_key, remembered) -> GateContext | None`
  - `decision_from(value: Any) -> dict[str, Any]`
  - `AuthItem(call: dict, plan: AuthTarget, call_id: str)` + `AuthItem.payload`（property）+ `AuthItem.rejected_message()`
  - `plan_items(calls: list[dict], ctx: GateContext) -> list[AuthItem]`
  - `make_gate_node(lookup) -> node`，`lookup(config) -> GateContext | None`
  - `agent_graph`：`_gate_context(config) -> GateContext | None`、`route_after_gate(state) -> str`
  - `stream_graph(..., gate: GateContext | None = None)`

- [ ] **Step 1: 写失败测试（P5/P6 钉成回归锁）**

追加到 `backend/tests/test_chat_auth.py`。顶部补：

```python
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools.observation import FileObservationStore
from aitester.orchestration.gate import (
    APPROVE,
    REJECT,
    build_gate_context,
    decision_from,
    plan_items,
)
```

R2 的执行计数器与工具装配（放在测试文件 import 之后、测试之前）：

```python
class _Counting(FileObservationStore):
    """写执行计数器：只挂真工具的 mark 钩子（write.py:65 每次成功写入必过这里）。

    不造第二个工具实现——MagicMock 过不了 ToolNode 的 schema 校验，而自写假工具会绕开
    「判定与执行认同一个解析口」这条真正要验的东西。
    """

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def mark(self, session_id: str, path: str, version: str) -> None:
        self.writes.append(path)
        super().mark(session_id, path, version)


def _project_tools(tmp_path: Path, counter: _Counting) -> list:
    """cwd 落在项目目录里：界外文件就是 tmp_path 根，工具真写得动、计数器抓得到。"""
    registry = build_default_registry(
        cwd=str(tmp_path / "proj"), session_id="s1", observed=counter)
    return registry.get_many(["write"])


def _calls(*items) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"id": cid, "name": name, "args": args, "type": "tool_call"}
        for cid, name, args in items])


def _outside(name: str = "out.md") -> dict:
    return {"file_path": f"../{name}", "content": "x"}


def _gate_ctx(tmp_path: Path, perm_mode: str, remembered: set[str] | None = None) -> "GateContext":
    from aitester.orchestration.gate import GateContext
    # 传进来的集合必须原样挂上（哪怕还是空的）：记住表靠同一对象身份被 gate 回填
    return GateContext(perm_mode=perm_mode,
                       project_dir=str(resolve_path(str(tmp_path / "proj"), ".")),
                       session_key="case_design:sess_1",
                       remembered=set() if remembered is None else remembered)
```

测试本体：

```python
def test_free_context_never_touches_the_gate(tmp_path: Path) -> None:
    """默认档零行为：gate 在场但直通，工具照旧立刻执行。"""
    counter = _Counting()
    provider = ScriptProvider([_calls(("c1", "write", _outside())), AIMessage(content="完成")])
    events = list(stream_graph(build_agent_graph, provider, _project_tools(tmp_path, counter),
                               [HumanMessage(content="写界外")],
                               gate=build_gate_context("free", "anything", "k", set())))
    assert [e["type"] for e in events].count("wait") == 0
    assert counter.writes == [str(resolve_path(str(tmp_path), "out.md"))]


def test_build_gate_context_short_circuits_free_and_platform(tmp_path: Path) -> None:
    assert build_gate_context("free", str(tmp_path), "k", set()) is None
    assert build_gate_context("strict", "", "k", set()) is None       # 平台智能体无项目落点


def test_boundary_out_of_bounds_waits_before_executing(tmp_path: Path) -> None:
    counter = _Counting()
    provider = ScriptProvider([_calls(("c1", "write", _outside()))])
    events = list(stream_graph(build_agent_graph, provider, _project_tools(tmp_path, counter),
                               [HumanMessage(content="写界外")],
                               thread_id="w1", gate=_gate_ctx(tmp_path, "boundary")))
    assert counter.writes == []                                  # 走查项 4 的单测锁：挂起即零副作用
    wait = [e for e in events if e["type"] == "wait"]
    assert len(wait) == 1 and wait[0]["call_id"] == "c1"
    assert wait[0]["tool"] == "write"
    assert wait[0]["action"] == "写入项目目录外的文件"


def test_two_parallel_calls_execute_exactly_once_each(tmp_path: Path) -> None:
    """P5 否决形态的正面锁：两个都批 → 真执行恰为两次，gate 重跑不重复副作用。"""
    counter = _Counting()
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("a.md")), ("c2", "write", _outside("b.md"))),
        AIMessage(content="两个都写了"),
    ])
    ctx = _gate_ctx(tmp_path, "strict")
    args = dict(build=build_agent_graph, provider=provider,
                tools=_project_tools(tmp_path, counter),
                messages=[HumanMessage(content="并行写两个")], thread_id="r2", gate=ctx)
    first = list(stream_graph(**args))
    assert [e["call_id"] for e in first if e["type"] == "wait"] == ["c1"]
    assert counter.writes == []                                  # 批第一条之后仍未开跑（spec 风险节）
    second = list(stream_graph(resume=decision_from({"decision": APPROVE}), **args))
    assert [e["call_id"] for e in second if e["type"] == "wait"] == ["c2"]
    assert counter.writes == []
    third = list(stream_graph(resume=decision_from({"decision": APPROVE}), **args))
    assert counter.writes == [str(resolve_path(str(tmp_path), "a.md")),
                              str(resolve_path(str(tmp_path), "b.md"))]        # 各恰好一次
    assert third[-1]["reply"] == "两个都写了"


def test_reject_keeps_the_round_alive_and_asks_the_model(tmp_path: Path) -> None:
    """裁定 3：拒绝单条、本轮继续。被拒的不执行，模型收到中文拒绝结果后接着作答。"""
    counter = _Counting()
    provider = ScriptProvider([_calls(("c1", "write", _outside())), AIMessage(content="好的，我不写了")])
    args = dict(build=build_agent_graph, provider=provider,
                tools=_project_tools(tmp_path, counter),
                messages=[HumanMessage(content="写文件")], thread_id="r3",
                gate=_gate_ctx(tmp_path, "strict"))
    list(stream_graph(**args))
    out = list(stream_graph(resume=decision_from({"decision": REJECT}), **args))
    assert counter.writes == []
    assert out[-1]["reply"] == "好的，我不写了"
    assert out[-1]["tool_traces"] == []                          # R9：拒绝不进过程行


def test_rejected_call_is_rewritten_out_of_the_pending_list(tmp_path: Path) -> None:
    """P6 的两条硬形状：同 id 覆盖那条 AIMessage（tool_calls 只剩批准的）；合成拒绝三字段对齐。"""
    from langchain_core.messages import ToolMessage
    counter = _Counting()
    # 一条批准 + 一条拒绝：批准的 c2 真跑，拒绝的 c1 被剔出清单并收到 error
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("bad.md")), ("c2", "write", _outside("good.md"))),
        AIMessage(content="一个写了一个没写"),
    ])
    ctx = _gate_ctx(tmp_path, "strict")
    tools = _project_tools(tmp_path, counter)
    # 另装一个图只为了读状态：checkpointer 是进程级单例（Task 2），同 thread_id 读得到同一份
    graph = build_agent_graph(provider, tools)
    args = dict(build=build_agent_graph, provider=provider, tools=tools,
                messages=[HumanMessage(content="并行两个")], thread_id="r7", gate=ctx)
    list(stream_graph(**args))                                   # 挂起在 c1
    list(stream_graph(resume=decision_from({"decision": REJECT}), **args))    # 拒 c1 → 挂起在 c2
    list(stream_graph(resume=decision_from({"decision": APPROVE}), **args))   # 批 c2 → 执行
    msgs = _thread_state(graph, "r7").values["messages"]
    original = msgs[1]                                           # 脚本第一轮那条 AIMessage
    assert isinstance(original, AIMessage)
    rewritten = [m for m in msgs if isinstance(m, AIMessage) and m.id == original.id]
    assert len(rewritten) == 1                                   # 同 id 覆盖，不是新追加一条
    assert [c["id"] for c in rewritten[0].tool_calls] == ["c2"]  # 只留批准的
    # 批准的 c2 执行后也有 ToolMessage（成功结果）：合成拒绝按 status=="error" 筛，
    # 与 spec 测试 3「拒绝的形状」的判据一致。
    rejected = [m for m in msgs if isinstance(m, ToolMessage) and m.status == "error"]
    assert [m.tool_call_id for m in rejected] == ["c1"]
    assert rejected[0].status == "error" and rejected[0].name == "write"
    assert rejected[0].content.startswith("用户拒绝了此操作：")
    assert counter.writes == [str(resolve_path(str(tmp_path), "good.md"))]


def test_remember_lands_only_after_the_whole_loop(tmp_path: Path) -> None:
    """位置匹配的下标钉死：中途落记住表会让后一条继承前一条的决策（Critical 回归锁）。"""
    from langchain_core.messages import ToolMessage
    counter = _Counting()
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("a.md")), ("c2", "write", _outside("b.md"))),
        AIMessage(content="一个记住一个拒绝"),
    ])
    ctx = _gate_ctx(tmp_path, "strict")
    tools = _project_tools(tmp_path, counter)
    graph = build_agent_graph(provider, tools)
    args = dict(build=build_agent_graph, provider=provider, tools=tools,
                messages=[HumanMessage(content="并行两个")], thread_id="r9", gate=ctx)
    list(stream_graph(**args))                              # 挂起在 c1
    list(stream_graph(resume=decision_from({"decision": APPROVE, "remember": True}), **args))
    assert counter.writes == []                              # 批 c1 并记住 → 挂起在 c2，一条都没执行
    list(stream_graph(resume=decision_from({"decision": REJECT}), **args))
    msgs = _thread_state(graph, "r9").values["messages"]
    rejected = [m for m in msgs if isinstance(m, ToolMessage) and m.status == "error"]
    assert [m.tool_call_id for m in rejected] == ["c2"]      # 拒绝必须落在 c2 自己头上
    assert counter.writes == [str(resolve_path(str(tmp_path), "a.md"))]   # b.md 一个字都没写
    assert ctx.remembered == {plan_target("write", _outside("a.md"),
                                          ctx.project_dir).remember_key}


def test_all_rejected_routes_back_to_agent(tmp_path: Path) -> None:
    """全拒不经过 tools 节点：断言路由函数本身，不吃 ToolNode 拿到空清单时「恰好不报错」的巧合。"""
    from aitester.orchestration.agent_graph import route_after_gate
    assert route_after_gate({"messages": [AIMessage(content="", tool_calls=[])]}) == "agent"
    call = {"id": "c1", "name": "write", "args": {}, "type": "tool_call"}
    assert route_after_gate({"messages": [AIMessage(content="", tool_calls=[call])]}) == "tools"


def test_remembered_set_reaps_the_next_identical_call(tmp_path: Path) -> None:
    """裁定 4：勾选记住后同一路径不再问，且记住的是规范化后的绝对路径"""
    counter = _Counting()
    remembered: set[str] = set()
    provider = ScriptProvider([
        _calls(("c1", "write", _outside("a.md"))),
        _calls(("c2", "write", _outside("a.md"))),
        AIMessage(content="好了"),
    ])
    ctx = _gate_ctx(tmp_path, "strict", remembered)
    tools = _project_tools(tmp_path, counter)
    list(stream_graph(build_agent_graph, provider, tools,
                      [HumanMessage(content="先写")], thread_id="r4a", gate=ctx))
    list(stream_graph(build_agent_graph, provider, tools, [HumanMessage(content="先写")],
                      thread_id="r4a", gate=ctx,
                      resume=decision_from({"decision": APPROVE, "remember": True})))
    assert remembered == {f"write|{resolve_path(str(tmp_path / 'proj'), '../a.md')}"}
    tail = list(stream_graph(build_agent_graph, provider, tools,
                             [HumanMessage(content="再写同一文件")], thread_id="r4b", gate=ctx))
    assert [e["type"] for e in tail].count("wait") == 0          # 新线程也不问了
    assert counter.writes.count(str(resolve_path(str(tmp_path), "a.md"))) == 2


def test_decision_from_rejects_garbage(tmp_path: Path) -> None:
    from aitester.orchestration.gate import GateAuthError
    assert decision_from({"decision": "approve", "remember": None}) == {"decision": "approve", "remember": False}
    for bad in (None, "approve", {"decision": "yes"}, {}):
        with pytest.raises(GateAuthError):
            decision_from(bad)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q -k "gate or reject or parallel or remembered or boundary or free_context or decision"`
Expected: collection error — `ModuleNotFoundError: No module named 'aitester.orchestration.gate'`

- [ ] **Step 3: 写 `gate.py`（最终形态，一次写对）**

```python
"""授权 gate：纯判定 + 逐条 interrupt，工具内部零挂起（P5 的教训）。

节点体会被 langgraph 在每次 resume 时从头重跑，已答过的 interrupt 直接返回缓存值，
所以除了往 remembered 集合加键之外，这里不得有任何副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from aitester.orchestration.auth_rules import AuthTarget, needs_approval, plan_target

# 进 config.configurable 的键：与 RUN_CONTROL_KEY 同一条注入通道（GraphBuilder 签名不许多带参数）
GATE_KEY = "aitester_gate"

APPROVE = "approve"
REJECT = "reject"
REJECT_PREFIX = "用户拒绝了此操作："


class GateAuthError(ValueError):
    """决策值非法 = 装配 bug：不外泄原文，路由按固定中文处理。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class GateContext:
    perm_mode: str
    project_dir: str          # 已 expanduser 再 resolve 的字符串（与工具 cwd 认同同一个展开）
    session_key: str          # f"{agent_id}:{session_id}"：记住表的分组键（R5）
    remembered: set[str] = field(default_factory=set)


@dataclass
class AuthItem:
    call: dict[str, Any]
    plan: AuthTarget
    call_id: str

    @property
    def tool_id(self) -> str:
        return str(self.call.get("name") or "")

    @property
    def payload(self) -> dict[str, Any]:
        """`wait` 事件载荷 = 卡片所需全部（R11：命令全文，不适用 DETAIL_MAX）。"""
        return {"type": "wait", "call_id": self.call_id, "tool": self.tool_id,
                "action": self.plan.action, "target": self.plan.target,
                "command": self.plan.command, "cwd": self.plan.cwd}

    def rejected_message(self) -> ToolMessage:
        shown = self.plan.target or self.plan.command
        return ToolMessage(
            content=f"{REJECT_PREFIX}{self.plan.action} {shown}".strip(),
            tool_call_id=self.call_id, name=self.tool_id, status="error")


def build_gate_context(perm_mode: str, project_dir: str, session_key: str,
                       remembered: set[str]) -> GateContext | None:
    """free 档与平台智能体（无项目落点）都是 None：节点整段直通，零行为变化。"""
    if perm_mode == "free" or not project_dir:
        return None
    return GateContext(perm_mode=perm_mode, project_dir=project_dir,
                       session_key=session_key, remembered=remembered)


def decision_from(value: Any) -> dict[str, Any]:
    """`interrupt()` 的返回值 → 决策。只认两个字面值，其余一律响亮失败。"""
    if not isinstance(value, dict):
        raise GateAuthError("授权决策格式不正确")
    decision = str(value.get("decision") or "")
    if decision not in (APPROVE, REJECT):
        raise GateAuthError("授权决策格式不正确")
    return {"decision": decision, "remember": bool(value.get("remember"))}


def plan_items(calls: list[dict[str, Any]], ctx: GateContext) -> list[AuthItem]:
    """纯判定：这一批调用里哪些要过闸门（保持数组顺序 = interrupt 的逐个挂起顺序）。"""
    out: list[AuthItem] = []
    for call in calls:
        tool_id = str(call.get("name") or "")
        if plan_target(tool_id, call.get("args") or {}, ctx.project_dir) is None:
            continue
        if not needs_approval(tool_id, call.get("args") or {}, ctx.perm_mode,
                              ctx.project_dir, ctx.remembered):
            continue
        out.append(AuthItem(call=call,
                            plan=plan_target(tool_id, call.get("args") or {}, ctx.project_dir),
                            call_id=str(call.get("id") or "")))
    return out


def make_gate_node(lookup: Callable[[RunnableConfig | None], GateContext | None]):
    """返回 gate 节点体。spec「架构与拦截点」的三件事一气呵成，不拆函数：
    逐条判定 → 逐条 interrupt → 按决策改写清单 + 合成拒绝。
    """

    def gate_node(state: dict, config: RunnableConfig) -> dict[str, list]:
        ctx = lookup(config)
        if ctx is None:
            return {"messages": []}                       # R3：空 list，绝不返回 {}
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return {"messages": []}
        items = plan_items(list(last.tool_calls), ctx)
        if not items:
            return {"messages": []}
        rejected_ids: set[str] = set()
        remembered_keys: list[str] = []
        for item in items:
            decision = decision_from(interrupt(item.payload))
            if decision["decision"] == APPROVE:
                if decision["remember"]:
                    remembered_keys.append(item.plan.remember_key)
            else:
                rejected_ids.add(item.call_id)
        # 唯一允许的副作用（同键同值覆盖幂等），但必须等整条循环跑完才落表：
        # langgraph 的续跑值按「第几次挂起」位置匹配（实测 scratchpad.resume[idx]），
        # 中途改 remembered 会让下一次重跑的 plan_items 变短，后面的调用继承前一条的决策。
        ctx.remembered.update(remembered_keys)
        if not rejected_ids:
            return {"messages": []}
        kept = [c for c in last.tool_calls if str(c.get("id")) not in rejected_ids]
        replaced = last.model_copy(update={"tool_calls": kept})   # R4：同 id 覆盖
        return {"messages": [replaced,
                             *[i.rejected_message() for i in items if i.call_id in rejected_ids]]}

    return gate_node
```

- [ ] **Step 4: 接进 `agent_graph`**

顶部 import 补：

```python
from aitester.orchestration.gate import GATE_KEY, GateContext, make_gate_node
```

模块级新增（放在 `_run_control` 之后，与它同族）：

```python
def _gate_context(config: RunnableConfig | None) -> GateContext | None:
    """从注入的 RunnableConfig 取执法上下文；没注入 = 不执法（echo、单测直调、free 档）。"""
    if not config:
        return None
    return (config.get("configurable") or {}).get(GATE_KEY)


def route_after_gate(state: AgentState) -> str:
    """gate 之后：还有批准的就执行，一条不剩的就回模型。

    显式边走全拒（P6 场景 3 实测 ToolNode 空跑不抛，但那是未承诺行为，不能当语义用）。
    同 id 覆盖是**就地替换**，合成的拒绝 ToolMessage 落在被改写 AIMessage 之后（实测），
    所以判据取最后一条 AIMessage——与 ToolNode 自己的取消息口径一致（实测它反向找 AIMessage），
    批准的那条才能真的开跑。
    """
    last = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
    if last is not None and last.tool_calls:
        return "tools"
    return "agent"
```

拓扑（`:113-125`）改为：

```python
    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "gate"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("gate", make_gate_node(_gate_context))
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"gate": "gate", END: END})
    graph.add_conditional_edges("gate", route_after_gate, {"tools": "tools", "agent": "agent"})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=get_checkpointer())
```

模块 docstring 第一行同步：`"""工具感知 Agent 图：agent → gate → tools → agent → … → END；无工具时退化为单节点直答图。"""`

- [ ] **Step 5: `stream_graph` 把 gate 投进 config**

```python
def stream_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
    control: RunControl | None = None,
    thread_id: str = "",
    resume: Any = None,
    gate: GateContext | None = None,
) -> Iterator[dict[str, Any]]:
    ...
    configurable: dict[str, Any] = {"thread_id": thread_id or new_thread_id()}
    if control is not None:
        configurable[RUN_CONTROL_KEY] = control
    if gate is not None:
        configurable[GATE_KEY] = gate
```

- [ ] **Step 6: 跑测试**

给 `test_boundary_out_of_bounds_waits_before_executing` 与 `test_two_parallel_calls_execute_exactly_once_each` 两条挂上判别性标记——它们断言的 `wait` 事件要到 Task 4 才从 `__interrupt__` 折出来，但「挂起时零副作用」这半边现在就已经成立，标记必须 strict，Task 4 落地后 XPASS 会把它自己顶成失败，逼着删：

```python
@pytest.mark.xfail(strict=True, reason="wait 事件要到 Task 4 从 __interrupt__ 折出来；Task 4 Step 4 删掉本行")
```

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q`
Expected: `0 failed`——通过的 + 两条 `xfailed` = 本文件全部用例数。不许用 `--deselect` 或 `skip` 绕过（那等于把要求从账上抹掉）。

Run: `cd backend && python -m pytest -q`
Expected: `0 failed`——拓扑多一个节点，但 `free` 档（`gate=None`）下既有事件序列逐字不变，`test_stream_graph.py` / `test_chat_stream.py` 不许改断言。总数按实测报（估算：Task 2 后 499 + 本任务新增数）。

- [ ] **Step 7: Commit**

```bash
git add backend/src/aitester/orchestration backend/tests/test_chat_auth.py
git commit -m "feat(chat): gate 节点——逐条 interrupt、同 id 覆盖待执行清单、合成拒绝结果"
```

---

### Task 4: `wait` 事件折叠 + `finish.pending`

**Files:**
- Modify: `backend/src/aitester/orchestration/agent_graph.py`（updates 支与末帧）
- Modify: `backend/src/aitester/orchestration/__init__.py`（无新名字，仅在需要时补导出）
- Test: `backend/tests/test_chat_auth.py`（整文件跑绿 + 两条新测试）

**Interfaces:**
- Consumes: Task 3 的 gate
- Produces:
  - 事件表多一种：`{"type": "wait", "call_id", "tool", "action", "target", "command", "cwd"}`
  - `finish` 帧多一键：`pending: bool`
  - `run_graph` / `svc.send` 的返回形状不变

- [ ] **Step 1: 写失败测试**

```python
def test_wait_event_strict_keys_and_pending_finish(tmp_path: Path) -> None:
    counter = _Counting()
    provider = ScriptProvider([_calls(("c1", "pwsh", {"command": "pytest -q", "cwd": "D:/elsewhere"}))])
    ctx = _gate_ctx(tmp_path, "boundary")
    shell = build_default_registry(cwd=str(tmp_path / "proj"), session_id="s1",
                                   observed=counter).get_many(["pwsh"])
    events = list(stream_graph(build_agent_graph, provider, shell,
                               [HumanMessage(content="跑命令")], thread_id="w2", gate=ctx))
    wait = [e for e in events if e["type"] == "wait"][0]
    assert sorted(wait) == sorted(["type", "call_id", "tool", "action", "target",
                                   "command", "cwd"])
    assert wait["call_id"] == "c1" and wait["tool"] == "pwsh"
    assert wait["command"] == "pytest -q"                  # 批准前必须看全
    assert wait["cwd"] == "D:/elsewhere"
    assert events[-1]["pending"] is True


def test_run_graph_shell_shape_unchanged(tmp_path: Path) -> None:
    from aitester.orchestration.agent_graph import run_graph
    out = run_graph(build_agent_graph, ScriptProvider([AIMessage(content="直答")]), [],
                    [HumanMessage(content="生成用例")])
    assert set(out) == {"reply", "tool_traces", "drafts"}     # 壳形状一字不动
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q -k "wait_event or run_graph_shell"`
Expected: FAIL — `IndexError: list index out of range`（没有 `wait` 事件）/ `KeyError: 'pending'`

- [ ] **Step 3: 折叠 `__interrupt__` 分片**

`stream_graph` 循环前初始化 `pending = False`；updates 支里 `produced = payload.get("tools") or {}` **之前**插入：

```python
        hits = payload.get("__interrupt__")
        if hits:
            # P1：挂起以 updates 分片多一个 __interrupt__ 键出现，流干净结束、不抛异常。
            # 载荷由 gate 备齐，这里严格取键：缺字段即 KeyError，正是 spec 测试 7 要的响亮失败。
            for hit in hits:
                value = hit.value
                yield {"type": "wait", "call_id": value["call_id"], "tool": value["tool"],
                       "action": value["action"], "target": value["target"],
                       "command": value["command"], "cwd": value["cwd"]}
            pending = True
            continue
```

末帧加一键：

```python
    yield {
        "type": "finish",
        "reply": reply,
        "tool_traces": tool_traces,
        "drafts": drafts,
        "stopped": stopped,
        "pending": pending,
    }
```

`custom` 支一行不动（`wait` 不走 custom 通道）。`stream_graph` docstring 的「末条恒为 finish」那句后面补一句：`挂起时 finish.pending=True，且它前面一定有至少一条 wait 事件。`

- [ ] **Step 4: 跑测试 + 全量**

Run: `cd backend && python -m pytest tests/test_chat_auth.py -q`
Expected: Task 1~4 全 passed——本任务 Step 4 落地后**删掉 Task 3 那两条的 `xfail(strict=True)` 标记**，让它们真跑（留着不删会被 XPASS 顶成失败）

Run: `cd backend && python -m pytest -q`
Expected: `0 failed`。若既有测试对 `finish` 帧做「等值字典」断言，按新增的 `"pending": False` 就地补一条（spec 数据流节明写这一契约变更）；除此之外一条断言不许改。

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/orchestration/agent_graph.py backend/tests
git commit -m "feat(chat): 折叠 __interrupt__ 为 wait 事件，finish 增加 pending 判据"
```

---

### Task 5: `PendingRegistry`——待批条目、决策队列、记住表、会话级联

**Files:**
- Create: `backend/src/aitester/services/pending.py`
- Test: `backend/tests/test_chat_pending.py`（新建）

**Interfaces:**
- Consumes: 无（`PreparedRun` 只在类型层引用）
- Produces:
  - `PENDING_GONE_DETAIL` / `CALL_DECIDED_DETAIL` / `RESUME_NOT_READY_DETAIL`
  - `PendingGoneError(ValueError)` `.detail` → 404、`CallDecidedError(ValueError)` → 409、`ResumeNotReadyError(ValueError)` → 400
  - `PendingEntry`（`run_id thread_id prepared perm_mode project_id project_dir session_key session_id agent_id prefix_text steps created_at queue answers consumed` + `decided` property（六键 + decision，按队列序）+ `waiting()`）
  - `PendingRegistry`：`open` / `peek` / `take` / `update_hold` / `answer` / `take_resume` / `view` / `view_for` / `remembered_for` / `thread_ids_for_session` / `drop_session`

- [ ] **Step 1: 写失败测试**

```python
"""pending 表：同会话多条并存、决策按队列顺序消费、级联清理。全是进程内内存态（裁定 2）。"""

from dataclasses import dataclass

import pytest

from aitester.services.pending import (
    CALL_DECIDED_DETAIL,
    PENDING_GONE_DETAIL,
    CallDecidedError,
    PendingEntry,
    PendingGoneError,
    PendingRegistry,
    ResumeNotReadyError,
)


@dataclass
class _Prepared:                 # 本测试不吃 PreparedRun 的真实字段，只当占位
    key: str = "case_design:sess_1"
    session_id: str = "sess_1"


def _item(call_id: str) -> dict:
    return {"call_id": call_id, "tool": "write", "action": "写入项目目录外的文件",
            "target": "../out.md", "command": "", "cwd": ""}


def _entry(run_id: str, call_ids: tuple[str, ...], session_id: str = "sess_1",
           project_id: str = "p1") -> PendingEntry:
    return PendingEntry(
        run_id=run_id, thread_id=run_id, prepared=_Prepared(), perm_mode="boundary",
        project_id=project_id, project_dir="D:/proj",
        session_key=f"case_design:{session_id}", session_id=session_id,
        agent_id="case_design", prefix_text="[moc",
        steps=[{"tool": "read", "ok": True, "round": 1, "detail": "{}"}],
        created_at=0, queue=[_item(c) for c in call_ids])


def test_multiple_pending_runs_coexist_per_session() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.open(_entry("r2", ("c2",)))
    assert [r.run_id for r in reg.view("sess_1")] == ["r1", "r2"]      # 裁定 10：新发送不隐式作废旧 pending


def test_view_for_scopes_by_agent_and_project() -> None:
    """R14：UI 取数按「智能体 × 项目」——挂起会话没落盘，刷新后只能按这一维找回来。"""
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",), session_id="sess_orphan"))
    reg.open(_entry("r2", ("c2",), project_id="p2"))
    reg.open(_entry("r3", ("c3",), session_id="sess_9"))
    assert [r.run_id for r in reg.view_for("case_design", "p1")] == ["r1", "r3"]
    assert [r.run_id for r in reg.view_for("case_design", "p2")] == ["r2"]
    assert reg.view_for("review", "p1") == []                          # 别的智能体一条不给


def test_answer_requires_known_call_id() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.answer("r1", "c1", "approve", False)
    assert reg.peek("r1").decided == [{**_item("c1"), "decision": "approve"}]
    with pytest.raises(CallDecidedError) as exc:
        reg.answer("r1", "c1", "approve", False)
    assert exc.value.detail == CALL_DECIDED_DETAIL
    with pytest.raises(CallDecidedError):
        reg.answer("r1", "cX", "approve", False)                        # R7：未知 call_id 同文案


def test_unknown_run_raises_gone() -> None:
    reg = PendingRegistry()
    with pytest.raises(PendingGoneError) as exc:
        reg.answer("nope", "c1", "approve", False)
    assert exc.value.detail == PENDING_GONE_DETAIL
    assert reg.take("nope") is None
    with pytest.raises(PendingGoneError):
        reg.take_resume("nope")                                          # 未知 run 与「还没批」是两种失败


def test_take_resume_consumes_answers_in_queue_order() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1", "c2")))
    with pytest.raises(ResumeNotReadyError):
        reg.take_resume("r1")                                            # 一条都没批就没有可喂的值（R6）
    reg.answer("r1", "c2", "reject", False)
    reg.answer("r1", "c1", "approve", True)
    assert reg.take_resume("r1") == {"call_id": "c1", "decision": "approve", "remember": True}
    assert reg.take_resume("r1") == {"call_id": "c2", "decision": "reject", "remember": False}
    with pytest.raises(ResumeNotReadyError):
        reg.take_resume("r1")


def test_update_hold_appends_new_waiting_without_losing_decided() -> None:
    reg = PendingRegistry()
    entry = _entry("r1", ("c1",))
    reg.open(entry)
    reg.answer("r1", "c1", "approve", False)
    reg.take_resume("r1")
    reg.update_hold(entry, prefix="[mock 继续", steps=[], waiting=[_item("c2"), _item("c1")])
    assert [c["call_id"] for c in reg.peek("r1").queue] == ["c1", "c2"]   # 按 call_id 去重
    assert reg.peek("r1").prefix_text == "[mock 继续"
    assert reg.peek("r1").decided == [{"call_id": "c1", "decision": "approve"}]


def test_update_hold_is_a_noop_after_removal() -> None:
    reg = PendingRegistry()
    entry = _entry("r1", ("c1",))
    reg.open(entry)
    reg.take("r1")                                                       # 已被停止摘除
    reg.update_hold(entry, prefix="x", steps=[], waiting=[_item("c2")])
    assert reg.peek("r1") is None                                        # 不复活


def test_remembered_table_is_per_session_and_live() -> None:
    reg = PendingRegistry()
    reg.remembered_for("case_design:sess_1").add("write|D:/x")
    assert reg.remembered_for("case_design:sess_1") == {"write|D:/x"}     # 同一对象：gate 写、判定读
    assert reg.remembered_for("case_design:sess_2") == set()              # R5：不跨会话解锁


def test_drop_session_clears_entries_and_remembered() -> None:
    reg = PendingRegistry()
    reg.open(_entry("r1", ("c1",)))
    reg.open(_entry("r2", ("c2",), session_id="sess_2"))
    reg.remembered_for("case_design:sess_1").add("write|D:/x")
    assert reg.drop_session("sess_1") == ["r1"]
    assert reg.peek("r1") is None and reg.peek("r2") is not None
    assert reg.view("sess_1") == []
    assert reg.remembered_for("case_design:sess_1") == set()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_chat_pending.py -q`
Expected: collection error（模块不存在）

跑绿判据里的一处改动：`test_answer_requires_known_call_id` 的 `decided` 断言由
`[{"call_id": "c1", "decision": "approve"}]` 改成 `[{**_item("c1"), "decision": "approve"}]`
（R14：已答项要连同六键一起给前端渲染痕迹，只回 `{call_id, decision}` 的卡刷新后读不出内容）。

- [ ] **Step 3: 写 `services/pending.py`**

```python
"""待批注册表：run_id → 挂起中的回合。进程内内存态，后端重启即丢（spec 裁定 2）。

与 RunRegistry 同层同风格：只挂 app.state，不落盘、不鉴权、不设 TTL（裁定 10）。
`run_id` 与 `thread_id` 全程同值（P4），续跑沿用同一个才有中断状态可接。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # 只在类型层：services.chat 反向 import 本模块
    from aitester.services.chat import PreparedRun

# 三条 detail 逐字取自 spec「错误处理」表；第四条是 R6 补的口子（spec 表未覆盖 UI 发不出的请求）
PENDING_GONE_DETAIL = "这条回答已经结束，无法再批准"
CALL_DECIDED_DETAIL = "这条授权请求已经处理过了"
RESUME_NOT_READY_DETAIL = "这条回答还在等你批准"


class PendingGoneError(ValueError):
    """条目不在表中（未知、已被停止摘除、或后端已重启）：路由按 404 落 detail。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CallDecidedError(ValueError):
    """这条 call_id 已答过（或不认识）：路由按 409 落 detail。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ResumeNotReadyError(ValueError):
    """还没有可喂给中断点的决策就要求续跑：路由按 400 落 detail。"""

    def __init__(self, detail: str = RESUME_NOT_READY_DETAIL) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class PendingEntry:
    run_id: str
    thread_id: str
    prepared: "PreparedRun"
    perm_mode: str                 # 创建时那一档（锁档）：续跑按它判定，不读请求当下的字段值
    project_id: str
    project_dir: str
    session_key: str
    session_id: str
    agent_id: str
    prefix_text: str               # 已投递正文前缀：只存内存，供刷新恢复渲染（裁定 8）
    steps: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    queue: list[dict[str, Any]] = field(default_factory=list)     # wait 事件载荷，顺序 = gate 逐条顺序
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    consumed: list[str] = field(default_factory=list)             # 已喂给 Command(resume=…) 的 call_id

    @property
    def decided(self) -> list[dict[str, Any]]:
        """已答项按队列顺序回显，六键跟着一起给（R14）：刷新后的痕迹要还能读出「批准了哪一次写入」。"""
        return [{**c, "decision": self.answers[c["call_id"]]["decision"]}
                for c in self.queue if c["call_id"] in self.answers]

    def waiting(self) -> list[dict[str, Any]]:
        return [c for c in self.queue if c["call_id"] not in self.answers]


class PendingRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, PendingEntry] = {}
        self._remembered: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def open(self, entry: PendingEntry) -> None:
        with self._lock:
            self._runs[entry.run_id] = entry

    def peek(self, run_id: str) -> PendingEntry | None:
        with self._lock:
            return self._runs.get(run_id)

    def take(self, run_id: str) -> PendingEntry | None:
        """原子摘除：停止与批准同时到达时「先摘除者胜」（spec 生命周期节）。"""
        with self._lock:
            return self._runs.pop(run_id, None)

    def update_hold(self, entry: PendingEntry, *, prefix: str,
                    steps: list[dict[str, Any]], waiting: list[dict[str, Any]]) -> None:
        """续跑又撞下一条中断：原地更新前缀、过程行与队列（按 call_id 去重）。"""
        with self._lock:
            if self._runs.get(entry.run_id) is not entry:
                return                                   # 已被摘除：本次挂起作废，绝不复活
            known = {c["call_id"] for c in entry.queue}
            entry.prefix_text = prefix
            entry.steps = list(steps)
            entry.queue.extend(c for c in waiting if c["call_id"] not in known)

    def answer(self, run_id: str, call_id: str, decision: str, remember: bool) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is None:
                raise PendingGoneError(PENDING_GONE_DETAIL)
            known = {c["call_id"] for c in entry.queue}
            if call_id not in known or call_id in entry.answers:
                raise CallDecidedError(CALL_DECIDED_DETAIL)
            entry.answers[call_id] = {"decision": decision, "remember": remember}

    def take_resume(self, run_id: str) -> dict[str, Any]:
        """按队列顺序取下一条「已答未喂」的决策，喂给 Command(resume=…)。"""
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is None:
                raise PendingGoneError(PENDING_GONE_DETAIL)
            for call in entry.queue:
                cid = call["call_id"]
                if cid in entry.answers and cid not in entry.consumed:
                    entry.consumed.append(cid)
                    return {"call_id": cid, **entry.answers[cid]}
            raise ResumeNotReadyError()

    def view(self, session_id: str) -> list[PendingEntry]:
        with self._lock:
            return sorted((e for e in self._runs.values() if e.session_id == session_id),
                          key=lambda e: e.created_at)

    def view_for(self, agent_id: str, project_id: str) -> list[PendingEntry]:
        """UI 的取数口：按「智能体 × 项目」取，不按会话（R14）。

        挂起时那条会话什么都没落盘（裁定 8），刷新后前端既不知道它的 id、项目列表里也查不到它；
        按会话查等于让「新会话第一条就界外写」的走查项 11 必然失败。
        """
        with self._lock:
            return sorted((e for e in self._runs.values()
                           if e.agent_id == agent_id and e.project_id == project_id),
                          key=lambda e: e.created_at)

    def remembered_for(self, session_key: str) -> set[str]:
        """返回活对象：gate 拿到决策后往里加，判定函数读同一个集合。"""
        with self._lock:
            return self._remembered.setdefault(session_key, set())

    def thread_ids_for_session(self, session_id: str) -> list[str]:
        with self._lock:
            return [e.thread_id for e in self._runs.values() if e.session_id == session_id]

    def drop_session(self, session_id: str) -> list[str]:
        """删会话级联（裁定 10 第三条）：返回要清的检查点 thread_id，由调用方交给 drop_thread。"""
        with self._lock:
            gone = [rid for rid, e in self._runs.items() if e.session_id == session_id]
            for rid in gone:
                del self._runs[rid]
            for key in [k for k in self._remembered if k.split(":", 1)[1] == session_id]:
                del self._remembered[key]
            return gone
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && python -m pytest tests/test_chat_pending.py -q`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/src/aitester/services/pending.py backend/tests/test_chat_pending.py
git commit -m "feat(chat): 待批注册表——决策按队列顺序消费、记住表按会话分组、删会话级联"
```

---

### Task 6: `ChatService` 接线——perm_mode、共用折叠、挂起分支、续跑与停止

**Files:**
- Modify: `backend/src/aitester/services/chat.py`（`:13` import、`:42-57` `PreparedRun`、`:63-79` 构造、`:106-188` `prepare`、`:190-266` `_persist`/`stream_turn`、`:268-304` `send`）
- Test: `backend/tests/test_chat_pending.py`（追加服务层一组）

**Interfaces:**
- Consumes: Task 1 `validate_perm_mode` / `DEFAULT_PERM_MODE`、Task 2 `new_thread_id` / `drop_thread`、Task 3 `GateContext` / `build_gate_context`、Task 4 的 `wait` 事件与 `finish.pending`、Task 5 `PendingRegistry` / `PendingEntry` / `PendingGoneError` / `PENDING_GONE_DETAIL`
- Produces:
  - `ChatService(..., pending: PendingRegistry | None = None)`，`.pending` 恒非 None
  - `PreparedRun` 多五字段：`perm_mode: str`、`agent_id: str`、`project_id: str`、`project_dir: str`、`gate: GateContext | None`
  - `prepare(session_id, message, agent_id, project_id="", perm_mode="free")`
  - `stream_turn(prepared, control=None, run_id="")`
  - `_guard_project(project_id) -> dict[str, Any]`（send/resume 共用那一段）
  - `resume_stream(run_id, control=None) -> tuple[str, Iterator[dict[str, Any]]]`
  - `approve(run_id, call_id, decision, remember) -> None`
  - `cancel_pending(run_id) -> bool`
  - `pending_view_for(agent_id, project_id) -> list[PendingEntry]`（UI 取数口，R14）
  - `drop_session(session_id) -> None`
  - `send(..., perm_mode="free")`

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_chat_pending.py`；顶部补：

```python
import shutil
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from test_chat_stream import _runtime, project  # noqa: F401  复用服务层夹具，本片不重写一遍装配
from streaming_fakes import ChunkedStreamMixin

from aitester.config import Settings
from aitester.orchestration.checkpoint import get_checkpointer
from aitester.services import ChatService
from aitester.services.pending import PENDING_GONE_DETAIL
from aitester.services.project_config import ProjectConfigError
from aitester.services.session_store import SessionStore


class _Scripted(ChunkedStreamMixin):
    """剧本用完仍要答得出：续跑后模型还要收一句，脚本见底不能炸测试。"""

    name = "scripted"
    model_ref = "scripted/model"

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def complete(self, messages: list) -> str:
        return ""

    def bind_tools(self, tools: list) -> "_Scripted":
        return self

    def invoke_messages(self, messages: list) -> AIMessage:
        return self._script.pop(0) if self._script else AIMessage(content="收到")


def _call_round(*paths: str, text: str = "") -> AIMessage:
    """write 调用轮：c1/c2… 依序编号，正文可给（停止用例要有前缀可落）。"""
    return AIMessage(content=text, tool_calls=[
        {"id": f"c{i}", "name": "write", "args": {"file_path": p, "content": "x"},
         "type": "tool_call"} for i, p in enumerate(paths, start=1)])


def _svc(tmp_path: Path, svc_proj, provider) -> ChatService:
    return ChatService(provider=provider, agent_runtime=_runtime(tmp_path),
                       sessions=SessionStore(tmp_path / "sessions"), projects=svc_proj)


def _hold(tmp_path: Path, svc_proj, pid: str, perm_mode: str, run_id: str,
          script: list[AIMessage]) -> tuple[ChatService, str]:
    """发到挂起：回服务与 session_id，pending 表里躺着一条等待的。"""
    svc = _svc(tmp_path, svc_proj, _Scripted(script))
    prepared = svc.prepare("", "写界外", "case_design", pid, perm_mode)
    list(svc.stream_turn(prepared, run_id=run_id))
    return svc, prepared.session_id
```

测试本体：

```python
def test_free_mode_never_holds(tmp_path, project) -> None:
    """默认档零行为（红线）：gate 不在场、事件流照旧收到 done、pending 表空。"""
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj, _Scripted([AIMessage(content="直答")]))
    prepared = svc.prepare("", "生成用例", "case_design", pid, "free")
    assert prepared.gate is None and prepared.perm_mode == "free"
    assert prepared.project_dir == str(Path(str(tmp_path / "reqs")).resolve())
    events = list(svc.stream_turn(prepared, run_id="rf"))
    assert events[-1]["type"] == "done"
    assert svc.pending.view(prepared.session_id) == []


def test_invalid_perm_mode_detail_verbatim(tmp_path, project) -> None:
    from aitester.orchestration.auth_rules import PERM_MODE_DETAIL, PermModeError
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj, _Scripted([AIMessage(content="直答")]))
    with pytest.raises(PermModeError) as exc:
        svc.prepare("", "hi", "case_design", pid, "yolo")
    assert exc.value.detail == PERM_MODE_DETAIL


def test_boundary_out_of_bounds_holds_without_persisting(tmp_path, project) -> None:
    """裁定 7+8：挂起即断流、一条不落；界外文件确实还没被写出去（走查项 4 的单测锁）。"""
    svc_proj, pid, root = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../escape.md"), AIMessage(content="好的")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    events = list(svc.stream_turn(prepared, run_id="r1"))
    assert [e["type"] for e in events][-1] == "wait"
    assert store.messages(prepared.session_id) == []
    assert not (tmp_path / "escape.md").exists()
    entry = svc.pending.peek("r1")
    assert entry is not None and entry.thread_id == "r1"
    assert [c["call_id"] for c in entry.queue] == ["c1"]
    assert entry.prefix_text == "" and entry.perm_mode == "boundary"


def test_approve_then_resume_persists_exactly_one_row(tmp_path, project) -> None:
    """裁定 8 的正面锁：批准续跑到收尾，磁盘只有一 user 一 assistant；线程收摊。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../escape.md"), AIMessage(content="写好了")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="r2"))
    svc.approve("r2", "c1", "approve", False)
    sid, stream = svc.resume_stream("r2")
    events = list(stream)
    assert sid == prepared.session_id
    assert events[-1]["type"] == "done" and events[-1]["reply"] == "写好了"
    assert (tmp_path / "escape.md").read_text(encoding="utf-8") == "x"
    rows = store.messages(sid)
    assert [r.role for r in rows] == ["user", "assistant"]
    assert svc.pending.peek("r2") is None
    assert get_checkpointer().get_tuple({"configurable": {"thread_id": "r2"}}) is None


def test_reject_then_resume_answers_without_writing(tmp_path, project) -> None:
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj, _Scripted([_call_round("../escape.md"), AIMessage(content="好的，不写了")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="r3"))
    svc.approve("r3", "c1", "reject", False)
    _, stream = svc.resume_stream("r3")
    done = list(stream)[-1]
    assert done["type"] == "done" and done["reply"] == "好的，不写了"
    assert not (tmp_path / "escape.md").exists()
    assert store.messages(prepared.session_id)[-1].steps == []      # R9：拒绝不进过程行


def test_second_interrupt_appends_to_the_same_entry(tmp_path, project) -> None:
    """两条待批串行挂：第二条的 wait 追加进同一条目，已答的不丢、去重按 call_id。"""
    svc_proj, pid, _ = project
    svc = _svc(tmp_path, svc_proj,
               _Scripted([_call_round("../a.md", "../b.md"), AIMessage(content="两个都写了")]))
    prepared = svc.prepare("", "并行两个", "case_design", pid, "strict")
    list(svc.stream_turn(prepared, run_id="r4"))
    svc.approve("r4", "c1", "approve", False)
    _, stream = svc.resume_stream("r4")
    assert [e["type"] for e in stream][-1] == "wait"               # 又挂一次：仍是断流收尾
    entry = svc.pending.peek("r4")
    assert [c["call_id"] for c in entry.queue] == ["c1", "c2"]
    assert entry.decided == [{"call_id": "c1", "decision": "approve"}]
    assert not (tmp_path / "a.md").exists()                        # 批准的也要等 c2 决策后才执行（spec 风险节）


def test_stop_while_pending_persists_prefix_and_kills_resume(tmp_path, project) -> None:
    """裁定 10 第二条：待批期间停止 → 落 stopped 截断行、条目摘除、再续跑必 404 文案。"""
    svc_proj, pid, _ = project
    store = SessionStore(tmp_path / "sessions")
    svc = _svc(tmp_path, svc_proj,
               _Scripted([_call_round("../escape.md", text="我先想想"), AIMessage(content="好的")]))
    prepared = svc.prepare("", "写界外", "case_design", pid, "boundary")
    list(svc.stream_turn(prepared, run_id="r5"))
    assert svc.pending.peek("r5").prefix_text == "我先想想"
    assert svc.cancel_pending("r5") is True
    assert svc.cancel_pending("r5") is False                       # 已摘除：二次停止不再落盘
    rows = store.messages(prepared.session_id)
    assert rows[-1].content == "我先想想" and rows[-1].stopped is True
    assert svc.pending.peek("r5") is None
    with pytest.raises(PendingGoneError) as exc:               # 顶部已 import（Task 5 那组用过）
        svc.resume_stream("r5")
    assert exc.value.detail == PENDING_GONE_DETAIL


def test_resume_reuses_the_same_project_guard_detail(tmp_path, project) -> None:
    """守门只有一段：挂起后删掉项目目录，续跑必被同一条中文 detail 拦下且没建出目录。"""
    svc_proj, pid, root = project
    svc, _ = _hold(tmp_path, svc_proj, pid, "boundary", "r6",
                   [_call_round("../escape.md"), AIMessage(content="好的")])
    shutil.rmtree(root)
    svc.approve("r6", "c1", "approve", False)
    with pytest.raises(ProjectConfigError) as exc:
        svc.resume_stream("r6")
    assert exc.value.detail == (
        f"项目「订单系统」的目录 {root} 不存在或不可访问，请到项目页确认路径")
    assert not root.exists()
    assert not (tmp_path / "escape.md").exists()


def test_drop_session_releases_pending_and_thread(tmp_path, project) -> None:
    """验收 10 的服务侧：删会话级联摘 pending 并释放检查点线程。"""
    svc_proj, pid, _ = project
    svc, sid = _hold(tmp_path, svc_proj, pid, "boundary", "r7",
                     [_call_round("../escape.md"), AIMessage(content="好的")])
    assert svc.pending.peek("r7") is not None
    svc.drop_session(sid)
    assert svc.pending.peek("r7") is None
    assert get_checkpointer().get_tuple({"configurable": {"thread_id": "r7"}}) is None
```

上面 `test_stop_while_pending_persists_prefix_and_kills_resume` 用到 `PendingGoneError`，与文件顶部已有的 pending import 合并成一行：

```python
from aitester.services.pending import PENDING_GONE_DETAIL, PendingGoneError
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_chat_pending.py -q -k "free_mode or perm_mode or holds or resume or reject_then or second_interrupt or stop_while or drop_session"`
Expected: FAIL — `TypeError: ChatService.prepare() got an unexpected keyword argument` / `AttributeError: 'ChatService' object has no attribute 'pending'`

- [ ] **Step 3: `PreparedRun` 与构造口**

import 段：`:13` 那一行扩成四项，其后按字母序补三行（`pending` 的 import 放在 `services.project_config` 之前，与包内字母序一致）：

```python
from aitester.orchestration import drop_thread, new_thread_id, run_echo, stream_graph
from aitester.orchestration.auth_rules import DEFAULT_PERM_MODE, validate_perm_mode
from aitester.orchestration.gate import GateContext, build_gate_context
from aitester.services.pending import (
    PENDING_GONE_DETAIL,
    PendingEntry,
    PendingGoneError,
    PendingRegistry,
)
```

两条 import 纪律：`new_thread_id` / `drop_thread` 只走 `aitester.orchestration` 包口这一条路径（Task 2 已在 `__init__` 导出），别再从 `aitester.orchestration.checkpoint` 各引一遍；`services/pending.py` 反向只在 `TYPE_CHECKING` 下引用 `PreparedRun`，所以运行期没有循环。

`PreparedRun` 在 `memory` 字段之后补五字段（顺序无关，全带默认值，`send`/既有测试零改动）：

```python
    key: str                      # 记忆键 f"{agent_id}:{session_id}"
    session_id: str
    message: str
    provider: LlmProvider
    system_prompt: str
    build: GraphBuilder
    tools: list[AiTooler]
    memory: MemoryStore
    messages: list[BaseMessage]
    perm_mode: str = DEFAULT_PERM_MODE
    agent_id: str = ""
    project_id: str = ""
    project_dir: str = ""         # expanduser 再 resolve：判定与续跑守卫都认它
    gate: GateContext | None = None
```

构造函数加末位形参：

```python
        pending: PendingRegistry | None = None,
    ) -> None:
        ...
        # 待批表：装配位注入（与 run_registry 同处 app.state），单测直调时自持一份
        self.pending = pending if pending is not None else PendingRegistry()
```

- [ ] **Step 4: `_guard_project` 抽口 + `prepare` 收 perm_mode**

`prepare` 体里那段项目校验（`:120-134`）整块换成一次调用，新函数紧挨 `prepare` 之前：

```python
    def _guard_project(self, project_id: str) -> dict[str, Any]:
        """守门只有一段：send 与 resume 复用同一函数、同一条 detail（第 2 片教训）。

        复用项目页读侧同一只探测（裁定 3）：展开 ~、绝不 mkdir、吞 (OSError, ValueError)，
        畸形 dir（NUL 走 ValueError）在此同样答「不可达」→ 中文 400，绝不外泄成 500。
        """
        pid = (project_id or "").strip()
        if not pid:
            raise ProjectConfigError("请先选择项目，再发送消息")
        if self.projects is None:
            raise ProjectConfigError("服务未装配项目配置，请通过 create_app 启动后端")
        project = self.projects.get(pid)          # 未知项目 → ConfigNotFoundError → 路由 404
        if not dir_exists(project["dir"]):
            raise ProjectConfigError(
                f"项目「{project['name']}」的目录 {project['dir']} 不存在或不可访问，"
                "请到项目页确认路径"
            )
        return project
```

`prepare` 签名与体内三处改动：

```python
    def prepare(
        self, session_id: str, message: str, agent_id: str, project_id: str = "",
        perm_mode: str = DEFAULT_PERM_MODE,
    ) -> PreparedRun:
```

```python
        mode = validate_perm_mode(perm_mode)      # 第一句：非法档位在任何副作用之前 400
        platform = is_platform_agent(agent_id)
        project: dict[str, Any] | None = None
        pid = ""
        if not platform:
            project = self._guard_project(project_id)
            pid = (project_id or "").strip()
```

`instance = self.agent_runtime.build(...)` 之后、`return PreparedRun(` 之前补：

```python
        # 判定与落点认同一个展开：dir 配成 ~/x 时两侧都看到家目录（第 2 片教训的对称面）
        project_dir = (str(Path(project["dir"]).expanduser().resolve())
                       if project is not None else "")
        key = f"{instance.agent_id}:{sid}"
```

（`key = ...` 那行原本就在 `:173`，此处只是上移到 `project_dir` 之前；`return PreparedRun(` 的实参补五项）

```python
        return PreparedRun(
            key=key,
            session_id=sid,
            message=message,
            provider=instance.provider,
            system_prompt=instance.system_prompt,
            build=instance.build_graph,
            tools=instance.tools,
            memory=memory,
            messages=messages,
            perm_mode=mode,
            agent_id=instance.agent_id,
            project_id=pid,
            project_dir=project_dir,
            gate=build_gate_context(mode, project_dir, key, self.pending.remembered_for(key)),
        )
```

- [ ] **Step 5: 共用折叠 `_fold_turn`——挂起分支、停止优先、终态收摊**

`stream_turn` 改成薄壳（docstring 原文照搬，末尾补一句 run_id 语义），新增 `_WAIT_KEYS` 常量放在 `_STEP_KEYS` 之后：

```python
# wait 事件喂给 pending 队列的字段：严格取键，缺字段即 KeyError（与 _STEP_KEYS 同口径）
_WAIT_KEYS = ("call_id", "tool", "action", "target", "command", "cwd")
```

```python
    def stream_turn(
        self, prepared: PreparedRun, control: RunControl | None = None, run_id: str = ""
    ) -> Iterator[dict[str, Any]]:
        """<迁移前 docstring 原文，一字不动>

        run_id 非空时它就是图线程 id（P4：pending→resume 复用同一个），空串则自造。
        """
        control = control or RunControl()
        thread_id = run_id or new_thread_id()
        events = stream_graph(
            prepared.build, prepared.provider, prepared.tools, prepared.messages,
            control=control, thread_id=thread_id, gate=prepared.gate,
        )
        return self._fold_turn(events, prepared, control, thread_id)

    def _fold_turn(
        self, events: Iterator[dict[str, Any]], prepared: PreparedRun,
        control: RunControl, thread_id: str, entry: PendingEntry | None = None,
    ) -> Iterator[dict[str, Any]]:
        """首回合与续跑共用的一条折叠：collect → 挂起入表 / 终态落盘 → 线程收摊。

        挂起分支 return 在 persist 之前（裁定 8：不落盘），且不发 done——流就在 wait 之后断掉
        （裁定 7）。R8：finish.pending 同时 control.cancelled 时停止优先，落 stopped 截断行。
        """
        steps: list[dict[str, Any]] = []
        visible: dict[int, str] = {}
        waiting: list[dict[str, Any]] = []
        outcome: dict[str, Any] | None = None
        try:
            for event in events:
                kind = event["type"]
                if kind == "finish":
                    outcome = event
                    continue
                if kind == "wait":
                    waiting.append({k: event[k] for k in _WAIT_KEYS})
                elif kind == "step":
                    steps.append({k: event[k] for k in _STEP_KEYS})
                elif kind == "delta":
                    r = int(event["round"])
                    visible[r] = visible.get(r, "") + str(event["text"])
                yield event
            if outcome is not None and outcome["pending"] and not control.cancelled:
                self._hold(prepared, thread_id, entry, visible, steps, waiting)
                return
            # stream_graph 保证终帧 finish：走到这里 outcome 必非空，无需兜底
            reply = str(outcome["reply"])
            stopped = bool(outcome["stopped"])
            self._persist(prepared, reply, steps, stopped)
            outcome = None               # 已落盘：done 帧后再被 close() 不得二次落盘
            self._release(thread_id, entry)
            stored = (
                self.sessions.get(prepared.session_id)
                if self.sessions is not None else None
            )
            yield {
                "type": "done",
                "reply": reply,
                "steps": steps,
                "session_id": prepared.session_id,
                "title": stored.title if stored is not None else "",
                "stopped": stopped,
            }
        except GeneratorExit:
            control.cancel()
            try:
                for event in events:      # 无人消费也要跑到停笔点，只为拿到 finish
                    if event["type"] == "step":
                        steps.append({k: event[k] for k in _STEP_KEYS})
                    elif event["type"] == "wait":
                        waiting.append({k: event[k] for k in _WAIT_KEYS})
                    elif event["type"] == "finish":
                        outcome = event
            except Exception:             # 收尾路径的失败绝不能盖掉原始断开
                logger.warning("断开收尾时图未跑完，本次不落截断盘", exc_info=True)
            if outcome is not None:
                prefix = visible[max(visible)] if visible else ""
                self._persist(prepared, prefix, steps, stopped=True)
            # 断开 == 停止（第 4 片同语义）：留下的条目一律作废，线程也不再等批准
            self._release(thread_id, entry)
            raise
```

两个小尾巴：

```python
    def _hold(self, prepared: PreparedRun, thread_id: str,
              entry: PendingEntry | None, visible: dict[int, str],
              steps: list[dict[str, Any]], waiting: list[dict[str, Any]]) -> None:
        """挂起入表：首挂开条目，续跑又撞卡就原地更新（队列按 call_id 去重）。"""
        prefix = visible[max(visible)] if visible else ""
        if entry is not None:
            self.pending.update_hold(entry, prefix=prefix, steps=steps, waiting=waiting)
            return
        self.pending.open(PendingEntry(
            run_id=thread_id, thread_id=thread_id, prepared=prepared,
            perm_mode=prepared.perm_mode, project_id=prepared.project_id,
            project_dir=prepared.project_dir, session_key=prepared.key,
            session_id=prepared.session_id, agent_id=prepared.agent_id,
            prefix_text=prefix, steps=list(steps), queue=list(waiting)))

    def _release(self, thread_id: str, entry: PendingEntry | None) -> None:
        """终态收摊：条目摘除 + 检查点线程删除。带 checkpointer 后不删就是每轮泄漏一条线程。"""
        if entry is not None:
            self.pending.take(entry.run_id)
        drop_thread(thread_id)
```

- [ ] **Step 6: 续跑、批准、停止、查看、级联**

```python
    def resume_stream(self, run_id: str,
                      control: RunControl | None = None) -> tuple[str, Iterator[dict[str, Any]]]:
        """从 gate 的中断处续跑一条已登记的待批。

        守门全部留在 HTTP 空间（第 4 片「守门同步跑」同口径）：PendingGoneError /
        ProjectConfigError / ResumeNotReadyError 都在返回迭代器之前抛出，路由据此回 404/400。
        判定按条目创建时那一档（prepared.gate 是锁档的对象），不读请求当下的字段值。
        messages 在这里只是占位：resume 投的是 Command，图从检查点续，不再吃新输入。
        """
        entry = self.pending.peek(run_id)
        if entry is None:
            raise PendingGoneError(PENDING_GONE_DETAIL)
        self._guard_project(entry.project_id)        # 同函数、同 detail：挂起期间目录可能被删
        decision = self.pending.take_resume(run_id)  # 无决策可喂 → ResumeNotReadyError（R6）
        prepared = entry.prepared
        control = control or RunControl()
        events = stream_graph(
            prepared.build, prepared.provider, prepared.tools, prepared.messages,
            control=control, thread_id=entry.thread_id, gate=prepared.gate,
            resume={"decision": decision["decision"], "remember": decision["remember"]},
        )
        return prepared.session_id, self._fold_turn(
            events, prepared, control, entry.thread_id, entry)

    def approve(self, run_id: str, call_id: str, decision: str, remember: bool) -> None:
        """只登记决策：续跑由 resume_stream 触发（批准与续跑分开，双弹层与重复提交才好收敛）。"""
        self.pending.answer(run_id, call_id, decision, remember)

    def cancel_pending(self, run_id: str) -> bool:
        """待批期间的停止（裁定 10 第二条）：原子摘除 → 以已投递前缀落 stopped 行 → 线程收摊。

        这是本片唯一一次在收尾之前落盘，与第 4 片的断开落盘同语义；摘除后 resume 必 404。
        """
        entry = self.pending.take(run_id)
        if entry is None:
            return False
        self._persist(entry.prepared, entry.prefix_text, entry.steps, True)
        drop_thread(entry.thread_id)
        return True

    def pending_view_for(self, agent_id: str, project_id: str) -> list[PendingEntry]:
        # UI 只按「智能体 × 项目」取数（R14）：挂起会话没落盘，按会话查在刷新后必然查空
        return self.pending.view_for(agent_id, project_id)

    def drop_session(self, session_id: str) -> None:
        """删会话级联：pending 条目与对应的检查点线程一起清。"""
        for thread_id in self.pending.drop_session(session_id):
            drop_thread(thread_id)
```

`send` 只加一个透传形参（默认 `free`，pending 只属于流式通道）：

```python
    def send(self, session_id: str, message: str, agent_id: str, project_id: str = "",
             perm_mode: str = DEFAULT_PERM_MODE) -> dict[str, Any]:
        prepared = self.prepare(session_id, message, agent_id, project_id, perm_mode)
```

- [ ] **Step 7: 跑测试 + 全量**

Run: `cd backend && python -m pytest tests/test_chat_pending.py -q`
Expected: `18 passed`（Task 5 的 10 条 + 本任务 8 条）

Run: `cd backend && python -m pytest -q`
Expected: `0 failed`。`test_chat_stream.py` / `test_chat_service.py` 一条断言都不许改——它们就是 `free` 零行为的服务层回归锁。

- [ ] **Step 8: Commit**

```bash
git add backend/src/aitester/services/chat.py backend/tests/test_chat_pending.py
git commit -m "feat(chat): 服务层接线——perm_mode、挂起入表、续跑复用同一段守门"
```

---

### Task 7: 端点面——wait 帧、approve / resume / pending、stop 双路、装配与级联

**Files:**
- Modify: `backend/src/aitester/interaction/schemas.py`（`:16-22` `SendRequest`，其后加授权侧四个 schema）
- Modify: `backend/src/aitester/interaction/router.py`（`:54-61` 两张守卫表、`:102-207` send 拆口、`:210-215` stop 双路、新增三端点）
- Modify: `backend/src/aitester/main.py:73-79`（pending 表装配）
- Modify: `backend/src/aitester/interaction/sessions.py:77-81`（删会话级联）
- Test: `backend/tests/test_chat_auth_api.py`（新建）

**Interfaces:**
- Consumes: Task 1 `PermModeError`、Task 5 四个异常、Task 6 `approve` / `resume_stream` / `cancel_pending` / `pending_view_for` / `drop_session`
- Produces:
  - `SendRequest.perm_mode: str = "free"`、`ApproveRequest`、`ResumeRequest`、`PendingCallInfo`、`PendingDecidedCall`、`PendingRunInfo`、`PendingResponse`
  - `POST /api/chat/approve` → 204 / 404 / 409；`POST /api/chat/resume/stream` → SSE；`GET /api/chat/pending?agent_id=&project_id=` → `PendingResponse`
  - `_stream_response(runs, run_id, session_id, turn) -> StreamingResponse`、`_http_guard(exc, mapping) -> HTTPException`、`_pending_info(entry) -> PendingRunInfo`
  - SSE 事件 `wait` 载荷 = `run_id` + 折叠给的六键

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_chat_auth_api.py`：

```python
"""授权侧的端点契约：码与 detail 逐字对齐 spec 错误表，帧序对齐 spec 数据流。

TestClient 会缓冲整段响应（第 4 片实测），所以这里只断顺序与内容，不断节奏。
"""

import shutil
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from test_chat_pending import _Scripted, _call_round  # 复用剧本 provider，不再抄第三份
from test_chat_stream_api import _NoopKbManager, _pid, _stream
from streaming_fakes import sse_frames

from aitester.config import Settings
from aitester.main import create_app
from aitester.services import ChatService


def _app(tmp_path: Path, provider=None):
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        projects_path=tmp_path / "p.json",
        sessions_dir=tmp_path / "sessions",
        settings=Settings(_env_file=None, kb_bases_dir=str(tmp_path / "bases")),
        kb_manager=_NoopKbManager(),
    )
    # 待批表必须用 app.state 那一份：端点读的是它，服务写的也必须是它（同一条内存表两个口）
    application.state.chat_service = ChatService(
        provider=provider if provider is not None else _Scripted([_call_round("../escape.md"),
                                                                  AIMessage(content="写好了")]),
        agent_runtime=application.state.agent_runtime,
        sessions=application.state.sessions,
        projects=application.state.project_config,
        pending=application.state.pending_registry,
    )
    return application


def _hold(tmp_path: Path) -> tuple[TestClient, str, str, str]:
    """发到挂起：回 client、项目 id、run_id、session_id——pending 按项目取数（R14），
    而读会话正文要 session_id，四个都得给，调用方才不必为了一条断言再建一次 app。"""
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "写界外", "agent_id": "case_design",
                              "project_id": pid, "perm_mode": "boundary"})
    return client, pid, frames[0][1]["run_id"], frames[0][1]["session_id"]


def _pending(client: TestClient, pid: str) -> list[dict]:
    """待批表按「智能体 × 项目」取数（R14）：走查用的智能体恒为 case_design。"""
    return client.get("/api/chat/pending",
                      params={"agent_id": "case_design", "project_id": pid}).json()["runs"]


def _stream_resume(client: TestClient, run_id: str) -> list[tuple[str, dict]]:
    with client.stream("POST", "/api/chat/resume/stream", json={"run_id": run_id}) as resp:
        assert resp.status_code == 200, resp.read().decode("utf-8")
        return sse_frames(resp)


def test_free_mode_emits_no_wait_frame(tmp_path: Path) -> None:
    """默认档零行为（红线）：不发 perm_mode 就等于 free，一帧 wait 都不该有。"""
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "生成用例", "agent_id": "case_design", "project_id": pid})
    kinds = [e for e, _ in frames]
    assert "wait" not in kinds and kinds[-1] == "done"


def test_invalid_perm_mode_is_400_with_zero_frames(tmp_path: Path) -> None:
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    r = client.post("/api/chat/send/stream", json={"message": "hi", "agent_id": "case_design",
                                                   "project_id": pid, "perm_mode": "yolo"})
    assert r.status_code == 400
    assert r.json()["detail"] == "无效的权限模式，请选择自由权限、只批界外或严格权限"


def test_wait_frame_then_approve_resume_to_single_done(tmp_path: Path) -> None:
    """spec 测试 8 的端到端：wait 帧带 run_id，批准后续跑收尾，磁盘只有一行 assistant。"""
    client, pid, run_id, session_id = _hold(tmp_path)
    pending = _pending(client, pid)
    assert [p["run_id"] for p in pending] == [run_id]
    assert pending[0]["waiting"][0]["call_id"] == "c1"
    assert pending[0]["waiting"][0]["tool"] == "write"
    assert pending[0]["decided"] == [] and pending[0]["perm_mode"] == "boundary"
    assert pending[0]["session_id"] == session_id              # R14：停止后要知道去重拉哪条会话
    r = client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                               "decision": "approve", "remember": False})
    assert r.status_code == 204 and r.content == b""
    events = _stream_resume(client, run_id)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "done" and kinds.count("done") == 1
    assert events[0][0] == "start" and events[0][1]["run_id"] == run_id   # 续跑沿用同一个 run_id
    rows = client.get(f"/api/chat/sessions/{session_id}/messages").json()["messages"]
    assert [m["role"] for m in rows] == ["user", "assistant"]
    assert _pending(client, pid) == []


def test_approve_unknown_run_is_404(tmp_path: Path) -> None:
    client, _, _run_id, _sid = _hold(tmp_path)
    r = client.post("/api/chat/approve", json={"run_id": "nope", "call_id": "c1",
                                               "decision": "approve", "remember": False})
    assert r.status_code == 404
    assert r.json()["detail"] == "这条回答已经结束，无法再批准"


def test_approve_twice_is_409(tmp_path: Path) -> None:
    client, _, run_id, _sid = _hold(tmp_path)
    client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                           "decision": "approve", "remember": False})
    r = client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                                "decision": "approve", "remember": False})
    assert r.status_code == 409
    assert r.json()["detail"] == "这条授权请求已经处理过了"


def test_resume_without_decision_is_400(tmp_path: Path) -> None:
    """R6：只批准不续跑是正常态，但没有任何决策就要求续跑必须被拦下。"""
    client, _, run_id, _sid = _hold(tmp_path)
    r = client.post("/api/chat/resume/stream", json={"run_id": run_id})
    assert r.status_code == 400
    assert r.json()["detail"] == "这条回答还在等你批准"


def test_resume_after_dir_deleted_keeps_one_detail_and_creates_nothing(tmp_path: Path) -> None:
    """spec 测试 8 末条：与 send 逐字同文案，且 write 的 mkdir 一次都没跑。"""
    application = _app(tmp_path)
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "写界外", "agent_id": "case_design",
                              "project_id": pid, "perm_mode": "boundary"})
    run_id = frames[0][1]["run_id"]
    root = tmp_path / "reqs"
    client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                           "decision": "approve", "remember": False})
    shutil.rmtree(root)
    r = client.post("/api/chat/resume/stream", json={"run_id": run_id})
    assert r.status_code == 400
    assert r.json()["detail"] == (
        f"项目「订单系统」的目录 {root} 不存在或不可访问，请到项目页确认路径")
    assert not root.exists()


def test_stop_during_pending_then_resume_is_404(tmp_path: Path) -> None:
    """spec 测试 9：待批期间停止 → 截断行落盘、卡消失、再续跑 404。"""
    application = _app(tmp_path, provider=_Scripted([
        _call_round("../escape.md", text="我先想想"), AIMessage(content="好的")]))
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    frames = _stream(client, {"message": "写界外", "agent_id": "case_design",
                              "project_id": pid, "perm_mode": "boundary"})
    run_id, sid = frames[0][1]["run_id"], frames[0][1]["session_id"]
    r = client.post("/api/chat/stop", json={"run_id": run_id})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert _pending(client, pid) == []
    rows = client.get(f"/api/chat/sessions/{sid}/messages").json()["messages"]
    assert rows[-1]["content"] == "我先想想" and rows[-1]["stopped"] is True
    r2 = client.post("/api/chat/resume/stream", json={"run_id": run_id})
    assert r2.status_code == 404


def test_pending_is_empty_for_a_fresh_process(tmp_path: Path) -> None:
    """裁定 2：内存挂起、重启即丢——新 app 的 pending 表必空（走查项 14 的单测锁）。"""
    client, pid, _run_id, _sid = _hold(tmp_path)
    assert _pending(client, pid) != []
    fresh = TestClient(_app(tmp_path))
    assert fresh.get("/api/chat/pending",
                   params={"agent_id": "case_design", "project_id": pid}).json()["runs"] == []


def test_delete_session_cascades_pending(tmp_path: Path) -> None:
    """spec 测试 10：删会话后 pending 表不再含其条目。

    挂起期会话行还不存在（裁定 8），所以先跑完一轮把行落出来，再发第二条并挂住，才谈得上删会话。
    """
    application = _app(tmp_path, provider=_Scripted([
        _call_round("../a.md"), AIMessage(content="第一个写了"),
        _call_round("../b.md"), AIMessage(content="第二个写了")]))
    pid = _pid(application, tmp_path)
    client = TestClient(application)
    first = _stream(client, {"message": "写界外", "agent_id": "case_design",
                             "project_id": pid, "perm_mode": "boundary"})
    run_id, sid = first[0][1]["run_id"], first[0][1]["session_id"]
    client.post("/api/chat/approve", json={"run_id": run_id, "call_id": "c1",
                                           "decision": "approve", "remember": False})
    _stream_resume(client, run_id)                 # 落全行：会话此刻才存在
    second = _stream(client, {"message": "再写一个", "agent_id": "case_design",
                              "project_id": pid, "session_id": sid, "perm_mode": "boundary"})
    assert _pending(client, pid) != []
    assert client.delete(f"/api/chat/sessions/{sid}").status_code == 204
    assert _pending(client, pid) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_chat_auth_api.py -q`
Expected: FAIL — `AttributeError: 'State' object has no attribute 'pending_registry'` / 404 on `/api/chat/approve`

- [ ] **Step 3: schemas**

`SendRequest` 末尾补一字段：

```python
class SendRequest(BaseModel):
    # 空串 = 服务端新建会话；sess_* 续写；其余形态按临时键处理（chat.py 三态判据）
    session_id: str = ""
    message: str = Field(min_length=1)
    agent_id: str = "case_design"
    # 必填判据在 service 层：schema 无从知道 is_platform_agent（平台智能体天然不属于项目）
    project_id: str = ""
    # 三档权限（第 5 片）：缺省 free = 零行为变化；合法性由 auth_rules.validate_perm_mode 判，
    # 这里不做 Literal——/kb 与旧客户端不发这字段，而中文 detail 要过路由的 _GUARD_MAP
    perm_mode: str = "free"
```

在 `StreamStopResponse` 之后补授权侧四个（`ApproveRequest.decision` 用 Literal：这里没有「不发字段」的兼容包袱，非法值让 FastAPI 422 即可）：

```python
class ApproveRequest(BaseModel):
    """只登记决策；续跑是另一条请求，两步分开才好收敛重复提交与双弹层。"""

    run_id: str
    call_id: str
    decision: Literal["approve", "reject"]
    remember: bool = False


class ResumeRequest(BaseModel):
    # 与 StreamStopRequest 同键：续跑、停止、批准三者都以 run_id 定位同一条待批
    run_id: str


class PendingCallInfo(BaseModel):
    """一张授权卡：与后端 wait 事件六键逐字对齐（R11）。"""

    call_id: str
    tool: str
    action: str
    target: str
    command: str
    cwd: str


class PendingDecidedCall(PendingCallInfo):
    """已答项：六键照给，多一个 decision——刷新后痕迹卡要能还原「批准/拒绝的是哪一次」。"""

    decision: str


class PendingRunInfo(BaseModel):
    run_id: str
    session_id: str         # 待批停止后这条会话才第一次落盘：前端按它判断要不要重拉正文
    perm_mode: str          # 锁档：卡片按挂起时那一档展示，不受用户事后切档影响
    prefix: str             # 已投递正文前缀：刷新后气泡照显示（裁定 8）
    steps: list[StepInfo]
    waiting: list[PendingCallInfo]
    decided: list[PendingDecidedCall]
    created_at: float


class PendingResponse(BaseModel):
    runs: list[PendingRunInfo]
```

顶部 import 补 `from typing import Any, Literal`（`Any` 已在）。

- [ ] **Step 4: 路由的守卫表与两个抽口**

`_GUARD_MAP` / `_GUARD_TYPES` 改为（新增两行、ProviderError 进表，`_GUARD_TYPES` 改由表推导，一条真相）：

```python
# 流开始前 = HTTP，流开始后 = error 事件（spec 错误口径表）
# 顺序即判据：子类在前（ProviderConfigError 是 ProviderError 的子类，反过来会把 400 洗成 502）
_GUARD_MAP = (
    (ConfigNotFoundError, 404),
    (ProjectConfigError, 400),
    (SessionStoreError, 404),
    (ProviderConfigError, 400),
    (PermModeError, 400),
    (ProviderError, 502),
)
_GUARD_TYPES = tuple(klass for klass, _ in _GUARD_MAP)
# 续跑独有的两条（pending 表在流前判，口径同「守门同步跑」）
_RESUME_GUARD_MAP = ((PendingGoneError, 404), (ResumeNotReadyError, 400)) + _GUARD_MAP
_RESUME_GUARD_TYPES = tuple(klass for klass, _ in _RESUME_GUARD_MAP)


def _http_guard(exc: Exception, mapping: tuple[tuple[type[Exception], int], ...]) -> HTTPException:
    """守门异常 → HTTPException：send 与 resume 共用同一个映射口（第 2 片「守门只有一段」的传输层版）。"""
    for klass, code in mapping:
        if isinstance(exc, klass):
            return HTTPException(status_code=code, detail=exc.detail)
    raise exc        # 不在表内 = 内部异常：原样抛出去，让它成为 500 而不是伪装成 4xx
```

`chat_send_stream` 的 try/except 换成一次委托（原注释随 `_http_guard` 走）：

```python
    try:
        prepared: PreparedRun = await run_in_threadpool(
            service.prepare, req.session_id, req.message, req.agent_id, req.project_id,
            req.perm_mode)
    except _GUARD_TYPES as exc:
        raise _http_guard(exc, _GUARD_MAP) from exc
```

- [ ] **Step 5: `_stream_response` 抽口（泵线程原样搬，send/resume 共用）**

把 `chat_send_stream` 里 `:104-114` 的 docstring、`:131-133` 的具名生成器注释、`:136-207` 的泵/队列/relay 三段整体搬进模块级函数：形参接 `runs`/`run_id`/`session_id`/`turn`，`prepared.session_id` 换成 `session_id`，其余一行不改；只新增 `wait` 一支。落地后的完整函数（照抄即得，注释一并搬）：

```python
def _stream_response(runs: RunRegistry, run_id: str, session_id: str,
                     turn: Iterator[dict[str, Any]]) -> StreamingResponse:
    """把一条已装配的事件流接成 SSE：守门已在调用方跑完，这里只负责推与收摊。

    这里的泵线程不是风格选择，是走查第 13 项的修复本体。原形态「同步生成器直接交给
    StreamingResponse」在客户端真断开（SPA 跳页/关页触发的 fetch abort）时会把这一回合
    整个丢掉了：Starlette 1.7 对 spec_version>=2.4 不再派监听断开的任务，同步迭代器经
    iterate_in_threadpool 包装，取消只落在「等下一次 next()」上——既不 close 生成器，
    也不给那个线程再投取消。生成器从此冻结在 yield 上：既不落截断盘也不落全量盘，
    runs.finish 永不执行（真机实测该 run 的 /chat/stop 在 165 s 后仍回 200）。
    现在 async relay 一被取消就在 finally 里置 stop，泵线程据此跳出并显式 close()，
    GeneratorExit 才真正落进 stream_turn 的断开分支（截断落盘）与 frames 的 finally。
    """
    # 内层事件生成器具名持有，且只由泵线程触碰：close() 是唯一能把 GeneratorExit
    # 准时送进 stream_turn 断开分支的通道（等 GC 回收等于不落盘）

    def frames() -> Iterator[str]:
        # Task 5 的欠条：finish 必须在每条退出路径上跑（正常收尾、error 帧、客户端断开），
        # 否则在途条目永久泄漏，/chat/stop 会对已结束的 run 恒回成功
        try:
            yield _frame("start", {"run_id": run_id, "session_id": session_id})
            try:
                for event in turn:
                    kind = event["type"]
                    if kind == "draft":
                        frame = _draft_frame(event["draft"])
                        if frame is not None:
                            yield frame
                    elif kind == "step":
                        yield _frame(kind, _step_payload(event))
                    elif kind == "wait":
                        # 折叠已严格取键（Task 4）：这里只把续跑与停止要用的 run_id 附上
                        yield _frame(kind, {**{k: v for k, v in event.items() if k != "type"},
                                            "run_id": run_id})
                    elif kind == "done":
                        yield _frame(kind, {
                            "reply": event["reply"],
                            "steps": [_step_payload(s) for s in event["steps"]],
                            "session_id": event["session_id"],
                            "title": event["title"],
                            "stopped": event["stopped"],
                        })
                    else:                                   # delta / call
                        yield _frame(kind, {k: v for k, v in event.items() if k != "type"})
            except ProviderError as exc:
                # 流中失败：HTTP 已经 200，只能走事件；detail 原样（key 已在 provider 侧打星）
                yield _frame("error", {"detail": exc.detail})
            except Exception:
                # 非 ProviderError 一律是内部异常（含装配 bug）：str(exc) 不是用户文案，
                # 只落固定中文，诊断留在 exc_info 日志（spec「detail 一律中文」）
                logger.warning("流中非 ProviderError 异常", exc_info=True)
                yield _frame("error", {"detail": "流式输出异常，本条回答未完成"})
        finally:
            runs.finish(run_id)

    q: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def post(item: str | None) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:                    # 事件循环已关（进程退出中）：丢帧即可
            pass

    def pump() -> None:
        gen = frames()
        try:
            for frame in gen:
                post(frame)
                if stop.is_set():               # 客户端已走：下一帧前停笔
                    break
        finally:
            try:
                gen.close()                     # 先让 frames 跑完自己的 finally
            finally:
                if hasattr(turn, "close"):      # 桩测试用 iter() 替身，关闭通道不是它的契约
                    turn.close()                # 再触发 stream_turn 的截断落盘
                post(None)

    async def relay() -> AsyncIterator[str]:
        try:
            while True:
                frame = await q.get()
                if frame is None:
                    return
                yield frame
        finally:
            stop.set()                          # 取消与正常收尾都经此通知泵线程

    return StreamingResponse(relay(), media_type="text/event-stream")
```

`chat_send_stream` 收口成三行式（守门 → 起 run → 交给共用通道）：

```python
@router.post("/chat/send/stream")
async def chat_send_stream(req: SendRequest, request: Request) -> StreamingResponse:
    """真实链路的唯一传输：守门同步跑，过后逐事件推流，终态恒为一条 done 或一条 error。"""
    service: ChatService = request.app.state.chat_service
    runs = request.app.state.run_registry
    try:
        prepared: PreparedRun = await run_in_threadpool(
            service.prepare, req.session_id, req.message, req.agent_id, req.project_id,
            req.perm_mode)
    except _GUARD_TYPES as exc:
        raise _http_guard(exc, _GUARD_MAP) from exc
    run_id = new_run_id()
    turn = service.stream_turn(prepared, control=runs.start(run_id), run_id=run_id)
    return _stream_response(runs, run_id, prepared.session_id, turn)
```

- [ ] **Step 6: 三端点 + stop 双路**

```python
@router.post("/chat/approve", status_code=204)
def chat_approve(req: ApproveRequest, request: Request) -> None:
    """登记一条决策：204 无体。404=条目已不在（未知/已停/已重启），409=这条已答过。"""
    service: ChatService = request.app.state.chat_service
    try:
        service.approve(req.run_id, req.call_id, req.decision, req.remember)
    except (PendingGoneError, CallDecidedError) as exc:
        code = 404 if isinstance(exc, PendingGoneError) else 409
        raise HTTPException(status_code=code, detail=exc.detail) from exc


@router.post("/chat/resume/stream")
async def chat_resume_stream(req: ResumeRequest, request: Request) -> StreamingResponse:
    """批准后续跑：守门（含目录守卫）仍在 HTTP 空间，过后走同一条泵通道。

    run_id 不新建：待批条目、图线程、停止键三者始终是同一个 id（P4），
    所以 stop 在「在途」与「待批」两种状态下都能命中同一条回答。
    """
    service: ChatService = request.app.state.chat_service
    runs = request.app.state.run_registry
    control = runs.start(req.run_id)
    try:
        session_id, turn = await run_in_threadpool(service.resume_stream, req.run_id, control)
    except _RESUME_GUARD_TYPES as exc:
        runs.finish(req.run_id)                  # 守门没过就没有在途：不留半条 run
        raise _http_guard(exc, _RESUME_GUARD_MAP) from exc
    return _stream_response(runs, req.run_id, session_id, turn)


def _pending_info(entry) -> PendingRunInfo:
    # 严格构造、不逐条容错：条目由本片服务端自己写，畸形即装配 bug（与 _step_payload 同口径）
    return PendingRunInfo(
        run_id=entry.run_id, session_id=entry.session_id,
        perm_mode=entry.perm_mode, prefix=entry.prefix_text,
        created_at=entry.created_at,
        steps=[StepInfo(tool=s["tool"], ok=s["ok"], round=s["round"], detail=s["detail"])
               for s in entry.steps],
        waiting=[PendingCallInfo(**c) for c in entry.waiting()],
        decided=[PendingDecidedCall(**d) for d in entry.decided])


@router.get("/chat/pending", response_model=PendingResponse)
def chat_pending(request: Request, agent_id: str = Query(min_length=1),
                 project_id: str = Query(min_length=1)) -> PendingResponse:
    """待批表（内存态，后端重启即空）：刷新与重开页面靠它恢复卡片、前缀文本与已答痕迹。

    按「智能体 × 项目」取而不是按会话（R14）：挂起那一轮一个字都没落盘（裁定 8），
    刷新后前端只知道当前智能体与项目，那条会话在项目侧根本查不到。
    """
    service: ChatService = request.app.state.chat_service
    return PendingResponse(runs=[_pending_info(e)
                                 for e in service.pending_view_for(agent_id, project_id)])
```

`chat_stop` 换成双路（`StreamStopResponse` 与 404 文案一字不动）：

```python
@router.post("/chat/stop", response_model=StreamStopResponse)
def chat_stop(req: StreamStopRequest, request: Request) -> StreamStopResponse:
    """服务端真停：先打在途流，再打待批条目（裁定 10 第二条——待批期间停止钮照旧可用）。"""
    if request.app.state.run_registry.cancel(req.run_id):
        return StreamStopResponse(ok=True)
    service: ChatService = request.app.state.chat_service
    if service.cancel_pending(req.run_id):
        return StreamStopResponse(ok=True)
    raise HTTPException(status_code=404, detail="这条回答已经结束")
```

router 顶部 import 补：

```python
from fastapi import APIRouter, HTTPException, Query, Request
from aitester.orchestration.auth_rules import PermModeError
from aitester.services.chat import PreparedRun
from aitester.services.pending import CallDecidedError, PendingGoneError, ResumeNotReadyError
from aitester.services.run_registry import RunRegistry, new_run_id
from aitester.interaction.schemas import (
    ..., ApproveRequest, PendingCallInfo, PendingDecidedCall, PendingResponse, PendingRunInfo, ResumeRequest, ...)
```

- [ ] **Step 7: 装配与删会话级联**

`main.py`（在 `run_registry` 之后、`chat_service` 之前）：

```python
    # 待批注册表：与 run_registry 同层同风格，只挂 app.state（裁定 2：内存挂起，重启即丢）
    application.state.pending_registry = PendingRegistry()
    application.state.chat_service = ChatService(
        agent_runtime=application.state.agent_runtime,
        sessions=sessions,
        projects=project_config,
        pending=application.state.pending_registry,
    )
```

（import 补 `from aitester.services.pending import PendingRegistry`）

`sessions.py` 的删除端点补级联——会话是 pending 的归属维度，行没了条目必须跟着走：

```python
@router.delete("/{session_id}", status_code=204)
def sessions_delete(request: Request, session_id: str) -> None:
    if not _store(request).delete(session_id):
        raise _missing()
    # 级联（裁定 10 第三条）：会话没了，挂在它上面的待批与检查点线程一起收摊
    request.app.state.chat_service.drop_session(session_id)
```

- [ ] **Step 8: 跑测试 + 全量**

Run: `cd backend && python -m pytest tests/test_chat_auth_api.py -q`
Expected: `10 passed`

Run: `cd backend && python -m pytest -q`
Expected: `0 failed`，`test_chat_stream_api.py` 整套不改断言继续绿（`_stream_response` 抽口是逐字搬运，第 4 片的 13 项修复不许回退）。

- [ ] **Step 9: Commit**

```bash
git add backend/src/aitester backend/tests/test_chat_auth_api.py
git commit -m "feat(chat): 授权端点三件——approve/resume/pending，stop 双路与装配级联"
```

---

### Task 8: 前端传输层与折叠——`wait` 事件、`perm_mode` 入 body、授权三函数

**Files:**
- Modify: `frontend/src/api/client.ts`（`:293-312` `StreamEvent` 联合与 `StreamBody`、`:314` `STREAM_EVENTS`、`:316-372` `chatSendStream`、`:374-378` `chatStop` 之后）
- Modify: `frontend/src/pages/chat/streamState.ts`（`:13-30` `StreamingState` 与 `newStreamState`、`:36-73` `applyEvent`、`:113-116` `isWaiting`）

**Interfaces:**
- Consumes: T7 的 SSE 帧（`wait` 载荷 = 折叠六键 + `run_id`）、`POST /api/chat/approve`（204 无体）、`POST /api/chat/resume/stream`（SSE，帧序与 send 同形）、`GET /api/chat/pending?agent_id=&project_id=`
- Produces（T9/T10 逐字按此消费）:
  - `type PermMode = "free" | "boundary" | "strict"`、`type AuthDecision = "approve" | "reject"`
  - `StreamEvent` 多一支 `Frame<"wait", { call_id: string; tool: string; action: string; target: string; command: string; cwd: string; run_id: string }>`
  - `StreamBody.perm_mode?: PermMode`（不发 = 后端缺省 `free`）
  - `chatSendStream(body, onEvent, signal?)` 签名不变；`chatResumeStream(runId: string, onEvent, signal?): Promise<void>`
  - `chatApprove(runId: string, callId: string, decision: AuthDecision, remember: boolean): Promise<null>`
  - `getPending(agentId: string, projectId: string): Promise<{ runs: PendingRunInfo[] }>`
  - `interface PendingCallInfo { call_id; tool; action; target; command; cwd }`、
    `interface PendingDecidedCall extends PendingCallInfo { decision: AuthDecision }`、
    `interface PendingRunInfo { run_id; session_id; perm_mode; prefix; steps: ChatStep[]; waiting: PendingCallInfo[]; decided: PendingDecidedCall[]; created_at: number }`
  - `interface AuthAsk { callId; tool; action; target; command; cwd }`、`StreamingState.auths: AuthAsk[]`（按 `call_id` 幂等追加）、`held(state): boolean`

- [ ] **Step 1: 事件表与 body**

`StreamEvent` 联合在 `draft` 之后、`done` 之前插一支（顺序按后端帧序走，读起来才对得上）：

```ts
export type StreamEvent =
  | Frame<"start", { run_id: string; session_id: string }>
  | Frame<"delta", { round: number; text: string }>
  | Frame<"call", { tool: string; round: number; detail: string }>
  | Frame<"step", { tool: string; ok: boolean; round: number; detail: string }>
  | Frame<"draft", { draft: KbDraft }>
  | Frame<"wait", { call_id: string; tool: string; action: string; target: string;
                    command: string; cwd: string; run_id: string }>
  | Frame<"done", { reply: string; steps: ChatStep[]; session_id: string; title: string; stopped: boolean }>
  | Frame<"error", { detail: string }>;
```

`PermMode` / `AuthDecision` 两个别名放在 `StreamEvent` 之前（`ChatStep` 之后），`StreamBody` 补可选字段：

```ts
/** 三档权限（第 5 片）：id 与后端 auth_rules 的三个常量逐字同值，前端不做第二套命名。 */
export type PermMode = "free" | "boundary" | "strict";
export type AuthDecision = "approve" | "reject";

export interface StreamBody {
  session_id: string;
  message: string;
  agent_id: string;
  project_id: string;
  /** 缺省即 `free`：/kb 助手与旧客户端不发这个字段，后端也不报错（默认档零行为是红线）。 */
  perm_mode?: PermMode;
}

const STREAM_EVENTS = new Set(["start", "delta", "call", "step", "draft", "wait", "done", "error"]);
```

`feed()` 里那句注释「前端只认这 7 类」同步改成 8 类——数字留在注释里而白名单漏改，症状正是 spec 说的「授权卡根本不出现」。

- [ ] **Step 2: 抽 `postSse`，两条流共用一个读取器**

`chatSendStream` 的整段实现（`!resp.ok` 的 `ApiError` 口径、`feed`、reader 循环、尾包 flush）一行不改地搬进模块级私有函数，只把 URL 变成形参；然后两个导出函数各一行委托：

```ts
/** POST + 流解析：EventSource 不能带 JSON body，WebSocket 又是多余的语义，故 fetch + getReader 手解。
 *  守门未过时后端回普通 JSON（400/404/502），照 apiFetch 口径抛 ApiError；守门过后才有事件。
 *  send 与 resume 共用这一段：两条流的帧格式、坏帧容错与断开语义完全同形，抄两份必漂。 */
async function postSse(url: string, body: object, onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal): Promise<void> {
  const resp = await fetch(url, {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body), signal });
  // ——以下到函数结尾与迁移前 chatSendStream 逐字相同——
}

export function chatSendStream(
  body: StreamBody,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return postSse("/api/chat/send/stream", body, onEvent, signal);
}

/** 批准后的续跑：帧序与首回合同形（start / delta / call / step / wait / done / error），
 *  run_id 沿用挂起那一条，所以停止钮打的也是同一个 id（P4）。 */
export function chatResumeStream(
  runId: string,
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return postSse("/api/chat/resume/stream", { run_id: runId }, onEvent, signal);
}
```

- [ ] **Step 3: 授权三函数的类型与调用**

放在 `chatStop` 之后（同一条聊天传输面，不散到 sessions 那边去）：

```ts
export interface PendingCallInfo {
  call_id: string;
  tool: string;
  action: string;      // 中文动作短语，卡片标题位（R11：不走 DETAIL_MAX 截断）
  target: string;      // 写盘目标路径 / 命令的工作目录，读类调用为空串
  command: string;     // 命令全文，非命令类为空串
  cwd: string;
}

export interface PendingDecidedCall extends PendingCallInfo {
  decision: AuthDecision;
}

export interface PendingRunInfo {
  run_id: string;
  session_id: string;   // 待批停止后这条会话第一次落盘：前端按它决定要不要重开正文（R14）
  perm_mode: string;    // 挂起时锁的那一档，只用于展示，不受用户事后切档影响
  prefix: string;       // 已投递正文前缀：刷新后气泡照显示
  steps: ChatStep[];
  waiting: PendingCallInfo[];
  decided: PendingDecidedCall[];
  created_at: number;
}

/** 登记一条决策：204 无体（apiFetch 短路成 null）。404=这条已结束，409=这条已答过。 */
export function chatApprove(runId: string, callId: string, decision: AuthDecision,
  remember: boolean): Promise<null> {
  return apiFetch<null>("/api/chat/approve", {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ run_id: runId, call_id: callId, decision, remember }) });
}

/** 待批表按「智能体 × 项目」取（R14）：挂起那轮没落盘，刷新后前端只认得这两个键。 */
export function getPending(agentId: string, projectId: string): Promise<{ runs: PendingRunInfo[] }> {
  return apiFetch<{ runs: PendingRunInfo[] }>(
    `/api/chat/pending?${new URLSearchParams({ agent_id: agentId, project_id: projectId }).toString()}`);
}
```

- [ ] **Step 4: `streamState` 的 `wait` 折叠与「挂起」判据**

```ts
/** 一条投过授权卡的调用：live 气泡只靠它判「挂起」，卡片正文由 pending 表那份渲染（R12）。 */
export interface AuthAsk {
  callId: string; tool: string; action: string; target: string; command: string; cwd: string
}
```

`StreamingState` 在 `drafts` 之后加 `auths: AuthAsk[];`，`newStreamState()` 的返回对象同步加 `auths: []`。

`applyEvent` 的 `draft` 分支之后插：

```ts
    case "wait": {
      // 幂等（spec 测试 7）：同一 call_id 的 wait 重发不得复制卡片，也不得把已投的挪位
      if (state.auths.some((a) => a.callId === ev.call_id)) return state;
      return {
        ...state,
        auths: [...state.auths, { callId: ev.call_id, tool: ev.tool, action: ev.action,
          target: ev.target, command: ev.command, cwd: ev.cwd }],
      };
    }
```

`isWaiting` 补一条判据（挂起时模型没在吐字，三点占位会把「等你批准」演成「还在忙」）：

```ts
  return !state.terminal && state.rounds.length === 0 && state.pending.length === 0
    && state.steps.length === 0 && state.auths.length === 0;
```

文件末尾加判据函数：

```ts
/** 挂起判据：流断了、没有终态、但投过授权卡——这是「等你批准」，不是「连接坏了」。
 *  后端挂起时不发 done（裁定 7），所以 terminal 恒假；决策真相反而在 pending 表那侧。 */
export function held(state: StreamingState): boolean {
  return !state.terminal && state.auths.length > 0;
}
```

`finalize` 一行不动：挂起不会走到终态折叠，`done` 分支只在续跑收尾时跑。

> **前端折叠怎么测**：本项目前端无 vitest 设施（第 4 片同口径），`applyEvent` 的幂等由 `tsc` 穷尽检查 + 走查项 11 钉；决策真相在服务端 `answers` 表，那条已在 T5 的 `test_answer_requires_known_call_id` 单测锁死。此处登记为偏离，别在走查时把它当「已测」。

- [ ] **Step 5: 门禁**

Run: `cd frontend && npm run build`
Expected: `0 error`。传输层与折叠必须同任务落地：只加 `StreamEvent` 那支而不给 `applyEvent` 的 `wait` 分支，`tsc` 的穷尽检查就红在这里——`switch` 少一支是类型层报错，不是「先红后绿」的余地。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/chat/streamState.ts
git commit -m "feat(chat): 前端认 wait 事件——传输层三函数、perm_mode 入 body、折叠幂等"
```

---

### Task 9: `AuthCard`——一张卡三种状态

**Files:**
- Create: `frontend/src/pages/chat/AuthCard.tsx`
- Modify: `frontend/src/App.css`（`.kb-draft` 段 `:546-560` 之后补一行 `.auth-rem`）

**Interfaces:**
- Consumes: T8 的 `PendingCallInfo` / `AuthDecision`；`.kb-draft` 一族既有类（`d-head`/`d-op`/`d-path`/`d-sum`/`d-diff`/`d-acts`/`d-state`）、`.ws-btn.main`、`.mini-btn`
- Produces（T10 逐字按此消费）:
  - `interface AuthCardProps { ask: PendingCallInfo; decided: AuthDecision | null; queued: boolean; busy: boolean; onDecide: (decision: AuthDecision, remember: boolean) => void }`
  - `export default function AuthCard(p: AuthCardProps)`

- [ ] **Step 1: 组件**

新建 `frontend/src/pages/chat/AuthCard.tsx`：

```tsx
import { useState } from "react";
import type { AuthDecision, PendingCallInfo } from "../../api/client";

interface Props {
  ask: PendingCallInfo;
  /** 已答的痕迹态：null 表示还没答。六键由 decided 项自带（R14），刷新后照样读得出内容。 */
  decided: AuthDecision | null;
  /** 队列里排在后面的未答项：只给「排队中」，不给按钮——逐条批是本片接受的语义（P5/P7）。 */
  queued: boolean;
  busy: boolean;
  onDecide: (decision: AuthDecision, remember: boolean) => void;
}

/** 授权卡（spec「事件与前端折叠」）：复用草案卡那族形态内嵌在气泡里，不做遮罩弹窗。
 *  命令与路径一律全文展示，不套 DETAIL_MAX——批准前看不全就等于骗用户点确认。 */
export default function AuthCard(p: Props) {
  const [remember, setRemember] = useState(false);
  // 态标只在头部一处（草案卡同款收口）：pending「⏳ 等待授权」/ queued「排队中」/ 已答 ✓✕
  const stateLabel = p.decided === "approve" ? "✓ 已批准"
    : p.decided === "reject" ? "✕ 已拒绝"
      : p.queued ? "排队中"
        : "⏳ 等待授权";
  const locked = p.decided !== null || p.queued || p.busy;

  return (
    // 批准后的痕迹沿用草案卡 done 的绿（同一个「事情成了」的视觉口径）；
    // 拒绝保持中性底色——绿色的「已拒绝」是反话，不新造色值
    <div className={`kb-draft${p.decided === "approve" ? " done" : ""}`}>
      <div className="d-head">
        <span className="d-op">{p.ask.command ? "执行命令" : "写文件"}</span>
        <span>{p.ask.action}</span>
        <div className="spacer" />
        <span className="d-state">{stateLabel}</span>
      </div>
      {/* 目标路径给写类调用，工作目录给命令类；空串就整行不渲染，不留光杆标签 */}
      {p.ask.target ? <div className="d-path">{p.ask.target}</div> : null}
      {p.ask.command
        ? <pre className="d-diff">{p.ask.command}{p.ask.cwd ? `\n工作目录：${p.ask.cwd}` : ""}</pre>
        : <div className="d-sum">批准后立即执行，拒绝则跳过这一步并让模型继续作答。</div>}
      <div className="d-acts">
        {p.decided === null && !p.queued && (
          <>
            <button className="ws-btn main" disabled={locked}
              title={p.busy ? "续跑中，请稍候" : "批准后立即执行这一步"}
              onClick={() => p.onDecide("approve", remember)}>✓ 批准</button>
            <button className="mini-btn" disabled={locked}
              title="拒绝这一步，模型收到拒绝后继续作答"
              onClick={() => p.onDecide("reject", remember)}>✕ 拒绝</button>
            <label className="auth-rem" title="只记「这个工具 + 这个目标」，换路径仍会问">
              <input type="checkbox" checked={remember} disabled={locked}
                onChange={(e) => setRemember(e.target.checked)} />
              本次会话内同路径不再询问
            </label>
          </>
        )}
        <div className="spacer" />
        <span className="d-state">{p.ask.tool}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 一条样式补口**

`App.css` 的 `.kb-draft.done .d-op{…}`（`:560`）之后补：

```css
  /* 授权卡的「记住」勾选：卡片行内的小控件，取值全用既有 token，不新增色值 */
  .auth-rem{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--text-2);cursor:pointer}
  .auth-rem input{accent-color:var(--primary);margin:0}
```

- [ ] **Step 3: 门禁**

Run: `cd frontend && npm run build`
Expected: `0 error`（`AuthCard` 还没有消费方，`tsc` 只查它自身类型；`noUnusedLocals` 若报警就是 Step 1 里多写了没用到的导入）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/chat/AuthCard.tsx frontend/src/App.css
git commit -m "feat(chat): AuthCard——待批 / 已批 / 已拒三态，命令与路径全文可见"
```

---

### Task 10: 聊天页接线——三档 chip、pending 气泡、批准续跑、待批停止

**Files:**
- Modify: `frontend/src/pages/chat/utils.ts`（文件末尾补档位元数据与持久化）
- Modify: `frontend/src/pages/chat/Composer.tsx`（`:3-24` Props、`:70-76` 权限 chip）
- Modify: `frontend/src/pages/chat/MessageList.tsx`（`:18-28` Props、`:62-76` 欢迎态判据、`:110` 之后的列表尾部）
- Modify: `frontend/src/pages/ChatPage.tsx`（`:3-8` import、`:40-64` 状态、`:209-290` send/stop、`:472-500` 渲染）

**Interfaces:**
- Consumes: T8 `chatResumeStream` / `chatApprove` / `getPending` / `PendingRunInfo` / `PermMode` / `AuthDecision` / `held`、T9 `AuthCard`
- Produces:
  - `PERM_MODES` / `permMeta(id)` / `loadPermMode()` / `savePermMode(m)`
  - `Composer` 新 props：`permMode: PermMode`、`permLocked: boolean`、`onPermMode: (m: PermMode) => void`
  - `MessageList` 新 props：`pending: PendingRunInfo[]`、`resumeBusy: boolean`、`onDecide: (runId: string, callId: string, decision: AuthDecision, remember: boolean) => void`、`onStopPending: (run: PendingRunInfo) => void`
  - `ChatPage`：`drain(opts)` 收尾共用段、`decide(...)`、`stopPending(run)`、`reloadPending()`

- [ ] **Step 1: 档位元数据与持久化**

`pages/chat/utils.ts` 末尾补（`fmtTime`/`contextUsage` 之后，同文件同类小工具）：

```ts
import type { PermMode } from "../../api/client";

/** chip 三档元数据：id / label / desc 逐字对齐 spec「事件与前端折叠」表。
 *  原型 :1452-1453 只有两档且 strict 置灰，第 5 片 strict 真的能用了，boundary 是新增档。 */
export const PERM_MODES: { id: PermMode; icon: string; label: string; desc: string }[] = [
  { id: "free", icon: "🛡", label: "自由权限", desc: "所有操作（写文件、执行命令等）自动执行，无需你授权" },
  { id: "boundary", icon: "⚑", label: "只批界外", desc: "项目目录外的写入、以及所有命令执行需你授权" },
  { id: "strict", icon: "🔒", label: "严格权限", desc: "每次写盘 / 执行命令前需你授权" },
];

export const permMeta = (id: PermMode) => PERM_MODES.find((m) => m.id === id) ?? PERM_MODES[0];

/** 档位是视图偏好，与 projectId 同址落 localStorage（走查项 2：刷新后档位保留）。 */
const PERM_STORAGE_KEY = "aitester.chat.permMode";

export function loadPermMode(): PermMode {
  const v = window.localStorage.getItem(PERM_STORAGE_KEY);
  // 脏值、旧值、隐私模式下的 null 一律回 free：默认档零行为是红线，不能靠存储兜住语义
  return v === "boundary" || v === "strict" ? v : "free";
}

export function savePermMode(m: PermMode): void {
  window.localStorage.setItem(PERM_STORAGE_KEY, m);
}
```

- [ ] **Step 2: Composer 的三档 chip**

Props 在 `sendBlock` 之前加三项（`projectName` 之后）：

```ts
  permMode: PermMode;      // 当前档位：chip 的图标、文案与选中态唯一来源
  permLocked: boolean;     // 有待批就置灰（走查 15）：档位与挂起中那轮的判定必须一致
  onPermMode: (m: PermMode) => void;
```

import 补 `useEffect, useState`（`useEffect` 已在，只需补 `useState`）、`type PermMode from "../../api/client"` 与 `PERM_MODES, permMeta from "./utils"`。

把现在那颗只弹「严格权限暂未开放」toast 的按钮整段换成 chip + 弹层。chip 必须是 `span` 而不是 `button`——`.pop` 是 div，塞进 button 是非法内容模型，浏览器会把弹层挪出锚点（登记为偏离，键盘可达性用 `role`/`tabIndex` 补齐）：

```tsx
          <span
            className="c-chip blue perm"
            role="button"
            tabIndex={p.permLocked ? -1 : 0}
            aria-disabled={p.permLocked}
            title={p.permLocked ? "还有回答在等你批准，先处理完再切档位" : "权限模式 · 点击选择"}
            onClick={() => { if (!p.permLocked) setPermOpen((v) => !v); }}
            onKeyDown={(e) => {
              if (p.permLocked) return;
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPermOpen((v) => !v); }
              if (e.key === "Escape") setPermOpen(false);
            }}
          >
            {permMeta(p.permMode).icon} {permMeta(p.permMode).label} ▾
            <div className={`pop up${permOpen ? " show" : ""}`} onClick={(e) => e.stopPropagation()}>
              <div className="p-title">权限模式</div>
              {PERM_MODES.map((m) => (
                <div className={`opt${m.id === p.permMode ? " sel" : ""}`} key={m.id}
                  title={m.desc}
                  onClick={() => { setPermOpen(false); p.onPermMode(m.id); }}>
                  <span>{m.icon} {m.label}</span>
                  {m.id === p.permMode ? <span className="ck">✓</span> : null}
                </div>
              ))}
            </div>
          </span>
```

组件体顶部补开合与「点外面就关」：

```ts
  const [permOpen, setPermOpen] = useState(false);
  useEffect(() => {
    if (!permOpen) return;
    const close = () => setPermOpen(false);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [permOpen]);
```

`busy` 期间不禁 chip（挂起与在途是两种状态，chip 只在有待批时置灰）。`onToast` 这条 prop 随旧按钮一起删掉（Props 接口里的 `onToast` 与 `ChatPage` 调用点的 `onToast={toast}` 一并消失）：切档的 toast 由 ChatPage 的 `onPermMode` 发，chip 不再自己造文案——留着就是一只没人用的口子。

- [ ] **Step 3: MessageList 的 pending 气泡**

Props 加四项：

```ts
  pending: PendingRunInfo[];      // 待批的回合（R12：一条一个 agent 气泡，排在历史之后、live 之前）
  resumeBusy: boolean;            // 有一条续跑在途：所有卡的按钮一起锁，防双提交
  onDecide: (runId: string, callId: string, decision: AuthDecision, remember: boolean) => void;
  onStopPending: (run: PendingRunInfo) => void;
```

欢迎态判据改成三条都空才显示（挂起时消息可能一条没落，刷新后 `messages` 是空的、`pending` 却非空——此时画欢迎页等于把等授权的回答藏起来）：

```tsx
  if (!p.messages.length && !p.busy && !p.pending.length) {
```

列表尾部（`{p.busy && isWaiting(p.live) …}` 之前）插入 pending 段：

```tsx
      {/* 待批的回合（R12）：正文与过程行取 pending 表那份内存（裁定 8 的前缀），
          「⏳ 等待授权」挂在 meta 行——spec 说的「live 气泡保留」在这里由 pending 气泡接手，
          因为挂起收尾时 live 已让位，同一条画两次是第二套真相 */}
      {p.pending.map((run) => (
        <div className="msg agent" key={run.run_id}>
          <div className="who"><span className="avatar">Ai</span>AiTester</div>
          <Steps steps={run.steps} />
          {run.prefix
            ? <div className="body md-preview" dangerouslySetInnerHTML={{ __html: mdRender(run.prefix) }} />
            : null}
          {/* 先痕迹后待答：decided 按队列序就是「已经批过的在上面」，与逐条批的时间线一致 */}
          {run.decided.map((d) => (
            <AuthCard key={d.call_id} ask={d} decided={d.decision} queued={false} busy
              onDecide={() => undefined} />
          ))}
          {run.waiting.map((c, i) => (
            <AuthCard key={c.call_id} ask={c} decided={null} queued={i > 0} busy={p.resumeBusy}
              onDecide={(d, remember) => p.onDecide(run.run_id, c.call_id, d, remember)} />
          ))}
          <div className="meta">
            ⏳ 等待授权
            <span className="copy" title="停止这条回答：已生成的部分留在会话里"
              onClick={() => p.onStopPending(run)}>■ 停止</span>
          </div>
        </div>
      ))}
```

> **痕迹卡为什么按钮全给禁**：`run.waiting` 只含未答项、`run.decided` 只含已答项（后端 `waiting()` / `decided` 两个口分得干净），已答的卡再点一次必回 409，`busy` 传死 `true` 就是「0 个死按钮」判据下最诚实的只读表达。

import 补 `import type { AuthDecision, PendingRunInfo } from "../../api/client";` 与 `import AuthCard from "./AuthCard";`。

- [ ] **Step 4: ChatPage 的状态与取数**

import 补：`chatApprove, getPending, type AuthDecision, type PermMode, type PendingRunInfo`（`../api/client`）与 `held`（`./chat/streamState`）、`loadPermMode, permMeta?, savePermMode`（`./chat/utils`；`permMeta` 页面用不上就别导）。

状态区（`:64` `toastMsg` 之前）加四行：

```ts
  const [permMode, setPermMode] = useState<PermMode>(loadPermMode);
  const [pendingRuns, setPendingRuns] = useState<PendingRunInfo[]>([]);
  const pendingSeq = useRef(0);              // 待批表的「最新一次请求」序号：迟到的整表不得盖回来
  const [resumeRunId, setResumeRunId] = useState("");   // 续跑在途的那条 run：它的卡先摘掉，改由 live 气泡画
```

`reloadPending`（放在 `reloadSessions` 之后，同款序号守卫口径）与它的 effect：

```ts
  const reloadPending = useCallback(async () => {
    if (!agentId || !projectId) {           // 没得查就先清：留着上一个项目的卡是假 affordance
      ++pendingSeq.current;
      setPendingRuns([]);
      return;
    }
    const seq = ++pendingSeq.current;
    try {
      const j = await getPending(agentId, projectId);
      if (seq === pendingSeq.current) setPendingRuns(j.runs);
    } catch {
      // 取不到就当没有：卡片宁可少画一张，也不画一条早已结束的（后端本就重启即丢）
      if (seq === pendingSeq.current) setPendingRuns([]);
    }
  }, [agentId, projectId]);

  useEffect(() => { void reloadPending(); }, [reloadPending]);
```

- [ ] **Step 5: `drain`——把 send 的收尾段抽成两条流共用的结算**

`send()` 里 `if (aborted) return;` 到函数结尾那一段（现 `:242-274`）整体搬进 `drain`，行为一行不改，只加两处：挂起分支、以及 `optimistic` 为空时不撤 user 行。`send()` 与续跑各自 `await drain(...)`：

```ts
  /** 一条流式请求（首回合与续跑）共用的收尾：挂起 / 完成 / 失败三选一，busy 一定落地。
   *  抽出来是因为续跑的收尾与首回合逐字同形——抄两份必漂，第 2 片「守门只有一段」的同一条判据。 */
  const drain = useCallback(async (opts: {
    optimistic: ChatMessage | null;   // send 的乐观 user 行；续跑没有这一行
    text: string;                     // 失败时回填输入框的原文；续跑传 ""
    aborted: boolean;
    errMsg: string;
  }) => {
    // 换页/卸载导致的 abort：连接已断，后端走 GeneratorExit 落截断盘，本页不再改任何 state
    if (opts.aborted) return;
    abortRef.current = null;
    busyRef.current = false;
    setBusy(false);
    setStopRequested(false);
    setResumeRunId("");
    runIdRef.current = "";
    void reloadPending();               // 三条出路都要重取表：尾巴上可能还挂着下一条 wait（R13）
    const st = liveRef.current;
    const done = st ? st.done : null;
    if (st && held(st)) {
      // 挂起（裁定 7/8）：流在 wait 之后断掉、一条不落。live 让位给 pending 气泡，
      // 乐观 user 行留着——它和那张卡说的是同一件事，撤掉它就是「发出去却没回」的空框
      liveRef.current = null;
      setLive(null);
      return;
    }
    let errMsg = opts.errMsg;
    if (!done && !errMsg) errMsg = st?.fail || "连接中断，本条回答未完成";
    if (done && st) {
      const f = finalize(st, done);
      liveRef.current = null;
      setLive(null);
      setMessages((prev) => [...prev, {
        role: "assistant", content: f.content, ts: Date.now(), steps: f.steps, stopped: f.stopped,
      }]);
      if (f.sessionId !== activeId) setActiveId(f.sessionId);
      setWsSeq((n) => n + 1);
      const rows = await reloadSessions();
      const mine = rows.find((r) => r.id === f.sessionId);
      if (mine && f.title && mine.title !== f.title) {
        setSessions((prev) => prev.map((r) => (r.id === mine.id ? { ...r, title: f.title } : r)));
      }
      return;
    }
    // 失败必须可见：撤掉乐观 user 气泡并回填原文，不让用户对着「发出去了却没回」的空框
    liveRef.current = null;
    setLive(null);
    if (opts.optimistic) {
      setMessages((prev) => prev.filter((m) => m !== opts.optimistic));
      setInput(opts.text);
    }
    toast(errMsg);
  }, [activeId, reloadPending, reloadSessions, toast]);
```

`send()` 的尾巴收成一次委托（`onEvent` 与 try/catch 保持原样，body 多一项 `perm_mode: permMode`）：

```ts
    let aborted = false;
    let errMsg = "";
    try {
      await chatSendStream(
        { session_id: activeId ?? "", message: text, agent_id: agentId,
          project_id: projectId, perm_mode: permMode },
        onEvent, controller.signal);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") aborted = true;
      else errMsg = err instanceof ApiError ? err.message : "连接中断，本条回答未完成";
    }
    await drain({ optimistic, text, aborted, errMsg });
  }, [agentId, drain, guard, input, permMode, projectId]);
```

- [ ] **Step 6: `decide`——登记决策并续跑**

```ts
  /** 批准或拒绝一条待批：登记决策后立刻另起一条流续跑（拒绝也要跑，模型得收到拒绝继续作答）。
   *  两步分开做才收敛重复提交：approve 失败根本不发起续跑。 */
  const decide = useCallback(async (runId: string, callId: string,
    decision: AuthDecision, remember: boolean) => {
    if (resumeRunId) { toast("这条回答正在续跑，请稍候"); return; }
    if (!health) { toast("后端未就绪，请稍候或重试"); return; }
    if (busyRef.current || loadingRef.current || mutRef.current) {
      toast("上一条操作还在执行，请稍候"); return;
    }
    try {
      await chatApprove(runId, callId, decision, remember);
    } catch (err) {
      // 404「这条回答已经结束」/ 409「这条已经答过了」都是真相：重取表让卡片按后端的样子消失
      toast(err instanceof ApiError ? err.message : "授权登记失败，请重试");
      void reloadPending();
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setResumeRunId(runId);
    stopRequestedRef.current = false;
    setStopRequested(false);
    runIdRef.current = runId;               // 续跑沿用同一条 run：停止钮打的还是它（P4）
    liveRef.current = newStreamState();
    setLive(liveRef.current);
    const controller = new AbortController();
    abortRef.current = controller;
    const onEvent = (ev: StreamEvent) => {
      const cur = liveRef.current ?? newStreamState();
      liveRef.current = applyEvent(cur, ev);
      setLive(liveRef.current);
    };
    let aborted = false;
    let errMsg = "";
    try {
      await chatResumeStream(runId, onEvent, controller.signal);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") aborted = true;
      else errMsg = err instanceof ApiError ? err.message : "连接中断，本条回答未完成";
    }
    await drain({ optimistic: null, text: "", aborted, errMsg });
  }, [busyRef, drain, health, reloadPending, resumeRunId, toast]);
```

（`busyRef` 是 ref，不需要进 deps——列在这里只为与既有 `guard` 回调的写法对齐；若 `tsc`/eslint 不认，删掉 `busyRef` 这一项即可，行为不变。）

- [ ] **Step 7: `stopPending` 与 chip 回调**

```ts
  /** 待批期间的停止（裁定 10 第二条）：后端 cancel_pending 是挂起期唯一一次落盘，
   *  那条会话可能第一次出现，正看着它的用户必须重开才看得见截断行。 */
  const stopPending = useCallback(async (run: PendingRunInfo) => {
    try {
      await chatStop(run.run_id);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : "停止请求失败，这条回答可能还挂着");
      void reloadPending();
      return;
    }
    toast("已停止这条回答：已生成的部分留在会话里");
    await reloadPending();
    const rows = await reloadSessions();
    if (rows.some((s) => s.id === run.session_id)
      && (activeId === run.session_id || activeId === null)) void openSession(run.session_id);
  }, [activeId, openSession, reloadPending, reloadSessions, toast]);

  const onPermMode = useCallback((m: PermMode) => {
    if (m === permMode) return;
    setPermMode(m);
    savePermMode(m);
    // 文案取原型 :1473 逐字（free 说清「自动执行」，另两档不给长句）
    toast(m === "free" ? "已切换为「自由权限」：操作无需你授权，自动执行" : "已切换权限模式");
  }, [permMode, toast]);
```

渲染段接线（`:472-500`）：

```tsx
        <MessageList
          messages={messages}
          agentName={agent?.name ?? "用例设计智能体"}
          projectName={currentProject?.name ?? ""}
          busy={busy}
          live={live}
          pending={resumeRunId ? pendingRuns.filter((r) => r.run_id !== resumeRunId) : pendingRuns}
          resumeBusy={!!resumeRunId}
          onCopy={(t) => void copy(t)}
          onDecide={(runId, callId, decision, remember) => void decide(runId, callId, decision, remember)}
          onStopPending={(run) => void stopPending(run)}
          onChip={(t) => { setInput(t); inputRef.current?.focus(); }}
        />
```

`Composer` 补三项：

```tsx
          permMode={permMode}
          permLocked={pendingRuns.length > 0}
          onPermMode={onPermMode}
```

`remove()` 删会话成功后也补一句 `void reloadPending();`（级联在后端做了，前端的卡必须跟着消失，否则是一张点不动的假卡）。

- [ ] **Step 8: 门禁**

Run: `cd frontend && npm run build`
Expected: `0 error`。

Run: `cd backend && python -m pytest -q`
Expected: `0 failed`（本片后端不动前端，重跑只为确认没把契约改歪）。

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/chat/utils.ts frontend/src/pages/chat/Composer.tsx frontend/src/pages/chat/MessageList.tsx frontend/src/pages/ChatPage.tsx
git commit -m "feat(chat): 聊天页接线——三档权限 chip、待批气泡、批准续跑与待批停止"
```

---

### Task 11: 全量门禁、README 端点清单与真机走查交接

**Files:**
- Modify: `README.md`（`:35-42` 聊天端点段）

- [ ] **Step 1: 后端全量**

Run: `cd backend && python -m pytest -q`
Expected: `0 failed`，总数 = 基线 **487** + T1 起的本片新增数（T1~T7 各自 Step 的 passed 数相加）。若对不上：先 `python -m pytest -q --collect-only | wc -l` 定位差在哪一层的增删，**不许为凑数删既有用例或改断言**；`test_chat_stream_api.py` 整套必须一字不改继续绿（默认档零行为那条回归锁）。

- [ ] **Step 2: 前端门禁**

Run: `cd frontend && npm run build` → 0 error、0 warning 新增。

- [ ] **Step 3: README 端点清单**

`README.md` 聊天段里 `POST /api/chat/send/stream` 那条的事件枚举改为八类，并在 `POST /api/chat/stop` 那条之后补三条：

```markdown
- `POST /api/chat/send/stream`：…（前段不动）… 逐 token 推 `start / delta / call / step / draft / wait / done / error`
  八类事件，终态恒为一条（`done`，或被停止时 `done{stopped:true}`；流中模型失败 → `error`；
  待授权时不发终态，`wait` 之后直接断流，本轮一个字都不落盘）
- `POST /api/chat/approve`：`{run_id, call_id, decision: approve|reject, remember}` 登记一条授权决策，
  成功 204 无体；`run_id` 已结束 / 已重启 → 404，这条已答过 → 409；`remember` 为真时同一会话内
  同「工具 + 目标」不再询问
- `POST /api/chat/resume/stream`：`{run_id}` 把挂起的那一回合用 `Command(resume=…)` 续跑，帧序与
  `send/stream` 同形（可能再次以 `wait` 收尾）；守门仍在 HTTP 空间，目录不可达回 400 且文案与发送时逐字相同
- `GET /api/chat/pending?agent_id=&project_id=`：待批表（进程内内存态，后端重启即空）；
  返回每条挂起回答的 `run_id / session_id / perm_mode / prefix / steps / waiting / decided / created_at`
```

`POST /api/chat/stop` 那条末尾补一句：`待批期间同样可停：命中在途流优先，否则摘除待批条目并以已生成的前缀落一条 stopped 行`。

- [ ] **Step 4: 交真机走查（不由本计划代跑）**

后端 `127.0.0.1:8000` 与 vite `[::1]:5173` 由用户本人自起——**任何任务都不得占用或杀掉这两个端口**；`scripts/dev.ps1` 会杀 8000，禁止运行。
真实 LLM 段（三档判定、逐条批准、界外写盘、命令执行）按既有约定**须用户当面授权后才发起**。

**真机走查清单（= spec「验收清单」18 条逐条过；付费项标 ¥，免费项先跑）**

| # | 项 | 判据 | 成本 |
|---|---|---|---|
| 1 | 默认档零行为 | 档位显示「自由权限」，发消息与第 4 片逐字一致，一张卡都不弹 | ¥ |
| 2 | chip 三档与持久化 | 点开是三档、文案与 spec 表逐字；切到「只批界外」后 F5 档位仍在 | 免费 |
| 3 | boundary 界内写 | 项目 dir 内写文件不挂起、直接落盘、工作区树自动刷新 | ¥ |
| 4 | boundary 界外写 | 往 `D:/tmp/…` 写 → 气泡出授权卡，磁盘上**该文件还不存在** | ¥ |
| 5 | 批准后续跑 | 点「✓ 批准」→ 文件真出现、回答续到收尾、会话只落一行 assistant | ¥ |
| 6 | 拒绝 | 点「✕ 拒绝」→ 模型收到拒绝继续作答、本轮不终止、界外文件始终不存在 | ¥ |
| 7 | 记住同路径 | 勾「本次会话内同路径不再询问」→ 同路径不弹卡，换路径仍弹 | ¥ |
| 8 | strict 并行两写 | dir 内 `write` 也弹卡；两个调用 → 串行两张卡逐条批，最终两个文件各只写一次 | ¥ |
| 9 | strict 命令 | `pwsh` 卡里命令全文可见（滚动不算截断），批准后确实执行 | ¥ |
| 10 | boundary 下读界外 | `read`/`grep` 读界外不弹卡（读不拦） | ¥ |
| 11 | 刷新恢复 | 待批期间 F5 → 卡与前缀文本恢复，点批准仍能续跑 | ¥ |
| 12 | 待批期间停止 | 点 pending 气泡的「■ 停止」→ 卡消失、会话留「（已停止）」截断行、再 resume 回 404 | ¥ |
| 13 | 挂起后目录被删 | 批准续跑被 400 拦下，detail 与发送时逐字相同，且**目录没被 `mkdir` 建出来** | ¥ |
| 14 | 后端重启 | 旧待批消失，会话里没有半截 assistant 行 | 免费（需用户重启一次自己的后端） |
| 15 | 有 pending 时 chip 置灰 | 灰掉且 title 说清原因；档位与挂起那轮判定一致 | 免费 |
| 16 | /kb 零回归 | 无 chip、无授权卡，草案卡照旧，链路一字不动 | ¥ |
| 17 | 卫生 | 0 处 `zhb`、0 个死按钮、0 新增色值、console 0 error | 免费 |
| 18 | 存量归零 | `projects.json` 走查前后 md5 对照一致，探针产物全部还原 | 免费 |

零成本自检（不烧 token、不落会话行）：

```bash
export MSYS2_ARG_CONV_EXCL='*'
curl -s -o /dev/null -w '%{http_code}\n' 'http://localhost:5173/api/chat/pending?agent_id=case_design&project_id=nope'
```
期望 **200** 且体为 `{"runs":[]}`——`/chat/pending` 已经挂上、vite 代理通；未知项目 id 不报 404 是设计（待批表按内存取，不做项目存在性校验）。回 404 说明后端还是旧代码，先按第 0 项重启/热更。

**不要**拿 mock 模型去 curl `/chat/send/stream` 验授权：MockProvider 不发工具调用，永远不会挂起，得到的「没弹卡」是假结论。

- [ ] **Step 5: 偏离登记与提交**

spec「偏离登记」若尚未含本计划落地的三条，补进 spec 文末（一次性写明，不改判据）：
1. `wait` 之后 live 气泡让位给 pending 气泡（R12），spec 那句「live 气泡保留并显示『⏳ 等待授权』」由 pending 气泡的 meta 行承担。
2. 权限 chip 用 `span[role=button]` 而非 `button`（`.pop` 是 div，button 内容模型非法）。
3. 前端折叠幂等无 vitest，由 `tsc` 穷尽 + 走查 11 钉（T8 就地登记）。

```bash
git add README.md docs/superpowers/specs/2026-10-04-chat-boundary-auth-design.md
git commit -m "docs(chat-auth): 授权三端点进 README，18 项走查清单交接真机"
```
