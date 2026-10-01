# 智能体实例专项（后端装配）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `POST /api/chat/send` 按 `agent_id` 装配出一次性可运行的智能体实例（提示词 + 有效模型 + 携带工具 + 图拓扑），并让「新增一个智能体」在多数情况下零机制代码。

**Architecture:** 两缝设计——**数据缝**：智能体定义住在新增的零依赖包 `aitester/agents/`（`AgentSpec` 注册表 + `prompts/<id>.md`），加智能体＝加一条 spec＋一份 md；**行为缝**：`orchestration/graph_registry.py` 的 `GRAPH_BUILDERS` 注册表，循环范式不同才注册新拓扑，默认 `react`。`services/agent_runtime.py` 是装配器（常驻、无状态），每请求 `build()` 出一个 `AgentInstance` 用完即弃；状态只存在数据层（memory/storage/落盘配置），按 `agent_id:session_id` 键隔离。

**Tech Stack:** Python 3.11+（uv 管理）、FastAPI、LangGraph（`StateGraph`/`ToolNode`）、LangChain Core（`BaseTool`/`AIMessage`）、pytest + `fastapi.testclient`。

**Spec:** `docs/superpowers/specs/2026-09-30-agent-instance-design.md`（本计划逐节实现它；两份文档一起读）

## Global Constraints

每个任务的实现要求都隐含包含本节。

- 依赖方向：`interaction → services →（orchestration / context / memory / adapters / storage）`；`services` 是组合根；禁止任何反向 import。新增规则：**`aitester/agents/` 包零依赖**（只 import 标准库），任何层都可以 import 它，它不 import 任何业务层。
- 前端零改动：不改 `frontend/**`、不改 `prototype/**`。（已核实：前端源码里没有 `a1`、也没有调 `chat/send`，所以改名不波及前端。）
- 模型侧标识/字段用英文，面向用户的 UI 与 API `detail` 文案用中文。
- 任何日志、响应、提交里都不得出现 API Key；检查 Key 只做存在性判断。
- 用例设计智能体的 `name` / `desc` / `prompt` 三处文案**逐字迁出**，不许改写一个字。
- `GET /api/health`、`POST /api/chat/echo` 两条链路一字不动；`echo` 仍用占位 `SYSTEM_PROMPT` + `MockProvider` + 七层 trace。
- 响应 `trace` 层名列表保持 `services / context / orchestration / adapters / (tool:x)* / memory / storage`，交互层仍前缀 `["interaction"]`。
- 智能体 ID 用可读 snake_case：本期 `case_design`；`a1` 只在一次性迁移表里出现，**不做别名**。
- 脚本/命令以 Windows 为准：后端命令一律在 `backend/` 下用 `uv run ...`；测试基线 **177 项全绿**（`cd backend && uv run pytest -q`）。
- 每个任务结尾：`cd backend && uv run pytest -q` 全绿才允许提交；提交信息用中文 `feat(...)/refactor(...)/test(...)` 前缀，与仓库现有风格一致。

## 前置：基线入库（开工前必须处理，需用户点头）

工作区现有**未提交的既往成果**（工具层文件全部未跟踪 + 约 29 个已修改文件，对应 177 测试全绿）。不先入库，本计划每个任务的 `git add` 都会把它们一起卷进去，任务边界和回滚点就没了。

- [ ] **Step 0.1：向用户确认入库方式**（一条总提交 / 拆「工具层 · 前端 · 文档脚本」三条），得到答复前不动任何存量文件，禁止 `git checkout`/`reset`/`stash`。

- [ ] **Step 0.2：确认后建立基线提交**

```bash
cd /d/code/github/AiTester && git status
uv --version && cd backend && uv run pytest -q    # 必须 177 passed 才算基线可用
```

按用户选定的粒度提交（示例为一条总提交）：

```bash
cd /d/code/github/AiTester && git add backend frontend docs README.md && git commit -m "chore: 内置工具层与能力配置存量成果入库（177 测试基线）"
```

提交后再 `git status --porcelain` 应为空（`backend/data/*.json` 属本机运行期状态，若被跟踪则保持跟踪，不额外改动）。

---

## 文件结构（改动地图）

| 文件 | 动作 | 职责 |
|---|---|---|
| `backend/src/aitester/agents/__init__.py` | 新建 | 导出 `AgentSpec / AGENT_CATALOG / DEFAULT_AGENT_STATE / LEGACY_AGENT_IDS / find_agent` |
| `backend/src/aitester/agents/spec.py` | 新建 | `AgentSpec`（frozen dataclass）：一个智能体的静态定义 |
| `backend/src/aitester/agents/catalog.py` | 新建 | 注册表 + md 提示词加载 + 旧 id 一次性迁移表 + `find_agent` |
| `backend/src/aitester/agents/prompts/case_design.md` | 新建 | 用例设计智能体系统提示词（唯一真相，文案逐字自 `capability_config.py` 迁出） |
| `backend/src/aitester/orchestration/graph_registry.py` | 新建 | 行为缝：`GraphBuilder` 类型、`GRAPH_BUILDERS`、`get_graph_builder` |
| `backend/src/aitester/orchestration/agent_graph.py` | 修改 | `build_agent_graph` 无工具退化单节点；新增 `run_graph`，`run_agent` 收敛为特例 |
| `backend/src/aitester/orchestration/__init__.py` | 修改 | 导出 `run_graph / GraphBuilder / GRAPH_BUILDERS / get_graph_builder` |
| `backend/src/aitester/services/model_config.py` | 修改 | 新增 `build_provider(uid)`；`build_default_provider()` 收敛为特例 |
| `backend/src/aitester/services/capability_config.py` | 修改 | 删本地 agent 常量改用 `agents` 包；`_normalized` 加旧 id 迁移；新增公开 `agent_state()` / `effective_uid()` |
| `backend/src/aitester/services/agent_runtime.py` | 新建 | `AgentInstance` + `AgentRuntime`（每请求现装现弃） |
| `backend/src/aitester/services/chat.py` | 修改 | `send(session_id, message, agent_id)` 走 runtime；会话键 `agent_id:session_id`；prompt 来自实例 |
| `backend/src/aitester/main.py` | 修改 | `app.state.agent_runtime` 装配，`ChatService` 改注入 runtime |
| `backend/src/aitester/interaction/router.py` | 修改 | `chat_send` 不再自己拼工具；新增 `ConfigNotFoundError → 404` |
| `backend/src/aitester/interaction/schemas.py` | 修改 | `SendRequest.agent_id` 默认 `"case_design"` |
| `backend/tests/test_agents.py` | 新建 | agents 包单测 |
| `backend/tests/test_agent_runtime.py` | 新建 | 装配器单测 |
| `backend/tests/test_agent_graph.py` | 修改 | 注册表、单节点退化、`run_graph` |
| `backend/tests/test_model_config.py` | 修改 | `build_provider(uid)` 全分支 |
| `backend/tests/test_capability_config.py` | 修改 | 改用 `agents` 包、`a1→case_design` 更名、迁移与公开读入口 |
| `backend/tests/test_api_capabilities.py` | 修改 | 端点路径与断言里的 `a1 → case_design` |
| `backend/tests/test_api.py` / `test_chat_service.py` | 修改 | send 端到端改走 runtime；404/提示词/会话键断言 |
| `backend/pyproject.toml` | 可能修改 | 仅当 `uv build` 出的 wheel 不含 md 时补打包配置（Task 7） |

不在范围（spec §1）：流式/SSE、项目目录接入（工具 `cwd` 恒 `"."`）、权限确认、提示词编辑、增删智能体的界面、`chat/echo` 改动、任何前端改动。

---

## Task 1: agents 数据包（AgentSpec + md 提示词 + 迁移表）

**Files:**
- Create: `backend/src/aitester/agents/__init__.py`
- Create: `backend/src/aitester/agents/spec.py`
- Create: `backend/src/aitester/agents/catalog.py`
- Create: `backend/src/aitester/agents/prompts/case_design.md`
- Test: `backend/tests/test_agents.py`

**Interfaces:**
- Consumes: 无（本包零依赖）
- Produces:
  - `AgentSpec(id: str, icon: str, name: str, desc: str, prompt: str, default_tool_ids: tuple[str, ...], graph_builder: str = "react")`（frozen dataclass）
  - `AGENT_CATALOG: tuple[AgentSpec, ...]`（本期仅 `case_design`）
  - `DEFAULT_AGENT_STATE: dict[str, dict[str, Any]]` = `{id: {"default_uid": "", "tool_ids": [...]}}`（由 catalog 派生）
  - `LEGACY_AGENT_IDS: dict[str, str]` = `{"a1": "case_design"}`
  - `find_agent(agent_id: str) -> AgentSpec | None`（**不含旧 id**：未知与旧 id 都返回 `None`）
  - `PROMPTS_DIR: Path`、`_load_prompt(agent_id: str) -> str`（缺文件抛 `FileNotFoundError`，消息含完整路径）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_agents.py`：

```python
from pathlib import Path

import pytest

from aitester.agents import (
    AGENT_CATALOG,
    DEFAULT_AGENT_STATE,
    LEGACY_AGENT_IDS,
    find_agent,
)
from aitester.agents.catalog import PROMPTS_DIR, _load_prompt

# 与 capability_config.py 原多行串逐字一致（迁移不许改一个字）
PROMPT = """你是「用例设计智能体」，服务对象是软件测试工程师。

## 职责
- 依据需求说明、接口文档与存量用例，设计功能 / 接口 / 回归测试用例
- 用等价类划分、边界值、状态迁移、异常注入保证覆盖，并标注 P0 / P1 / P2
- 产出统一用例表：编号、需求号、前置条件、步骤、预期结果、优先级

## 约束
- 项目文件只读，新增文件一律落在 cases/ 下，不改动生产配置
- 需求信息不足时先列出待澄清问题，不臆造验收标准
- 每条用例必须能被测试执行智能体直接跑：步骤可操作、预期结果可判定
- 全程使用中文与 Markdown 表格，不省略步骤"""


def test_catalog_has_exactly_one_readable_id_agent() -> None:
    assert [s.id for s in AGENT_CATALOG] == ["case_design"]
    spec = AGENT_CATALOG[0]
    assert spec.icon == "📋"
    assert spec.name == "用例设计智能体"
    assert spec.desc == (
        "读需求与接口文档，产出可直接执行的测试用例并同步用例平台，覆盖等价类、边界值与异常路径。"
    )
    assert spec.graph_builder == "react"
    assert isinstance(spec.default_tool_ids, tuple)


def test_prompt_is_loaded_verbatim_from_md_file() -> None:
    spec = find_agent("case_design")
    assert spec is not None
    assert spec.prompt == PROMPT
    assert (PROMPTS_DIR / "case_design.md").read_text(encoding="utf-8").strip() == PROMPT


def test_default_agent_state_is_derived_from_catalog() -> None:
    assert DEFAULT_AGENT_STATE == {
        "case_design": {
            "default_uid": "",
            "tool_ids": [
                "read",
                "write",
                "edit",
                "grep_search",
                "glob_search",
                "web_search",
            ],
        }
    }


def test_legacy_id_is_migration_table_not_alias() -> None:
    assert LEGACY_AGENT_IDS == {"a1": "case_design"}
    assert find_agent("a1") is None
    assert find_agent("ghost") is None


def test_missing_prompt_file_raises_with_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aitester.agents.catalog.PROMPTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError) as exc_info:
        _load_prompt("case_design")
    assert "case_design.md" in str(exc_info.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_agents.py -q`
Expected: 收集期报错 `ModuleNotFoundError: No module named 'aitester.agents'`

- [ ] **Step 3: 写 `agents/spec.py`**

```python
"""一个智能体的静态定义：数据缝的最小单元。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    icon: str
    name: str
    desc: str
    prompt: str
    default_tool_ids: tuple[str, ...]
    graph_builder: str = "react"
```

- [ ] **Step 4: 写 `agents/prompts/case_design.md`**

内容为上面 `PROMPT` 常量的原文（首行 `你是「用例设计智能体」…`，末行 `- 全程使用中文与 Markdown 表格，不省略步骤`），文件末尾可带一个换行（加载时 `strip()`）。不要加 frontmatter、标题层级保持原样。

- [ ] **Step 5: 写 `agents/catalog.py`**

```python
"""智能体注册表：定义在本包，提示词在 prompts/<id>.md。

`LEGACY_AGENT_IDS` 是一次性迁移表（旧键 → 新键），不是别名机制：状态搬完、旧键随
未知键一起被归一化丢弃后，本表即可删除。
"""

from pathlib import Path
from typing import Any

from aitester.agents.spec import AgentSpec

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(agent_id: str) -> str:
    path = PROMPTS_DIR / f"{agent_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"智能体「{agent_id}」缺少提示词文件：{path}")
    return path.read_text(encoding="utf-8").strip()


AGENT_CATALOG: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="case_design",
        icon="📋",
        name="用例设计智能体",
        desc="读需求与接口文档，产出可直接执行的测试用例并同步用例平台，覆盖等价类、边界值与异常路径。",
        prompt=_load_prompt("case_design"),
        default_tool_ids=(
            "read",
            "write",
            "edit",
            "grep_search",
            "glob_search",
            "web_search",
        ),
    ),
)

DEFAULT_AGENT_STATE: dict[str, dict[str, Any]] = {
    spec.id: {"default_uid": "", "tool_ids": list(spec.default_tool_ids)}
    for spec in AGENT_CATALOG
}

LEGACY_AGENT_IDS: dict[str, str] = {"a1": "case_design"}


def find_agent(agent_id: str) -> AgentSpec | None:
    for spec in AGENT_CATALOG:
        if spec.id == agent_id:
            return spec
    return None
```

- [ ] **Step 6: 写 `agents/__init__.py`**

```python
"""智能体领域数据包：定义与提示词，零业务依赖（只 import 标准库）。"""

from aitester.agents.catalog import (
    AGENT_CATALOG,
    DEFAULT_AGENT_STATE,
    LEGACY_AGENT_IDS,
    find_agent,
)
from aitester.agents.spec import AgentSpec

__all__ = [
    "AGENT_CATALOG",
    "DEFAULT_AGENT_STATE",
    "LEGACY_AGENT_IDS",
    "AgentSpec",
    "find_agent",
]
```

- [ ] **Step 7: 跑测试确认通过 + 全量回归**

Run: `cd backend && uv run pytest tests/test_agents.py -q && uv run pytest -q`
Expected: 新测试 5 passed；全量 `182 passed`（基线 177 + 5）

- [ ] **Step 8: 提交**

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/agents backend/tests/test_agents.py && git commit -m "feat(agents): 智能体数据包——AgentSpec 注册表、md 提示词外置与旧 id 迁移表"
```

---

## Task 2: 行为缝（图构建器注册表 + 无工具退化 + run_graph）

**Files:**
- Create: `backend/src/aitester/orchestration/graph_registry.py`
- Modify: `backend/src/aitester/orchestration/agent_graph.py:31-72`
- Modify: `backend/src/aitester/orchestration/__init__.py`
- Test: `backend/tests/test_agent_graph.py`（追加）

**Interfaces:**
- Consumes: `build_agent_graph(provider, tools)`（现有）；`AGENT_CATALOG`（Task 1）
- Produces:
  - `GraphBuilder = Callable[[LlmProvider, list[AiTooler]], CompiledStateGraph]`——**定义在 `agent_graph.py`**，由 `graph_registry.py` 再导出。原因：注册表要 import `build_agent_graph`，若类型别名反过来住在 `graph_registry`，`agent_graph` 就需要 import 回去 → 循环导入（`cannot import name 'GraphBuilder' from partially initialized module`）。
  - `GRAPH_BUILDERS: dict[str, GraphBuilder]`（`{"react": build_agent_graph}`）
  - `get_graph_builder(name: str) -> GraphBuilder`（未注册 → `ValueError`）
  - `run_graph(build: GraphBuilder, provider: LlmProvider, tools: list[AiTooler], messages: list[BaseMessage]) -> dict[str, Any]` → `{"reply": str, "tool_traces": list[dict[str, str]]}`
  - `run_agent(provider, tools, messages)` 保持原签名，内部＝ `run_graph(build_agent_graph, ...)`
  - `build_agent_graph(provider, [])` 编译出**单节点**图（不 `bind_tools`、不建 `ToolNode`）

- [ ] **Step 1: 追加失败测试**

在 `backend/tests/test_agent_graph.py` 末尾追加（顶部 import 补 `pytest`、`MockProvider`、`AGENT_CATALOG`、注册表符号）：

```python
from aitester.agents import AGENT_CATALOG
from aitester.adapters.llm import MockProvider
from aitester.orchestration import (
    GRAPH_BUILDERS,
    build_agent_graph,
    get_graph_builder,
    run_graph,
)


def test_registry_resolves_react_to_build_agent_graph() -> None:
    assert get_graph_builder("react") is build_agent_graph


def test_registry_rejects_unregistered_builder_name() -> None:
    with pytest.raises(ValueError) as exc_info:
        get_graph_builder("plan_execute")
    assert "未注册" in str(exc_info.value)


def test_every_catalog_agent_has_a_registered_builder() -> None:
    for spec in AGENT_CATALOG:
        assert GRAPH_BUILDERS[spec.graph_builder] is get_graph_builder(spec.graph_builder)


def test_graph_without_tools_answers_in_single_node() -> None:
    result = run_graph(build_agent_graph, MockProvider(), [], [HumanMessage(content="生成用例")])
    assert result["reply"] == "[mock] 生成用例"
    assert result["tool_traces"] == []


def test_run_graph_on_react_matches_run_agent(tmp_path: Path) -> None:
    def script() -> list[AIMessage]:
        return [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write",
                        "args": {"file_path": "h.txt", "content": "v"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已写入"),
        ]

    tools = _tools(tmp_path)
    by_helper = run_agent(ScriptedProvider(script()), tools, [HumanMessage(content="写")])
    by_graph = run_graph(
        get_graph_builder("react"),
        ScriptedProvider(script()),
        tools,
        [HumanMessage(content="写")],
    )
    assert by_graph["reply"] == by_helper["reply"] == "已写入"
    assert [t["tool"] for t in by_graph["tool_traces"]] == ["write"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_agent_graph.py -q`
Expected: FAIL，`ImportError: cannot import name 'GRAPH_BUILDERS' from 'aitester.orchestration'`

- [ ] **Step 3: 写 `orchestration/graph_registry.py`**

```python
"""行为缝：图构建器注册表。

默认智能体都走 `react`（下面的工具循环）；循环范式不同的智能体在这里注册自己的
LangGraph 拓扑。构建器名属于开发者面，未注册即代码缺陷，不做运行期兜底。
"""

from aitester.orchestration.agent_graph import GraphBuilder, build_agent_graph

GRAPH_BUILDERS: dict[str, GraphBuilder] = {"react": build_agent_graph}


def get_graph_builder(name: str) -> GraphBuilder:
    builder = GRAPH_BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"未注册的图构建器「{name}」，请在 GRAPH_BUILDERS 注册")
    return builder
```

本模块只 import `agent_graph`，绝不反向被它 import（`GraphBuilder` 就住在 `agent_graph`，这里再导出给上层用）。

- [ ] **Step 4: 改 `build_agent_graph` 支持无工具退化，并定义 `GraphBuilder`**

`backend/src/aitester/orchestration/agent_graph.py`，先在 `class AgentState` 定义之后插入类型别名（顶部第 5 行的 `from typing import Annotated, Any` 改成 `from typing import Annotated, Any, Callable`）：

```python
GraphBuilder = Callable[[LlmProvider, list[AiTooler]], CompiledStateGraph]
```

再把现有 `build_agent_graph` 换成：

```python
def build_agent_graph(provider: LlmProvider, tools: list[AiTooler]) -> CompiledStateGraph:
    """构建 Agent 状态图：有工具走 agent↔tools 循环，无工具退化为单节点直答。"""
    if not tools:
        def answer_node(state: AgentState) -> dict[str, list[BaseMessage]]:
            return {"messages": [provider.invoke_messages(state["messages"])]}

        plain = StateGraph(AgentState)
        plain.add_node("agent", answer_node)
        plain.add_edge(START, "agent")
        plain.add_edge("agent", END)
        return plain.compile()

    tool_node = ToolNode(tools, handle_tool_errors=_tool_error_message)
    bound = provider.bind_tools(tools)

    def agent_node(state: AgentState) -> dict[str, list[BaseMessage]]:
        response = bound.invoke_messages(state["messages"])
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
```

- [ ] **Step 5: 加 `run_graph`，`run_agent` 收敛为特例**

同文件末尾，把现有 `run_agent` 整体替换为：

```python
def run_graph(
    build: GraphBuilder,
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """按指定拓扑执行一轮，返回 {reply, tool_traces}。"""
    graph = build(provider, tools)
    result = graph.invoke({"messages": messages})

    reply = ""
    tool_traces: list[dict[str, Any]] = []
    for msg in result["messages"]:
        if isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
            reply = str(msg.content)
        if isinstance(msg, ToolMessage):
            tool_traces.append({"tool": msg.name or "", "result": str(msg.content)})

    return {"reply": reply, "tool_traces": tool_traces}


def run_agent(
    provider: LlmProvider,
    tools: list[AiTooler],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """默认 react 循环的便捷入口（等价于 run_graph(build_agent_graph, …)）。"""
    return run_graph(build_agent_graph, provider, tools, messages)
```

`run_graph` 的 `build: GraphBuilder` 用本模块 Step 4 定义的别名，**不要**从 `graph_registry` 往回 import（循环）。`Any / BaseMessage / AIMessage / ToolMessage / StateGraph / START / END / ToolNode / CompiledStateGraph` 该文件均已在，本步不需要新 import。

- [ ] **Step 6: 更新 `orchestration/__init__.py`**

```python
"""编排 loop 层：LangGraph 状态图。"""

from aitester.orchestration.agent_graph import AgentState, build_agent_graph, run_agent, run_graph
from aitester.orchestration.graph import EchoState, build_echo_graph, run_echo
from aitester.orchestration.graph_registry import GRAPH_BUILDERS, GraphBuilder, get_graph_builder

__all__ = [
    "GRAPH_BUILDERS",
    "AgentState",
    "EchoState",
    "GraphBuilder",
    "build_agent_graph",
    "build_echo_graph",
    "get_graph_builder",
    "run_agent",
    "run_echo",
    "run_graph",
]
```

- [ ] **Step 7: 跑测试确认通过 + 全量回归**

Run: `cd backend && uv run pytest tests/test_agent_graph.py -q && uv run pytest -q`
Expected: 该文件 10 passed（原 5 + 新 5）；全量 `187 passed`

- [ ] **Step 8: 提交**

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/orchestration backend/tests/test_agent_graph.py && git commit -m "feat(orchestration): 图构建器注册表（行为缝）+ 无工具退化单节点 + run_graph"
```

---

## Task 3: `build_provider(uid)`——按 uid 建 provider，默认 provider 收敛为特例

**Files:**
- Modify: `backend/src/aitester/services/model_config.py:270-292`
- Test: `backend/tests/test_model_config.py`（追加）

**Interfaces:**
- Consumes: `_provider(pid)`、`_model(provider, mid)`、`OpenAICompatProvider`（现有）
- Produces:
  - `ModelConfigService.build_provider(uid: str) -> LlmProvider`
  - `ModelConfigService.build_default_provider() -> LlmProvider`（＝ `build_provider(self.default_uid)`，签名与语义不变）
  - 异常：空 uid → `ProviderConfigError`（文案逐字不变）；未配 Key / 已停用 → `ProviderConfigError`；uid 指向不存在的提供商/模型 → `ConfigNotFoundError`

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_model_config.py` 末尾追加（复用文件里已有的 `_svc(tmp_path, **kwargs)` 与 `openai_compat`、`ProviderConfigError`、`ConfigNotFoundError` import；`FakeChatOpenAI` 若文件里已有同名假件，改名 `_FakeChatForBuild` 以免冲突）：

```python
def test_build_provider_with_explicit_uid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    class _FakeChatForBuild:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChatForBuild)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789")
    provider = svc.build_provider("dashscope/qwen3.7-max")
    assert provider.model_ref == "dashscope/qwen3.7-max"
    assert recorded["model"] == "qwen3.7-max"
    assert recorded["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_build_provider_empty_uid_keeps_default_model_copy(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.build_provider("")
    # 文案逐字保持：现有 400 断言依赖「设置 · 模型设置」
    assert exc_info.value.detail == "尚未配置默认模型：请在 设置 · 模型设置 中填写 API Key 并选择默认 LLM"


def test_build_provider_rejects_unusable_model(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    svc.set_model_enabled("deepseek", "deepseek-flash", False)
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.build_provider("deepseek/deepseek-flash")
    assert "已停用" in exc_info.value.detail

    other_dir = Path(tmp_path, "other")
    other_dir.mkdir()
    other = _svc(other_dir)  # 两个提供商都没配 Key
    with pytest.raises(ProviderConfigError) as exc_info:
        other.build_provider("dashscope/qwen3.7-max")
    assert "未配置 API Key" in exc_info.value.detail


def test_build_provider_unknown_uid_raises_not_found(tmp_path: Path) -> None:
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    with pytest.raises(ConfigNotFoundError):
        svc.build_provider("deepseek/nope")
    with pytest.raises(ConfigNotFoundError):
        svc.build_provider("glm/deepseek-flash")


def test_build_default_provider_delegates_to_build_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[str] = []

    class _FakeChatDeleg:
        def __init__(self, **kwargs: object) -> None:
            recorded.append(str(kwargs["model"]))

    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChatDeleg)
    svc = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert svc.build_default_provider().model_ref == "deepseek/deepseek-flash"
    assert svc.build_provider("deepseek/deepseek-flash").model_ref == "deepseek/deepseek-flash"
    assert recorded == ["deepseek-flash", "deepseek-flash"]
```

注：`_svc(tmp_path, **kwargs)` 是该文件已有的帮助函数（`ModelConfigService` + `Settings(_env_file=None, ...)`）；`openai_compat`、`pytest`、`Path`、`ProviderConfigError`、`ConfigNotFoundError` 已在该文件顶部 import。`_FakeChatForBuild` / `_FakeChatDeleg` 是新建类名，若与文件里现有假件重名则改名，不要复用别人的假件。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_model_config.py -q`
Expected: FAIL，`AttributeError: 'ModelConfigService' object has no attribute 'build_provider'`

- [ ] **Step 3: 实现 `build_provider`，`build_default_provider` 改为委派**

`backend/src/aitester/services/model_config.py`，把现有 `build_default_provider`（:270-292）整体替换为：

```python
    def build_provider(self, uid: str) -> LlmProvider:
        """按 uid 构建 provider：空 uid / 未配 Key / 已停用 → ProviderConfigError（400 可照做文案）。"""
        if not uid:
            raise ProviderConfigError(
                "尚未配置默认模型：请在 设置 · 模型设置 中填写 API Key 并选择默认 LLM"
            )
        pid, _, mid = uid.partition("/")
        provider = self._provider(pid)
        model = self._model(provider, mid)
        if not provider["api_key"]:
            raise ProviderConfigError(
                f"模型「{uid}」的提供商未配置 API Key：请在 设置 · 模型设置 中填写"
            )
        if not model["enabled"]:
            raise ProviderConfigError(
                f"模型「{uid}」已停用：请在 设置 · 模型设置 中启用或改选模型"
            )
        return OpenAICompatProvider(
            name=pid,
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model=mid,
        )

    def build_default_provider(self) -> LlmProvider:
        return self.build_provider(self._config["default_uid"])
```

文案说明：后两条把「默认模型」泛化为「模型」（同一个函数现在也服务智能体级默认模型），关键短语 `设置 · 模型设置`、`未配置 API Key`、`已停用` 全部保留，既有断言不受影响。

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd backend && uv run pytest tests/test_model_config.py -q && uv run pytest -q`
Expected: 该文件全绿；全量 `192 passed`（数字以实际为准，只要求零失败）

- [ ] **Step 5: 提交**

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/services/model_config.py backend/tests/test_model_config.py && git commit -m "feat(services): build_provider(uid) 按 uid 建 provider，默认 provider 收敛为特例"
```

---

## Task 4: 能力配置改用 agents 包（改名 + 一次性迁移 + 公开读入口）

**Files:**
- Modify: `backend/src/aitester/services/capability_config.py:81-107`（删常量）、`:152-169`（迁移）、`:197-245`（公开读入口与视图）、`:1-12`（import）
- Test: `backend/tests/test_capability_config.py`（改名 + 新增）、`backend/tests/test_api_capabilities.py`（改名）

**Interfaces:**
- Consumes: `AGENT_CATALOG / DEFAULT_AGENT_STATE / LEGACY_AGENT_IDS`（Task 1）
- Produces:
  - `CapabilityConfigService.agent_state(agent_id: str) -> dict[str, Any]`（返回副本 `{"default_uid": str, "tool_ids": list[str]}`；未知 → `ConfigNotFoundError`）
  - `CapabilityConfigService.effective_uid(agent_id: str) -> str`（读侧回落，不写回；未知 → `ConfigNotFoundError`）
  - `GET /api/capabilities` 响应形状不变，`agents[0].id` 变为 `"case_design"`，`prompt` 来源为 md
  - 本地 `backend/data/capability_config.json` 首次加载即完成 `a1 → case_design` 迁移（用户已勾的 `pwsh` 携带状态必须保留）

- [ ] **Step 1: 改 `test_capability_config.py` 的 import 与断言（先让它红）**

顶部 import 换成从 `aitester.agents` 取目录常量：

```python
from aitester.agents import AGENT_CATALOG, DEFAULT_AGENT_STATE, LEGACY_AGENT_IDS
```

全文把智能体 id 字面量 `"a1"` 替换为 `"case_design"`（含端点路径与 `stored["agents"]["a1"]`）；目录断言换成 dataclass 访问：

```python
def test_catalog_seeds_exactly_one_agent_and_eight_tools() -> None:
    assert [s.id for s in AGENT_CATALOG] == ["case_design"]
    assert AGENT_CATALOG[0].name == "用例设计智能体"
    assert [t["id"] for t in TOOL_CATALOG] == [
        "read",
        "write",
        "edit",
        "grep_search",
        "glob_search",
        "pwsh",
        "bash",
        "web_search",
    ]
```

并把这条既有断言同步更名（`test_first_start_seeds_and_persists`）：

```python
    assert stored["agents"] == {
        "case_design": {
            "default_uid": "",
            "tool_ids": ["read", "write", "edit", "grep_search", "glob_search", "web_search"],
        }
    }
```

- [ ] **Step 2: 追加迁移与公开读入口测试（红）**

`backend/tests/test_capability_config.py` 末尾追加：

```python
def test_legacy_agent_id_state_is_migrated_once(tmp_path: Path) -> None:
    # 只用全平台可用的 read/write：本机 pwsh 携带态的保留由 Task 6 Step 9 实测覆盖
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(
        {
            "version": 1,
            "tool_state": _seed_tool_state(),
            "agents": {
                "a1": {
                    "default_uid": "deepseek/deepseek-flash",
                    "tool_ids": ["read", "write"],
                }
            },
        }
    )
    capability, _ = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    stored = _stored(tmp_path)
    # 状态整搬过来，旧键消失（不是别名）
    assert "a1" not in stored["agents"]
    assert stored["agents"]["case_design"]["default_uid"] == "deepseek/deepseek-flash"
    assert stored["agents"]["case_design"]["tool_ids"] == ["read", "write"]
    agent = capability.get_view()["agents"][0]
    assert agent["id"] == "case_design"
    assert agent["default_uid"] == "deepseek/deepseek-flash"
    assert agent["effective_uid"] == "deepseek/deepseek-flash"


def test_migration_does_not_overwrite_existing_new_key(tmp_path: Path) -> None:
    FileJsonConfigRepository(tmp_path / "capability_config.json").save(
        {
            "version": 1,
            "tool_state": _seed_tool_state(),
            "agents": {
                "a1": {"default_uid": "deepseek/deepseek-flash", "tool_ids": ["read"]},
                "case_design": {"default_uid": "", "tool_ids": ["write"]},
            },
        }
    )
    _svc(tmp_path)
    stored = _stored(tmp_path)
    assert stored["agents"] == {"case_design": {"default_uid": "", "tool_ids": ["write"]}}


def test_legacy_id_is_not_an_alias_at_read_time(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    assert [a["id"] for a in capability.get_view()["agents"]] == ["case_design"]
    assert LEGACY_AGENT_IDS == {"a1": "case_design"}
    with pytest.raises(ConfigNotFoundError) as exc_info:
        capability.agent_state("a1")
    assert "未知智能体" in exc_info.value.detail


def test_agent_state_returns_copy_and_is_public(tmp_path: Path) -> None:
    capability, _ = _svc(tmp_path)
    state = capability.agent_state("case_design")
    assert state == {
        "default_uid": "",
        "tool_ids": ["read", "write", "edit", "grep_search", "glob_search", "web_search"],
    }
    state["tool_ids"].append("pwsh")
    state["default_uid"] = "hacked"
    assert capability.agent_state("case_design") == {
        "default_uid": "",
        "tool_ids": ["read", "write", "edit", "grep_search", "glob_search", "web_search"],
    }


def test_effective_uid_is_public_and_rejects_unknown_agent(tmp_path: Path) -> None:
    capability, model_config = _svc(tmp_path, deepseek_api_key="sk-x123456789")
    assert capability.effective_uid("case_design") == "deepseek/deepseek-flash"
    capability.set_agent_default_model("case_design", "deepseek/deepseek-v4-pro")
    assert capability.effective_uid("case_design") == "deepseek/deepseek-v4-pro"
    with pytest.raises(ConfigNotFoundError):
        capability.effective_uid("ghost")
```

- [ ] **Step 3: `test_api_capabilities.py` 同步更名（红）**

全文 `"a1"` → `"case_design"`：`assert [a["id"] for a in body["agents"]] == ["case_design"]`、端点 `/api/capabilities/agents/case_design/...`、`stored["agents"]["case_design"]`。另在 `test_capabilities_seed_view` 里补一条提示词来源断言（证明读的是 md，内容不变）：

```python
    assert "## 职责" in agent["prompt"]
    assert agent["prompt"] == find_agent("case_design").prompt
```

并在该文件顶部补 `from aitester.agents import find_agent`。

- [ ] **Step 4: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_capability_config.py tests/test_api_capabilities.py -q`
Expected: FAIL（`AttributeError: 'CapabilityConfigService' object has no attribute 'agent_state'`、视图仍是 `a1`）

- [ ] **Step 5: 实现——删本地 agent 常量，改用 agents 包**

`backend/src/aitester/services/capability_config.py`：

1. import 段加：

```python
from aitester.agents import AGENT_CATALOG, DEFAULT_AGENT_STATE, LEGACY_AGENT_IDS
```

2. 删除文件里的 `AGENT_CATALOG: list[dict[str, Any]] = [...]`（:81-100，含多行 prompt 串）与 `DEFAULT_AGENT_STATE: dict[...] = {...}`（:102-107）——两者现在来自 `agents` 包，`TOOL_CATALOG` 留在原处不动。

3. `_normalized()` 里在 `raw_agents` 归一之前插入一次性迁移（`agents[agent_id]` 重建循环只遍历 `seed["agents"]`，旧键由此自然消失）：

```python
    raw_agents = config.get("agents")
    raw_agents = raw_agents if isinstance(raw_agents, dict) else {}
    # 一次性迁移：旧 id 的状态搬到新 id（保住用户改过的默认模型与携带工具），旧键随后被丢弃
    for legacy_id, new_id in LEGACY_AGENT_IDS.items():
        if legacy_id in raw_agents and new_id not in raw_agents:
            raw_agents[new_id] = raw_agents[legacy_id]
```

- [ ] **Step 6: 实现——公开读入口 + 视图复用**

同文件，把 `_agent_state`（:197-201）之后补两个公开方法（内部 setter 继续用 `_agent_state` 拿可变引用）：

```python
    def agent_state(self, agent_id: str) -> dict[str, Any]:
        """公开读入口：返回副本，调用方改不坏配置真相。"""
        state = self._agent_state(agent_id)
        return {"default_uid": state["default_uid"], "tool_ids": list(state["tool_ids"])}

    def effective_uid(self, agent_id: str) -> str:
        """该智能体的有效模型：自身默认「可用」则用之，否则回落全局默认（读侧回落，不写回）。"""
        uid = self._agent_state(agent_id)["default_uid"]
        if uid and self._model_config.is_usable_uid(uid):
            return uid
        return self._model_config.default_uid
```

并把 `get_view()` 的 agents 段（:229-244）换成按 `AgentSpec` 构造（响应形状逐字段不变）：

```python
        views: list[dict[str, Any]] = []
        for spec in AGENT_CATALOG:
            state = agents_state[spec.id]
            views.append(
                {
                    "id": spec.id,
                    "icon": spec.icon,
                    "name": spec.name,
                    "desc": spec.desc,
                    "prompt": spec.prompt,
                    "default_uid": state["default_uid"],
                    "effective_uid": self.effective_uid(spec.id),
                    "tool_ids": list(state["tool_ids"]),
                }
            )
        return {"tools": tools, "agents": views}
```

- [ ] **Step 7: 跑测试确认通过 + 全量回归**

Run: `cd backend && uv run pytest tests/test_capability_config.py tests/test_api_capabilities.py -q && uv run pytest -q`
Expected: 全绿。注意：`test_chat_service.py` / `test_api.py` 此时仍应全绿（send 链路还没改，`SendRequest` 默认 id 还是 `a1`）。

- [ ] **Step 8: 提交**

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/services/capability_config.py backend/tests/test_capability_config.py backend/tests/test_api_capabilities.py && git commit -m "refactor(services): 能力配置以 agents 包为单一真相（a1 迁移为 case_design、公开 agent_state/effective_uid）"
```

---

## Task 5: `AgentRuntime`——每请求现装现弃的装配器

**Files:**
- Create: `backend/src/aitester/services/agent_runtime.py`
- Test: `backend/tests/test_agent_runtime.py`（新建）

**Interfaces:**
- Consumes: `find_agent`（Task 1）、`get_graph_builder` / `GraphBuilder`（Task 2）、`ModelConfigService.build_provider`（Task 3）、`CapabilityConfigService.effective_uid / agent_state`（Task 4）、`build_default_registry`（现有）
- Produces:
  - `AgentInstance(agent_id: str, system_prompt: str, provider: LlmProvider, tools: list[AiTooler], build_graph: GraphBuilder)`（frozen dataclass）
  - `AgentRuntime(capability: CapabilityConfigService, model_config: ModelConfigService, observations: FileObservationStore | None = None)`
  - `AgentRuntime.build(agent_id: str, session_id: str, provider_override: LlmProvider | None = None) -> AgentInstance`
  - 错误：未知 `agent_id` → `ConfigNotFoundError(f"未知智能体「{agent_id}」")`（交互层映射 404）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_agent_runtime.py`：

```python
"""装配器单测：实例内容来自数据（提示词/模型/工具），实例本身不持有状态。"""

from pathlib import Path
from typing import Any

import pytest

from aitester.agents import find_agent
from aitester.adapters.llm import MockProvider, ProviderConfigError
from aitester.adapters.llm import openai_compat
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.config import Settings
from aitester.orchestration import build_agent_graph
from aitester.services.agent_runtime import AgentInstance, AgentRuntime
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService
from aitester.storage import FileJsonConfigRepository


def _runtime(
    tmp_path: Path, **settings_kwargs: object
) -> tuple[AgentRuntime, CapabilityConfigService, ModelConfigService]:
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"),
        Settings(_env_file=None, **settings_kwargs),  # type: ignore[arg-type]
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return AgentRuntime(capability, model_config), capability, model_config


class _FakeChat:
    """记录构造参数，避免真建 OpenAI 客户端。"""

    last: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last = dict(kwargs)


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_compat, "ChatOpenAI", _FakeChat)
    _FakeChat.last = {}


def test_unknown_agent_raises_config_not_found(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ConfigNotFoundError) as exc_info:
        runtime.build("ghost", "s1", provider_override=MockProvider())
    assert exc_info.value.detail == "未知智能体「ghost」"


def test_legacy_id_is_not_resolved_by_runtime(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ConfigNotFoundError):
        runtime.build("a1", "s1", provider_override=MockProvider())


def test_prompt_and_builder_come_from_spec(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    spec = find_agent("case_design")
    assert isinstance(instance, AgentInstance)
    assert instance.agent_id == "case_design"
    assert instance.system_prompt == spec.prompt
    assert instance.build_graph is build_agent_graph


def test_agent_default_model_wins_over_global(tmp_path: Path) -> None:
    runtime, capability, _ = _runtime(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
    instance = runtime.build("case_design", "s1")
    assert instance.provider.model_ref == "dashscope/qwen3.7-max"
    assert _FakeChat.last["model"] == "qwen3.7-max"


def test_falls_back_to_global_default_when_agent_default_unusable(tmp_path: Path) -> None:
    runtime, capability, model_config = _runtime(
        tmp_path, deepseek_api_key="sk-x123456789", dashscope_api_key="sk-d123456789"
    )
    capability.set_agent_default_model("case_design", "dashscope/qwen3.7-max")
    model_config.set_model_enabled("dashscope", "qwen3.7-max", False)
    instance = runtime.build("case_design", "s1")
    assert instance.provider.model_ref == "deepseek/deepseek-flash"


def test_nothing_configured_raises_actionable_400(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    with pytest.raises(ProviderConfigError) as exc_info:
        runtime.build("case_design", "s1")
    assert "设置 · 模型设置" in exc_info.value.detail


def test_provider_override_skips_model_resolution(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)  # 全局也没配 Key
    mock = MockProvider()
    instance = runtime.build("case_design", "s1", provider_override=mock)
    assert instance.provider is mock
    assert _FakeChat.last == {}


def test_tools_are_carried_exactly_as_enabled_state(tmp_path: Path) -> None:
    runtime, capability, _ = _runtime(tmp_path)
    capability.set_agent_tools("case_design", ["read", "web_search"])
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    assert [t.tool_id() for t in instance.tools] == ["read", "web_search"]


def test_empty_carrier_list_builds_no_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, capability, _ = _runtime(tmp_path)
    capability.set_agent_tools("case_design", [])

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("无携带工具时不应构建工具注册表")

    monkeypatch.setattr("aitester.services.agent_runtime.build_default_registry", boom)
    instance = runtime.build("case_design", "s1", provider_override=MockProvider())
    assert instance.tools == []


def test_registry_uses_dot_cwd_and_shared_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Registry:
        def get_many(self, tool_ids: list[str]) -> list[Any]:
            return []

    def fake_registry(cwd: str = ".", session_id: str = "default", observed: Any = None) -> Any:
        captured.update({"cwd": cwd, "session_id": session_id, "observed": observed})
        return _Registry()

    monkeypatch.setattr("aitester.services.agent_runtime.build_default_registry", fake_registry)
    model_config = ModelConfigService(
        FileJsonConfigRepository(tmp_path / "model_config.json"), Settings(_env_file=None)
    )
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    store = FileObservationStore()
    runtime = AgentRuntime(capability, model_config, store)
    runtime.build("case_design", "s9", provider_override=MockProvider())
    assert captured["cwd"] == "."  # 项目目录接入是留给项目专项的缝
    assert captured["session_id"] == "s9"
    assert captured["observed"] is store


def test_instances_are_independent_objects(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    mock = MockProvider()  # 复用同一个实例，否则 is 断言恒 False、测不到任何行为
    a = runtime.build("case_design", "s1", provider_override=mock)
    b = runtime.build("case_design", "s1", provider_override=mock)
    assert a is not b
    assert a.provider is b.provider  # 注入的是同一个 mock，但实例本身各自新建
    assert a.tools and len(a.tools) == len(b.tools)
    assert [t.tool_id() for t in a.tools] == [t.tool_id() for t in b.tools]
    assert all(x is not y for x, y in zip(a.tools, b.tools))  # 工具对象也每次新建，不跨请求复用
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'aitester.services.agent_runtime'`

- [ ] **Step 3: 实现 `services/agent_runtime.py`**

```python
"""智能体实例装配：每请求现装现弃，实例不持有状态。

装配器本身是常驻的（无状态服务）：它只做「读数据 → 拼一个一次性实例」。会话历史与
持久记录住在 memory / storage 层并按键隔离，所以切智能体、切会话、切项目都不需要
维护实例生命周期，也没有跨请求可变状态可竞争。
"""

from dataclasses import dataclass
from typing import Any

from aitester.agents import find_agent
from aitester.adapters.llm import LlmProvider
from aitester.adapters.tools import build_default_registry
from aitester.adapters.tools.base import AiTooler
from aitester.adapters.tools.file_tools import FileObservationStore
from aitester.orchestration.graph_registry import GraphBuilder, get_graph_builder
from aitester.services.capability_config import CapabilityConfigService
from aitester.services.model_config import ConfigNotFoundError, ModelConfigService


@dataclass(frozen=True)
class AgentInstance:
    agent_id: str
    system_prompt: str
    provider: LlmProvider
    tools: list[AiTooler]
    build_graph: GraphBuilder


class AgentRuntime:
    """装配器（组合根）：产出一次性 AgentInstance。"""

    def __init__(
        self,
        capability: CapabilityConfigService,
        model_config: ModelConfigService,
        observations: FileObservationStore | None = None,
    ) -> None:
        self._capability = capability
        self._model_config = model_config
        self._observations = observations if observations is not None else FileObservationStore()

    def build(
        self,
        agent_id: str,
        session_id: str,
        provider_override: LlmProvider | None = None,
    ) -> AgentInstance:
        spec = find_agent(agent_id)
        if spec is None:
            raise ConfigNotFoundError(f"未知智能体「{agent_id}」")

        provider: LlmProvider
        if provider_override is not None:
            provider = provider_override  # 注入即短路，不解析模型（测试缝与内部调用同一入口）
        else:
            provider = self._model_config.build_provider(
                self._capability.effective_uid(agent_id)
            )

        state: dict[str, Any] = self._capability.agent_state(agent_id)
        tools: list[AiTooler] = []
        if state["tool_ids"]:
            registry = build_default_registry(
                cwd=".", session_id=session_id, observed=self._observations
            )
            tools = registry.get_many(state["tool_ids"])

        return AgentInstance(
            agent_id=spec.id,
            system_prompt=spec.prompt,
            provider=provider,
            tools=tools,
            build_graph=get_graph_builder(spec.graph_builder),
        )
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd backend && uv run pytest tests/test_agent_runtime.py -q && uv run pytest -q`
Expected: 该文件 11 passed；全量零失败

说明（与 spec §4 第 1 步的对应关系）：spec 写的是「`find_agent(agent_id)`；未知 → `ConfigNotFoundError`」。因为 `agents` 包必须零依赖（只 import 标准库），它不能 import 住在 `services.model_config` 的 `ConfigNotFoundError`，所以落地为：`find_agent` 返回 `None`，由 `AgentRuntime.build` 抛 `ConfigNotFoundError`。观测语义与 spec 完全一致（未知 id → 同一条中文文案 → HTTP 404），只是抛错点上移到有依赖权限的层。

- [ ] **Step 5: 提交**

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/services/agent_runtime.py backend/tests/test_agent_runtime.py && git commit -m "feat(services): AgentRuntime 装配器——按 agent_id 现装现弃一次性实例"
```

---

## Task 6: 端到端接线（ChatService / create_app / router / schemas / 会话键）

**Files:**
- Modify: `backend/src/aitester/services/chat.py:13-83`
- Modify: `backend/src/aitester/main.py:15-36`
- Modify: `backend/src/aitester/interaction/router.py:1-68`
- Modify: `backend/src/aitester/interaction/schemas.py:17`
- Test: `backend/tests/test_chat_service.py:41-81`、`backend/tests/test_api.py:63-108`（改造）+ 新增断言

**Interfaces:**
- Consumes: `AgentRuntime.build(...)`（Task 5）、`run_graph`（Task 2）、`find_agent`（Task 1）
- Produces:
  - `ChatService(provider=None, memory=None, context=None, repo=None, agent_runtime=None)`（**去掉 `model_config` 参数**：provider 解析已归口 runtime）
  - `ChatService.send(session_id: str, message: str, agent_id: str) -> dict[str, Any]`（不再有 `tools` 参数；`agent_id` **必填**，默认值只有一个落点＝`SendRequest.agent_id`）
  - `ChatService.echo(session_id, message)` 行为与 trace 一字不变
  - send 的 memory/storage 键 = `f"{agent_id}:{session_id}"`
  - `POST /api/chat/send` 请求体 `{session_id, message, agent_id="case_design"}`；未知 `agent_id` → 404
  - `app.state.agent_runtime`

- [ ] **Step 1: 改 `test_chat_service.py` 的 send 测试（红）**

把该文件里四条 send 测试替换为（echo 两条与顶部 import 保持；import 补 `AgentRuntime` / `CapabilityConfigService`）：

```python
from aitester.services.agent_runtime import AgentRuntime
from aitester.services.capability_config import CapabilityConfigService


def _runtime(tmp_path, **settings_kwargs: object) -> AgentRuntime:
    model_config = _model_config(tmp_path, **settings_kwargs)
    capability = CapabilityConfigService(
        FileJsonConfigRepository(tmp_path / "capability_config.json"), model_config
    )
    return AgentRuntime(capability, model_config)


def test_send_uses_injected_provider_and_reports_model(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path))
    result = svc.send("s1", "生成用例", "case_design")
    assert result["reply"] == "[mock] 生成用例"
    assert result["model"] == "mock/mock"
    assert result["trace"] == [
        "services",
        "context",
        "orchestration",
        "adapters",
        "memory",
        "storage",
    ]


def test_send_scopes_history_by_agent_and_session(tmp_path) -> None:
    svc = ChatService(provider=MockProvider(), agent_runtime=_runtime(tmp_path))
    svc.send("s1", "生成用例", "case_design")
    assert svc.memory.recall("case_design:s1")
    assert svc.memory.recall("s1") == []  # 与 echo 的裸键互不串
    assert svc.repo.get("session:case_design:s1") == {
        "session_id": "case_design:s1",
        "last_reply": "[mock] 生成用例",
    }
    svc.echo("s1", "echo 一句")
    assert [m["content"] for m in svc.memory.recall("s1")] == ["echo 一句", "[mock] echo 一句"]


def test_send_requires_agent_runtime() -> None:
    svc = ChatService(provider=MockProvider())
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design")
    assert "服务未装配智能体运行时，请通过 create_app 启动后端" in exc_info.value.detail


def test_send_uses_agent_system_prompt_from_md(tmp_path) -> None:
    seen: list[list[object]] = []

    class _SpyProvider:
        """独立假 provider，bind_tools 返回自身——MockProvider.bind_tools 会返回新的
        MockProvider()，用它做子类会把 spy 丢掉，断言永远抓不到消息。"""

        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            return "[spy]"

        def bind_tools(self, tools: list) -> "_SpyProvider":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    svc = ChatService(provider=_SpyProvider(), agent_runtime=_runtime(tmp_path))
    result = svc.send("s1", "生成用例", "case_design")
    assert result["reply"] == "[spy] 收到"
    assert type(seen[0][0]).__name__ == "SystemMessage"
    assert str(seen[0][0].content) == find_agent("case_design").prompt


def test_send_resolves_default_from_model_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            recorded.update(kwargs)

        def bind_tools(self, tools: list) -> "FakeChatOpenAI":
            return self

        def invoke(self, messages: list[dict[str, str]]) -> object:
            return type("R", (), {"content": "真实回复"})()

    monkeypatch.setattr(openai_compat, "ChatOpenAI", FakeChatOpenAI)
    svc = ChatService(
        agent_runtime=_runtime(tmp_path, deepseek_api_key="sk-x123456789")
    )
    result = svc.send("s1", "hi", "case_design")
    assert result["reply"] == "真实回复"
    assert result["model"] == "deepseek/deepseek-flash"
    assert recorded["model"] == "deepseek-flash"


def test_send_without_usable_default_raises_actionable_config_error(tmp_path) -> None:
    svc = ChatService(agent_runtime=_runtime(tmp_path))
    with pytest.raises(ProviderConfigError) as exc_info:
        svc.send("s1", "hi", "case_design")
    assert "设置 · 模型设置" in exc_info.value.detail
```

该文件顶部 import 补两行：`from aitester.agents import find_agent`、`from langchain_core.messages import AIMessage`。旧的 `test_send_uses_injected_provider_and_reports_model` / `test_send_resolves_default_from_model_config` / `test_send_without_usable_default_raises_actionable_config_error` 三条被上面同名版本取代，`ChatService(...)` 里传的 `model_config=` 参数全部换成 `agent_runtime=_runtime(tmp_path, ...)`。

为什么 `FakeChatOpenAI` 要加 `bind_tools`：改造后 send 一定走图，而种子配置里 `case_design` 携带 6 件工具 → 走工具循环 → `OpenAICompatProvider.bind_tools` 会调 `self._client.bind_tools(...)`，假件没有这个方法就 `AttributeError`。

- [ ] **Step 2: 改 `test_api.py` 的 send 测试（红）**

`test_send_uses_injected_provider_and_reports_model` 的 override 加 runtime，并新增 404 两条。文件顶部 import 补：

```python
from aitester.agents import find_agent
from langchain_core.messages import AIMessage
from aitester.services.agent_runtime import AgentRuntime
```

```python
def test_send_uses_injected_provider_and_reports_model(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=MockProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s2", "message": "生成用例"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "[mock] 生成用例"
    assert body["trace"] == ALL_LAYERS
    assert body["model"] == "mock/mock"


def test_send_with_unknown_agent_returns_404(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=MockProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s3", "message": "hi", "agent_id": "ghost"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "未知智能体「ghost」"


def test_send_with_legacy_agent_id_returns_404(tmp_path: Path) -> None:
    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=MockProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"message": "hi", "agent_id": "a1"}
    )
    assert resp.status_code == 404
    assert "未知智能体「a1」" in resp.json()["detail"]


def test_send_uses_agent_prompt_and_default_agent_id(tmp_path: Path) -> None:
    seen: list[list[object]] = []

    class _SpyProvider:
        name = "spy"
        model_ref = "spy/model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            return "[spy]"

        def bind_tools(self, tools: list) -> "_SpyProvider":
            return self

        def invoke_messages(self, messages: list) -> AIMessage:
            seen.append(list(messages))
            return AIMessage(content="[spy] 收到")

    application = create_app(
        model_config_path=tmp_path / "m.json",
        capability_config_path=tmp_path / "c.cap.json",
        settings=Settings(_env_file=None),
    )
    application.state.chat_service = ChatService(
        provider=_SpyProvider(), agent_runtime=application.state.agent_runtime
    )
    resp = TestClient(application).post(
        "/api/chat/send", json={"session_id": "s4", "message": "生成登录用例"}
    )
    assert resp.status_code == 200
    # 请求体不带 agent_id 时取 SendRequest 默认值 case_design；系统提示词来自 md
    assert str(seen[0][0].content) == find_agent("case_design").prompt
    assert resp.json()["reply"] == "[spy] 收到"
```

`test_send_upstream_failure_returns_502` 的 override 同样改成 `ChatService(provider=FailingProvider(), agent_runtime=application.state.agent_runtime)`；`test_send_without_configured_default_returns_400` 不动（走真实装配链路，空配置 → 400 文案仍含「设置 · 模型设置」）。

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_chat_service.py tests/test_api.py -q`
Expected: FAIL——`ChatService.__init__() got an unexpected keyword argument 'agent_runtime'`、`create_app` 无 `agent_runtime` state、send 返回 400 而非 404

- [ ] **Step 4: 改 `services/chat.py`**

先改 import 段（文件顶部 :1-11）：`ModelConfigService` 那行删掉，`from aitester.orchestration import run_agent, run_echo` 改成 `from aitester.orchestration import run_echo, run_graph`，并补下面三行；其余 import（`Any`、`LlmProvider/MockProvider/ProviderConfigError`、`AiTooler`、context/memory/storage、`SYSTEM_PROMPT`、`_to_langchain_messages`）保持不变。

```python
from aitester.orchestration import run_echo, run_graph
from aitester.orchestration.graph_registry import GraphBuilder
from aitester.services.agent_runtime import AgentRuntime
```

再把 `ChatService` 的 `__init__`、`_complete`、`echo`、`send` 整体换成：

```python
class ChatService:
    """业务门面：装配四层并记录穿透 trace。"""

    def __init__(
        self,
        provider: LlmProvider | None = None,
        memory: MemoryStore | None = None,
        context: ContextBuilder | None = None,
        repo: Repository | None = None,
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self.provider = provider
        self.agent_runtime = agent_runtime
        self.memory = memory or InMemoryMemoryStore()
        self.context = context or PassthroughContextBuilder()
        self.repo = repo or InMemoryRepository()

    def _complete(
        self,
        key: str,
        message: str,
        provider: LlmProvider,
        system_prompt: str,
        build: GraphBuilder | None = None,
        tools: list[AiTooler] | None = None,
    ) -> dict[str, Any]:
        trace: list[str] = ["services"]

        history = self.memory.recall(key)
        messages = self.context.build(system_prompt, history, message)
        trace.append("context")

        trace += ["orchestration", "adapters"]
        if build is None:
            reply = run_echo(provider, messages)
        else:
            result = run_graph(build, provider, tools or [], _to_langchain_messages(messages))
            reply = result["reply"]
            for tt in result["tool_traces"]:
                trace.append(f"tool:{tt['tool']}")

        self.memory.save(key, "user", message)
        self.memory.save(key, "assistant", reply)
        trace.append("memory")

        self.repo.put(f"session:{key}", {"session_id": key, "last_reply": reply})
        trace.append("storage")

        return {"reply": reply, "trace": trace, "model": provider.model_ref}

    def echo(self, session_id: str, message: str) -> dict[str, Any]:
        """七层 trace 回归链路：永远走 mock，与真实 provider 配置无关。"""
        result = self._complete(session_id, message, MockProvider(), SYSTEM_PROMPT)
        return {"reply": result["reply"], "trace": result["trace"]}

    def send(
        self,
        session_id: str,
        message: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """真实链路：装配一次性实例（提示词/模型/工具/拓扑）后按图执行。"""
        if self.agent_runtime is None:
            raise ProviderConfigError(
                "服务未装配智能体运行时，请通过 create_app 启动后端"
            )
        instance = self.agent_runtime.build(
            agent_id, session_id, provider_override=self.provider
        )
        return self._complete(
            f"{instance.agent_id}:{session_id}",
            message,
            instance.provider,
            instance.system_prompt,
            build=instance.build_graph,
            tools=instance.tools,
        )
```

`trace` 里 `orchestration / adapters` 的位置在工具 trace 之前，与改造前一致（`ALL_LAYERS` 断言不受影响）。

- [ ] **Step 5: 改 `main.py` 装配 runtime**

```python
from aitester.services.agent_runtime import AgentRuntime
```

`create_app` 里的装配段（:30-35）替换为：

```python
    application = FastAPI(title="AiTester backend")
    application.state.model_config = model_config
    application.state.capability_config = capability_config
    application.state.file_observations = FileObservationStore()
    application.state.agent_runtime = AgentRuntime(
        capability_config, model_config, application.state.file_observations
    )
    application.state.chat_service = ChatService(agent_runtime=application.state.agent_runtime)
    application.include_router(router)
```

- [ ] **Step 6: 改 `interaction/router.py` 与 `schemas.py`**

`chat_send`（:43-68）整体替换——工具装配移出交互层，只保留协议转换与错误映射：

```python
@router.post("/chat/send", response_model=SendResponse)
def chat_send(req: SendRequest, request: Request) -> SendResponse:
    service: ChatService = request.app.state.chat_service
    try:
        result = service.send(req.session_id, req.message, req.agent_id)
    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except ProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    return SendResponse(
        reply=result["reply"],
        trace=["interaction"] + result["trace"],
        model=result["model"],
    )
```

并删除该文件里已不再使用的 import：`from aitester.adapters.tools import build_default_registry`。`schemas.py:17` 改：

```python
class SendRequest(BaseModel):
    session_id: str = "default"
    message: str = Field(min_length=1)
    agent_id: str = "case_design"
```

- [ ] **Step 7: 跑全量回归 + 扫残留**

Run: `cd backend && uv run pytest -q`
Expected: 全绿（0 failed）

若仍红，八成是 `a1` 或旧参数没改干净，按下面两条定位：

```bash
cd backend && uv run pytest -q 2>&1 | tail -30
grep -rn '"a1"\|a1\b' src tests | grep -v LEGACY_AGENT_IDS
grep -rn 'model_config=\|tools=' src/aitester/services/chat.py src/aitester/interaction/router.py tests/test_chat_service.py
```

Expected: 第一条 grep 只命中 `LEGACY_AGENT_IDS` 相关与旧 id 迁移测试；第二条 grep 无命中（`ChatService` 已不接 `model_config`，`send` 已不接 `tools`）。

- [ ] **Step 8: 真机看一眼装配完整性（本机无 Key，用 provider 注入这条路）**

这一步会构造真实 `create_app()`，因此**会把本机 `backend/data/capability_config.json` 的 `a1` 键迁移成 `case_design`**——这是设计内的行为，且用户已改的状态原样保留（下一步验证）。

```bash
cd backend && uv run python -c "
from aitester.adapters.llm import MockProvider
from aitester.config import Settings
from aitester.main import create_app
app = create_app(settings=Settings(_env_file=None))
r = app.state.agent_runtime.build('case_design', 's1', provider_override=MockProvider())
print('prompt 首行:', r.system_prompt.splitlines()[0])
print('tools:', [t.tool_id() for t in r.tools])
print('builder:', r.build_graph.__name__)
"
```

Expected: 首行 `你是「用例设计智能体」，服务对象是软件测试工程师。`；`tools` 为已启用且已携带的清单（本机含 `pwsh`，因为用户手勾过）；`builder: build_agent_graph`。

- [ ] **Step 9: 确认本地配置的旧键已被真实迁移且状态没丢**

```bash
cd backend && uv run python -c "
import json
d = json.load(open('data/capability_config.json', encoding='utf-8'))
print(list(d['agents']), d['agents']['case_design']['tool_ids'])
"
```

Expected: `['case_design'] ['read', 'write', 'edit', 'grep_search', 'glob_search', 'pwsh', 'web_search']`——键名换了，用户勾的 `pwsh` 与其余携带项一个不少。

- [ ] **Step 10: 提交**

```bash
cd /d/code/github/AiTester && git add backend/src/aitester/services/chat.py backend/src/aitester/main.py backend/src/aitester/interaction backend/tests/test_chat_service.py backend/tests/test_api.py && git commit -m "feat(chat): send 按 agent_id 装配一次性实例（提示词/模型/工具/拓扑 + 会话键 scoped + 未知智能体 404）"
```

---

## Task 7: 分发与全量验收（wheel 带 md、后端全绿、前端零改动仍绿）

**Files:**
- Verify: `backend/pyproject.toml`（仅当 wheel 缺 md 才改）
- Verify: `README.md` / 设置弹窗人工一眼

- [ ] **Step 1: 验证 wheel 是否带上提示词 md**

```bash
cd backend && uv build --wheel -q && python -c "
import glob, zipfile
w = sorted(glob.glob('dist/*.whl'))[-1]
hits = [n for n in zipfile.ZipFile(w).namelist() if n.endswith('agents/prompts/case_design.md')]
print(w); print(hits); assert hits, 'wheel 里没有 md'
" && rm -rf dist
```

Expected: 打印出非空 `hits`。若断言失败（hatchling 没把 md 收进包），在 `backend/pyproject.toml` 的 `[tool.hatch.build.targets.wheel]` 段后补一条并重建验证：

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/aitester/agents/prompts" = "aitester/agents/prompts"
```

- [ ] **Step 2: 后端全量回归**

Run: `cd backend && uv run pytest -q`
Expected: 全部通过、0 failed（基线 177 + 本计划新增，实测收口为 **214 passed**）

- [ ] **Step 3: 前端零改动仍绿**

Run: `cd frontend && npm run build`
Expected: `tsc && vite build` 通过（本计划不动 `frontend/**`；已核实前端不引用 `a1`、不调 `chat/send`）

- [ ] **Step 4: 起服务，人看一眼设置弹窗（提示词照常显示）**

```bash
cd backend && uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000   # 后台跑
cd frontend && npm run dev                                                     # 后台跑
```

打开 `http://localhost:5173` → ⚙ 设置 · 能力设置：智能体卡片标题应是「📋 用例设计智能体」，提示词预览显示完整「## 职责 / ## 约束」原文，携带工具勾选态与迁移前一致（本机含 pwsh 为勾选）。

- [ ] **Step 5: 提交（若 Step 1 未触发，则无代码改动，跳过本步）**

```bash
cd /d/code/github/AiTester && git add backend/pyproject.toml && git commit -m "build(backend): wheel 显式带上智能体提示词 md"
```

---

## Task 8: 真机冒烟（前提：用户先配好 API Key）

**阻塞前提：** 本机 `backend/data/model_config.json` 两个提供商的 Key 均为空、`default_uid` 为空。**必须由用户在「⚙ 设置 · 模型设置」填 Key 并选默认模型**，此任务才能跑；不得替用户写入或打印任何 Key。

**Files:** 无代码改动（验证 + 必要时把发现记进 spec 修订记录）。

- [ ] **Step 1: 起后端并确认健康**

```bash
cd backend && uv run uvicorn aitester.main:app --host 127.0.0.1 --port 8000 &
curl -s http://127.0.0.1:8000/api/health
```
Expected: `llm_provider` 不再是 `"mock"`，而是用户配置的 `提供商/模型`。

- [ ] **Step 2: 验证身份（提示词真进来了）**

```bash
curl -s -X POST http://127.0.0.1:8000/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{"session_id": "smoke-1", "message": "为登录模块设计 5 条用例，只给用例表", "agent_id": "case_design"}'
```
Expected: `reply` 是中文 Markdown 用例表（带优先级列，符合提示词「统一用例表」约束）；`model` 等于该智能体的有效模型；`trace` 以 `interaction` 开头且含 `services/context/orchestration/adapters/memory/storage`。

- [ ] **Step 3: 验证工具注入与循环可用（读一个真实文件）**

```bash
curl -s -X POST http://127.0.0.1:8000/api/chat/send \
  -H "Content-Type: application/json" \
  -d '{"session_id": "smoke-2", "message": "用 read 工具读 backend/README.md 的前 5 行并复述", "agent_id": "case_design"}'
```
Expected: `trace` 里出现 `tool:read`，回复内容与文件真实开头一致（不是编造）。若该智能体未携带 `read`，先在设置 · 能力里勾上再跑。

- [ ] **Step 4: 验证回落与 404 语义（真机一次）**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/chat/send \
  -H "Content-Type: application/json" -d '{"agent_id": "ghost", "message": "hi"}'
```
Expected: `404`；`{"detail": "未知智能体「ghost」"}`。

- [ ] **Step 5: 把冒烟结论记进 spec（仅在发现与设计不符时改文档）**

若三步全部符合设计，无需改动文档；若发现偏差，按 §9 修订记录格式在 `docs/superpowers/specs/2026-09-30-agent-instance-design.md` 追加一条「实测偏差 + 处置」，与用户确认后提交。

- [ ] **Step 6: 清理后台进程**

```bash
curl -s -o /dev/null http://127.0.0.1:8000/api/health; echo "手动结束 uvicorn / vite 两个后台进程（不 kill 用户的其他 python 进程）"
```
